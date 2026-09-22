"""The z-order guard must see windows on EVERY monitor (issue #89).

Report (raycornea): with the panel open, toggling NR left the panel invisible
on the second display - but it WAS in the screenshot. The screenshot bakes the
panel from its own coordinates, so the panel really was drawn; what failed was
the pair being re-asserted above the picture, which is what the 30-frame
z-order guard does.

The guard skips helper windows by testing each rect against the desktop - and
it tested the PRIMARY screen (SM_CXSCREEN), not the virtual desktop. On a
monitor to the LEFT of the primary every window has a negative x: a
full-screen window there is (-2560, 0, 0, 1440) and the old test
`rect.right > 0 and rect.left < screen_w` rejected it. So on a left-hand
second display the guard never found a real window, never re-asserted the HUD,
and the panel stayed under the picture.

Checked here:

* a full-screen window on a monitor LEFT of the primary counts (the reported
  case) - this is what the old rule failed;
* the same on a monitor RIGHT of the primary, above and below it;
* helper windows are still skipped: the 1x1 dwm thumbnail, the 0x0 IME
  entries, the off-screen Narrator helper at -40000,-40000;
* the virtual desktop really is queried by the Display method, not the
  primary screen.

Run:  runtime\\python.exe tests\\test_top_window_virtual_desktop.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import display  # noqa: E402

user32 = ctypes.windll.user32


class R:
    """A rect stand-in; the real callers pass a wintypes.RECT."""

    def __init__(self, left, top, right, bottom):
        self.left, self.top = left, top
        self.right, self.bottom = right, bottom


def main() -> int:
    failures = []

    # The primary screen used by the old rule, and a typical left monitor.
    PRIMARY = (0, 0, 2560, 1440)
    LEFT = (-2560, 0, 2560, 1440)          # x = -2560, w = 2560
    RIGHT = (2560, 0, 2560, 1440)
    ABOVE = (0, -1440, 2560, 1440)
    BELOW = (0, 1440, 2560, 1440)

    # --- the rule itself --------------------------------------------------
    cases = [
        # (rect, virtual, expected, why)
        (R(0, 0, 2560, 1440), PRIMARY, True, "full-screen on the primary"),
        (R(-2560, 0, 0, 1440), LEFT, True,
         "#89: full-screen on a LEFT monitor - the reported case"),
        (R(2560, 0, 5120, 1440), RIGHT, True,
         "full-screen on a monitor to the right"),
        (R(0, -1440, 2560, 0), ABOVE, True, "full-screen on a monitor above"),
        (R(0, 1440, 2560, 2880), BELOW, True, "full-screen on a monitor below"),
        # A window that only TOUCHES the left monitor's right edge (x == 0)
        # does not overlap it: right > vx fails.
        (R(-2560, 0, -2560, 1440), LEFT, False, "zero width"),
        (R(-2560, 0, 0, 8), LEFT, False, "too short to cover anything"),
        # Helpers the guard exists to skip.
        (R(0, 0, 1, 1), PRIMARY, False, "the 1x1 dwm thumbnail helper"),
        (R(0, 0, 0, 0), PRIMARY, False, "an invisible 0x0 IME entry"),
        (R(-40000, -40000, -39980, -39980), PRIMARY, False,
         "the off-screen Narrator helper"),
    ]
    for rect, virtual, want, why in cases:
        got = display.window_can_cover(rect, virtual)
        if got != want:
            failures.append(f"{why}: window_can_cover -> {got}, want {want}")

    # The heart of #89: the OLD rule must fail the reported case, or this test
    # would not be covering the regression it was written for.
    def old_rule(rect, screen_w, screen_h, min_px=16):
        return ((rect.right - rect.left) >= min_px
                and (rect.bottom - rect.top) >= min_px
                and rect.right > 0 and rect.bottom > 0
                and rect.left < screen_w and rect.top < screen_h)

    left_window = R(-2560, 0, 0, 1440)
    if old_rule(left_window, 2560, 1440):
        failures.append("the old primary-screen rule accepted a left-monitor "
                        "window - this test no longer reproduces #89, so it "
                        "would pass with the bug present")

    # --- the live path queries the virtual desktop ------------------------
    # A static check on the method would be brittle; ask the function the code
    # calls and confirm it answers the virtual bounds, not the primary ones.
    vs = display.Display._virtual_screen()
    if len(vs) != 4:
        failures.append(f"_virtual_screen must answer (x, y, w, h), got {vs}")
    else:
        vx, vy, vw, vh = vs
        prim_w, prim_h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        # Either there is really one screen (then they agree), or the virtual
        # bounds cover at least the primary one.
        if vw < prim_w or vh < prim_h:
            failures.append(f"the virtual desktop {vs} is smaller than the "
                            f"primary screen {prim_w}x{prim_h}")
    src = (BASE / "app" / "display.py").read_text(encoding="utf-8", errors="replace")
    if "SM_CXSCREEN" in src:
        failures.append("display.py still mentions SM_CXSCREEN - the guard is "
                        "back on the primary screen")
    for code in ("76", "77", "78", "79"):
        if f"GetSystemMetrics({code})" not in src:
            failures.append(f"the virtual-desktop metric {code} is not read")

    print("=" * 60)
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: a window on any monitor counts, helpers are still skipped, and "
          "the guard reads the virtual desktop")
    return 0


if __name__ == "__main__":
    sys.exit(main())
