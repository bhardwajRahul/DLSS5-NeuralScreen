"""Run a file through the neural pass: an image or a video, off the desktop.

The overlay exists to process what is on screen, which means the one thing
the network could always do - take a picture and give it back better - was
the one thing this program could not do to a FILE. The machinery was already
here: compatibility_runtime drives a worker with synthetic frames and no
capture and no window at all, which is a converter with the file part
missing. This module is that missing part.

What it does NOT do, on purpose:

* It does not touch the live pipeline. It starts its own worker, converts, and
  reaps it; the overlay's worker keeps running. Two workers on one card was
  measured rather than assumed (a second one created and evaluated in 1.17 s
  while the first kept answering) - they share the card, so both run slower
  while a file converts, and that is the whole cost.
* It does not interpret settings. It is handed the same `params` dict the
  overlay builds, so a conversion is the picture the sliders were showing,
  not a second tuning surface that could drift from them. The output choices
  it does take - codec, quality, image format, audio - are about the FILE,
  and the panel has no other place for them.
* It knows nothing about the menu or the queue. Progress is a callback and
  cancellation is an Event, so the engine can be driven by a test, by the
  queue in convert_jobs, or from a shell, and none of those is the "real"
  caller.

The motion field is the part worth understanding. The network is temporal:
it is handed motion vectors and a reset flag, and it accumulates across
frames. A still image is therefore ONE frame with reset=True and zero
motion - there is no history to correlate against and pretending otherwise
smears it. A video is the opposite: the frames are a real sequence, so the
guides run exactly as they do live, and the result is temporally stable for
the same reason the desktop is.
"""
from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Callable

import numpy as np

from guides import TemporalGuideGenerator
from pipeline import shutdown_worker, start_worker
from protocol import send_frame, send_resize

#: Stills the converter will open. Kept to what the bundled Pillow can both
#: read and write without extra plugins - a format that opens and then fails
#: to save is a worse experience than one that was never offered.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp")

#: Containers PyAV can demux with the codecs in the bundled runtime
#: (h264/hevc/av1/vp9 are all present - verified against av.codecs_available).
VIDEO_SUFFIXES = (".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv")

#: Pillow's own name for each suffix we offer. Needed because the bytes are
#: written to a ".partial" file first and Pillow derives the format from the
#: extension, which that name does not have.
_PIL_FORMATS = {
    ".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP",
    ".tif": "TIFF", ".tiff": "TIFF", ".webp": "WEBP",
}

#: The container name for each suffix, for the same reason as _PIL_FORMATS:
#: the bytes go to a ".partial" file first and PyAV, like Pillow, reads the
#: format off the extension - "Could not determine output format" otherwise.
_AV_FORMATS = {
    ".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "matroska",
    ".webm": "webm", ".avi": "avi", ".wmv": "asf",
}

#: NVENC encoders, best first - the same chain and the same reason as
#: recorder.VideoRecorder: the RTX 30 series has no AV1 encoder at all, and
#: add_stream() succeeds there anyway, so the failure only surfaces when the
#: encoder is opened.
CODEC_CHAIN = ("av1_nvenc", "hevc_nvenc", "h264_nvenc")

#: What the panel offers for the video codec. "auto" is the chain above; a
#: named codec starts the chain there, so a card without it still produces a
#: file (and the result says which codec it got) instead of failing the job.
CODEC_CHOICES = ("auto", "av1", "hevc", "h264")

#: Quality steps -> NVENC constant quality. 16 is what the recorder uses and
#: is visually lossless for this content; 23 is the usual "high quality web"
#: point; 30 is for when the file size matters more than the last detail.
QUALITY_CQ = {"high": 16, "balanced": 23, "small": 30}

#: The CPU encoder that ends every chain. NVENC refuses small frames outright
#: (measured on the bundled build: 128x128 fails in all three encoders,
#: 640x360 opens) and a card can be out of encoder sessions, while x264
#: opens at any size. Slower by an order of magnitude - which is why it is
#: last, and why the result says when it was used.
SOFTWARE_CODEC = "libx264"
SOFTWARE_CRF = {"high": 17, "balanced": 21, "small": 26}

#: What the panel offers for a still: the source's own format, or one of two.
IMAGE_FORMATS = ("keep", "png", "jpg")

