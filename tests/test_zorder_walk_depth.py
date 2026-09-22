r"""The z-order walk has to reach our own windows, however deep they sit.

THE BUG (#96, measured 20.09.2026)

`Display._top_real_window` walked the window stack from the top looking for
the first window that could be covering our layer, and the walk was bounded at
SIXTEEN steps. Helper windows - invisible IME entries, a 1x1 DWM thumbnail
helper, an off-screen Narrator window - are skipped, but they still spend
steps. On a busy desktop our own windows sit deeper than that, the walk ends
with "found nothing", and on that answer the guard does nothing at all: it
cannot see that the picture is above the panel, so it never puts the panel
back. The panel is drawn, and invisible.

Measured on the dev machine, walking 200 entries: the first window that could
cover anything was the SIXTEENTH, the next the forty-seventh, and seven of the
eight coverable windows were beyond the old bound. In the reporter's two
packages the healthy verdict `hud-on-top` appears ZERO times, against 63 on a
machine where the same code works.

WHAT THIS LOCKS

1. A stack with more helpers than the old bound still finds our panel.
2. The walk stops on OUR windows as themselves - including a picture window
   too small to pass the "can this cover us" test, which is exactly what
   one-window mode produces.
3. An invisible window of ours is NOT a stopping point: the thing above us is
   then whatever is above it.
4. A chain longer than the limit says `walk-exhausted`, not `nothing-covers`.
   Those are two different facts about the world, and printing them as one
   line is what made three diagnostic packages look healthy.

Run:  runtime\python.exe tests\test_zorder_walk_depth.py
"""
from __future__ import annotations

import contextlib
import ctypes
import io
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import display  # noqa: E402

HUD = 0x1111
PRESENT = 0x2222
#: A helper: visible but far too small to be covering anything.
HELPER = (True, (0, 0, 4, 4))
#: A real window, full screen.
BIG = (True, (0, 0, 2560, 1440))
#: One-window mode's picture: ours, and smaller than some helpers are wide.
SMALL = (True, (100, 100, 118, 118))


class _FakeUser32:
    """Just the surface the walk touches, over a scripted stack."""

    def __init__(self, stack: list[tuple[int, bool, tuple]]):
        # stack is top-first: (hwnd, visible, (l, t, r, b))
        self._order = [h for h, _v, _r in stack]
        self._by = {h: (v, r) for h, v, r in stack}
        self.raises: list[int] = []

    def GetTopWindow(self, _parent):
        return self._order[0] if self._order else 0

    def GetWindow(self, hwnd, _cmd):
        try:
            i = self._order.index(hwnd)
        except ValueError:
            return 0
        return self._order[i + 1] if i + 1 < len(self._order) else 0

    def IsWindowVisible(self, hwnd):
        return 1 if self._by.get(hwnd, (False, None))[0] else 0

    def GetWindowRect(self, hwnd, out):
        entry = self._by.get(hwnd)
        if entry is None:
            return 0
        l, t, r, b = entry[1]
        rect = ctypes.cast(out, ctypes.POINTER(display.wintypes.RECT)).contents
        rect.left, rect.top, rect.right, rect.bottom = l, t, r, b
        return 1

    # raise_topmost also reaches for these; harmless defaults.
    def FindWindowW(self, cls, _title):
        return PRESENT if cls == "NeuralScreenPresent" else 0

    def SetWindowPos(self, hwnd, *_a):
        self.raises.append(int(hwnd))
        return 1

    def GetWindowLongW(self, _hwnd, _idx):
        return 0x00000008          # WS_EX_TOPMOST

    def GetLayeredWindowAttributes(self, *_a):
        return 0


class _Walker(display.Display):
    """A Display with no window behind it - only the walk is under test."""

    def __init__(self):
        self._layer_state = "hud"
        self._zlog_sig = None
        self._zlog_t = 0.0

    def _virtual_screen(self):
        return (0, 0, 2560, 1440)


def walk(stack, ours=(HUD, PRESENT)):
    fake = _FakeUser32(stack)
    real = display.user32
    display.user32 = fake
    try:
        d = _Walker()
        got = d._top_real_window(ours)
        return got, bool(getattr(d, "_walk_exhausted", False))
    finally:
        display.user32 = real


def main() -> int:
    failures: list[str] = []

    # 1. Deeper than the old bound of sixteen.
    for depth in (10, 16, 20, 60, 200):
        stack = [(0x9000 + i, *HELPER) for i in range(depth)]
        stack.append((HUD, *BIG))
        got, exhausted = walk(stack)
        if got != HUD:
            failures.append(
                f"{depth} helpers above the panel: the walk returned "
                f"{got!r}, not the panel - the guard would see nothing to do "
                f"while the picture sat on top of it")
        if exhausted:
            failures.append(f"{depth} helpers: reported as exhausted, wrongly")

    # 2. Our picture window counts even when it is small (one-window mode).
    stack = [(0x9000 + i, *HELPER) for i in range(30)]
    stack.append((PRESENT, *SMALL))
    stack.append((HUD, *BIG))
    got, _ = walk(stack)
    if got != PRESENT:
        failures.append(
            f"a small picture window above the panel returned {got!r}: in "
            f"one-window mode the picture IS that small, and skipping it "
            f"means never noticing it took the top")

    # 3. An invisible window of ours does not end the walk.
    stack = [(HUD, False, (0, 0, 2560, 1440)), (0x7777, *BIG)]
    got, _ = walk(stack)
    if got != 0x7777:
        failures.append(
            f"an invisible panel ended the walk ({got!r}): what is above us "
            f"is then whatever is above IT")

    # 4. A chain past the limit is exhausted, not empty.
    stack = [(0x9000 + i, *HELPER) for i in range(display.WALK_LIMIT + 5)]
    got, exhausted = walk(stack)
    if got is not None:
        failures.append(f"a stack of helpers returned {got!r}")
    if not exhausted:
        failures.append(
            "a chain longer than the limit was not reported as exhausted - "
            "'we gave up' and 'nothing is above us' would print the same "
            "line again, which is what hid #96 for a week")

    # ...and the guard says so, in the word that appears in a user's log.
    fake = _FakeUser32(stack)
    real = display.user32
    real_wm = display.pygame.display.get_wm_info
    display.user32 = fake
    display.pygame.display.get_wm_info = lambda: {"window": HUD}
    buf = io.StringIO()
    try:
        d = _Walker()
        with contextlib.redirect_stdout(buf):
            d.raise_topmost()
    finally:
        display.user32 = real
        display.pygame.display.get_wm_info = real_wm
    line = buf.getvalue().strip()
    if "walk-exhausted" not in line:
        failures.append(f"the exhausted walk logged {line!r}, not walk-exhausted")
    if "hud=" not in line:
        failures.append(
            f"the decision line says nothing about our own panel: {line!r} - "
            f"under the picture, on another monitor and fully transparent are "
            f"the same log without it")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the walk reaches our windows past any helper stack, small and "
          "invisible ones are handled, and giving up says so")
    return 0


if __name__ == "__main__":
    sys.exit(main())
