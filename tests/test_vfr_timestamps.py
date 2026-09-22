"""A variable-rate source must not be written on its own average rate.

The regression this covers killed a real conversion of a real recording at the
last step - after every frame had been through the network:

    Application provided invalid, non monotonically increasing dts to muxer
    in stream 0: 1459324 >= 1459324

The recorder writes VFR files, and `stream.average_rate` is an average over the
whole file: the real one was 19620000/364831 = 53.778 fps for frames sitting on
a 60 fps grid. 1/rate is then 0.018595 s while frames arrive every 0.016667 s -
1.12 frames per encoder tick - so two adjoining frames round onto the same tick
and the mp4 muxer refuses the stream.

So the source below declares exactly that average rate over frames spaced on a
60 fps grid, which is what the recorder produced. The fix writes the frames on
the source's own grid (its time_base) instead of 1/average_rate; both halves are
asserted here, with no GPU and no worker involved.
"""
import os
import struct
import sys
import tempfile
from fractions import Fraction
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import av  # noqa: E402
import numpy as np  # noqa: E402

from media_convert import (_add_video_stream, _encoder_grid,  # noqa: E402
                           SOFTWARE_CODEC)

WIDTH, HEIGHT = 128, 72
GRID = Fraction(1, 60000)                 # the grid the frames sit on
TICKS_PER_FRAME = 1000                    # 1000/60000 s = one 60 fps slot
VFR_RATE = Fraction(19620000, 364831)     # 53.778 fps - the real VFR average
GAPS = (1, 1, 1, 1, 1, 1, 1, 2)           # 60 fps with frames dropped
FRAMES = 330


def build_vfr_source(path: Path) -> list[float]:
    """A file whose frames are on GRID while its average rate is coarser."""
    container = av.open(str(path), mode="w", format="mp4")
    stream = container.add_stream(SOFTWARE_CODEC, rate=VFR_RATE)
    stream.width, stream.height = WIDTH, HEIGHT
    stream.pix_fmt = "yuv420p"
    stream.codec_context.time_base = GRID
    stream.open()

    times, tick = [], 0
    for index in range(FRAMES):
        image = np.full((HEIGHT, WIDTH, 3), (index * 7) % 255, np.uint8)
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        frame.pts = tick
        frame.time_base = GRID
        for packet in stream.encode(frame):
            container.mux(packet)
        times.append(float(tick * GRID))
        tick += GAPS[index % len(GAPS)] * TICKS_PER_FRAME
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return times


def mp4_timescale(path: Path) -> int:
    """The timescale in the video track's mdhd, read out of the bytes.

    The container's time_base becomes this number, and it is what a player
    multiplies timestamps by - a huge timescale is a real defect (a 32-bit
    player overflows it), not a cosmetic one.
    """
    data = path.read_bytes()
    pos = data.find(b"mdhd")
    if pos < 0:
        return -1
    if data[pos + 4] == 0:
        return struct.unpack(">I", data[pos + 16:pos + 20])[0]
    return struct.unpack(">I", data[pos + 24:pos + 28])[0]


def write_like_the_converter(path: Path, rate, grid, source_times) -> str:
    """Encode frames the way convert_video does; return "" or the failure.

    The rate is passed in rather than read off the stream: the source container
    is closed by the time the second run happens, and a stream of a closed
    container cannot be read.
    """
    container = av.open(str(path), mode="w", format="mp4")
    stream = _add_video_stream(container, SOFTWARE_CODEC, rate, WIDTH, HEIGHT,
                               "balanced", grid=grid)
    container.start_encoding()
    time_base = Fraction(stream.time_base)
    last = -1
    try:
        for when in source_times:
            image = np.full((HEIGHT, WIDTH, 3), 96, np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            pts = int(round(when / float(time_base)))
            pts = max(pts, last + 1)
            last = pts
            frame.pts = pts
            frame.time_base = time_base
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
        container.close()
        return ""
    except Exception as exc:
        try:
            container.close()
        except Exception:
            pass
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        source_path = folder / "vfr.mp4"
        times = build_vfr_source(source_path)

        source_container = av.open(str(source_path))
        source = next(s for s in source_container.streams if s.type == "video")
        average = Fraction(source.average_rate).limit_denominator(1001 * 1000)
        own_grid = Fraction(source.time_base)
        source_container.close()

        gaps = sorted({round(times[i + 1] - times[i], 6)
                       for i in range(len(times) - 1)})
        shortest = min(gaps)

        # 1. The premise: the file is variable-rate, and its average rate is
        #    coarser than the spacing of its frames. Without both, the rest of
        #    the test proves nothing about the real bug.
        if len(gaps) < 2:
            failures.append(f"the synthesised file is not variable-rate: {gaps}")
        frames_per_tick = float(Fraction(1, 1) / average) / shortest
        if not frames_per_tick > 1.0:
            failures.append(f"the average rate is not coarser than the frame "
                            f"spacing ({frames_per_tick:.4f} frames per tick) - "
                            f"this file would not reproduce the bug")

        # 2. The grid the converter picks is the source's own, not 1/average.
        grid = _encoder_grid(source, average)
        if grid != own_grid:
            failures.append(f"_encoder_grid returned {grid}, not the source "
                            f"grid {own_grid}")
        if grid == Fraction(1, 1) / average:
            failures.append("the grid is still 1/average_rate - the fix is gone")

        # 3. The bug itself: on the average-rate grid the muxer refuses the
        #    stream. Asserted, not assumed - if a future PyAV stops refusing,
        #    this check says the premise changed rather than passing quietly.
        failure = write_like_the_converter(folder / "avg.mp4", average,
                                           Fraction(1, 1) / average, times)
        if not failure:
            failures.append("writing on 1/average_rate no longer fails - the "
                            "premise of this regression test changed")

        # 4. With the fix the same frames go through and decode back holding the
        #    source's own timings.
        fixed_path = folder / "fixed.mp4"
        failure = write_like_the_converter(fixed_path, average, grid, times)
        if failure:
            failures.append(f"writing on the source grid failed: {failure}")
        else:
            # The container's clock is the mp4 timescale. Pinning only the
            # encoder leaves the muxer on 1/average_rate, which is
            # 19620000 for this file - six minutes before a 32-bit player
            # overflows it.
            timescale = mp4_timescale(fixed_path)
            if timescale <= 0:
                failures.append("the output has no readable timescale")
            elif timescale > 1000000:
                failures.append(f"the mp4 timescale is {timescale} - the muxer "
                                f"was left on 1/average_rate")
            back = av.open(str(fixed_path))
            stream_out = next(x for x in back.streams if x.type == "video")
            if Fraction(stream_out.time_base) != grid:
                failures.append(f"the output declares {stream_out.time_base}, "
                                f"not the source grid {grid}")
            got = [f.time for f in back.decode(video=0)]
            back.close()
            if len(got) != len(times):
                failures.append(f"the output holds {len(got)} frames, the "
                                f"source {len(times)}")
            else:
                worst = max(abs(got[i] - times[i]) for i in range(len(times)))
                if worst > 1e-4:
                    failures.append(f"a frame moved by {worst * 1000:.2f} ms")

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} - {failures}")
        return 1
    print("OK: a variable-rate file is written on its own grid, and the "
          "average-rate grid that used to break the muxer still does")
    return 0


if __name__ == "__main__":
    sys.exit(main())
