"""The status line must not call Frame Generation "not processing" (#107).

Reporter: on v1.16.0, with NR off and FG on, the line above the menu said
"Not processing" - and he read that as Frame Generation being dead. The
switch was on, the presenter was interpolating, and the status line said
nothing was happening.

The cause was the order of the checks: `status_text` returned the NR-off
string as soon as NR was off, without ever looking at Frame Generation. That
was true before v1.16.0, when NR off really meant the pipeline did nothing.
Since v1.16.0 the presenter runs on the bypass path too, so the same string
is now a lie.

Checked here:
  * NR on                      -> processing;
  * NR off, FG off             -> not processing (unchanged);
  * NR off, FG on with a rate  -> the Frame Generation string, not "off";
  * NR off, FG on, no rate yet -> the "starting" string, still not "off";
  * a failed neural pass with NR on still outranks everything;
  * every language carries both new keys (a missing one falls back to English,
    which a 12-language menu must not do silently).

Run:  runtime\\python.exe tests\\test_status_fg_off.py
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))  # the modules live in app/

import pygame                           # noqa: E402
pygame.init()

import overlay_ui                       # noqa: E402
from i18n import STRINGS                # noqa: E402


def font_loader(size):
    """A real font: the menu sizes itself through _u and this callable."""
    try:
        return pygame.font.SysFont("consolas", size)
    except Exception:
        return pygame.font.Font(None, size)


def status_of(menu, **state) -> str:
    """The status text for a given set of state values."""
    base = dict(menu.state)
    base.update(state)
    menu.state.clear()
    menu.state.update(base)
    text, _broken = menu.status_text(STRINGS["en"])
    return text


def main() -> int:
    failures: list[str] = []
    menu = overlay_ui.OverlayMenu(1.0, font_loader)
    en = STRINGS["en"]

    cases = [
        # (label, state overrides, expected key or literal)
        ("NR on", {"nr": True, "gpu_ok": True, "idle": False,
                   "frame_generation": False, "display_fps": None},
         en["status_on"]),
        ("NR off, FG off", {"nr": False, "gpu_ok": True, "idle": False,
                            "frame_generation": False, "display_fps": None},
         en["status_off"]),
        ("NR off, FG on and reporting", {"nr": False, "gpu_ok": True, "idle": False,
                                         "frame_generation": True,
                                         "display_fps": 118.7},
         en["status_fg_only"]),
        ("NR off, FG on, no rate yet", {"nr": False, "gpu_ok": True, "idle": False,
                                        "frame_generation": True,
                                        "display_fps": None},
         en["status_fg_waiting"]),
        ("NR on, FG on (NR still the main work)", {"nr": True, "gpu_ok": True,
                                                   "idle": False,
                                                   "frame_generation": True,
                                                   "display_fps": 118.7},
         en["status_on"]),
    ]
    for label, overrides, expected in cases:
        got = status_of(menu, **overrides)
        if got != expected:
            failures.append(f"{label}: said {got!r}, expected {expected!r}")
            continue
        # The reported bug in one assertion: whatever the frame rate, the line
        # must never claim nothing is happening while FG is on and working.
        if overrides.get("frame_generation") and overrides.get("display_fps") is not None:
            if got == en["status_off"]:
                failures.append(f"{label}: FG is running and the line still "
                                f"says {got!r} (the #107 report)")

    # A failed pass with NR on must still be reported as broken.
    text, broken = (lambda m: (m.status_text(STRINGS["en"])))(
        _with(menu, {"nr": True, "gpu_ok": False, "idle": False,
                     "frame_generation": False, "display_fps": None}))
    if not broken or text != en["gpu_no_nr"]:
        failures.append(f"a failed neural pass no longer outranks: {text!r} "
                        f"broken={broken}")

    # All twelve languages carry the keys: a silent English fallback would show
    # an English phrase inside a Russian menu.
    for lang, table in STRINGS.items():
        for key in ("status_fg_only", "status_fg_waiting"):
            if not str(table.get(key, "")).strip():
                failures.append(f"language {lang!r} has no {key}")

    print("=" * 60)
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: Frame Generation is named as the work it is, and 'not "
          "processing' is said only when nothing is happening")
    return 0


def _with(menu, state):
    base = dict(menu.state)
    base.update(state)
    menu.state.clear()
    menu.state.update(base)
    return menu


if __name__ == "__main__":
    sys.exit(main())
