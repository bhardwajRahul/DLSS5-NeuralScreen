"""#96: the pointer stays put, and a monitor change clears the window target.

Two complaints from the same ticket, both about a state the user did not ask
for.

**The pointer.** Opening the panel from the tray or the taskbar used to move
the cursor to the middle of the title bar (Seedmanc: "hijacks the mouse
cursor and puts it at the top of the screen, ruining muscle memory and
expectations from UI, this is poor UX"). It is deliberate code, not a side
effect, and it is gone: the reveal is what makes the opening visible.

**The window target.** In window mode, changing the capture monitor made the
program capture the window AND the whole desktop of that monitor (Codemned).
A monitor switch tears the pipeline down and rebuilds it for the new size,
but only leaving window mode cleared `window_hwnd` - so after the switch the
program still held the old handle, the next negotiation found it alive, and
the worker was told to capture a window on top of a pipeline built for the
whole monitor.

Checked without a GPU, by reading the source of the two functions that own
the decisions - no unit test can watch a running worker rebuild its capture
source, and the whole bug is which function clears which field.
"""

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
COMMANDS = BASE / "commands.py"
PIPELINE = BASE / "pipeline.py"


def _function_body(path: Path, name: str) -> str:
    """The source of one top-level function, by name."""
    src = path.read_text(encoding="utf-8")
    match = re.search(rf"^def {re.escape(name)}\(", src, re.M)
    if not match:
        raise AssertionError(f"{path.name}: no top-level def {name}")
    start = match.start()
    nxt = re.search(r"^def ", src[start + 1:], re.M)
    end = start + 1 + nxt.start() if nxt else len(src)
    return src[start:end]


def main() -> int:
    failures = []
    commands = COMMANDS.read_text(encoding="utf-8")
    pipeline = PIPELINE.read_text(encoding="utf-8")

    # 1. The pointer jump is gone: no SetCursorPos anywhere in the module
    #    that handles the menu opening.
    if "SetCursorPos" in commands:
        failures.append("commands.py still calls SetCursorPos - opening the "
                        "panel moves the user's pointer")
    if "title_center" in commands:
        failures.append("commands.py still asks for the title centre - the "
                        "cursor hand-over was not fully removed")
    print(f"    SetCursorPos in commands.py: {'yes' if 'SetCursorPos' in commands else 'no'}")
    print(f"    title_center in commands.py: {'yes' if 'title_center' in commands else 'no'}")

    # 2. The reveal that makes the opening visible must have survived the
    #    removal - otherwise opening from the tray shows nothing.
    drain = _function_body(COMMANDS, "drain_commands")
    for token in ("set_visible(True)", "reveal()"):
        if token not in drain:
            failures.append(f"the menu-open path lost {token}: the panel may "
                            f"open invisibly now that the pointer does not "
                            f"move to it")
    print(f"    reveal kept in the open path: "
          f"{'yes' if 'reveal()' in drain else 'no'}")

    # 3. A monitor change clears the window target.
    switch = _function_body(PIPELINE, "switch_monitor")
    if "window_hwnd = None" not in switch:
        failures.append("switch_monitor does not clear window_hwnd: after a "
                        "monitor change in window mode the old window is "
                        "still captured on top of the new desktop pipeline "
                        "(#96)")
    if "window_hwnd" in switch and "= None" not in switch.split("window_hwnd", 1)[1][:60]:
        pass  # the check above is the real one
    # The follow state describes that window and has to go with it.
    for field in ("follow_size", "follow_pos"):
        if f"{field} = None" not in switch:
            failures.append(f"switch_monitor leaves {field} stale - it "
                            f"describes a window that is no longer captured")
    print(f"    switch_monitor clears the target: "
          f"{'yes' if 'window_hwnd = None' in switch else 'no'}")

    # 4. And it clears it only when there IS one: a plain desktop-to-desktop
    #    switch must not log a bogus message or touch the follow state.
    if "if st.window_hwnd is not None:" not in switch:
        failures.append("switch_monitor clears the target unconditionally - "
                        "the log line would fire on every desktop-to-desktop "
                        "switch")
    print(f"    guarded by a real target: "
          f"{'yes' if 'if st.window_hwnd is not None:' in switch else 'no'}")

    # 5. The window-switch path still works as before - the fix must not have
    #    been made by breaking the other owner of that field.
    switch_window = _function_body(PIPELINE, "switch_window")
    if "st.window_hwnd = int(hwnd)" not in switch_window:
        failures.append("switch_window no longer SETS the window target: "
                        "picking a window stopped working")
    if "st.window_hwnd = None" not in switch_window:
        failures.append("switch_window no longer clears the target when "
                        "leaving window mode")
    print(f"    switch_window still owns the field both ways: yes")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK: the cursor is not moved, and a monitor change drops the "
          "window target it can no longer honour")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
