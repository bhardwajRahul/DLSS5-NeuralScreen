"""A converted photo or video comes out the way its source is seen.

Four things the pixels alone do not carry, each of which the converter lost
(code review, 23.09):

  * a photo's EXIF orientation - a phone stores a portrait photo as landscape
    pixels and a "turn me" tag, and the converted file came out sideways;
  * a photo's colour profile and EXIF - a Display P3 picture was written as
    plain sRGB, washed out;
  * a 16-bit grey image's range - Pillow's I;16 -> RGBA conversion clips
    instead of scaling, and a 16-bit PNG came out 99.6% white;
  * a video's display matrix - a phone's portrait clip played sideways.

And the worker's size limit, which is landscape-shaped (7680x4320): a portrait
still taller than 4320 goes through on its side and comes back upright, and
only a frame that fits neither way is refused - with a sentence of its own,
not the worker failure it used to be reported as.

The worker is a stub that hands each frame back untouched and remembers the
size it was built for, so nothing here needs a GPU.

Run:  runtime\\python.exe tests\\test_convert_orientation.py
"""
import io
import os
import struct
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import numpy as np  # noqa: E402
from PIL import Image, ImageCms  # noqa: E402

import convert_jobs  # noqa: E402
import media_convert  # noqa: E402
from i18n import STRINGS  # noqa: E402
from settings_io import PROFILES  # noqa: E402

ORIENTATION = 0x0112


class StubEngine:
    """Gives every frame back as it came; records the size it was built for."""

    built: list = []

    def __init__(self, params, width, height, work_w, work_h, nr_passes=1):
        StubEngine.built.append((width, height))
        self.width, self.height = width, height
        self.work_w, self.work_h = work_w, work_h
        self.motion_small = False
        self.out_shm = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    @property
    def motion_size(self):
        return self.work_w, self.work_h

    def evaluate(self, index, rgba, motion, reset):
        return np.ascontiguousarray(rgba).copy()


def with_stub(call):
    """Run `call` with the stub worker and x264 in place of the real ones."""
    real_engine, real_chain = media_convert._Engine, media_convert.codec_chain
    media_convert._Engine = StubEngine
    media_convert.codec_chain = lambda choice: ()
    try:
        return call()
    finally:
        media_convert._Engine = real_engine
        media_convert.codec_chain = real_chain


def params() -> dict:
    return dict(next(iter(PROFILES.values())))


def marked(width: int, height: int) -> Image.Image:
    """Grey, with a red block in the top-left corner - where it is says which way up."""
    pixels = np.full((height, width, 3), 90, np.uint8)
    pixels[:12, :12] = (230, 20, 20)
    return Image.fromarray(pixels, "RGB")


def red_at(image: Image.Image, corner: str) -> bool:
    arr = np.asarray(image.convert("RGB")).astype(int)
    h, w = arr.shape[:2]
    y = slice(2, 8) if corner.startswith("top") else slice(h - 8, h - 2)
    x = slice(2, 8) if corner.endswith("left") else slice(w - 8, w - 2)
    block = arr[y, x]
    return block[..., 0].mean() > 170 and block[..., 1].mean() < 80


def check_photo_orientation(folder: Path, failures: list) -> None:
    # Stored 160x96 with the tag a phone writes for a portrait shot (6: turn
    # 90 degrees clockwise to view), so the photo is seen 96x160 with the
    # stored top-left corner at the top right.
    source = folder / "portrait.jpg"
    exif = Image.Exif()
    exif[ORIENTATION] = 6
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    marked(160, 96).save(source, format="JPEG", quality=95, exif=exif.tobytes(),
                         icc_profile=icc)
    output = folder / "portrait-nr.jpg"
    StubEngine.built.clear()
    result = with_stub(lambda: media_convert.convert_image(
        source, output, params(), work_scale=0.65, nr_small=True))
    with Image.open(output) as done:
        done.load()
        if done.size != (96, 160):
            failures.append(f"the photo came out {done.size[0]}x{done.size[1]}, "
                            f"not upright at 96x160 - the EXIF turn was lost")
        elif not red_at(done, "top-right"):
            failures.append("the photo is 96x160 but turned the wrong way")
        if done.info.get("icc_profile") != icc:
            failures.append("the colour profile did not reach the converted photo")
        if done.getexif().get(ORIENTATION, 1) != 1:
            failures.append("the output still carries the turn tag - viewers "
                            "would turn the already-turned picture again")
    if (result.width, result.height) != (96, 160):
        failures.append(f"the result reports {result.width}x{result.height}")


def check_sixteen_bit(folder: Path, failures: list) -> None:
    ramp = np.linspace(0, 65535, 128 * 96).reshape(96, 128).astype(np.uint16)
    source = folder / "grey16.png"
    Image.fromarray(ramp).save(source, format="PNG")
    with Image.open(source) as handle:
        mode = handle.mode
    output = folder / "grey16-nr.png"
    with_stub(lambda: media_convert.convert_image(
        source, output, params(), work_scale=0.65, nr_small=True))
    with Image.open(output) as done:
        grey = np.asarray(done.convert("L")).astype(float)
    mean, white = grey.mean() / 255.0, float((grey >= 255).mean())
    if abs(mean - 0.5) > 0.05 or white > 0.05:
        failures.append(f"a 16-bit ({mode}) grey ramp came out with mean "
                        f"{mean:.3f} and {white:.1%} white - clipped, not scaled")


