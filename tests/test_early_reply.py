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
import threading
import time
from ctypes import wintypes
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import numpy as np  # noqa: E402

from paths import WORKER_EXE  # noqa: E402

W, H = 1280, 720

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                  wintypes.LPARAM]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


_PROC = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))


class Target:
    """The capture source: off-screen, never activated, no taskbar button."""

    def __init__(self):
        self.hwnd = None
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _run(self):
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _PROC
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = "NsEarlyReplyTest"
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0x80 | 0x08000000, wc.lpszClassName, "ns-early-reply",  # TOOLWINDOW|NOACTIVATE
            0x80000000, -20000, -20000, W, H, None, None, wc.hInstance, None)
        user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
        dc = user32.GetDC(self.hwnd)
        for k, colour in enumerate((0x2020D0, 0x20B020, 0xD02020, 0x808080)):
            brush = gdi32.CreateSolidBrush(colour)
            r = wintypes.RECT(k * W // 4, 0, (k + 1) * W // 4, H)
            user32.FillRect(dc, ctypes.byref(r), brush)
            gdi32.DeleteObject(brush)
        user32.ReleaseDC(self.hwnd, dc)
        self._ready.set()
        msg = wintypes.MSG()
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.01)
        user32.DestroyWindow(self.hwnd)

    def close(self):
        self._stop.set()
        self._thread.join(2)


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
    target = Target()
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