#: Quality-targeted VBR, as the recorder uses. A conversion is not realtime,
#: so it can afford p7 where the recorder settles for p6. `cq` is replaced by
#: the chosen quality step.
ENCODER_OPTIONS = {
    "preset": "p7",
    "tune": "hq",
    "rc": "vbr",
    "cq": "16",
    "maxrate": "250M",
    "bufsize": "500M",
}
BIT_RATE = 120_000_000

#: Audio codecs the MP4 muxer takes as they are. Anything else (Vorbis, PCM,
#: WMA...) is re-encoded to AAC rather than dropped: a converted video that
#: comes back silent reads as a broken converter, whatever the reason.
MP4_AUDIO_COPY = frozenset({"aac", "mp3", "ac3", "eac3", "opus", "flac", "alac"})
AAC_SAMPLE_RATE = 48000
AAC_BIT_RATE = 192_000

#: The worker is handed one frame at a time and answers before the next is
#: sent, so this is a per-frame ceiling and not a whole-file one. A 4K frame
#: through four NR passes is the slow case that sets it.
FRAME_TIMEOUT_S = 60.0

#: Discarded evaluations before the first real frame. The live pipeline pays
#: 120 on a cold card because the frame watchdog would otherwise kill the
#: worker on frame 0; a conversion has no watchdog and no picture to keep
#: alive, so it pays the smallest warm-up that still lets NGX settle.
WARMUP_FRAMES = 8


class ConversionCancelled(RuntimeError):
    """Raised inside the engine when the caller's cancel event is set."""


class ConversionError(RuntimeError):
    """A conversion that could not be completed, with the stage that failed."""

    def __init__(self, stage: str, cause: BaseException | str):
        self.stage = stage
        self.cause = cause
        super().__init__(f"{stage}: {cause}")


@dataclass
class Progress:
    """What the caller is told while a conversion runs.

    `total` is 0 when it is not knowable - a container that declares neither
    a frame count nor a duration - so a progress bar has to cope rather than
    lie about it. `stage` is one of: decoding, starting, processing, writing.
    """
    stage: str
    done: int = 0
    total: int = 0
    detail: str = ""

    @property
    def fraction(self) -> float:
        return min(1.0, self.done / self.total) if self.total > 0 else 0.0


@dataclass
class ConversionResult:
    """Where the output went and what it cost."""
    source: Path
    output: Path
    kind: str                     # "image" | "video"
    frames: int = 0
    seconds: float = 0.0
    codec: str = ""
    width: int = 0
    height: int = 0
    work_width: int = 0
    work_height: int = 0
    skipped: int = 0
    #: "copied" | "aac" | "none" (the source had none) | "off" (not asked
    #: for) | "dropped" (it could not be carried - the reason is in notes).
    audio: str = "none"
    notes: list = field(default_factory=list)


def classify(path: Path) -> str:
    """"image", "video", or "" for something this module will not open."""
    suffix = Path(path).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return ""


def output_suffix(source: Path, image_format: str = "keep") -> str:
    """The extension a converted file gets.

    A still keeps its own format unless PNG or JPEG was asked for. A video is
    re-encoded with NVENC (AV1/HEVC/H.264), and not every source container
    can carry that: AVI and WMV cannot hold AV1 at all, and WebM holds
    neither HEVC nor H.264. So video goes to MP4 - which every player opens -
    except from MKV and WebM, whose files often carry audio and subtitle
    codecs MP4 refuses, and which become MKV so the audio survives as it was.
    """
    source = Path(source)
    suffix = source.suffix.lower()
    if classify(source) == "image":
        if image_format == "png":
            return ".png"
        if image_format == "jpg":
            return ".jpg"
        return suffix
    return ".mkv" if suffix in (".mkv", ".webm") else ".mp4"


def default_output_path(source: Path, out_dir: Path | None = None,
                        suffix: str | None = None) -> Path:
    """`<name>-nr<suffix>`, beside the source unless a folder was chosen.

    Never the source itself: a converter that can overwrite its own input
    destroys the original on a second run, and the second run is exactly
    what someone does after changing a slider. A ".partial" left by a run
    that died counts as taken too - its name is the one a retry would use.
    """
    source = Path(source)
    folder = Path(out_dir) if out_dir else source.parent
    stem = source.stem
    suffix = suffix or source.suffix
    candidate = folder / f"{stem}-nr{suffix}"
    n = 2
    while candidate.exists() or Path(f"{candidate}.partial").exists():
        candidate = folder / f"{stem}-nr-{n}{suffix}"
        n += 1
    return candidate


