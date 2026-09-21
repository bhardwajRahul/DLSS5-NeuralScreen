r"""Shell chrome and our own windows never count as "something covered us".

THE MEASUREMENT (#107, a reporter's v2.0.1 log, 18 windows took the top)

    06:22:40  [z] foreign-above-hud  class='Shell_TrayWnd'                 pid=8020
    06:22:45  [z] foreign-above-hud  class='XamlExplorerHostIslandWindow'  pid=8020
    06:24:08  [z] foreign-above-hud  class='#32770' title='Save As'        pid=46044
    ... five times for the dialog, plus tooltips_class32 and SysDragImage

pid 8020 owns Shell_TrayWnd: that is the shell. pid 46044 is the client
itself - #32770 is its own modal Save As dialog, the window the user is looking
at while the screenshot is being written (GetSaveFileNameW in dialogs.py).

Neither is a stranger that took our place. Both were read as one, so the guard
raised the picture and then the HUD over a state that was already correct - one
DWM recompose with the picture over the panel in between, which is the flash
reported in #96 (taskbar) and #107 (dialog). The worker's own
ReassertPresentTopmost did the same thing every 300 frames.

WHAT THIS LOCKS

1. Both sides apply the rule: the client walk (display.py) and the worker's
   re-assert (native/dlss5-feed-host64.cpp).
2. The rule is by PROCESS, not by a class list: the shell's pid comes from
   GetShellWindow, because the flyouts above the taskbar are not Shell_TrayWnd
   and a list would have to name every one.
3. The window that this exists for - a real application over the picture - still
   raises it.

The classification is read as source; the two file paths cannot be exercised
without two processes and a live desktop.

Run:  runtime\python.exe tests\test_foreign_window_rule.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def strip_prose(text: str) -> str:
    """Drop comments and docstrings so a check reads code, not prose.

    Learned here: three different assertions were satisfied by the explanatory
    docstring alone (the function names GetShellWindow and Shell_TrayWnd while
    explaining what the rule is for), so a version that did none of it stayed
    green. A test that a comment can satisfy tests nothing.
    """
    without_docs = re.sub(r'""".*?"""', "", text, flags=re.S)
    without_docs = re.sub(r"'''.*?'''", "", without_docs, flags=re.S)
    return "\n".join(line for line in without_docs.splitlines()
                     if not line.lstrip().startswith("#"))


def main() -> int:
    failures: list[str] = []
    display = (BASE / "display.py").read_text(encoding="utf-8")
    cpp = (BASE / "native" / "dlss5-feed-host64.cpp").read_text(
        encoding="utf-8", errors="surrogateescape")

    # ---- 1. the client side -------------------------------------------------
    helper = re.search(r"def _own_or_shell_window\(self, hwnd\).*?(?=\n    @|\n    def )",
                       display, re.S)
    if helper is None:
        failures.append(
            "display.py has no _own_or_shell_window: the z-order walk reads "
            "the taskbar and our own dialog as strangers again (#96, #107)")
    else:
        body = helper.group(0)
        # Code only: the docstring names GetShellWindow and Shell_TrayWnd to
        # explain the rule, and a check that reads prose as code stays green on
        # a version that does neither (found by mutation twice).
        code = strip_prose(body)
        if "GetShellWindow" not in code:
            failures.append(
                "the shell's process is no longer asked for - the shell's pid "
                "comes from its desktop window, not from the taskbar's class")
        if "GetCurrentProcessId" not in code:
            failures.append(
                "_own_or_shell_window does not compare against our own pid - "
                "the program's own Save As dialog would count as a stranger")
        # The comparison itself: both pids have to be in it. Checking that the
        # attribute is merely mentioned passes on a version that reads it and
        # then ignores it (found by mutation: dropping the shell pid from the
        # tuple kept the test green).
        if not re.search(r"got\s+in\s*\(\s*self\._own_pid\s*,\s*self\._shell_pid\s*\)",
                         code):
            failures.append(
                "_own_or_shell_window does not compare against BOTH our own and "
                "the shell's pid - the taskbar would count as a stranger again "
                "(#96)")
        # The walk has to consult it, or the helper is dead code.
        walk = display.split("def _top_real_window", 1)[-1].split("\n    def ", 1)[0]
        if "_own_or_shell_window(" not in strip_prose(walk):
            failures.append(
                "_top_real_window never calls _own_or_shell_window: the rule "
                "exists but the guard does not apply it")
        if "Shell_TrayWnd" in code:
            failures.append(
                "_own_or_shell_window decides by class name: the shell's pid is "
                "the rule, because the flyouts above the taskbar are not "
                "Shell_TrayWnd")

    # ---- 2. the worker side -------------------------------------------------
    reassert = re.search(r"static void ReassertPresentTopmost\(\)\s*\{.*?\n\}",
                         cpp, re.S)
    if reassert is None:
        failures.append("ReassertPresentTopmost is gone from the worker")
    else:
        # Prose stripped: the block's comments name the shell, the dialog and
        # the log lines it reasons about, and a check that a comment can
        # satisfy is not a check.
        b = strip_prose(reassert.group(0))
        if "GetShellWindow" not in cpp.split("static DWORD ShellProcessId()", 1)[-1][:400]:
            failures.append(
                "the worker does not ask for the shell's desktop window: the "
                "shell pid must come from there, not from a class list")
        if "GetCurrentProcessId" not in b:
            failures.append(
                "the worker's re-assert does not compare against its own pid")
        if "GetWindowThreadProcessId" not in b:
            failures.append(
                "the worker's re-assert does not look up the top window's "
                "process - it raises the picture over anything again")
        # The comparison itself, as one expression. Checking that the helper
        # exists passes on a version that never consults it, and checking for
        # the substrings passes on one that only names them in the log line
        # (both found by mutation) - so the gate is matched as written.
        gate = re.sub(r"\s+", " ",
                      " ".join(re.findall(r"if \(pid != 0.*?\)\)", b, re.S)))
        for term, what in (("(shell != 0 && pid == shell)", "the shell's pid"),
                           ("(client != 0 && pid == client)", "the client's own pid"),
                           ("pid == self", "this process")):
            if term not in gate:
                failures.append(
                    f"the worker's re-assert gate does not compare against "
                    f"{what}: that window would raise the picture over the "
                    f"panel again")
        if "SetWindowPos" not in b:
            failures.append(
                "ReassertPresentTopmost no longer raises the picture at all: "
                "the borderless-game case (a real window over ours) is what "
                "this function exists for")
        if "IsWindowVisible" not in b:
            failures.append(
                "the worker's re-assert no longer skips hidden windows")

    # ---- 3. both sides agree on the same names ------------------------------
    if "ShellProcessId" not in cpp:
        failures.append(
            "the worker reads the shell pid inline instead of through one "
            "helper - the client and the worker are supposed to share the rule")

    if failures:
        print("FAIL: shell and own-program windows are treated as strangers")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: shell chrome and our own windows are skipped by both the client "
          "walk and the worker's re-assert, while a real window still raises")
    return 0


if __name__ == "__main__":
    sys.exit(main())
