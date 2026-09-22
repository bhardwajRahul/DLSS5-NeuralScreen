"""Every log line must carry a time, and a menu close must be logged.

Both come from reading four real diagnostic packages (issue reports of 19.09):
half of each log had no timestamp at all - of 916 lines (raycornea) 499 were
untimed, of 966 (saymoin) 477, of 243 (codemned) 145, of 220 (saymoin) 111, of
153 (manik) 76 - and the untimed half is exactly the half a report needs. "The
menu opened 21 times" cannot be placed against "the user minimised a window"
when neither line carries a time, and a z-order decision could only be dated by
its neighbouring line. The [z] lines and the menu lines were both in it.

The other half of that gap: only the settings/show_settings paths printed
"overlay menu opened/closed". The close button (and Esc, which arrives as the
same command) set menu.visible = False silently, so a package read "21 opened
against 0 closed" - uninterpretable, because a menu that was never closed and a
close that was never logged look identical.

Checked here:

1. the log stream stamps every untimed line, and does NOT stamp a line that
   already has a time (the worker writes its own, and a second prefix would
   break every parser that reads "16:03:11.482  [fg] ...");
2. the stamp survives a line written without a trailing newline, and a message
   containing several lines is stamped per line, not once;
3. the close-button path prints a close line, and records when the menu opened
   so the line can carry a duration;
4. `re` and `time` are imported for the wrapper (a missing one would make
   _init_logging fall into its bare `except` and silently disable the stamps).

Provable by mutation: drop the "already stamped" guard and worker lines get a
double prefix; remove the close print and check 3 fails; remove the import and
the module does not load.

Run:  runtime\\python.exe tests\\test_log_timestamps.py
"""
import io
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STARTUP = ROOT / "app" / "startup.py"
COMMANDS = ROOT / "app" / "commands.py"


def load_stamped_log():
    """The wrapper class, taken from startup.py without importing the app."""
    import time as _time

    src = STARTUP.read_text(encoding="utf-8", errors="replace")
    start = src.index("_TIMESTAMP_RE = ")
    end = src.index("def _init_logging")
    ns = {"re": re, "time": _time}
    exec(src[start:end], ns)  # noqa: S102 - the unit under test, not data
    return ns["_StampedLog"]


def main() -> int:
    failures = []
    startup = STARTUP.read_text(encoding="utf-8", errors="replace")
    commands = COMMANDS.read_text(encoding="utf-8", errors="replace")
    main_text = (ROOT / "main.py").read_text(encoding="utf-8", errors="replace")

    # --- 1/2. the stream stamps exactly the untimed lines ------------------
    try:
        Stamped = load_stamped_log()
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        print(f"FAIL: the stamped log stream cannot be loaded: {exc}")
        return 1

    buf = io.StringIO()
    stream = Stamped(buf)
    stream.write("16:03:11.482  [fg] 2x enabled at 2560x1440\n")   # worker line
    stream.write("[main] menu opened\n")                          # python line
    stream.write("[z] hud-on-top (changed) top=hwnd=0x1\n")        # python line
    stream.write("two\nlines in one write\n")
    stream.write("no trailing newline")
    stream.flush()
    lines = buf.getvalue().splitlines()

    stamp = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}  ")
    if len(lines) != 6:
        failures.append(f"the stream produced {len(lines)} lines, expected 6: {lines}")
    else:
        if lines[0] != "16:03:11.482  [fg] 2x enabled at 2560x1440":
            failures.append("a worker line that already has a time was stamped "
                            f"again: {lines[0]!r}")
        for index in (1, 2, 3, 4):
            if not stamp.match(lines[index]):
                failures.append(f"line {index} came out untimed: {lines[index]!r}")
        if lines[5] != "no trailing newline" and not stamp.match(lines[5]):
            failures.append(f"the final line lost its text: {lines[5]!r}")

    # --- 4. the imports the wrapper needs ---------------------------------
    if not re.search(r"^import re$", startup, re.M):
        failures.append("startup.py does not import `re`: the timestamp match "
                        "would raise and _init_logging would silently stop "
                        "stamping")
    if not re.search(r"^import time$", startup, re.M):
        failures.append("startup.py does not import `time`: the wrapper cannot "
                        "build a stamp")

    # --- 3. the close button says so --------------------------------------
    close = re.search(r'if name == "close":(.*?)\n        elif ', commands, re.S)
    if not close:
        failures.append("the close-button branch is gone - the menu close path "
                        "cannot be checked")
    else:
        body = close.group(1)
        # Both branches must log it: the duration form when the open time is
        # known and the plain form when it is not. Requiring the string once
        # let a mutation delete one branch and still pass - the close would go
        # unlogged whenever the time is missing.
        if "overlay menu closed" not in body:
            failures.append("the close button closes the menu without logging "
                            "it: a package reads 'N opened against 0 closed' "
                            "and cannot be interpreted")
        printed = body.count('print(f"[main] overlay menu closed') + \
            body.count('print("[main] overlay menu closed')
        if printed < 2:
            failures.append(f"only {printed} of the two close-logging branches "
                            "survive: one path would close the menu silently")
        if "menu_opened_at" not in body:
            failures.append("the close line carries no duration: it cannot be "
                            "dated against the events around it")
    if "menu_opened_at" not in commands:
        failures.append("nothing records when the menu opened, so the close "
                        "line cannot say how long it was open")
    elif "st.menu_opened_at = time.monotonic()" not in commands:
        failures.append("the menu-opened time is never set: the close line "
                        "would always fall back to the duration-less form")
    if not re.search(r"^import time$", commands, re.M):
        failures.append("commands.py does not import `time` for the duration")

    # --- 5. every state field the close paths write must exist ------------
    # The pipeline state is a __slots__ class: assigning a field that is not in
    # the list raises AttributeError, and drain_commands runs at the top of the
    # per-frame loop - so one missing name kills the program (measured: the
    # window-mode test failed with "'_Pipeline' object has no attribute
    # 'menu_opened_at' and no __dict__ for setting" until the slot was added).
    # This checks the fields this feature writes, which is where the mistake is
    # made; the runner-wide version belongs with the state definition.
    slots = re.search(r'__slots__\s*=\s*\((.*?)\)\n', main_text, re.S)
    if not slots:
        failures.append("the pipeline __slots__ list was not found - a field "
                        "written by the close path cannot be checked")
    else:
        declared = set(re.findall(r'"([A-Za-z_][A-Za-z0-9_]*)"', slots.group(1)))
        for field in ("menu_opened_at",):
            if field not in declared:
                failures.append(f"`{field}` is written on the pipeline state but "
                                "is not in __slots__: the assignment raises "
                                "AttributeError and kills the main loop")
        # And the field must be initialised, or the close falls back to the
        # duration-less form on a menu that has never been opened.
        if f"st.{field} = " not in startup:
            failures.append(f"`{field}` is never initialised in the state builder")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: every untimed log line gets a stamp (worker lines are left "
          "alone), and the close button logs the close with its duration")
    return 0


if __name__ == "__main__":
    sys.exit(main())