def check_size_limit(folder: Path, failures: list) -> None:
    # The limit, scaled down so the images stay small: 320x180, the same
    # landscape shape as 7680x4320.
    real = media_convert.WORKER_MAX_W, media_convert.WORKER_MAX_H
    media_convert.WORKER_MAX_W, media_convert.WORKER_MAX_H = 320, 180
    try:
        tall = folder / "tall.png"
        marked(150, 250).save(tall, format="PNG")
        output = folder / "tall-nr.png"
        StubEngine.built.clear()
        with_stub(lambda: media_convert.convert_image(
            tall, output, params(), work_scale=0.65, nr_small=True))
        if StubEngine.built != [(250, 150)]:
            failures.append(f"a still taller than the limit was not handed to "
                            f"the worker on its side: built {StubEngine.built}")
        with Image.open(output) as done:
            done.load()
            if done.size != (150, 250) or not red_at(done, "top-left"):
                failures.append("the still handed over on its side did not come "
                                "back upright and unchanged")
        big = folder / "big.png"
        marked(400, 400).save(big, format="PNG")
        try:
            with_stub(lambda: media_convert.convert_image(
                big, folder / "big-nr.png", params()))
            failures.append("a still larger than the limit both ways was "
                            "converted instead of refused")
        except media_convert.ConversionError as exc:
            if exc.stage != "too_large":
                failures.append(f"a too-large still fails at stage "
                                f"{exc.stage!r}, not 'too_large'")
            elif convert_jobs.friendly_error(exc) != "too_large":
                failures.append("the queue does not name a too-large file as such")
    finally:
        media_convert.WORKER_MAX_W, media_convert.WORKER_MAX_H = real
    missing = [lang for lang, table in STRINGS.items()
               if not table.get("convert_err_too_large")]
    if missing:
        failures.append(f"convert_err_too_large is missing in {missing}")


def make_clip(av, path: Path, rotation_cw: int) -> None:
    """A small x264 clip; `rotation_cw` 90 patches in a phone's portrait matrix."""
    with av.open(str(path), "w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 128, 96, "yuv420p"
        for index in range(6):
            frame = av.VideoFrame.from_ndarray(np.asarray(marked(128, 96)),
                                               format="rgb24")
            frame.pts = index
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    if rotation_cw != 90:
        return
    data = bytearray(path.read_bytes())
    at = data.find(b"tkhd")
    version = data[at + 4]
    matrix_at = at + 8 + (36 if version == 0 else 48)
    # The tkhd matrix a phone writes for "turn 90 degrees clockwise to view",
    # which the demuxer reports as a display matrix of -90 (counter-clockwise).
    data[matrix_at:matrix_at + 36] = struct.pack(
        ">9i", 0, 0x10000, 0, -0x10000, 0, 0, 96 << 16, 0, 0x40000000)
    path.write_bytes(bytes(data))


def rotation_of(av, path: Path) -> tuple:
    with av.open(str(path)) as container:
        frame = next(container.decode(container.streams.video[0]))
        return int(round(float(frame.rotation or 0))), frame.width, frame.height


def check_video_rotation(av, folder: Path, failures: list) -> None:
    for turn in (90, 0):
        source = folder / f"clip{turn}.mp4"
        make_clip(av, source, turn)
        want = rotation_of(av, source)[0]
        if turn and want != -90:
            failures.append(f"the test clip reports {want}, not -90 - the "
                            f"patched matrix is not what a phone writes")
            continue
        output = folder / f"clip{turn}-nr.mp4"
        with_stub(lambda: media_convert.convert_video(
            source, output, params(), work_scale=0.65, nr_small=True,
            quality="small", codec="h264", copy_audio=False))
        got, width, height = rotation_of(av, output)
        if got != want:
            failures.append(f"a clip turned {want} came out turned {got} - "
                            f"the display matrix was lost")
        if (width, height) != (128, 96):
            failures.append(f"the frames were resized to {width}x{height}: "
                            f"they should go through as stored")


def main() -> int:
    try:
        import av
    except Exception as exc:                       # pragma: no cover
        print(f"SKIP: PyAV is not available ({exc})")
        return 0
    failures: list = []
    with tempfile.TemporaryDirectory(prefix="ns-orient-") as temporary:
        folder = Path(temporary)
        check_photo_orientation(folder, failures)
        check_sixteen_bit(folder, failures)
        check_size_limit(folder, failures)
        check_video_rotation(av, folder, failures)
    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: photos keep their turn, profile and 16-bit range, a tall still "
          "goes through on its side, a too-large one is named, and a video "
          "keeps its display matrix")
    return 0


if __name__ == "__main__":
    sys.exit(main())
