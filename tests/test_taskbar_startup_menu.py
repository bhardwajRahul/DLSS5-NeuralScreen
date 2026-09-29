"""The menu must not open by itself at launch, whatever the cursor is doing (#96 family).

A 1x1 APPWINDOW window gives the program its taskbar button. It used to be
shown with SW_SHOW, which makes it the FOREGROUND window - and the
WM_NCACTIVATE(WA_ACTIVE) pair Windows sends right after is exactly what a
click on that button looks like to the guard that separates a real click from
a system activation (#96). So the menu opened on its own at every launch
where the cursor happened to rest over the taskbar, and stayed shut anywhere
else. Measured on the bench before the fix: 4/4 launches with the cursor over
the taskbar, 0/4 with it anywhere else; after it, 0/4 and 0/4.

What this test locks, each a way the fix could silently not be a fix:

  * the menu stays CLOSED on a launch whose config says so, with the cursor
    parked over the taskbar - the exact state that reproduced the report;
  * the taskbar button still EXISTS and is visible, because SW_SHOWNA is a
    weaker request than the SW_SHOW it replaced and the button disappearing
    would be a regression the user would notice immediately;
  * the launch really produced a running program, so a launch that died
    early cannot pass by having no menu to open.

The click paths themselves are covered by test_taskbar_first_click.py and
test_taskbar_window.py, which drive the real messages.

Run:  runtime\\python.exe tests\\test_taskbar_startup_menu.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # the project root
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ (autocheck)

try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import autocheck  # noqa: E402

u = ctypes.windll.user32
u.FindWindowW.restype = ctypes.c_void_p
u.FindWindowExW.restype = ctypes.c_void_p

TASKBAR_CLASS = "NeuralScreenTaskbar"
#: The state the report reproduced in: the menu is not asked for at startup.
STARTUP_OFF = {"open_menu_on_start": False}


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def taskbar_rects() -> list:
    """Every taskbar rectangle on the desktop, primary and secondary."""
    rows = []
    for cls in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
        hwnd = u.FindWindowExW(None, None, cls, None)
        while hwnd:
            r = wt.RECT()
            if u.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(r)):
                if r.right > r.left and r.bottom > r.top:
                    rows.append(r)
            hwnd = u.FindWindowExW(None, ctypes.c_void_p(hwnd), cls, None)
    return rows


def park_over_taskbar() -> tuple | None:
    """Put the cursor inside a real taskbar rect; the guard reads it live."""
    for r in taskbar_rects():
        x = (r.left + r.right) // 2
        y = (r.top + r.bottom) // 2
        u.SetCursorPos(x, y)
        time.sleep(0.4)
        got = POINT()
        u.GetCursorPos(ctypes.byref(got))
        if (got.x, got.y) == (x, y):
            return x, y
    return None


def restore_cursor(home: POINT) -> None:
    for _ in range(5):
        u.SetCursorPos(home.x, home.y)
        back = POINT()
        u.GetCursorPos(ctypes.byref(back))
        if (back.x, back.y) == (home.x, home.y):
            return
        time.sleep(0.05)


def button_state() -> dict:
    """The taskbar button window: does it exist, and is it visible?"""
    hwnd = u.FindWindowW(TASKBAR_CLASS, "NeuralScreen")
    if not hwnd:
        return {"hwnd": 0, "visible": False}
    return {"hwnd": int(hwnd), "visible": bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))}


def main() -> int:
    busy = autocheck.running_instances()
    if busy:
        print(f"FAIL: NeuralScreen is already running ({busy}) - stop it first")
        return 1

    home = POINT()
    u.GetCursorPos(ctypes.byref(home))
    failures: list[str] = []
    parked = park_over_taskbar()
    if parked is None:
        print("FAIL: no taskbar rectangle to park the cursor over - "
              "the state this test depends on cannot be built")
        restore_cursor(home)
        return 1
    print(f"    cursor parked over the taskbar at {parked}")

    try:
        offset = autocheck.launch(STARTUP_OFF)
        if not autocheck.wait_for(offset, autocheck.NR_FRAME_MARKER, 30.0):
            failures.append("NeuralScreen did not start processing - the run "
                            "proves nothing about the menu")
        else:
            # Past the dedup window and past the activation burst of startup.
            time.sleep(2.0)
            text = autocheck.log_since(offset)
            button = button_state()
            print(f"    taskbar button: hwnd={button['hwnd'] or 'none'} "
                  f"visible={button['visible']}")
            if "overlay menu opened" in text:
                failures.append(
                    "the menu opened by itself at launch with the cursor over "
                    "the taskbar - a system activation is being read as a "
                    "click on our own button (#96 family)")
            if not button["hwnd"]:
                failures.append("the taskbar button window does not exist - "
                                "showing it without activation lost the button")
            elif not button["visible"]:
                failures.append("the taskbar button window exists but is not "
                                "visible - the button is gone from the taskbar")
    finally:
        left = autocheck.quit_app()
        restore_cursor(home)
        if left:
            failures.append(f"processes left behind: {left}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: the menu stays closed with the cursor over the taskbar, and "
          "the button is still there")
    return 0


if __name__ == "__main__":
    sys.exit(main())
