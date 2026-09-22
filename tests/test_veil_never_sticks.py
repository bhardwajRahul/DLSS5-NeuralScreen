"""The mode-switch veil must never outlive its transition (issues #89/#96).

Reported twice: "I launched 1.13.1, but the problem persists - the window
becomes invisible, then it may reappear to disappear". The veil is the frozen
picture with the assembling mark that covers a pipeline rebuild. It is taken
down by exit_switch_mode() followed by a drawn frame - and every one of those
call sites sits in the frame-receiving path.

With NR OFF the loop never reaches that path: it takes the low-cost branch and
calls only _service_idle_overlay(). A rebuild started from the menu in that
state (Spout2 / HDR / motion backend / monitor / GPU) raises the veil, no frame
ever arrives, and while the veil is up draw_overlay returns BEFORE painting the
menu - so the screen stays frozen under the mark and the interface is gone.

Checked here, with the real Display and no window:

* reproducing the idle sequence with no exit call leaves the veil up (the bug);
* the idle branch's own two calls take it down - the fade route with the menu
  visible, the immediate drop with it closed;
* the hold cap brings it down even when nobody draws and nobody exits: the
  backstop for a veil whose frame never came;
* the idle service really does make those calls - read out of main.py, because
  it is a closure inside main() and cannot be imported.

Run:  runtime\\python.exe tests\\test_veil_never_sticks.py
"""
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np  # noqa: E402

import display as D  # noqa: E402

FRAME = np.zeros((360, 640, 4), dtype=np.uint8)


def _display() -> "D.Display":
    return D.Display(640, 360, fullscreen=True, click_through=True)


def main() -> int:
    failures = []

    # 1. The bug itself, pinned so the fix cannot silently regress: with no
    #    exit call the veil survives any number of idle redraws.
    disp = _display()
    disp.enter_switch_mode(FRAME, 640, 360)
    if not disp.is_switch_active():
        failures.append("the veil did not come up at all")
    deadline = time.monotonic() + (D.SWITCH_FADE_OUT + 0.2)
    while time.monotonic() < deadline:
        disp._finish_switch_if_due(time.monotonic())
        time.sleep(0.02)
    if not disp.is_switch_active():
        failures.append("the veil fell on its own with the menu up - the idle "
                        "branch is what takes it down, not time")

    # 2. The fade route (menu visible): exit + the draw that follows it in
    #    _service_idle_overlay, repeated at the idle loop's own 20 Hz. The
    #    fade is SWITCH_FADE_OUT long, so it takes a few passes - but it must
    #    finish on its own, with no frame and no frame path.
    disp.exit_switch_mode()
    passes = 0
    deadline = time.monotonic() + D.SWITCH_FADE_OUT + 1.0
    while disp.is_switch_active() and time.monotonic() < deadline:
        disp.exit_switch_mode()
        disp.draw_overlay(0.0)
        passes += 1
        time.sleep(0.05)  # the idle loop's own pace
    if disp.is_switch_active():
        failures.append("the idle fade route never finished: the veil stayed "
                        "up with no frame coming to end it")
    elif disp._switch_phase != "off":
        failures.append(f"the veil phase is {disp._switch_phase!r} after the "
                        f"fade finished, expected 'off'")

    # 3. The immediate route (menu closed): nothing draws on that path, so a
    #    fade could never advance - drop_switch_mode is the only honest exit.
    disp2 = _display()
    disp2.enter_switch_mode(FRAME, 640, 360)
    disp2.drop_switch_mode()
    if disp2.is_switch_active():
        failures.append("drop_switch_mode() left the veil up")
    # and it is a no-op when no veil is up
    disp2.drop_switch_mode()

    # 4. The backstop: a veil nobody exits and nobody draws past the hold cap.
    #    The cap has to sit behind the worker watchdog (5 s silent recv + a
    #    restart), or a slow-but-alive worker would lose its veil.
    if D.SWITCH_HOLD_MAX <= 5.0 + 0.5:
        failures.append(f"SWITCH_HOLD_MAX is {D.SWITCH_HOLD_MAX}s - not "
                        f"behind the worker's 5s silent-recv watchdog")
    disp3 = _display()
    disp3.enter_switch_mode(FRAME, 640, 360)
    disp3._finish_switch_if_due(time.monotonic() + D.SWITCH_HOLD_MAX + 1.0)
    if disp3.is_switch_active():
        failures.append("the hold cap did not take the veil down - a veil "
                        "whose frame never comes outlives the session")

    # 4b. The exit path alone (audit M6): exit_switch_mode starts the fade, and
    #     the fade is advanced only by a draw. If the worker dies between the
    #     exit and the first draw, nothing draws - and the veil used to sit at
    #     full strength until something else happened to draw, which may be
    #     never. The hold cap is what ends it.
    disp4 = _display()
    disp4.enter_switch_mode(FRAME, 640, 360)
    disp4.exit_switch_mode()
    if disp4._switch_phase != "out":
        failures.append(f"exit_switch_mode left the phase at "
                        f"{disp4._switch_phase!r}, expected 'out'")
    # No draws at all, only time passing.
    disp4._finish_switch_if_due(time.monotonic() + D.SWITCH_HOLD_MAX + 1.0)
    if disp4.is_switch_active():
        failures.append("the veil stayed up after exit_switch_mode with no "
                        "draw ever coming - a worker that dies between the "
                        "exit and the first frame leaves it at full strength "
                        "for the rest of the session")
    disp4.drop_switch_mode()  # no-op, must not raise

    # 5. The idle branch must actually call them. It is a closure inside
    #    main(), so read the source and check the calls are in the function.
    src = (BASE / "main.py").read_text(encoding="utf-8")
    start = src.find("def _service_idle_overlay(")
    if start < 0:
        failures.append("_service_idle_overlay is gone from main.py - this "
                        "test no longer covers the idle path")
    else:
        end = src.find("\n        while st.running:", start)
        body = src[start:end if end > 0 else len(src)]
        for needle, why in (
            ("exit_switch_mode()", "the fade route (menu visible)"),
            ("drop_switch_mode()", "the immediate route (menu closed)"),
        ):
            if needle not in body:
                failures.append(f"the idle branch never calls {needle} - "
                                f"{why} is missing, and a rebuild with NR off "
                                f"leaves the veil over the interface")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the veil always comes down - fade, drop, and a hold cap behind "
          "the worker watchdog")
    return 0


if __name__ == "__main__":
    sys.exit(main())
