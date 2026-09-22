"""A drop-down that opens upward must stay out of the title bar (audit H4).

The expansion of a choice control flips up when the room below the strip
cannot hold two rows. The room above was measured to `panel_rect.top` - the top
of the panel - but the panel's first band is the title bar: it is the drag
handle and carries no content. Rows laid out from `strip.top - 4 - visible*oh`
could therefore start above `_viewport.top`, inside the title bar.

Reproduced on a short panel (1080p, the height dragged to 350 - the language
list on the settings page): 2 of 9 rows overlapped the title bar, 1 row's
centre was above the viewport, and a left click on the first row started a
panel drag instead of selecting the language - the value stayed `en`.

The same rows could not be reached with the keyboard either: the open-list
branch activates options[_opt_index] without checking that the row is inside
the viewport, and _focusables() does not include the options at all. So the
list could be scrolled into a state whose top rows were visible but
unreachable by any input.

Checked here with the real OverlayMenu at the reported geometry, through the
real open path: every visible row must be inside the viewport, hit() must
accept its centre, and a click on a row must not start a drag.

Run:  runtime\\python.exe tests\\test_dropdown_inside_viewport.py
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import fonts  # noqa: E402
from display import ui_scale_for  # noqa: E402
from overlay_ui import OverlayMenu  # noqa: E402

SCREEN = (1920, 1080)
# The reported case: short enough that the list flips up and its top rows
# used to land in the title bar. 400/450 keep it opening down - both are
# checked so the fix cannot simply always open down.
HEIGHTS = (350, 400, 450, 500)


class _Event:
    def __init__(self, type_, **kw):
        self.type = type_
        for k, v in kw.items():
            setattr(self, k, v)


def _menu(height: int) -> "OverlayMenu":
    if not pygame.font.get_init():
        pygame.font.init()
    scale = ui_scale_for(SCREEN[1])
    menu = OverlayMenu(scale, lambda size=14, mono=False, bold=False, L="en":
                       fonts.load(size, mono=mono, bold=bold, lang=L))
    menu.lang = "en"
    menu.set_state({"lang": "en"})
    menu.page = "settings"
    menu.settings_tab = "app"
    menu.user_height = height
    menu.visible = True
    menu.layout(*SCREEN)
    return menu


def _open_language_list(menu) -> bool:
    """Open the language choice through the real activate path."""
    item = next((i for i in menu.items if i.key == "lang"), None)
    if item is None:
        return False
    menu._set_focus(item, from_mouse=True)
    menu._activate_item(item)
    menu._relayout()
    return menu.open_choice is not None


def main() -> int:
    failures = []
    saw_up = False

    for height in HEIGHTS:
        menu = _menu(height)
        if not _open_language_list(menu):
            failures.append(f"height {height}: the language list did not open")
            continue
        rows = list(menu.options)
        if not rows:
            failures.append(f"height {height}: the open list has no rows")
            continue
        viewport = menu._viewport
        title = menu._title_bar
        item = next(i for i in menu.items if i.key == "lang")
        strip = item.extra["strip"]
        down_room = menu.panel_rect.bottom - strip.bottom - menu._u(8)
        oh = menu._u(26) + menu._u(6)
        up_room = strip.top - viewport.top - menu._u(8)
        opens_up = down_room < 2 * oh and up_room > down_room
        saw_up = saw_up or opens_up

        for idx, row in enumerate(rows):
            if not viewport.contains(row.rect):
                # The row's bottom may sit exactly on the viewport edge.
                if row.rect.bottom > viewport.bottom or row.rect.top < viewport.top:
                    failures.append(
                        f"height {height}: row {idx} at {tuple(row.rect)} is "
                        f"outside the viewport {tuple(viewport)} "
                        f"(opens_up={opens_up}) - drawn over the title bar and "
                        f"unreachable by mouse or keyboard")
            if row.rect.colliderect(title):
                failures.append(
                    f"height {height}: row {idx} at {tuple(row.rect)} overlaps "
                    f"the title bar {tuple(title)} - the drag handle takes the "
                    f"click instead of the row")
            if menu.hit(row.rect.center) is None:
                failures.append(
                    f"height {height}: hit() rejects the centre of visible row "
                    f"{idx} - clicking a row the user can see does nothing")

        # A click on the first visible row must not start a panel drag.
        first = rows[0].rect
        menu.handle_event(_Event(pygame.MOUSEBUTTONDOWN, pos=first.center,
                                 button=1))
        if menu._move_from is not None:
            failures.append(
                f"height {height}: a click on the first visible row started a "
                f"panel drag instead of selecting it "
                f"(row at {tuple(first)}, title bar at {tuple(title)})")
            menu._move_from = None
        menu.handle_event(_Event(pygame.MOUSEBUTTONUP, pos=first.center,
                                 button=1))

    if not saw_up:
        print("FAIL: the list never opened upward at any tested height - this "
              "test no longer covers the reported case")
        return 1

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: an up-opening list stays out of the title bar and every visible "
          "row is reachable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
