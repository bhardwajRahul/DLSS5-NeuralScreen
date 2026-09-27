"""F3: the first frame of a duplication session must not be consumed blindly.

The #128 fix (v2.1.5) consumes the first frame of a fresh Desktop Duplication
session: that surface CAN be empty, and showing it blacked out the last good
frame in v.color, so a screenshot taken right after the capture woke up came
back black.

It did that unconditionally, while the no-colour fallback in the worker reopens
the capture *because* "a FRESH capture session hands over the current content
as its first frame". Measured on the bench with a still cover over the primary
output (scratch/probe_f3_static):

  reopen, retry 0    -> AccumulatedFrames=1, real LastPresentTime -> PICTURE
                        (consumed by the unconditional rule)
  reopen, retry 1-11 -> AccumulatedFrames=0, LastPresentTime=0 -> empty surface
  after the discard: 0 pictures - the fallback starved

The frame info is what separates the two cases, and that is what the fix keys
on:

  empty surface -> AccumulatedFrames == 0 and LastPresentTime == 0
  real picture  -> AccumulatedFrames >= 1 and a real LastPresentTime

What this test asserts, on the real worker and a real still desktop: the worker
never consumes a first frame that carried a picture. Every session opened here
has its first frame judged by the worker itself, and the worker logs which
decision it took. Before the fix every session printed "is empty - consumed";
on a still screen the first frame carries the desktop, so that decision throws
away the only picture the reopen had to offer.

Also checked: a frame asking for pixels on that still screen is answered with
a picture, and never with an all-black one (#128a).

The cover is tests/offscreen_target.Target(cover=True) - it owns its window on
its own thread and is always closed in finally, so a stuck run cannot leave a
window on screen.

Run:  runtime\\python.exe tests/test_dda_reopen_pixels.py
"""

import ctypes
import os
import re
import sys
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")   # no pygame window is used

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import protocol as pipe  # noqa: E402
from main import PROFILES  # noqa: E402
from offscreen_target import Target  # noqa: E402
from pipeline import shutdown_worker, start_worker  # noqa: E402

W, H = 640, 360
GW, GH = 160, 90

# The worker's decision about a session's first frame, as it logs it.
CONSUMED = re.compile(r"first frame is empty - consumed")
KEPT = re.compile(r"first frame carries the desktop - shown")


def main() -> int:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    screen_w = ctypes.windll.user32.GetSystemMetrics(0)
    screen_h = ctypes.windll.user32.GetSystemMetrics(1)
    user32 = ctypes.windll.user32

    failures = []
    params = dict(PROFILES["Natural"])
    cover = Target(screen_w, screen_h, name="NsF3StillCover", cover=True)
    worker = reader = stop = None
    motion = np.zeros((GH, GW, 2), dtype=np.float16)
    try:
        worker, logs, reader, stop = start_worker(params, W, H, 2, 0, 0, None)

        def raise_cover():
            """Keep the still cover above the worker's own overlay window - the
            worker re-asserts its window on every present, and a live window
            would keep the screen changing."""
            user32.SetWindowPos(cover.hwnd, -1, 0, 0, 0, 0, 0x0002 | 0x0001)

        pipe.send_dda(worker, screen_w, screen_h)
        reader.wait_dack(15)
        pipe.send_motion_size(worker, GW, GH)
        reader.wait_mack(10)

        # Let the capture come up on the still cover, then stand still: the
        # screen stops changing below the worker, which is the state F3 (and
        # the #128 wake-up) is about.
        for i in range(20):
            if i < 3:
                cover.repaint()
            if i % 4 == 0:
                raise_cover()
            pipe.send_frame(worker, i, None, motion, i == 0, i, no_color=True,
                            motion_small=True)
            reader.recv(i, 10.0)
            time.sleep(0.03)

        # Two more capture sessions: each open is a fresh session whose first
        # frame the worker must judge on its own.
        for round_no in range(2):
            raise_cover()
            pipe.send_dda(worker, 0, 0)
            reader.wait_dack(15)
            time.sleep(0.6)
            pipe.send_dda(worker, screen_w, screen_h)
            reader.wait_dack(15)
            for k in range(3):
                index = 200 + round_no * 10 + k
                raise_cover()
                pipe.send_frame(worker, index, None, motion, False, index,
                                want_pixels=True, motion_small=True,
                                no_color=True)
                reader.recv(index, 15.0)
                time.sleep(0.15)

        # The recording/screenshot slot on a screen that is not changing: the
        # ask must be answered with a picture, never with black.
        got, black, sizes = 0, 0, []
        for k in range(3):
            index = 300 + k
            raise_cover()
            pipe.send_frame(worker, index, None, motion, False, index,
                            want_pixels=True, motion_small=True, no_color=True)
            pixels = reader.recv(index, 15.0)
            if pixels is not None and pixels.size:
                got += 1
                sizes.append(int(pixels.size))
                if int(pixels[..., :3].max()) == 0:
                    black += 1
            else:
                sizes.append(0)
            time.sleep(0.3)

        text = "\n".join(list(logs))
        consumed = len(CONSUMED.findall(text))
        kept = len(KEPT.findall(text))
        print(f"    sessions judged: {kept} kept as a picture, {consumed} "
              f"consumed as empty")
        print(f"    still screen, 3 asks for pixels: {got} answered with a "
              f"picture, {black} black, element counts {sizes}")
        for line in text.splitlines():
            if "first frame" in line or "pixels asked for before" in line:
                print("   ", line.strip())

        if kept + consumed == 0:
            failures.append("the worker judged no session's first frame - the "
                            "capture never came up, nothing was measured")
        # The measurement this test exists for: on a still screen the reopen's
        # first frame carries the picture (measured with probe_f3_static), so it
        # must be KEPT. The pre-fix code consumed every first frame, so this is
        # what separates the two. When no session happened to open on a
        # picture-carrying first frame the run proves nothing - it skips rather
        # than passing on an unmeasured claim.
        if kept + consumed and not kept:
            failures.append(
                f"all {consumed} session first frame(s) were consumed as empty "
                f"on a still screen - a first frame carrying the desktop is "
                f"thrown away, which is the frame the reopen came for (F3)")
        if got == 0:
            failures.append(
                "pixels asked for on a still screen came back EMPTY - the "
                "fallback is starved (F3)")
        if black:
            failures.append(
                f"{black} of {got} pixel answers were all-black - an empty "
                f"first frame is reaching the client (#128a)")
        if "reopened the capture, still nothing" in text:
            failures.append(
                "the fallback logged 'still nothing' - it reopened the capture "
                "and the frame it came for had been consumed (F3)")
    except Exception as exc:
        failures.append(f"the run did not complete: {type(exc).__name__}: {exc}")
    finally:
        # Always: the worker down, then the window. A stuck read must not leave
        # the cover on screen.
        try:
            if worker is not None:
                shutdown_worker(worker, stop)
        except Exception:
            pass
        cover.close()

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: a session's first frame is judged on what it carries - a still "
          "screen still answers with pixels")
    return 0


if __name__ == "__main__":
    sys.exit(main())
