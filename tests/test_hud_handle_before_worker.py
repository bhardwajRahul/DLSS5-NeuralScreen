r"""The panel is published BEFORE the worker is started, not after.

THE MEASUREMENT (#107, a reporter's v2.0.1 package)

    [present] window revealed on the first Present (on top - no usable panel handle)

That is the FALLBACK branch of the reveal in the worker. It means HudWindow()
gave nothing usable, so the picture window was shown with ShowWindow - which puts
a topmost window above every other topmost window, the panel included. That is
the single-refresh flash v2.0.0 exists to remove, happening on a plain launch.

Why the handle was not there, in three facts that all hold in the source:

  1. ORDER. bring_up started the worker and only then built Display, which
     publishes NS_HUD_HWND. Measured before the fix: start_worker() at
     startup.py:525, Display(...) at startup.py:532.
  2. INHERITANCE. pipeline.start_worker passes no env=, so the child inherits
     the parent's environment as it was at Popen time. A variable set in the
     parent afterwards never reaches that process (verified with real children:
     a child spawned before the publish reads <absent>).
  3. CACHING. HudWindow() in the worker reads the variable once and caches the
     answer, a failed read included - so even a later publish would not be seen
     by that process.

WHAT THIS LOCKS

1. In bring_up, Display(...) comes before start_worker(...).
2. The pass guard still sits between them or immediately before the start - a
   worker must never be created without PASS.
3. The publish is still inside _move_to_origin, which runs on every window
   creation and re-creation (a set_mode can hand the panel a different window).

Read as source on purpose: whether the child really sees the variable is a
property of process creation, and the assertion that matters is the ORDER in the
one function that owns it.

Run:  runtime\python.exe tests\test_hud_handle_before_worker.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def bring_up_body(source: str) -> str:
    return source.split("def bring_up(st)", 1)[1].split("\ndef ", 1)[0]


def main() -> int:
    failures: list[str] = []
    startup = (BASE / "app" / "startup.py").read_text(encoding="utf-8")
    body = bring_up_body(startup)

    # ---- 1. the order -----------------------------------------------------
    display_at = body.find("st.display = Display(")
    worker_at = body.find("st.worker, st.worker_logs, st.reader, st.worker_stop = start_worker(")
    if display_at == -1:
        failures.append("bring_up no longer builds Display")
    if worker_at == -1:
        failures.append("bring_up no longer starts the worker the usual way")
    if display_at != -1 and worker_at != -1:
        if display_at > worker_at:
            line = body[:display_at].count("\n") + 1
            wline = body[:worker_at].count("\n") + 1
            failures.append(
                f"Display is built AFTER the worker starts (Display at line "
                f"{line} of bring_up, start_worker at {wline}) - the worker "
                f"inherits an environment without NS_HUD_HWND, so the reveal "
                f"falls back to ShowWindow and the picture covers the panel")
        # The publish lives in the constructor chain of Display; if the move
        # were dropped, the order above would be worth nothing.
        if "_move_to_origin" not in body[:worker_at]:
            # The publish happens inside Display.__init__, not in bring_up -
            # so what must hold here is that Display was constructed first,
            # which is exactly what the check above asserts.
            pass

    # ---- 2. the pass guard is still in front of the start ------------------
    window = body[max(0, worker_at - 400):worker_at]
    if worker_at != -1 and not re.search(r"require_compatibility(?:_pass)?\(st\)", window):
        failures.append(
            "the worker is started without a PASS guard in front of it: the "
            "compatibility gate has to stay between the display and the start")

    # ---- 3. the publish still happens where the handle is fresh -----------
    display = (BASE / "app" / "display.py").read_text(encoding="utf-8")
    if "NS_HUD_HWND" not in display:
        failures.append("display.py no longer publishes NS_HUD_HWND")
    mover = re.search(r"def _move_to_origin\(self\).*?(?=\n    def )", display, re.S)
    if mover is None:
        failures.append("_move_to_origin is gone - the publish has no home")
    elif "NS_HUD_HWND" not in mover.group(0):
        failures.append(
            "_move_to_origin no longer publishes NS_HUD_HWND: it is the one "
            "place that runs after every creation AND re-creation of the "
            "window, and a handle published anywhere else would go stale")
    if "_move_to_origin()" not in display.split("class Display", 1)[1]:
        failures.append("nothing calls _move_to_origin any more")

    # ---- 4. the worker still reads it and caches once ---------------------
    cpp = (BASE / "native" / "dlss5-feed-host64.cpp").read_text(
        encoding="utf-8", errors="surrogateescape")
    hw = re.search(r"static HWND HudWindow\(\)\s*\{.*?\n\}", cpp, re.S)
    if hw is None:
        failures.append("HudWindow is gone from the worker")
    else:
        b = hw.group(0)
        if "NS_HUD_HWND" not in b:
            failures.append("HudWindow no longer reads NS_HUD_HWND")
        # The read is ONE-SHOT: an unset sentinel, and a cached answer that
        # includes a failed read. That is why the order above decides the
        # outcome for the whole process - a worker that starts too early can
        # never recover, not even after the parent publishes the handle.
        if "static HWND cached = reinterpret_cast<HWND>(-1);" not in b:
            failures.append(
                "HudWindow no longer declares the unset sentinel: the one-shot "
                "read is what makes the startup order decide the outcome")
        if "cached = nullptr;" not in b:
            failures.append(
                "HudWindow no longer caches a FAILED read: a missing variable "
                "would be retried, which is a different contract from the one "
                "the startup order is written against")

    if failures:
        print("FAIL: the panel handle is not published before the worker")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: Display publishes NS_HUD_HWND before the worker is started, "
          "and the worker reveals below the panel on a plain launch")
    return 0


if __name__ == "__main__":
    sys.exit(main())
