"""The menu must stay on screen when the captured window is minimised.

The report: "clicking the taskbar button no longer expands it". The log shows
why, and it is not the button:

    11:37:15  [wgc] the window is minimised - the overlay is hidden
    11:37:18  [main] overlay menu opened
    11:37:20  [main] overlay menu opened
    ...six "opened" in a row, not one visible menu...
    11:39:00  [wgc] the window is back - the overlay is shown

`follow_window` hides the layer whenever the captured window is minimised, so
the click that shows the menu and the frame that hides it are a race the menu
always loses. The layer IS the menu while the menu is open - its subject is our
own settings, not the captured picture - so hiding it makes the whole app look
dead until the source window is restored.

What is asserted here:

  * menu open + source minimised -> the layer is NOT hidden;
  * menu CLOSED + source minimised -> the layer IS hidden (the behaviour that
    exists on purpose: no picture to show, so nothing floats over the desktop);
  * menu open + source minimised -> follow_pos is not reset, so the layer does
    not jump when the window comes back.

The window is reported as minimised by patching IsIconic - the same ctypes
technique the other tests here use - so the test does not need a real minimised
window and cannot disturb the desktop.
"""
import ctypes
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import pipeline  # noqa: E402

RECT = (100, 100, 1354, 853)     # where the captured window sits


class _Menu:
    def __init__(self):
        self.visible = False


class _Display:
    """Only what follow_window touches, recording what it was told."""

    def __init__(self):
        self.menu = _Menu()
        self._visible = True
        self.visible_calls = []

    def is_visible(self):
        return self._visible

    def set_visible(self, on):
        self._visible = bool(on)
        self.visible_calls.append(bool(on))

    def move_to(self, x, y):
        pass

    def raise_topmost(self):
        pass

    def is_switch_active(self):
        return False


def _state(menu_open: bool):
    st = types.SimpleNamespace(
        window_hwnd=0x1707D8,
        worker_failed=False,
        dda_mode=False,
        width=RECT[2], height=RECT[3],
        follow_pos=(100, 100),
        follow_resize=None,
        follow_size=RECT[2:],
        frame_index=1,
        display=_Display())
    st.display.menu.visible = menu_open
    return st


def _drive(st, minimised: bool):
    """Run follow_window once with the source window minimised or not."""
    real_rect = pipeline.window_frame_rect
    real_iconic = ctypes.windll.user32.IsIconic
    pipeline.window_frame_rect = lambda hwnd: RECT
    ctypes.windll.user32.IsIconic = lambda hwnd: bool(minimised)
    try:
        pipeline.follow_window(st)
    finally:
        pipeline.window_frame_rect = real_rect
        ctypes.windll.user32.IsIconic = real_iconic


def main() -> int:
    failures = []

    # 1. The regression: the menu is open and the source window is minimised.
    #    The layer must stay visible - it is the menu.
    st = _state(menu_open=True)
    pos_before = st.follow_pos
    _drive(st, minimised=True)
    if not st.display.is_visible():
        failures.append(
            "the menu was hidden while the captured window was minimised - "
            "the taskbar button then looks dead (the reported regression)")
    if False in st.display.visible_calls:
        failures.append(
            f"set_visible(False) was called with the menu open: "
            f"{st.display.visible_calls}")
    if st.follow_pos != pos_before:
        failures.append(
            "follow_pos was reset while the menu was open, so the layer "
            "jumps when the window comes back")

    # 2. The behaviour that must NOT change: no menu, source minimised -
    #    the HUD goes with the window rather than floating over the desktop.
    st = _state(menu_open=False)
    _drive(st, minimised=True)
    if st.display.is_visible():
        failures.append(
            "the HUD stayed visible over the desktop with the source window "
            "minimised and no menu - that is the behaviour the hide is for")
    if st.display.visible_calls != [False]:
        failures.append(
            f"expected exactly one hide, got {st.display.visible_calls}")

    # 3. Control: a normal frame with the window up must not hide anything,
    #    menu open or not. Without this the fix could pass by never hiding.
    for menu_open in (False, True):
        st = _state(menu_open=menu_open)
        _drive(st, minimised=False)
        if not st.display.is_visible():
            failures.append(
                f"the layer was hidden for a live window (menu open="
                f"{menu_open}) - the fix broke the normal path")
        if st.display.visible_calls:
            failures.append(
                f"a live window changed visibility (menu open={menu_open}): "
                f"{st.display.visible_calls}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1

    print("OK: the menu survives a minimised source; the HUD still hides "
          "without one; the normal path is untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
