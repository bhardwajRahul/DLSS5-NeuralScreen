"""Every knob in the menu changes the picture, and the top of its range is stable.

A hundred tests checked that a slider's value reaches the worker. None
checked that it did anything there, and that is how four of the nine fields
we send NGX came to be dead without anyone noticing:

  profile, preset, ui_correction   identical output at every value
  skin_structure                   identical unless auto_mask is on
  intensity                        identical above 1.0 - the DLL clamps it

Measured 13.09 on the 310.8.0 runtime (colour-sweep-20260913): int=1, 1.25,
1.5, 2 and 2.5 all hash to the same frame, and a user turning that slider
through 60% of its travel was changing nothing. That is issue #40, "low
effect strength", and it was a measurement problem, not a model problem.

Two things are pinned here.

**Liveness.** Each parameter offered in the menu must change the output, in
the configuration where it can work at all - skin_structure only with
auto_mask=1, or the test would lie about it. Dead fields are reported rather
than failed: they are a property of this runtime build, and if a newer one
revives them this test says so in one line instead of failing the suite.

**Stability.** The same frame twelve times with no history reset. It does
not settle - measured, it plateaus at about 1 in 255 and stays there - so
what is pinned is the height of that plateau, and separately the shadows,
which sit higher and are where the shimmer is seen first.

Run:  runtime\\python.exe tests\\test_param_effect.py
"""
import hashlib
import os
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from main import (FRAME_FLAG_WANT_PIXELS, FRAME_FMT, FRAME_MAGIC,  # noqa: E402
                  HEADER_FMT, NATIVE_DIR, OUT_FMT, OUT_MAGIC, RACK_FMT,
                  RESIZE_ACK_MAGIC, RESIZE_FMT, RESIZE_MAGIC, VIDEO_MAGIC,
                  WORKER_EXE)
from worker_reply import read_reply  # noqa: E402

W, H = 1280, 720
WARMUP = 8
SETTLE = 3            # frames per configuration; the last one is measured
STABLE_FRAMES = 12    # for the stability half
SHADOW = 48           # luma below this is "the shadows", in 0..255

# The defaults every case starts from - the shipped Natural, minus the fields
# the menu does not expose.
DEFAULTS = dict(profile=0, preset=0, style=1, auto_mask=1, ui_correction=0,
                intensity=1.0, local_tone=1.0, local_structure=1.0,
                skin_structure=-1.0)

# (name, low, high, extra) - the two ends a live parameter must tell apart,
# and whatever else has to be true for it to work at all.
LIVE = (
    ("style", dict(style=0), dict(style=1), {}),
    ("intensity", dict(intensity=0.0), dict(intensity=1.0), {}),
    ("local_tone", dict(local_tone=0.0), dict(local_tone=2.0), {}),
    ("local_structure", dict(local_structure=0.0),
     dict(local_structure=2.0), {}),
    # Inert without the auto mask - which is one of the reasons the mask is
    # on in every profile now. The explicit extra stays: this test must not
    # depend on what the shipped defaults happen to be.
    ("skin_structure", dict(skin_structure=-1.0), dict(skin_structure=2.5),
     dict(auto_mask=1)),
    ("auto_mask", dict(auto_mask=0), dict(auto_mask=1), {}),
)

# Known dead on 310.8.0. Reported, not failed - see the docstring.
DEAD = (
    ("profile", dict(profile=0), dict(profile=2), {}),
    ("preset", dict(preset=0), dict(preset=2), {}),
    ("ui_correction", dict(ui_correction=0), dict(ui_correction=1), {}),
    ("intensity above 1.0", dict(intensity=1.0), dict(intensity=2.5), {}),
)