def codec_chain(choice: str) -> tuple[str, ...]:
    """The NVENC encoders to try for a panel choice, best first."""
    if choice == "hevc":
        return ("hevc_nvenc", "h264_nvenc")
    if choice == "h264":
        return ("h264_nvenc",)
    return CODEC_CHAIN


def encoder_options(quality: str) -> dict:
    """ENCODER_OPTIONS with the chosen quality step."""
    cq = QUALITY_CQ.get(quality, QUALITY_CQ["high"])
    return dict(ENCODER_OPTIONS, cq=str(cq))


def audio_plan(codec_name: str, container_format: str) -> str:
    """"copy" when the output container takes the source audio as it is,
    "aac" when it has to be re-encoded to be carried at all."""
    if container_format in ("matroska", "webm"):
        return "copy"
    return "copy" if str(codec_name).lower() in MP4_AUDIO_COPY else "aac"


def processing_size(width: int, height: int, work_scale: float,
                    nr_small: bool, nr_passes: int = 1) -> tuple[int, int]:
    """The resolution the network runs at for a frame of this size.

    The same two rules the live pipeline obeys, for the same reasons: the
    NGX cap is real (feature 18 goes silent above 2560x1440), and a work
    size must never exceed the frame it came from. With Boost off the
    network is handed the whole frame and the scale is inert - which is the
    measurement in TECHNICAL.md, not a decision made here.
    """
    from settings_io import _work_size
    if not nr_small:
        return _work_size(width, height, 1.0, nr_passes)
    return _work_size(width, height, float(work_scale), nr_passes)


class _Engine:
    """One worker, held open for the frames of one file."""

    def __init__(self, params: dict, width: int, height: int,
                 work_w: int, work_h: int, nr_passes: int = 1):
        self.params = dict(params)
        self.nr_passes = max(1, min(4, int(nr_passes or 1)))
        self.width, self.height = int(width), int(height)
        self.work_w, self.work_h = int(work_w), int(work_h)
        # Upscale mode exactly as the live pipeline decides it: the worker is
        # told the full size only when it differs from the work size, because
        # at work == full the legacy 1:1 path is the one that is known to work.
        self.upscale = (self.work_w != self.width or self.work_h != self.height)
        self.worker = None
        self.reader = None
        self.logs: list = []
        self.stop = None

    def __enter__(self) -> "_Engine":
        full_w = self.width if self.upscale else 0
        full_h = self.height if self.upscale else 0
        # The residual strength is NOT derived here. It was, briefly, as
        # 1/passes - which quietly made a two-pass conversion apply half the
        # effect of a one-pass one, the same surprise the overlay had. It is
        # a setting now (config residual_strength, or the environment), and a
        # conversion inherits whatever the process was started with so that a
        # converted file matches what the panel is showing.
        self.worker, self.logs, self.reader, self.stop = start_worker(
            self.params, self.work_w, self.work_h, WARMUP_FRAMES,
            full_w, full_h, None)
        # The cascade depth reaches the worker ONLY on a resize: the stream
        # header has no field for it (see tests/test_nr_passes_wire). A
        # converter that never sent one therefore ran a single pass whatever
        # the panel said - which is what this call fixes.
        if self.nr_passes > 1:
            send_resize(self.worker, self.params, self.work_w, self.work_h,
                        WARMUP_FRAMES, full_w, full_h,
                        True, False, self.nr_passes)
            self.reader.wait_rack(timeout=60.0)
            self.reader.set_output_size(full_w or self.work_w,
                                        full_h or self.work_h)
        return self

    def __exit__(self, *exc) -> None:
        if self.worker is not None:
            try:
                shutdown_worker(self.worker, self.stop)
            except Exception:
                pass
            self.worker = None

    def evaluate(self, index: int, rgba: np.ndarray,
                 motion: np.ndarray, reset: bool) -> np.ndarray | None:
        """One frame in, the processed frame out (or None if it was skipped)."""
        if self.worker is None or self.worker.poll() is not None:
            tail = "\n".join(self.logs[-12:]) or "(no worker output)"
            raise ConversionError("worker", f"the worker exited:\n{tail}")
        send_frame(self.worker, index, rgba, motion, reset, index,
                   shm=None, want_pixels=True, skip_static=False)
        return self.reader.recv(index, timeout=FRAME_TIMEOUT_S)


