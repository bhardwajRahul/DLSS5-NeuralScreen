"""Virtual-desktop placement: the overlay follows the taskbar window.

Windows 11 keeps the 1x1 taskbar window on the desktop the user moved the
program to (Task View), while the borderless HUD/picture windows can stay
behind - the menu then opens on a desktop nobody is looking at, which reads
as "the program does not expand" (issue #93, user Saymoin).

The module must degrade quietly: an unavailable interface (no manager) means
"unknown", never an exception and never a guess. This test drives the module
with a fake manager so the decision logic is checked without touching a real
desktop, and then checks the real interface once on this machine (a probe,
skipped when the interface is absent).

Run: runtime\\python.exe tests\\test_vdesk_follow.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import vdesk  # noqa: E402


DESK_A = vdesk._guid(1, 2, 3, (4, 5, 6, 7, 8, 9, 10, 11))
DESK_B = vdesk._guid(9, 9, 9, (1, 2, 3, 4, 5, 6, 7, 8) + ())


def _fake(manager):
    """Install a fake manager: returns (restore, calls)."""
    calls = SimpleNamespace(moves=[], ids=[], fail_get=False, fail_move=False)
    saved = (vdesk._get_manager, vdesk._calls, vdesk._manager_failed)
    vdesk._manager_failed = False

    def get_id(_obj, hwnd, out):
        calls.ids.append(hwnd)
        if calls.fail_get:
            return 1
        out._obj.Data1 = manager.get(hwnd, DESK_A).Data1
        out._obj.Data2 = manager.get(hwnd, DESK_A).Data2
        out._obj.Data3 = manager.get(hwnd, DESK_A).Data3
        for i, b in enumerate(manager.get(hwnd, DESK_A).Data4):
            out._obj.Data4[i] = b
        return 0

    def move(_obj, hwnd, guid):
        calls.moves.append((hwnd, str(guid._obj)))
        return 1 if calls.fail_move else 0

    def calls_for(obj):
        return (None, get_id, move)

    vdesk._get_manager = lambda: object()
    vdesk._calls = calls_for

    def restore():
        vdesk._get_manager, vdesk._calls, vdesk._manager_failed = saved

    return restore, calls


def main() -> int:
    failures = []

    # 1. Already on the same desktop: no move, reported as followed.
    restore, calls = _fake({1: DESK_A, 2: DESK_A})
    try:
        ok = vdesk.follow_window(1, 2)
        if not ok:
            failures.append("same desktop: expected True")
        if calls.moves:
            failures.append(f"same desktop: no move expected, got {calls.moves}")
    finally:
        restore()

    # 2. Different desktops: the window is moved onto the reference desktop.
    restore, calls = _fake({1: DESK_A, 2: DESK_B})
    try:
        ok = vdesk.follow_window(1, 2)
        if not ok:
            failures.append("different desktop: expected True")
        if len(calls.moves) != 1:
            failures.append(f"different desktop: one move expected, got {calls.moves}")
        elif calls.moves[0][1] != str(DESK_B):
            failures.append(f"moved to the wrong desktop: {calls.moves[0][1]} "
                            f"instead of {DESK_B}")
    finally:
        restore()

    # 3. An unreadable reference id means "unknown": nothing is moved.
    restore, calls = _fake({1: DESK_A, 2: DESK_B})
    calls.fail_get = True
    try:
        ok = vdesk.follow_window(1, 2)
        if ok:
            failures.append("unknown desktop: expected False")
        if calls.moves:
            failures.append(f"unknown desktop: no move expected, got {calls.moves}")
    finally:
        restore()

    # 4. A failed move is reported honestly.
    restore, calls = _fake({1: DESK_A, 2: DESK_B})
    calls.fail_move = True
    try:
        if vdesk.follow_window(1, 2):
            failures.append("a refused move must not be reported as followed")
    finally:
        restore()

    # 5. No manager (interface unavailable): everything degrades to "unknown"
    #    without raising - the caller then leaves the windows alone.
    restore, calls = _fake({1: DESK_A})
    vdesk._get_manager = lambda: None
    try:
        if vdesk.follow_window(1, 2):
            failures.append("no manager: expected False")
        if vdesk.desktop_id(1) is not None:
            failures.append("no manager: desktop_id must be None")
    finally:
        restore()

    # 6. A null window handle is never passed to COM.
    restore, calls = _fake({0: DESK_A})
    try:
        if vdesk.desktop_id(None) is not None:
            failures.append("None hwnd must not resolve to a desktop")
    finally:
        restore()

    # 7. Real interface on this machine: the calls answer (a probe, not a
    #    fixture). Skipped when the COM class is not available at all.
    obj = vdesk._get_manager()
    if obj is None:
        print("note: IVirtualDesktopManager unavailable - the real-call probe "
              "is skipped")
    else:
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        hwnd = ctypes.windll.user32.CreateWindowExW(
            0x00040000, "Static", "vdesk test", 0x80000000 | 0x10000000,
            0, 0, 2, 2, None, None, hinst, None)
        try:
            if not hwnd:
                print("note: could not create a probe window - skipping")
            else:
                own = vdesk.desktop_id(hwnd)
                print(f"real probe: window {hwnd} is on desktop {own}")
                if own is None:
                    failures.append("the real interface could not read a "
                                    "desktop id")
                else:
                    # Moving a window onto the desktop it is already on must
                    # be accepted.
                    if not vdesk.move_to_desktop(hwnd, own):
                        failures.append("MoveWindowToDesktop refused a no-op "
                                        "move")
                    if vdesk.desktop_id(hwnd) != own:
                        failures.append("the window changed desktop on a "
                                        "no-op move")
        finally:
            if hwnd:
                ctypes.windll.user32.DestroyWindow(hwnd)

    # 8. The taskbar class name the display side looks up matches the real
    #    one (a rename would silently stop the follow).
    import taskbar
    src = Path(taskbar.__file__).read_text(encoding="utf-8")
    if 'cls = "NeuralScreenTaskbar"' not in src:
        failures.append("the taskbar window class changed - display.py looks "
                        "it up by the old name")
    disp = (BASE / "app" / "display.py").read_text(encoding="utf-8")
    if 'FindWindowW("NeuralScreenTaskbar"' not in disp:
        failures.append("display.follow_taskbar_desktop does not look up the "
                        "taskbar window by class")

    if failures:
        print("FAIL: " + "\n      ".join(failures))
        return 1
    print("OK: the overlay follows the taskbar window across virtual desktops, "
          "and degrades quietly when the interface is absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
