"""The two composites are a live switch, and they really are two (A7).

With Boost on, the network runs at the work resolution and its result has to
get back to full size. There are two ways, and the program has only ever
shipped one:

  * **matched residual** - compose native + (nr_out_up - nr_in_up) * strength.
    The native frame stays the 1:1 anchor, so text and edges keep their full
    resolution and the network only contributes what it changed.
  * **direct reconstruction** - show what the network produced, stretched.
    A stronger, more obviously "processed" picture, and a softer one.

Which is better is a question about pictures, not about code, so the switch
had to be cheap enough to flip while looking at something: it travels in the
resize flags (RESIZE_FLAG_NR_DIRECT) and costs no NGX feature, exactly like
the four sliders since C1.

**It has now been measured, and residual wins on all three content types.**
Boost 832x468 -> 1280x720, profile Natural, 1:1 crops of a browser page, a
video frame and a game scene. Ratios are against the native input:

    source  composite   deviation   edges  detail
    text    residual         1.30   1.070   0.959
    text    direct           2.21   0.782   0.211
    film    residual        22.45   1.020   0.884
    film    direct          23.01   0.872   0.180
    game    residual         8.98   1.158   1.051
    game    direct           9.40   0.973   0.361

"detail" is the variance of the Laplacian - grain, texture, small type.
Direct reconstruction keeps a fifth to a third of it. It is not a stronger
picture in exchange, either: the deviation from native is the same within a
few tenths, so the extra is softness, not effect. On the game scene residual
even comes out above the native frame on both measures (1.158 / 1.051),
which is what the composite is for.

So: no menu control, and this stays a debug switch. The test keeps the
switch working so the question can be reopened cheaply - at a lower Boost
step, say, where the network's own output carries relatively more.

What this pins:

1. Flipping the composite does **not** rebuild the feature. One
   "video memory after feature create" for the whole run - the same
   evidence test_feature_leak reads.
2. The worker says which composite is live, so an A/B session can be read
   back out of a log afterwards.
3. The two composites really do produce different pixels. A switch that
   quietly does nothing would pass 1 and 2 and be worthless.
4. Flipping back returns the old picture - the switch is a switch, not a
   one-way door.

Run:  runtime\\python.exe tests\\test_direct_reconstruction.py
"""
import os
import re
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

from main import (FRAME_FLAG_WANT_PIXELS, FRAME_FMT, FRAME_MAGIC,  # noqa: E402
                  HEADER_FMT, NATIVE_DIR, OUT_FMT, OUT_MAGIC, PROFILES,
                  RACK_FMT, RESIZE_ACK_MAGIC, VIDEO_MAGIC, WORKER_EXE)
from protocol import send_resize  # noqa: E402
from worker_reply import read_reply  # noqa: E402

WORK_W, WORK_H = 640, 360     # what the network sees
FULL_W, FULL_H = 1280, 720    # what comes in and goes out
WARMUP = 4
SETTLE = 8                    # frames per phase: the network has a history

CREATE_RE = re.compile(r"video memory after feature create")
COMPOSITE_RE = re.compile(r"the feature stays \(([^)]+)\)")