def _zero_motion(work_w: int, work_h: int) -> np.ndarray:
    """The motion field the worker reads exactly work_w*work_h*4 bytes of."""
    return np.zeros((work_h, work_w, 2), dtype=np.float16)


def _as_rgba(array: np.ndarray) -> np.ndarray:
    """A contiguous HxWx4 uint8 view, whatever the decoder handed back."""
    if array.ndim == 2:
        array = np.dstack([array] * 3)
    if array.shape[2] == 3:
        alpha = np.full(array.shape[:2] + (1,), 255, dtype=np.uint8)
        array = np.concatenate([array, alpha], axis=2)
    return np.ascontiguousarray(array, dtype=np.uint8)


def _check(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise ConversionCancelled("cancelled")


def _drop_partial(partial: Path) -> None:
    try:
        partial.unlink()
    except OSError:
        pass


def convert_image(source: Path, output: Path, params: dict, *,
                  work_scale: float = 0.65, nr_small: bool = True,
                  nr_passes: int = 1,
                  progress: Callable[[Progress], None] | None = None,
                  cancel: threading.Event | None = None) -> ConversionResult:
    """One still through the network.

    One frame, reset=True, zero motion. The network is temporal and a single
    image has no history: handing it anything but a reset asks it to
    correlate against whatever the feature was last shown, which on a fresh
    worker is nothing at all.
    """
    from PIL import Image

    source, output = Path(source), Path(output)
    started = time.perf_counter()

    def say(stage: str, done: int = 0, total: int = 1, detail: str = "") -> None:
        if progress is not None:
            progress(Progress(stage, done, total, detail))

    say("decoding", 0, 1, source.name)
    try:
        with Image.open(source) as handle:
            handle.load()
            frame = _as_rgba(np.asarray(handle.convert("RGBA")))
    except Exception as exc:
        raise ConversionError("decode", exc) from exc

    height, width = frame.shape[0], frame.shape[1]
    if width < 64 or height < 64:
        raise ConversionError(
            "decode", f"{width}x{height} is below the 64x64 the worker accepts")
    work_w, work_h = processing_size(width, height, work_scale, nr_small,
                                     nr_passes)
    _check(cancel)

    say("starting", 0, 1, f"{width}x{height}")
    with _Engine(params, width, height, work_w, work_h, nr_passes) as engine:
        _check(cancel)
        say("processing", 0, 1, f"{width}x{height}")
        pixels = engine.evaluate(0, frame, _zero_motion(work_w, work_h), True)
    if pixels is None:
        raise ConversionError("process", "the worker returned no pixels")
    _check(cancel)

    say("writing", 1, 1, output.name)
    partial = output.with_name(output.name + ".partial")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        out = np.ascontiguousarray(pixels)[:, :, :3]
        image = Image.fromarray(out, mode="RGB")
        # The partial name is the recorder's rule, for the recorder's reason:
        # a file that exists is a file someone will open, and a conversion
        # that died halfway must not leave one that looks finished.
        # The format is named, never inferred: Pillow picks it from the
        # EXTENSION, and the partial name ends in ".partial", so letting it
        # guess raises "unknown file extension" after the frame has already
        # been through the network - the whole conversion lost at the last
        # step (found by the first real run).
        fmt = _PIL_FORMATS.get(output.suffix.lower(), "PNG")
        if fmt == "JPEG":
            image.save(partial, format=fmt, quality=97, subsampling=0)
        else:
            image.save(partial, format=fmt)
        os.replace(partial, output)
    except Exception as exc:
        _drop_partial(partial)
        raise ConversionError("encode", exc) from exc

    return ConversionResult(
        source=source, output=output, kind="image", frames=1,
        seconds=time.perf_counter() - started,
        width=width, height=height, work_width=work_w, work_height=work_h,
        codec=output.suffix.lstrip(".").lower(), audio="none")


def _estimate_frames(container, stream, rate) -> int:
    """How many frames the video holds, or 0 when nothing says.

    The declared count first; many containers leave it at zero (MKV, WebM,
    most AVIs), and there the duration times the rate is exact enough for a
    progress bar - which is all the number is for.
    """
    declared = int(getattr(stream, "frames", 0) or 0)
    if declared > 0:
        return declared
    seconds = 0.0
    try:
        if stream.duration and stream.time_base:
            seconds = float(stream.duration * stream.time_base)
        elif container.duration:
            seconds = float(container.duration) / 1_000_000.0  # AV_TIME_BASE
    except Exception:
        seconds = 0.0
    return max(0, int(round(seconds * float(rate)))) if seconds > 0 else 0


def _encoder_probe(av, name, rate, width, height, quality) -> bool:
    """Whether `name` opens for this size, tried in a throwaway container.

    Walking the chain inside the real output container would leave every
    refused candidate behind in it as a dead stream, and the muxer writes
    them all into the file header.
    """
    import io
    probe = av.open(io.BytesIO(), mode="w", format="mp4")
    try:
        stream = probe.add_stream(name, rate=rate)
        stream.width, stream.height = width, height
        stream.pix_fmt = "yuv420p"
        stream.time_base = Fraction(1, 1) / rate
        stream.options = (encoder_options(quality) if name != SOFTWARE_CODEC
                          else {"preset": "medium",
                                "crf": str(SOFTWARE_CRF.get(quality, 17))})
        stream.open()
        return True
    except Exception:
        return False
    finally:
        try:
            probe.close()
        except Exception:
            pass


def _pick_video_encoder(av, chain, rate, width, height, quality) -> str:
    """The first encoder of the chain that opens here, x264 as the last."""
    for name in tuple(chain) + (SOFTWARE_CODEC,):
        if _encoder_probe(av, name, rate, width, height, quality):
            return name
    raise ConversionError("encode", f"no video encoder opens at {width}x{height}")


def _add_video_stream(out_container, name, rate, width, height, quality):
    stream = out_container.add_stream(name, rate=rate)
    stream.width, stream.height = width, height
    stream.pix_fmt = "yuv420p"
    stream.time_base = Fraction(1, 1) / rate
    if name == SOFTWARE_CODEC:
        stream.options = {"preset": "medium",
                          "crf": str(SOFTWARE_CRF.get(quality, 17))}
    else:
        stream.bit_rate = BIT_RATE
        stream.options = encoder_options(quality)
    stream.open()
    return stream


class _AacTrack:
    """Source audio re-encoded to AAC, on one contiguous sample clock.

    The recorder's pattern (resampler -> fifo -> whole 1024-sample frames)
    for the same reason: AAC encodes fixed frames, a decoder hands out
    whatever its packets held.
    """

    def __init__(self, av, out_container):
        self.av = av
        self.stream = out_container.add_stream("aac", rate=AAC_SAMPLE_RATE)
        self.stream.bit_rate = AAC_BIT_RATE
        self.stream.layout = "stereo"
        self.stream.format = "fltp"
        self.stream.time_base = Fraction(1, AAC_SAMPLE_RATE)
        self.resampler = av.AudioResampler(format="fltp", layout="stereo",
                                           rate=AAC_SAMPLE_RATE)
        self.fifo = av.AudioFifo()
        self.samples = None

    def push(self, frame, container) -> None:
        for converted in self.resampler.resample(frame):
            self._append(converted, frame)
        self._drain(container)

    def _append(self, converted, source_frame) -> None:
        if converted is None or converted.samples <= 0:
            return
        if self.samples is None:
            # The track starts where the source's audio starts, so a file
            # whose sound begins after its picture stays in sync.
            start = getattr(source_frame, "time", None)
            self.samples = max(0, int(round(float(start) * AAC_SAMPLE_RATE))
                               ) if start is not None else 0
        converted.sample_rate = AAC_SAMPLE_RATE
        converted.time_base = self.stream.time_base
        converted.pts = self.samples
        self.samples += converted.samples
        self.fifo.write(converted)

    def _drain(self, container, flush: bool = False) -> None:
        size = self.stream.codec_context.frame_size or 1024
        while True:
            frame = self.fifo.read(size, partial=flush)
            if frame is None:
                return
            for packet in self.stream.encode(frame):
                container.mux(packet)

    def finish(self, container) -> None:
        for converted in self.resampler.resample(None):
            self._append(converted, None)
        self._drain(container, flush=True)
        for packet in self.stream.encode(None):
            container.mux(packet)


def convert_video(source: Path, output: Path, params: dict, *,
                  work_scale: float = 0.65, nr_small: bool = True,
                  nr_passes: int = 1, flow_preset: str = "fast",
                  copy_audio: bool = True, codec: str = "auto",
                  quality: str = "high",
                  progress: Callable[[Progress], None] | None = None,
                  cancel: threading.Event | None = None) -> ConversionResult:
    """A video through the network, frame by frame, with real motion guides.

    The guides are the live ones, at the live work size, for the reason the
    pipeline has them at all: the network accumulates across frames, and a
    sequence handed zero motion flickers where a sequence handed real
    vectors is stable. A scene cut is reported as a reset by the same
    scene-score rule the desktop uses.

    One pass over the source, video and audio packets in the order the file
    holds them, so the output is interleaved the way the source was. Audio
    is copied packet for packet when the output container takes it, and
    re-encoded to AAC when it does not - never silently dropped. (It was, in
    the first version: `add_stream(template=...)` is not an API of the
    bundled PyAV 18, the TypeError was caught as "audio not copied", and
    every converted video came back without sound.)

    Video timestamps follow the source frames rather than a frame counter,
    so a file with a variable frame rate, or with its picture starting after
    its sound, stays in sync with the audio that was carried over.
    """
    import av

    source, output = Path(source), Path(output)
    started = time.perf_counter()
    notes: list = []
    partial = output.with_name(output.name + ".partial")

    def say(stage: str, done: int, total: int, detail: str = "") -> None:
        if progress is not None:
            progress(Progress(stage, done, total, detail))

    say("decoding", 0, 0, source.name)
    try:
        container = av.open(str(source))
    except Exception as exc:
        raise ConversionError("decode", exc) from exc

    engine = None
    out_container = None
    finished = False
    stage = "decode"
    try:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ConversionError("decode", "the file carries no video stream")
        stream.thread_type = "AUTO"
        width = int(stream.codec_context.width)
        height = int(stream.codec_context.height)
        if width < 64 or height < 64:
            raise ConversionError(
                "decode", f"{width}x{height} is below the 64x64 the worker accepts")
        # An odd dimension cannot be encoded as yuv420p and the worker's own
        # sizing assumes even frames; rounding DOWN keeps us inside the source.
        even_w, even_h = width - (width % 2), height - (height % 2)
        rate = stream.average_rate or stream.guessed_rate or Fraction(30, 1)
        rate = Fraction(rate).limit_denominator(1001 * 1000)
        total = _estimate_frames(container, stream, rate)
        work_w, work_h = processing_size(even_w, even_h, work_scale,
                                         nr_small, nr_passes)
        size_text = f"{even_w}x{even_h}"

        guides = TemporalGuideGenerator(work_w, work_h, preset=flow_preset)
        stage = "encode"
        output.parent.mkdir(parents=True, exist_ok=True)
        container_format = _AV_FORMATS.get(output.suffix.lower(), "mp4")
        codec_used = _pick_video_encoder(av, codec_chain(codec), rate,
                                         even_w, even_h, quality)
        if codec_used == SOFTWARE_CODEC:
            notes.append(f"NVENC cannot encode {even_w}x{even_h} here - "
                         f"encoded on the CPU (x264)")
        elif codec != "auto" and not codec_used.startswith(codec):
            notes.append(f"{codec.upper()} is not available on this card - "
                         f"encoded with {codec_used.split('_')[0].upper()}")

        audio_in = next((s for s in container.streams if s.type == "audio"),
                        None)
        audio_mode = "off" if not copy_audio else (
            "none" if audio_in is None else
            audio_plan(audio_in.codec_context.name, container_format))
        # The header is written HERE, before a single frame goes through the
        # network: a copied audio track the container refuses fails at this
        # point, and it is cheap to rebuild the output without it now - not
        # after an hour of frames. Copy falls back to AAC, AAC to no audio.
        while True:
            out_container = av.open(str(partial), mode="w",
                                    format=container_format)
            out_stream = _add_video_stream(out_container, codec_used, rate,
                                           even_w, even_h, quality)
            audio_out = None
            aac = None
            try:
                if audio_mode == "copy":
                    audio_out = out_container.add_stream_from_template(audio_in)
                elif audio_mode == "aac":
                    aac = _AacTrack(av, out_container)
                out_container.start_encoding()
                break
            except Exception as exc:
                try:
                    out_container.close()
                except Exception:
                    pass
                out_container = None
                _drop_partial(partial)
                if audio_mode == "copy":
                    notes.append(f"the audio could not be copied as it was "
                                 f"({exc}) - re-encoded to AAC")
                    audio_mode = "aac"
                elif audio_mode == "aac":
                    notes.append(f"the audio could not be carried ({exc})")
                    audio_mode = "dropped"
                else:
                    raise ConversionError("encode", exc) from exc

        stage = "process"
        done = 0
        skipped = 0
        last_pts = -1
        time_base = out_stream.time_base
        streams = [stream] + ([audio_in] if audio_mode in ("copy", "aac") else [])
        say("starting", 0, total, size_text)
        for packet in container.demux(*streams):
            _check(cancel)
            if packet.stream.index != stream.index:
                if audio_mode == "copy":
                    if packet.dts is None:
                        continue          # the demuxer's flush packet
                    packet.stream = audio_out
                    out_container.mux(packet)
                elif aac is not None:
                    for audio_frame in packet.decode():
                        aac.push(audio_frame, out_container)
                continue
            for frame in packet.decode():
                _check(cancel)
                rgba = _as_rgba(frame.to_ndarray(format="rgba"))
                if rgba.shape[1] != even_w or rgba.shape[0] != even_h:
                    rgba = np.ascontiguousarray(rgba[:even_h, :even_w])
                if engine is None:
                    engine = _Engine(params, even_w, even_h,
                                     work_w, work_h, nr_passes)
                    engine.__enter__()
                guide = guides.process(rgba)
                pixels = engine.evaluate(done, rgba, guide.motion, guide.reset)
                if pixels is None:
                    skipped += 1
                    pixels = rgba
                out = np.ascontiguousarray(pixels)[:even_h, :even_w]
                video_frame = av.VideoFrame.from_ndarray(out, format="rgba")
                # The colour tags belong on the FRAME as well as the
                # stream: swscale takes its matrix from the frame while
                # the player reads the stream, and that mismatch is what
                # the recorder's own comment calls "the contrast".
                video_frame.color_range = 2          # full (sRGB)
                video_frame.colorspace = 1           # BT.709
                video_frame.color_primaries = 1
                video_frame.color_trc = 13           # sRGB
                when = frame.time
                pts = (int(round(float(when) / float(time_base)))
                       if when is not None else last_pts + 1)
                pts = max(pts, last_pts + 1)
                last_pts = pts
                video_frame.pts = pts
                video_frame.time_base = time_base
                for out_packet in out_stream.encode(video_frame):
                    out_container.mux(out_packet)
                done += 1
                say("processing", done, max(total, done), size_text)

        if done == 0:
            raise ConversionError("decode", "no frames could be decoded")

        stage = "encode"
        say("writing", done, max(total, done), output.name)
        for out_packet in out_stream.encode(None):
            out_container.mux(out_packet)
        if aac is not None:
            aac.finish(out_container)
        out_container.close()
        out_container = None
        os.replace(partial, output)
        finished = True
    except (ConversionCancelled, ConversionError):
        raise
    except Exception as exc:
        # The stage the failure happened in, not a catch-all: "process" is
        # the worker (a timeout, a pipe that closed), "encode" the file.
        raise ConversionError(stage, exc) from exc
    finally:
        if engine is not None:
            engine.__exit__(None, None, None)
        if out_container is not None:
            try:
                out_container.close()
            except Exception:
                pass
        if not finished:
            _drop_partial(partial)
        try:
            container.close()
        except Exception:
            pass

    return ConversionResult(
        source=source, output=output, kind="video", frames=done,
        seconds=time.perf_counter() - started, codec=codec_used,
        width=even_w, height=even_h, work_width=work_w, work_height=work_h,
        skipped=skipped, audio="copied" if audio_mode == "copy" else audio_mode,
        notes=notes)


def convert(source: Path, output: Path | None, params: dict, **kwargs) -> ConversionResult:
    """Convert by kind, so a caller does not have to know which this is.

    Accepts every option of both converters; the ones that do not apply to
    this kind of file are ignored, so a queue can hand the same settings to
    a still and a video. `out_dir` and `image_format` only decide the output
    name, and only when no `output` is given.
    """
    source = Path(source)
    kind = classify(source)
    if not kind:
        raise ConversionError(
            "decode", f"{source.suffix or 'that file'} is not a format this converts")
    out_dir = kwargs.pop("out_dir", None)
    image_format = kwargs.pop("image_format", "keep")
    if output is None:
        output = default_output_path(source, out_dir,
                                     output_suffix(source, image_format))
    if kind == "image":
        for video_only in ("flow_preset", "copy_audio", "codec", "quality"):
            kwargs.pop(video_only, None)
        return convert_image(source, Path(output), params, **kwargs)
    return convert_video(source, Path(output), params, **kwargs)
