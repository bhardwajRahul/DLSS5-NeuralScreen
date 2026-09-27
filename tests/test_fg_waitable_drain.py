"""FG started after ordinary presenting is really paced by the compositor (F6).

Every present chain is created with a frame-latency waitable, and every
retired present raises its semaphore - but the ordinary (non-FG) path never
waits on it. After four seconds of NR-only presenting it held ~40 counts, and
the FG presenter consumed one at start: every later wait returned at once, for
the whole session, while the log said "paced by the compositor (latency 1)".

Checked on the worker: NR on and FG off for four seconds (a window captured
through WGC, the overlay presenting normally), then FG 4x on a source fast
enough that 4x outruns the display. A presenter that is really paced waits
most of a vblank before each present; one that is not waits nothing. The
presenter's own phase line reports that wait.

Fails on the code before the fix (vblank wait 0.00 ms); passes after it
(about one vblank). Nothing shows: the overlay is far off every monitor and
the target is a ghost. Needs an RTX GPU with DLSS-G.

Run:  runtime\\python.exe tests\\test_fg_waitable_drain.py
"""
import ctypes
import os
import re
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
VBLANK_RE = re.compile(r"\[phase\] fg presenter: .*?vblank wait ([\d.]+)/([\d.]+) ms")


def refresh_hz() -> int:
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = ctypes.c_void_p
    user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    gdi32.GetDeviceCaps.argtypes = [ctypes.c_void_p, ctypes.c_int]
    dc = user32.GetDC(None)
    try:
        hz = gdi32.GetDeviceCaps(dc, 116)  # VREFRESH
    finally:
        user32.ReleaseDC(None, dc)
    return hz if hz > 1 else 60


def main() -> int:
    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
        return 0
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    os.environ["NS_WINDOW_POS"] = "-30000,-30000"
    os.environ["NS_NR_SMALL"] = "0"
    os.environ["NS_PHASE"] = "1"
    os.environ["NS_HDR"] = "0"
    from pipeline import shutdown_worker, start_worker
    from protocol import send_frame, send_motion_size, send_wgc, send_window
    from settings_io import PROFILES

    hz = refresh_hz()
    vblank_ms = 1000.0 / hz
    failures: list = []
    target = Target(W, H, name="NsFgWaitableDrain", ghost=True)
    # Half the refresh rate at 4x is twice the refresh rate: the presenter
    # always has a frame ready before the display takes the last one.
    target.animate_interval = 1.0 / min(100.0, max(30.0, hz / 2.0))
    target.animate = True
    worker, logs, reader, stop = start_worker(dict(PROFILES["Natural"]), W, H, 2,
                                              0, 0, None)
    state = {"index": 0}
    motion = np.zeros((GH, GW, 2), np.float16)

    def run(seconds: float, fg: bool) -> None:
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            i = state["index"]
            state["index"] += 1
            send_frame(worker, i, None, motion, i == 0, i, no_color=True,
                       motion_small=True, frame_generation=fg, frame_multiplier=4)
            reader.recv(i, timeout=10.0)

    means = []
    try:
        send_wgc(worker, target.hwnd)
        if reader.wait_wgak(15) != (W, H):
            failures.append("the capture target is not the expected size")
        send_motion_size(worker, GW, GH)
        reader.wait_mack(10)
        send_window(worker, W, H)
        reader.wait_wack(10)
        run(4.0, False)          # ordinary presenting: the semaphore fills
        seen = set()
        end = time.perf_counter() + 7.0
        while time.perf_counter() < end:
            run(0.5, True)
            for ln in list(logs):
                m = VBLANK_RE.search(ln)
                if m and ln not in seen:
                    seen.add(ln)
                    means.append(float(m.group(1)))
    except Exception as exc:
        failures.append(f"the run raised {type(exc).__name__}: {exc}")
    finally:
        shutdown_worker(worker, stop)
        target.close()
    # The first report can straddle the start; the rest is steady state.
    steady = means[1:]
    print(f"    display {hz} Hz ({vblank_ms:.2f} ms); FG presenter vblank wait "
          f"means after NR-only presenting: {steady}")
    if not any("paced by the compositor" in ln for ln in list(logs)):
        print("SKIP: the swap chain has no latency waitable here - nothing to drain")
        return 0
    if not steady:
        failures.append("the FG presenter reported nothing - FG never ran")
    elif sum(steady) / len(steady) < 0.3 * vblank_ms:
        failures.append(f"the FG presenter waited {sum(steady) / len(steady):.2f} ms "
                        f"for the compositor on average, against a {vblank_ms:.2f} ms "
                        f"vblank: the waitable never blocks - it kept the counts the "
                        f"ordinary path left in it")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: FG started after ordinary presenting waits for the compositor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