def make_frame(seed: int) -> np.ndarray:
    """Something with edges and texture - a flat colour would hide the difference."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:FULL_H, 0:FULL_W]
    frame = np.zeros((FULL_H, FULL_W, 4), dtype=np.uint8)
    base = ((x // 16 + y // 16) % 2) * 180 + 30            # a coarse checkerboard
    grain = rng.integers(0, 40, size=(FULL_H, FULL_W))     # fine detail on top
    frame[..., 0] = np.clip(base + grain, 0, 255).astype(np.uint8)
    frame[..., 1] = np.clip(base // 2 + grain, 0, 255).astype(np.uint8)
    frame[..., 2] = np.clip(255 - base, 0, 255).astype(np.uint8)
    frame[..., 3] = 255
    return np.ascontiguousarray(frame)


def read_exact(pipe, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = pipe.read(n - len(buf))
        if not chunk:
            raise EOFError(f"the worker closed stdout (got {len(buf)} of {n})")
        buf += chunk
    return buf


def send_and_get(worker, index: int, frame: np.ndarray,
                 motion: np.ndarray) -> np.ndarray | None:
    worker.stdin.write(struct.pack(FRAME_FMT, FRAME_MAGIC, index,
                                   1 if index == 0 else 0,
                                   FRAME_FLAG_WANT_PIXELS, index))
    worker.stdin.write(frame.tobytes())
    worker.stdin.write(motion.tobytes())
    worker.stdin.flush()
    head = read_reply(worker.stdout, struct.calcsize(OUT_FMT))
    magic, _idx, ok, nbytes, ngx, _pts = struct.unpack(OUT_FMT, head)
    if magic != OUT_MAGIC:
        raise RuntimeError(f"foreign reply 0x{magic:08X}")
    if not ok:
        raise RuntimeError(f"the worker returned ok=0, ngx=0x{ngx:08X}")
    if nbytes == 0:
        return None
    return np.frombuffer(read_exact(worker.stdout, nbytes),
                         dtype=np.uint8).reshape(FULL_H, FULL_W, 4).copy()


def flip(proc, params, nr_direct: bool) -> None:
    """Ask for the other composite and read the ack off the pipe.

    The ack has to be consumed here: the protocol is a stream, and the next
    thing read after a resize is RACK, not the reply to a frame.
    """
    send_resize(proc, params, WORK_W, WORK_H, WARMUP,
                FULL_W, FULL_H, nr_small=True, nr_direct=nr_direct)
    magic, ok, ngx, _r, _pts = struct.unpack(
        RACK_FMT, read_reply(proc.stdout, struct.calcsize(RACK_FMT)))
    if magic != RESIZE_ACK_MAGIC:
        raise RuntimeError(f"expected RACK, got 0x{magic:08X}")
    if not ok:
        raise RuntimeError(f"the worker refused the resize, ngx=0x{ngx:08X}")


def settle(proc, frame, motion, first_index: int) -> np.ndarray | None:
    """Feed the same picture until the temporal history stops moving."""
    out = None
    for i in range(SETTLE):
        out = send_and_get(proc, first_index + i, frame, motion)
    return out


def mean_abs(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.abs(a[..., :3].astype(np.int16)
                        - b[..., :3].astype(np.int16)).mean())


def main() -> int:
    failures = []
    if not WORKER_EXE.is_file():
        print(f"FAIL: worker not found: {WORKER_EXE}")
        return 1

    params = dict(PROFILES["Natural"])
    header = struct.pack(HEADER_FMT, VIDEO_MAGIC, WORK_W, WORK_H, WARMUP, 0,
                         0, 0, int(params["style"]), int(params["auto_mask"]),
                         int(params.get("ui_correction", 0)),
                         float(params["intensity"]), float(params["local_tone"]),
                         float(params["local_structure"]),
                         float(params["skin_structure"]), FULL_W, FULL_H)
    frame = make_frame(7)
    motion = np.zeros((WORK_H, WORK_W, 2), dtype=np.float16)

    env = dict(os.environ)
    env["NS_NR_SMALL"] = "1"       # Boost: without it there is no composite
    env.pop("NS_NR_RESIDUAL", None)  # the default (residual) is the starting point

    residual_a = direct = residual_b = None
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen([str(WORKER_EXE), "--live"], cwd=str(NATIVE_DIR),
                                env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=err,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            proc.stdin.write(header)
            proc.stdin.flush()
            time.sleep(2.0)

            # Phase A: the composite that ships.
            residual_a = settle(proc, frame, motion, 0)

            # Flip to direct reconstruction. Same sizes, same mode - this
            # must take the parameters-only path.
            flip(proc, params, nr_direct=True)
            direct = settle(proc, frame, motion, 100)

            # And back again.
            flip(proc, params, nr_direct=False)
            residual_b = settle(proc, frame, motion, 200)
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
            err.seek(0)
            text = err.read().decode("utf-8", "replace")

    if residual_a is None or direct is None or residual_b is None:
        print("FAIL: the worker did not return pixels in every phase")
        print(text[-2000:])
        return 1

    # 1. No feature was rebuilt by the two flips.
    creates = len(CREATE_RE.findall(text))
    print(f"    feature creates over two composite flips: {creates}")
    if creates > 1:
        failures.append(
            f"flipping the composite rebuilt the NGX feature ({creates} creates) "
            f"- it is a parameter, not a size, and a rebuild costs 113-148 ms "
            f"of frozen picture")

    # 2. The worker named the composite each time.
    named = COMPOSITE_RE.findall(text)
    print(f"    composites reported in the log: {named}")
    if named != ["direct reconstruction", "matched residual"]:
        failures.append(
            f"the log should name each composite as it is switched to, got "
            f"{named!r} - an A/B session is read back out of the log")

    # 3. They are actually two different pictures.
    delta = mean_abs(residual_a, direct)
    print(f"    residual vs direct: mean |difference| {delta:.2f} of 255")
    if delta < 1.0:
        failures.append(
            f"the two composites differ by {delta:.2f} of 255 - the switch is "
            f"not reaching the shader, or both paths do the same thing")

    # 4. Flipping back returns the same picture. Not bit-exact: the network
    # carries a temporal history and the phases differ in frame index, so
    # what is checked is that A and B are far closer to each other than
    # either is to the other composite.
    back = mean_abs(residual_a, residual_b)
    print(f"    residual vs residual again: mean |difference| {back:.2f} of 255")
    if back > delta / 4.0:
        failures.append(
            f"switching back did not restore the picture: {back:.2f} of 255 "
            f"against {delta:.2f} for the real difference - the flip is "
            f"one-way or leaves state behind")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK: the composite is a live switch, it costs no feature, and the "
          "two composites differ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
