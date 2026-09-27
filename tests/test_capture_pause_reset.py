"""A still screen is not a capture pause; a real interruption still is (F4).

#130 made the stall reset real: after a silence of more than a second the
next new picture resets the NR history and FG's interpolation. But "silence"
was any second without a new frame, and on a desktop that is just not
changing that is every second of it: DDA answers WAIT_TIMEOUT, WGC has no
frame. Since the static-frame skip went (a0371ca) NR and FG run on each of
those frames, so their history is still valid - and the reset put a hitch on
the first scroll, keystroke or video frame after any pause.

Checked on the worker, with a window captured through WGC:

1. the window still for 1.5 s, then moving again: no
   "[reset] capture resumed" line;
2. the window minimised for 1.5 s, then back and moving: the reset happens -
   WGC saw nothing while the window may have moved on;
3. the capture closed and reopened, with 1.5 s between: the reset happens.

(1) fails on the code before the fix; (2) and (3) are the #130 behaviour that
must stay.

Nothing shows: the worker's window is far off every monitor and the target is
a ghost (tests/offscreen_target.py). Needs an RTX GPU.

Run:  runtime\\python.exe tests\\test_capture_pause_reset.py
"""
import ctypes
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from offscreen_target import Target  # noqa: E402
from paths import WORKER_EXE  # noqa: E402

W, H = 640, 360
GW, GH = 160, 90
RESET = "[reset] capture resumed"
SW_MINIMIZE, SW_SHOWNOACTIVATE = 6, 4


def main() -> int:
    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
        return 0
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    os.environ["NS_WINDOW_POS"] = "-30000,-30000"
    os.environ["NS_NR_SMALL"] = "0"
    os.environ["NS_HDR"] = "0"
    from pipeline import shutdown_worker, start_worker
    from protocol import send_frame, send_motion_size, send_wgc, send_window
    from settings_io import PROFILES

    user32 = ctypes.windll.user32
    failures: list = []
    target = Target(W, H, name="NsCapturePauseReset", ghost=True)
    worker, logs, reader, stop = start_worker(dict(PROFILES["Natural"]), W, H, 2,
                                              0, 0, None)
    state = {"index": 0}
    motion = np.zeros((GH, GW, 2), np.float16)

    def frames(seconds: float) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            i = state["index"]
            state["index"] += 1
            send_frame(worker, i, None, motion, i == 0, i, no_color=True,
                       motion_small=True)
            reader.recv(i, timeout=10.0)
            time.sleep(0.01)

    def resets() -> int:
        return sum(RESET in ln for ln in list(logs))

    def moving(seconds: float = 1.0) -> None:
        target.animate = True
        frames(seconds)
        target.animate = False

    try:
        send_wgc(worker, target.hwnd)
        if reader.wait_wgak(15) != (W, H):
            failures.append("the capture target is not the expected size")
        send_motion_size(worker, GW, GH)
        reader.wait_mack(10)
        send_window(worker, W, H)
        reader.wait_wack(10)
        moving()

        # 1. Still, then moving: a desktop that did not change.
        before = resets()
        frames(1.5)
        moving()
        still = resets() - before
        print(f"    still 1.5 s, then moving: {still} reset(s)")
        if still:
            failures.append(f"a window that was only still for 1.5 s reset the NR "
                            f"and FG history {still} time(s) when it moved again")

        # 2. Minimised: WGC sees nothing while the window may change.
        before = resets()
        user32.ShowWindow(target.hwnd, SW_MINIMIZE)
        frames(1.5)
        user32.ShowWindow(target.hwnd, SW_SHOWNOACTIVATE)
        moving()
        minimised = resets() - before
        print(f"    minimised 1.5 s, then back and moving: {minimised} reset(s)")
        if minimised != 1:
            failures.append(f"a window back from 1.5 s minimised reset the history "
                            f"{minimised} time(s), not once (#130)")

        # 3. The capture reopened after a gap.
        before = resets()
        time.sleep(1.5)
        send_wgc(worker, target.hwnd)
        reader.wait_wgak(15)
        moving()
        reopened = resets() - before
        print(f"    capture reopened after 1.5 s: {reopened} reset(s)")
        if reopened != 1:
            failures.append(f"a capture reopened after 1.5 s reset the history "
                            f"{reopened} time(s), not once (#130)")
    except Exception as exc:
        failures.append(f"the run raised {type(exc).__name__}: {exc}")
    finally:
        shutdown_worker(worker, stop)
        target.close()
    if failures:
        for f in failures:
            print("FAIL:", f)
        print("worker log (tail):", *logs[-12:], sep="\n  ")
        return 1
    print("OK: a still window keeps its history; a minimised window and a "
          "reopened capture still reset it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
