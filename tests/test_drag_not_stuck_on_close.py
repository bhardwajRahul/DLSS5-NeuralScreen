"""A drag started on the title bar must not survive the menu closing (audit H3).

Press the left button on the panel's title bar and close the menu before
releasing it - the min icon, Num2, Esc, or the taskbar. Closing sets
menu.visible = False, and the matching MOUSEBUTTONUP is then dropped by
handle_event's own `if not self.visible: return []` guard. So `_move_from` was
never cleared and ReleaseCapture() was never called.

Two consequences, both visible to the user:

* `menu.dragging` stayed True forever, and while dragging the main loop skips
  the whole per-frame payload refresh - FPS, REC, the GPU verdict, the window
  list and the profile all froze at the moment the menu was closed;
* `_move_from` is a live drag anchor. The first MOUSEMOTION after reopening -
  a plain motion, no button held - is applied by the motion branch, so the
  panel teleported by the whole distance the cursor had travelled meanwhile
  (a probe measured offset [0,0] -> [-460,153] from one buttonless move).

Checked here with the real OverlayMenu: the close route is driven for real, and
the assertions are the two consequences above plus the capture bookkeeping.

Run:  runtime\\python.exe tests\\test_drag_not_stuck_on_close.py
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import overlay_ui  # noqa: E402
from overlay_ui import OverlayMenu  # noqa: E402


class _Event:
    """The pygame event fields the menu reads, without a display."""

    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


def _menu() -> "OverlayMenu":
    if not pygame.font.get_init():
        pygame.font.init()
    menu = OverlayMenu(1.0, lambda size=14: pygame.font.Font(None, size))
    menu.lang = "en"
    menu.visible = True
    menu.layout(1920, 1080)
    return menu


def _press_title(menu) -> bool:
    """Put the menu into the title-bar drag state through its own handler."""
    rect = menu._title_bar
    if rect.w <= 0:
        return False
    pos = rect.center
    menu.handle_event(_Event(pygame.MOUSEBUTTONDOWN, pos=pos, button=1))
    return menu._move_from is not None


def main() -> int:
    failures = []

    # 0. The premise: pressing the title bar really does start a drag.
    menu = _menu()
    if not _press_title(menu):
        failures.append("pressing the title bar did not start a drag - the "
                        "premise of this test is gone")
    if not menu.dragging:
        failures.append("the panel does not report dragging after a "
                        "title-bar press")

    # 1. Closing the menu mid-drag must end the drag, even with no button-up.
    for label, close in (
        ("menu.visible = False (tray, Num2, Esc)",
         lambda m: setattr(m, "visible", False)),
    ):
        menu = _menu()
        if not _press_title(menu):
            failures.append(f"{label}: could not start the drag")
            continue
        close(menu)
        # The button-up arrives after the menu is already gone, which is what
        # happens in the real sequence.
        menu.handle_event(_Event(pygame.MOUSEBUTTONUP, pos=(10, 10), button=1))
        if menu._move_from is not None:
            failures.append(f"{label}: _move_from survived the close - the "
                            f"next motion teleports the panel")
        if menu.dragging:
            failures.append(f"{label}: dragging stayed True - the per-frame "
                            f"payload refresh stays off and the menu's numbers "
                            f"freeze")

    # 2. Reopening and moving the mouse with NO button held must not move the
    #    panel: that is the visible symptom.
    menu = _menu()
    if not _press_title(menu):
        failures.append("could not start the drag for the teleport check")
    else:
        menu.visible = False
        menu.handle_event(_Event(pygame.MOUSEBUTTONUP, pos=(10, 10), button=1))
        before = tuple(menu.offset)
        menu.visible = True
        menu.layout(1920, 1080)
        menu.handle_event(_Event(pygame.MOUSEMOTION, pos=(500, 200),
                                 rel=(0, 0), buttons=(0, 0, 0)))
        after = tuple(menu.offset)
        if after != before:
            failures.append(f"a buttonless motion teleported the panel: "
                            f"{before} -> {after}")

    # 3. The same for the other three drag kinds - a resize that survives the
    #    close is the same class of bug.
    menu = _menu()
    if menu._grip.w > 0:
        menu.handle_event(_Event(pygame.MOUSEBUTTONDOWN,
                                 pos=menu._grip.center, button=1))
        if menu._resize_from is None:
            failures.append("the grip press did not start a resize")
        menu.visible = False
        if menu.dragging:
            failures.append("a grip resize survived the menu closing")

    # 4. The capture is released: a leftover SetCapture would keep routing
    #    every click to the menu's window after the menu is gone.
    calls = []
    menu = _menu()
    original = menu._capture_mouse

    def _spy(on):
        calls.append(on)
        return original(on)

    menu._capture_mouse = _spy
    if not _press_title(menu):
        failures.append("could not start the drag for the capture check")
    else:
        if True not in calls:
            failures.append("the title-bar press never took the mouse capture")
        menu.visible = False
        if False not in calls:
            failures.append("closing mid-drag never released the mouse "
                            "capture - clicks keep going to a menu that is gone")

    # 5. A normal drag still works: press, move with the button held, release.
    menu = _menu()
    if _press_title(menu):
        start = tuple(menu.offset)
        menu.handle_event(_Event(pygame.MOUSEMOTION, pos=(100, 100),
                                 rel=(-20, -5), buttons=(1, 0, 0)))
        moved = tuple(menu.offset)
        if moved == start:
            failures.append("a real drag (button held) no longer moves the "
                            "panel")
        menu.handle_event(_Event(pygame.MOUSEBUTTONUP, pos=(100, 100), button=1))
        if menu.dragging:
            failures.append("the panel is still dragging after a normal "
                            "button-up")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: a drag never outlives the menu, and a real drag still works")
    return 0


if __name__ == "__main__":
    sys.exit(main())
