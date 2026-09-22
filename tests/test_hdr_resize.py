"""HDR: a captured window changing size must not end the worker.

A video player toggling fullscreen hands WGC its new size at once, and the
client follows only after the new size has held for half a second
(follow_window), with a live RNSZ. For that half second the capture and the
output disagree. The SDR path clips and carries on; the HDR composite used to
refuse the frame ("capture/output size mismatch; refusing stale HDR frame"),
and the worker exited with 9 - then the client restarted it at the old size
and again for the new one, ~4 s of disruption on every toggle, four times in
one user's evening.

Checked here, the way the user runs it (HDR on, Boost at native with two
passes), with Frame Generation off and then on:

* the window grows while frames flow, and the worker keeps answering every
  frame until the client's live resize - composed clipped, said once;
* the live resize (WGCW + RNSZ + WNDO) brings the sizes back together;
* the window shrinks again (the capture is now SMALLER than the output) and
  the same holds;
* nothing refuses a frame, and the worker is alive at the end.

Needs an HDR display nearest the capture target (the worker only captures
FP16 there); elsewhere it says SKIP. Nothing shows: the worker's window is far
off every monitor, and the target is a "ghost" - on the primary monitor at
1/255 opacity, click-through, at the bottom of the z-order. It has to be on a
monitor: DWM stops delivering a window that lies wholly outside every monitor
after its first frame and its resize frame, so a window off-screen never
produces the frame of the new size that trips this.

Run:  runtime\\python.exe tests\\test_hdr_resize.py
"""
import ctypes
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402

from offscreen_target import Target  # noqa: E402
from paths import WORKER_EXE  # noqa: E402

W, H = 1280, 720
GROWN = H + 48            # 1392 -> 1440 is what the user's player did
PASSES = 2
STEADY = 30
MISMATCHED_S = 1.0        # longer than the client's 0.5 s debounce


class WorkerGone(Exception):
    pass


def run(fg: bool, failures: list) -> bool:
    """One worker through grow and shrink. False when HDR capture is not on."""
    from pipeline import shutdown_worker, start_worker
    from protocol import (SharedFrameBuffer, prepare_capture, send_frame,
                          send_gray, send_motion_size, send_resize, send_wgc,
                          send_window)
    from settings_io import PROFILES, _work_size

    label = f"FG {'on' if fg else 'off'}"
    target = Target(W, H, name=f"NsHdrResize{int(fg)}", ghost=True)
    params = dict(PROFILES["Natural"])
    params["style"] = 1
    shm = SharedFrameBuffer(W, H)
    work = _work_size(W, H, 1.0, PASSES)
    worker, logs, reader, stop = start_worker(params, work[0], work[1], 2, W, H, None)
    state = {"index": 0}
    motion = np.zeros((90, 160, 2), np.float16)

    def frames(n: int = 0, seconds: float = 0.0):
        """n frames, or as many as `seconds` takes while the window keeps
        repainting - a playing video, which is what hands WGC a frame of the
        new size."""
        t_end = time.perf_counter() + seconds
        painted = 0.0
        k = 0
        while (time.perf_counter() < t_end) if seconds else (k < n):
            k += 1
            if seconds and time.perf_counter() - painted > 0.05:
                target.repaint()
                painted = time.perf_counter()
            i = state["index"]
            state["index"] += 1
            try:
                prepare_capture(worker, reader, i, i)
                send_frame(worker, i, None, motion, i == 0, i, no_color=True,
                           motion_small=True, prepared=True,
                           frame_generation=fg, frame_multiplier=4)
                reader.recv(i, timeout=10.0)
            except Exception as exc:
                raise WorkerGone(f"frame {i}: {type(exc).__name__}: {exc} "
                                 f"(exit code {worker.poll()})") from exc

    def live_resize(height: int):
        """What resize_window_live does once the size has held."""
        send_wgc(worker, target.hwnd)
        size = reader.wait_wgak(15)
        if size != (W, height):
            failures.append(f"{label}: the capture re-pointed at {size}, "
                            f"not {W}x{height}")
        new = _work_size(W, height, 1.0, PASSES)
        send_resize(worker, params, new[0], new[1], 2, W, height, True, False,
                    PASSES)
        reader.wait_rack(60)
        send_window(worker, W, height)
        reader.wait_wack(10)

    try:
        send_wgc(worker, target.hwnd)
        if reader.wait_wgak(15) != (W, H):
            failures.append(f"{label}: the capture target is not {W}x{H}")
        shm.open_gray(160, 90)
        send_gray(worker, 160, 90, shm.gray_name)
        reader.wait_gak(10)
        send_motion_size(worker, 160, 90)
        reader.wait_mack(10)
        send_window(worker, W, H)
        reader.wait_wack(10)
        send_resize(worker, params, work[0], work[1], 2, W, H, True, False, PASSES)
        reader.wait_rack(60)
        frames(STEADY)
        if not any("capture=FP16" in ln for ln in logs):
            print(f"    {label}: no FP16 capture here (no HDR display nearest "
                  f"the target) - nothing to check")
            return False
        try:
            target.resize(W, GROWN)                 # the player goes fullscreen
            frames(seconds=MISMATCHED_S)            # the client's half second
            live_resize(GROWN)
            frames(STEADY)
            target.resize(W, H)                     # and back
            frames(seconds=MISMATCHED_S)
            live_resize(H)
            frames(STEADY)
        except WorkerGone as exc:
            failures.append(f"{label}: the worker died during a resize - {exc}")
        print(f"    {label}: {state['index']} frames answered through grow "
              f"and shrink")
    finally:
        shutdown_worker(worker, stop)
        shm.close()
        target.close()

    refused = [ln for ln in logs if "refusing stale HDR frame" in ln]
    if refused:
        failures.append(f"{label}: a frame was refused: {refused[0].strip()}")
    clipped = [ln for ln in logs if "[hdr]" in ln and "clipped" in ln]
    print(f"    {label}: HDR clip notices: {len(clipped)}"
          + (f" - {clipped[0].strip()}" if clipped else ""))
    if not refused and len(clipped) != 2:
        failures.append(f"{label}: expected one HDR clip notice per resize "
                        f"(2), found {len(clipped)}")
    capture_clip = [ln for ln in logs if "[cap] size mismatch" in ln]
    if len(capture_clip) > 2:
        failures.append(f"{label}: the capture clip is logged per frame "
                        f"({len(capture_clip)} lines), not once per resize")
    if failures:
        print(f"    {label}: worker log (tail):", *logs[-10:], sep="\n      ")
    return True


def main() -> int:
    if not WORKER_EXE.is_file():
        print("SKIP: native/nvngx.dll is not built - the worker cannot run")
        return 0
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    os.environ["NS_WINDOW_POS"] = "-30000,-30000"
    os.environ["NS_HDR"] = "1"
    os.environ["NS_NR_SMALL"] = "1"
    os.environ["NS_MOTION_BACKEND"] = "nvofa"
    failures: list = []
    ran = [run(fg, failures) for fg in (False, True)]
    if not any(ran):
        print("SKIP: no HDR display to capture from")
        return 0
    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: with HDR on, a captured window grows and shrinks under the "
          "worker without ending it - clipped until the live resize, said once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
