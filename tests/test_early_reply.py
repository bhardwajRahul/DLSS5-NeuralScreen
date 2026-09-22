"""The early reply: the worker answers a frame once it is queued, not shown.

FRAME_FLAG_EARLY_REPLY lets the client's own per-frame work (the HUD, the
commands, the next capture request) run while the GPU finishes the frame,
instead of between frames - on a 5080 at 1440p it took the loop from 120 to
148 fps with the client's measured per-frame work simulated. It is a change
to WHEN the answer comes, so what is checked here is the contract around it:

* exactly one answer per frame: never the early one AND the usual one;
* the early answer only where it is safe - a processed frame the worker
  presents, with nothing to send back. A frame that wants pixels still gets
  them, and the NR-off (bypass) path answers as it always did;
* the frames really are presented, early or not;
* and it pays: with the flag, the client waits a fraction of what it waits
  without it.

Both windows are far off every monitor (NS_WINDOW_POS and the capture
target's own position), and the capture target never activates and has no
taskbar button, so this runs without anything appearing on screen.

Run:  runtime\\python.exe tests\\test_early_reply.py
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

W, H = 1280, 720

user32 = ctypes.windll.user32


def main() -> int:
    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
        return 0
    user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    os.environ["NS_WINDOW_POS"] = "-30000,-30000"
    os.environ["NS_NR_SMALL"] = "0"
    os.environ["NS_PHASE"] = "1"
    from pipeline import shutdown_worker, start_worker
    from protocol import (SharedFrameBuffer, prepare_capture, send_frame,
                          send_gray, send_motion_size, send_out, send_wgc,
                          send_window)
    from settings_io import PROFILES

    failures: list = []
    target = Target(W, H, name="NsEarlyReplyTest")
    params = dict(PROFILES["Natural"])
    params["style"] = 1
    shm = SharedFrameBuffer(W, H)
    worker, logs, reader, stop = start_worker(params, W, H, 2, 0, 0, shm)
    try:
        send_wgc(worker, target.hwnd)
        if reader.wait_wgak(15) != (W, H):
            failures.append("the capture target is not the expected size")
        shm.open_gray(160, 90)
        send_gray(worker, 160, 90, shm.gray_name)
        reader.wait_gak(10)
        send_motion_size(worker, 160, 90)
        reader.wait_mack(10)
        shm.open_out(W, H)
        send_out(worker, W, H, shm.out_name)
        reader.wait_oak(10)
        send_window(worker, W, H)
        reader.wait_wack(10)
        motion = np.zeros((90, 160, 2), np.float16)
        state = {"index": 0}
        # Every answer the reader takes off the pipe, counted per frame. A
        # frame has exactly two: the capture's (CAP1) and its own. recv()
        # drops an answer it is not waiting for, so a doubled one would pass
        # unseen anywhere but here.
        counts: dict = {}
        real_put = reader._queue.put

        def counting_put(item, *args, **kwargs):
            if isinstance(item[0], int):
                counts[item[0]] = counts.get(item[0], 0) + 1
            return real_put(item, *args, **kwargs)

        reader._queue.put = counting_put

        def frame(early: bool, want_pixels: bool = False, bypass: bool = False):
            i = state["index"]
            state["index"] += 1
            prepare_capture(worker, reader, i, i)
            t = time.perf_counter()
            send_frame(worker, i, None, motion, i == 0, i, no_color=True,
                       motion_small=True, prepared=True, early_reply=early,
                       want_pixels=want_pixels, bypass=bypass)
            pixels = reader.recv(i, timeout=10.0)
            waited = time.perf_counter() - t
            # The client's own work, the size the user's log measured: the
            # early answer is worth exactly what this overlaps.
            end = time.perf_counter() + 0.0015
            while time.perf_counter() < end:
                pass
            return pixels, waited

        for _ in range(40):
            frame(False)
        timing = {}
        for early in (False, True, False, True):
            waits = [frame(early)[1] for _ in range(120)]
            timing.setdefault(early, []).extend(waits[20:])
        normal = float(np.median(timing[False])) * 1000
        fast = float(np.median(timing[True])) * 1000
        print(f"    median wait for the answer: {normal:.2f} ms usual, "
              f"{fast:.2f} ms early")
        if fast > normal * 0.5:
            failures.append(f"the early answer does not come early: {fast:.2f} ms "
                            f"against {normal:.2f} ms")

        # A frame that wants pixels gets them, early flag or not.
        pixels, _ = frame(True, want_pixels=True)
        if pixels is None or pixels.shape != (H, W, 4):
            failures.append(f"a pixel request with the early flag came back "
                            f"without pixels: {None if pixels is None else pixels.shape}")
        # NR off answers as it always did, and the next early frame too.
        for _ in range(5):
            frame(True, bypass=True)
        for _ in range(5):
            frame(True)
        time.sleep(0.2)
        wrong = {i: n for i, n in counts.items() if n != 2}
        print(f"    {len(counts)} frames, {sum(counts.values())} answers")
        if wrong:
            failures.append(f"frames answered other than twice (capture + "
                            f"frame): {dict(list(wrong.items())[:8])}")
        if len(counts) != state["index"]:
            failures.append(f"{state['index']} frames sent, {len(counts)} answered")
    except Exception as exc:
        failures.append(f"the run raised {type(exc).__name__}: {exc}")
    finally:
        shutdown_worker(worker, stop)
        shm.close()
        target.close()
    presented = [ln for ln in logs if "[present]" in ln and "failed" in ln]
    if presented:
        failures.append("present failures: " + "; ".join(presented[-3:]))
    if not any("window revealed on the first Present" in ln for ln in logs):
        failures.append("the worker never presented a frame")
    if failures:
        print("=" * 60)
        for f in failures:
            print("FAIL:", f)
        print("worker log (tail):", *logs[-12:], sep="\n  ")
        return 1
    print("OK: one answer per frame, early only where safe, pixels and bypass "
          "unchanged, and the wait for the answer cut")
    return 0


if __name__ == "__main__":
    sys.exit(main())
