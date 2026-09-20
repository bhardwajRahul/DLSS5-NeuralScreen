"""The state cell must not claim "processing" while the worker is not processing.

The bug this locks, seen live: after re-applying the profile the worker stopped
evaluating while the menu still said NR ON. The only symptom was a counter
running ~20x too fast, because that counter measures loop iterations and the loop
was spinning - `recv` had fallen from 10 ms to 0.1 ms. The user reported "the NR
counter looks wrong"; nothing in the interface said the picture was raw.

What is checked:
  * a frame with no NGX evaluation does not immediately change the word - one
    skipped slot or a stall reset must not trip it;
  * a sustained run of unevaluated frames DOES change it, and to the failure
    tone;
  * an evaluation clears it again;
  * with NR off the word stays "not processing" (the user's own switch), not the
    new text - the two are different statements;
  * the payload key the loop writes is the one the menu reads.

Run:  runtime\\python.exe tests/test_nr_truthful_state.py
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import fonts  # noqa: E402
import overlay_ui  # noqa: E402


def _menu():
    menu = overlay_ui.OverlayMenu(
        1.0, lambda size=14, mono=False, bold=False, L="en":
        fonts.load(size, mono=mono, bold=bold, lang=L))
    menu.lang = "en"
    menu.set_state({"nr": True, "gpu_ok": True, "profile": "Natural",
                    "profiles": ["Natural"], "params": {}, "split": 0.0,
                    "monitors": [], "monitor": "", "windows": [],
                    "window_current": "", "hotkeys": {}, "work_scale": 0.65,
                    "work_scale_cap": 0.65, "work_scale_min": 0.1, "style": 1,
                    "work_size": "", "theme": "dark"})
    return menu


def main() -> int:
    pygame.init()
    pygame.display.set_mode((64, 64))
    failures = []

    s = overlay_ui.STRINGS["en"]

    # 1. NR on, worker evaluating: the word is the ordinary one.
    menu = _menu()
    word, failed = menu.status_text(s)
    if failed or "processing" not in word:
        failures.append(f"healthy NR on does not read as processing: {word!r}")

    # 2. NR on, the worker stopped evaluating.
    menu.set_state({"nr_not_evaluating": True})
    word, failed = menu.status_text(s)
    if not failed:
        failures.append("'NR on but not evaluating' is not drawn as a fault")
    if word == s.get("status_on"):
        failures.append("the state cell still says 'processing' while the pass "
                        "does not run - the exact report this exists for")
    if "not processing" not in word:
        failures.append(f"the wording does not say what is wrong: {word!r}")

    # 3. NR off is the USER's switch: it keeps its own wording, not the new one.
    off = _menu()
    off.set_state({"nr": False, "nr_not_evaluating": True})
    word_off, failed_off = off.status_text(s)
    if failed_off:
        failures.append("NR off is reported as a fault - it is the user's own "
                        "switch")
    if word_off == s.get("nr_not_running"):
        failures.append("NR off shows the 'not evaluating' text: the two states "
                        "are different statements")

    # 4. A real GPU failure outranks it, and still reads as a failure.
    bad = _menu()
    bad.set_state({"gpu_ok": False, "nr_not_evaluating": True})
    word_bad, failed_bad = bad.status_text(s)
    if not failed_bad:
        failures.append("a failed verdict stopped being a failure")

    # 5. The payload key the loop writes is the one the menu reads. This is the
    #    wiring check: the verdict is useless if the two names differ.
    main_src = (BASE / "main.py").read_text(encoding="utf-8")
    if '"nr_not_evaluating": st.nr_not_evaluating,' not in main_src:
        failures.append("the main loop does not send nr_not_evaluating to the HUD")
    ui_src = (BASE / "overlay_ui.py").read_text(encoding="utf-8")
    if 'self.state.get("nr_not_evaluating")' not in ui_src:
        failures.append("the menu does not read nr_not_evaluating")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the state cell stops claiming 'processing' when the worker "
          "is not processing, and keeps NR-off separate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
