"""The overlapped converter writes the same file as the serial one.

A video conversion runs three stages - decode with the guides, the network,
and encode - and they now run on three threads. Overlap is the kind of
change that pays for itself in speed and charges for it in ways that do not
show up as an error: a frame written in the wrong place, audio that lands
after the picture it belonged in front of, a timestamp taken from a counter
instead of the source, or a cancel that leaves a thread running and a
half-written file behind.

So this test converts the same clip twice, once with the stages overlapped
and once with them on one thread, and requires the two files to agree frame
for frame - including the order, which is checked with a marker the encoder
cannot smear away.

The worker is a stub: it sleeps for a few milliseconds, like a card would,
and gives the frame back with that marker on it. Nothing here needs a GPU,
which is the point - the threading is what is being checked, and it is the
same threading whatever the worker is.
"""
import os
import sys
import tempfile
import threading
import time
from fractions import Fraction
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import numpy as np  # noqa: E402

import media_convert  # noqa: E402
from guides import TemporalGuideGenerator  # noqa: E402
from settings_io import PROFILES  # noqa: E402

#: The stub's cost per frame. Long enough that the stages have something to
#: overlap, short enough that the test is over in seconds.
STUB_MS = 4.0

#: The marker's ladder: levels far enough apart to survive x264, and a cycle
#: short enough that a frame swapped with its neighbour still shows.
MARK_STEP, MARK_BASE, MARK_CYCLE = 28, 20, 8


class StubEngine:
    """A worker that costs a few milliseconds and marks the frame."""

    def __init__(self, params, width, height, work_w, work_h, nr_passes=1):
        self.width, self.height = width, height
        self.work_w, self.work_h = work_w, work_h
        self.motion_small = True
        self.out_shm = True
        self.motion_shapes: set = set()
        self.resets: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    @property
    def motion_size(self):
        return media_convert.flow_size(self.work_w, self.work_h)

    def evaluate(self, index, rgba, motion, reset):
        self.motion_shapes.add(tuple(motion.shape))
        self.resets.append(bool(reset))
        # A sleep, not a spin: the real call waits on the reader's queue and
        # releases the GIL there, and a spin would starve the stages this
        # test is about.
        time.sleep(STUB_MS / 1000.0)
        out = rgba.copy()
        out[:32, :32] = MARK_STEP * (index % MARK_CYCLE) + MARK_BASE
        out[:32, :32, 3] = 255
        return out


def make_clip(av, path: Path, w: int, h: int, frames: int, audio: bool) -> None:
    rng = np.random.default_rng(5)
    noise = rng.integers(0, 255, size=(h + 32, w + 32, 3), dtype=np.uint8)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=30)
    stream.width, stream.height = w, h
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "ultrafast", "crf": "20"}
    track = None
    if audio:
        track = container.add_stream("aac", rate=48000)
        track.layout = "stereo"
    done = 0
    for i in range(frames):
        dx, dy = (i * 2) % 32, (i * 3) % 32
        rgb = np.ascontiguousarray(noise[dy:dy + h, dx:dx + w])
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame.pts, frame.time_base = i, Fraction(1, 30)
        for packet in stream.encode(frame):
            container.mux(packet)
        if track is not None:
            n = 1600  # 1/30 s at 48 kHz
            t = np.arange(done, done + n) / 48000.0
            wave = (np.sin(2 * np.pi * 330 * t) * 9000).astype(np.int16)
            sound = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(np.stack([wave, wave]).reshape(1, -1)),
                format="s16", layout="stereo")
            sound.sample_rate, sound.pts = 48000, done
            sound.time_base = Fraction(1, 48000)
            done += n
            for packet in track.encode(sound):
                container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    if track is not None:
        for packet in track.encode(None):
            container.mux(packet)
    container.close()


def probe(av, path: Path) -> dict:
    """The times, the markers and the audio a written file really holds."""
    container = av.open(str(path))
    audio = next((s for s in container.streams if s.type == "audio"), None)
    times, marks, packets = [], [], 0
    for packet in container.demux():
        if packet.stream.type == "audio":
            packets += 1 if packet.dts is not None else 0
            continue
        for frame in packet.decode():
            times.append(round(float(frame.time), 6))
            block = frame.to_ndarray(format="rgba")[4:28, 4:28, 0]
            marks.append(round(float(block.mean()), 3))
    container.close()
    return {"times": times, "marks": marks, "audio_packets": packets,
            "audio": audio.codec_context.name if audio else None}


def convert(av, src: Path, out: Path, params: dict, overlapped: bool,
            cancel=None, engines=None) -> object:
    """One conversion with the stages either overlapped or on one thread."""
    engine_holder = {}

    def factory(*args, **kwargs):
        engine = StubEngine(*args, **kwargs)
        engine_holder["engine"] = engine
        if engines is not None:
            engines.append(engine)
        return engine

    real_engine, real_chain = media_convert._Engine, media_convert.codec_chain
    was = media_convert.PIPELINE
    media_convert._Engine = factory
    # No NVENC: this test is about threads, and a machine without a card
    # must run it. x264 ends every chain anyway.
    media_convert.codec_chain = lambda choice: ()
    media_convert.PIPELINE = overlapped
    try:
        return media_convert.convert_video(src, out, params, work_scale=0.65,
                                           nr_small=True, quality="small",
                                           codec="h264", cancel=cancel)
    finally:
        media_convert._Engine = real_engine
        media_convert.codec_chain = real_chain
        media_convert.PIPELINE = was