def make_frame() -> np.ndarray:
    """Detail at several scales, and a genuinely dark corner for the shadows."""
    rng = np.random.default_rng(3)
    yy, xx = np.mgrid[0:H, 0:W]
    f = np.zeros((H, W, 4), dtype=np.uint8)
    base = ((xx * 7 + yy * 13) % 256).astype(np.uint8)
    f[..., 0] = base
    f[..., 1] = (base // 2 + rng.integers(0, 48, (H, W), dtype=np.uint8))
    f[..., 2] = (255 - base).astype(np.uint8)
    # The bottom third is pushed down into the shadows: the stability of the
    # dark part is measured separately and the synthetic pattern above is
    # nowhere near dark enough on its own.
    f[H * 2 // 3:, :3] = (f[H * 2 // 3:, :3] // 8)
    f[..., 3] = 255
    return np.ascontiguousarray(f)


FRAME = make_frame()
MOTION = np.zeros((H, W, 2), dtype=np.float16)
BODY = FRAME.tobytes() + MOTION.tobytes()
LUMA = (0.2126 * FRAME[..., 0] + 0.7152 * FRAME[..., 1]
        + 0.0722 * FRAME[..., 2])
DARK = LUMA < SHADOW


def read_exact(pipe, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = pipe.read(n - len(buf))
        if not chunk:
            raise EOFError("the worker closed stdout")
        buf += chunk
    return buf


def pack(fmt, magic, p):
    return struct.pack(fmt, magic, W, H, WARMUP, 0,
                       int(p.get("profile", 0)), int(p.get("preset", 0)), int(p["style"]),
                       int(p["auto_mask"]), int(p.get("ui_correction", 0)),
                       float(p["intensity"]), float(p["local_tone"]),
                       float(p["local_structure"]),
                       float(p["skin_structure"]), W, H)


def pump(proc, frames=SETTLE, reset_first=True, collect=False):
    out, series = None, []
    for i in range(frames):
        proc.stdin.write(struct.pack(FRAME_FMT, FRAME_MAGIC, i,
                                     1 if (i == 0 and reset_first) else 0,
                                     FRAME_FLAG_WANT_PIXELS, i))
        proc.stdin.write(BODY)
        proc.stdin.flush()
        magic, _idx, ok, nbytes, ngx, _pts = struct.unpack(
            OUT_FMT, read_reply(proc.stdout, struct.calcsize(OUT_FMT)))
        if magic != OUT_MAGIC or not ok:
            raise RuntimeError(f"bad reply ok={ok} ngx=0x{ngx:08X}")
        if nbytes:
            out = np.frombuffer(read_exact(proc.stdout, nbytes),
                                dtype=np.uint8).reshape(H, W, 4).copy()
            if collect:
                series.append(out)
    return series if collect else out


def apply(proc, p):
    proc.stdin.write(pack(RESIZE_FMT, RESIZE_MAGIC, p))
    proc.stdin.flush()
    magic, ok, ngx, _r, _p = struct.unpack(
        RACK_FMT, read_reply(proc.stdout, struct.calcsize(RACK_FMT)))
    if magic != RESIZE_ACK_MAGIC or not ok:
        raise RuntimeError(f"the worker refused the parameters 0x{ngx:08X}")


def digest(img) -> str:
    return hashlib.sha1(img.tobytes()).hexdigest()[:10]


def wiggle(series) -> tuple[float, float]:
    """How much consecutive outputs of the SAME input still differ."""
    whole, dark = 0.0, 0.0
    for a, b in zip(series, series[1:]):
        d = np.abs(a[..., :3].astype(np.int16) - b[..., :3].astype(np.int16))
        whole = max(whole, float(d.mean()))
        dark = max(dark, float(d[DARK].mean()))
    return whole, dark


def main() -> int:
    failures = []
    if not WORKER_EXE.is_file():
        print(f"FAIL: worker not found: {WORKER_EXE}")
        return 1

    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([str(WORKER_EXE), "--live"], cwd=str(NATIVE_DIR),
                                env=dict(os.environ), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            proc.stdin.write(pack(HEADER_FMT, VIDEO_MAGIC, DEFAULTS))
            proc.stdin.flush()
            time.sleep(2.0)
            pump(proc)

            print("    liveness - each end of the range, same frame:")
            for name, low, high, extra in LIVE:
                a = dict(DEFAULTS, **extra, **low)
                b = dict(DEFAULTS, **extra, **high)
                apply(proc, a)
                ha = digest(pump(proc))
                apply(proc, b)
                hb = digest(pump(proc))
                mark = "changes" if ha != hb else "NO EFFECT"
                print(f"      {name:22} {ha} -> {hb}  {mark}")
                if ha == hb:
                    failures.append(
                        f"{name} is offered in the menu and changes nothing "
                        f"between its two ends - the same frame comes back "
                        f"byte for byte")

            print("    known dead on this runtime (reported, not failed):")
            for name, low, high, extra in DEAD:
                apply(proc, dict(DEFAULTS, **extra, **low))
                ha = digest(pump(proc))
                apply(proc, dict(DEFAULTS, **extra, **high))
                hb = digest(pump(proc))
                print(f"      {name:22} {ha} -> {hb}  "
                      f"{'ALIVE NOW' if ha != hb else 'still dead'}")

            print(f"    stability - one frame {STABLE_FRAMES} times, no reset:")
            for label, over in (("defaults", {}),
                                ("top of the range",
                                 dict(local_tone=2.0, local_structure=2.0,
                                      skin_structure=2.5))):
                apply(proc, dict(DEFAULTS, **over))
                series = pump(proc, frames=STABLE_FRAMES, reset_first=True,
                              collect=True)
                whole, dark = wiggle(series[2:])
                print(f"      {label:22} whole frame {whole:.3f}, "
                      f"shadows {dark:.3f} of 255")
                if whole > STABLE_LIMIT:
                    failures.append(
                        f"at {label} the picture keeps moving on a frozen "
                        f"input: {whole:.3f} of 255 between consecutive "
                        f"outputs, over {STABLE_LIMIT} - that is shimmer")
                if dark > STABLE_DARK_LIMIT:
                    failures.append(
                        f"at {label} the shadows keep moving on a frozen "
                        f"input: {dark:.3f} of 255, over {STABLE_DARK_LIMIT}")
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except Exception:
                proc.kill()

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK: every menu parameter moves the picture, and the shimmer "
          "stays at its measured level")
    return 0


# Ceilings for the stability half, ours, from measuring this runtime on a
# frozen input over 24 frames. The picture does NOT converge: after two
# frames it plateaus and stays there.
#
#   defaults          1.47 1.07 1.01 1.02 1.02 1.00 1.01 0.99 ... 0.98
#   top of the range  1.86 1.35 1.32 1.18 1.18 1.16 1.15 1.15 ... 1.16
#
# Mean of about 1 in 255, peaks of 13-21, forever, on an input that never
# changes - and this is with Boost off, so it is the network itself and not
# our composite. That is the shimmer users report, and A3 on the roadmap is
# where it gets treated rather than measured.
#
# Those two rows were measured with the auto mask off, which is what the
# profiles carried at the time. With it on - the default since step 3 - the
# plateau is 0.95 and 1.08, and the shadows 1.10 and 1.15. The mask is
# worth between a tenth and a quarter of the trembling, measured on three
# real frames; the numbers are in settings_io beside the profiles.
#
# The ceilings sit above the plateau with enough room for another machine's
# noise and little enough to catch a doubling.
STABLE_LIMIT = 1.5
STABLE_DARK_LIMIT = 1.7


if __name__ == "__main__":
    sys.exit(main())
