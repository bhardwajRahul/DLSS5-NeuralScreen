"""A capture target for GPU tests that must not show anything on screen.

A plain window, never activated and without a taskbar button, painted in four
bars. The worker captures it through WGC like any window the user picks; with
NS_WINDOW_POS off-screen as well, a test drives the whole capture -> network
-> present chain with nothing visible.

Two placements:

* far off every monitor (the default). DWM delivers such a window's first
  frame and a resize frame, and nothing after that - enough for a test that
  needs one picture;
* a "ghost" (ghost=True): on the primary monitor, at 1/255 opacity,
  click-through and at the bottom of the z-order. DWM composes it, so every
  repaint reaches WGC - for a test that needs the picture to change.

It can be resized while it is being captured (a video player on every
fullscreen toggle), repainted with other colours (a scene cut), or left to
animate on its own (a playing video).

Not a test itself: tests import it (test_early_reply, test_hdr_resize,
test_worker_scene).
"""
import ctypes
import threading
import time
from ctypes import wintypes

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
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
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

user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                              wintypes.BYTE, wintypes.DWORD]

WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x80
WS_EX_NOACTIVATE = 0x08000000
WS_EX_LAYERED = 0x80000
WS_EX_TRANSPARENT = 0x20
LWA_ALPHA = 0x2
HWND_BOTTOM = wintypes.HWND(1)
SW_SHOWNOACTIVATE = 4
SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x2, 0x4, 0x10


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
                ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", wintypes.HICON)]


_PROC = WNDPROC(lambda h, m, w, l: user32.DefWindowProcW(h, m, w, l))
BARS = (0x2020D0, 0x20B020, 0xD02020, 0x808080)


class Target:
    """The capture source: never activated, no taskbar button, not visible.

    Everything that touches the window runs on its own thread, which owns
    it: creating, painting, resizing and destroying.
    """

    def __init__(self, width: int, height: int, name: str = "NsOffscreenTarget",
                 ghost: bool = False):
        self.hwnd = None
        self._size = (width, height)
        self._name = name
        self._ghost = ghost
        self.bars = tuple(BARS)
        # Repaint on every turn of the window's own loop (~100 a second).
        self.animate = False
        self._resize_to = None
        self._resized = threading.Event()
        self._ready = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(5)

    def _paint(self):
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        w, h = rect.right, rect.bottom
        dc = user32.GetDC(self.hwnd)
        # The last bar steps a shade on every paint: a paint that leaves every
        # pixel as it was gives WGC nothing new to deliver, and a playing video
        # - what this stands in for - never does that. Eight shades of one
        # channel's low bits: far below anything that reads as a scene cut.
        self._shade = (getattr(self, "_shade", 0) + 1) % 8
        bars = self.bars[:3] + (self.bars[3] ^ self._shade,)
        for k, colour in enumerate(bars):
            brush = gdi32.CreateSolidBrush(colour)
            r = wintypes.RECT(k * w // 4, 0, (k + 1) * w // 4, h)
            user32.FillRect(dc, ctypes.byref(r), brush)
            gdi32.DeleteObject(brush)
        user32.ReleaseDC(self.hwnd, dc)

    def _run(self):
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = _PROC
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = self._name
        user32.RegisterClassExW(ctypes.byref(wc))
        w, h = self._size
        ex = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        x = y = -20000
        if self._ghost:
            ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT
            x = y = 0
        self.hwnd = user32.CreateWindowExW(
            ex, wc.lpszClassName, self._name,
            WS_POPUP, x, y, w, h, None, None, wc.hInstance, None)
        if self._ghost:
            user32.SetLayeredWindowAttributes(self.hwnd, 0, 1, LWA_ALPHA)
            user32.SetWindowPos(self.hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                SWP_NOMOVE | 0x1 | SWP_NOACTIVATE)  # NOSIZE
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self._paint()
        self._ready.set()
        msg = wintypes.MSG()
        while not self._stop.is_set():
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            if self._resize_to is not None:
                w, h = self._resize_to
                self._resize_to = None
                user32.SetWindowPos(self.hwnd, None, 0, 0, w, h,
                                    SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
                self._paint()
                self._resized.set()
            elif self.animate:
                self._paint()
            time.sleep(0.01)
        user32.DestroyWindow(self.hwnd)

    def resize(self, width: int, height: int) -> None:
        """Resize (and repaint) on the window's own thread; returns when done."""
        self._resized.clear()
        self._resize_to = (width, height)
        self._resized.wait(5)

    def repaint(self) -> None:
        """A fresh frame for WGC without changing the size."""
        self.resize(*self._current())

    def set_bars(self, colours) -> None:
        """Four COLORREFs (0x00BBGGRR), painted at once - a scene cut."""
        self.bars = tuple(colours)
        self.repaint()

    def _current(self):
        rect = wintypes.RECT()
        user32.GetClientRect(self.hwnd, ctypes.byref(rect))
        return rect.right, rect.bottom

    def close(self):
        self._stop.set()
        self._thread.join(2)