def main() -> int:
    try:
        import av
    except Exception as exc:                       # pragma: no cover
        print(f"SKIP: PyAV is not available ({exc})")
        return 0

    failures: list = []
    params = dict(PROFILES["Natural"])
    params["style"] = 1

    # 1. The flow size the engine assumes is the one the guides produce.
    for w, h in ((1920, 1080), (1280, 720), (1248, 702), (640, 360),
                 (320, 180), (64, 64), (2560, 1440), (66, 150)):
        guides = TemporalGuideGenerator(w, h)
        if (guides.flow_width, guides.flow_height) != media_convert.flow_size(w, h):
            failures.append(
                f"flow_size{(w, h)} is {media_convert.flow_size(w, h)}, the "
                f"guides make {(guides.flow_width, guides.flow_height)}")

    # 2. A lane hands nothing over once it is stopped.
    stop = threading.Event()
    lane = media_convert._Lane(1, stop)
    if not lane.put("first"):
        failures.append("a lane with room refused an item")
    stop.set()
    if lane.put("second"):
        failures.append("a stopped lane took an item")
    if lane.get() is not None:
        failures.append("a stopped lane handed an item over")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        for w, h, frames, audio in ((320, 180, 36, True), (480, 270, 24, False)):
            src = folder / f"src_{w}x{h}.mp4"
            make_clip(av, src, w, h, frames, audio)
            source = probe(av, src)

            engines: list = []
            serial = folder / f"serial_{w}x{h}.mp4"
            overlap = folder / f"overlap_{w}x{h}.mp4"
            t0 = time.perf_counter()
            convert(av, src, serial, params, overlapped=False, engines=engines)
            serial_took = time.perf_counter() - t0
            t0 = time.perf_counter()
            convert(av, src, overlap, params, overlapped=True, engines=engines)
            overlap_took = time.perf_counter() - t0

            got_serial, got_overlap = probe(av, serial), probe(av, overlap)
            label = f"{w}x{h}"
            if got_serial["times"] != source["times"]:
                failures.append(f"{label}: the serial timestamps do not follow "
                                f"the source")
            if got_overlap["times"] != got_serial["times"]:
                failures.append(f"{label}: the overlapped timestamps differ "
                                f"from the serial ones")
            if got_overlap["marks"] != got_serial["marks"]:
                failures.append(f"{label}: the frames themselves differ")
            if len(got_overlap["marks"]) != len(source["times"]):
                failures.append(f"{label}: {len(got_overlap['marks'])} frames "
                                f"written, {len(source['times'])} in the source")
            # The marker climbs inside each group and drops once between two,
            # so a frame that moved shows as a drop where none belongs.
            marks = got_overlap["marks"]
            drops = [i for i in range(1, len(marks)) if marks[i] <= marks[i - 1]]
            if [i for i in drops if i % MARK_CYCLE != 0]:
                failures.append(f"{label}: frames are out of order at "
                                f"{[i for i in drops if i % MARK_CYCLE][:4]}")
            if len(drops) != (len(marks) - 1) // MARK_CYCLE:
                failures.append(f"{label}: {len(drops)} order drops, expected "
                                f"{(len(marks) - 1) // MARK_CYCLE}")
            if audio and got_overlap["audio_packets"] != got_serial["audio_packets"]:
                failures.append(f"{label}: {got_overlap['audio_packets']} audio "
                                f"packets, the serial run wrote "
                                f"{got_serial['audio_packets']}")
            if audio and not got_overlap["audio_packets"]:
                failures.append(f"{label}: the overlapped run carried no audio")
            # The field the worker is handed is the flow-size one, once.
            shapes = set().union(*(e.motion_shapes for e in engines))
            flow_w, flow_h = media_convert.flow_size(
                *media_convert.processing_size(w, h, 0.65, True, 1))
            if shapes != {(flow_h, flow_w, 2)}:
                failures.append(f"{label}: the worker was handed {shapes}, "
                                f"expected {{{(flow_h, flow_w, 2)}}}")
            print(f"  {label}: serial {serial_took:5.2f}s  "
                  f"overlapped {overlap_took:5.2f}s  "
                  f"({serial_took / max(overlap_took, 1e-9):.2f}x), "
                  f"{len(marks)} frames, audio {got_overlap['audio']}")

        # 3. A cancel stops every stage and leaves nothing behind.
        src = folder / "src_320x180.mp4"
        cancelled = folder / "cancelled.mp4"
        cancel = threading.Event()
        cancel.set()
        try:
            convert(av, src, cancelled, params, overlapped=True, cancel=cancel)
            failures.append("a cancelled conversion returned a result")
        except media_convert.ConversionCancelled:
            pass
        except Exception as exc:
            failures.append(f"a cancel raised {type(exc).__name__}: {exc}")
        if cancelled.exists():
            failures.append("a cancelled conversion left its output behind")
        if list(folder.glob("*.partial")):
            failures.append("a cancelled conversion left a .partial behind")
        for thread in threading.enumerate():
            if thread.name.startswith("convert-"):
                failures.append(f"{thread.name} is still running after a cancel")

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)}")
        for line in failures:
            print("  -", line)
        return 1
    print("OK: the overlapped stages write the file the serial loop writes - "
          "same frames, same order, same timestamps, same audio - and a "
          "cancel stops all three")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
