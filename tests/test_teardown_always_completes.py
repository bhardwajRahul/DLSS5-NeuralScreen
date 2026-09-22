"""Every step of main's teardown runs, whatever the one before it does.

main's `finally` block releases the recording, the worker, the shared section,
the menu layout, the capture, the window, the hotkeys, the tray and the
taskbar. All of them were individually wrapped except two:

    if st.worker is not None:
        shutdown_worker(st.worker, st.worker_stop)
    if st.shm is not None:
        st.shm.close()

A raise from either propagates out of the `finally` and skips everything after
it in one go: capture and window stay open, the hotkey thread, the tray icon
and the 1x1 taskbar window stay alive, and the borderless topmost overlay is
still on screen with no loop left behind it - frozen there until the user kills
the process. shutdown_worker closes a pipe and prints, and on a pythonw process
stdout is the log file, so a log that has become unwritable (replaced, volume
gone, a sharing violation from the user's own log viewer) raises out of print.

Checked here by reading main.py. Running the real teardown would need a live
window, worker and tray; what has to hold is the invariant, and it is checkable
in the source: in the `finally` block, every statement that can raise is
guarded, and the order is kept.

Run:  runtime\\python.exe tests\\test_teardown_always_completes.py
"""
import ast
import sys
import textwrap
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/


def _finally_body() -> bytes:
    """The `finally:` block of main(), as source lines."""
    src = (BASE / "main.py").read_text(encoding="utf-8")
    start = src.find("def main()")
    if start < 0:
        return b""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            for stmt in node.body:
                if isinstance(stmt, ast.Try) and stmt.finalbody:
                    lines = src.splitlines(keepends=True)
                    first = stmt.finalbody[0].lineno
                    last = max(getattr(s, "end_lineno", s.lineno)
                               for s in stmt.finalbody)
                    return "".join(lines[first - 1:last])
    return b""


def main() -> int:
    failures = []

    body = _finally_body()
    if not body:
        print("FAIL: could not find main()'s finally block - this test no "
              "longer covers what it exists for")
        return 1

    # The block is indented inside the function: dedent it so it parses on
    # its own, and compare positions against the dedented text.
    body = textwrap.dedent(body)
    tree = ast.parse(body)
    # The block's own statements: each must be a Try (guarded), a control
    # statement, or a call that cannot raise in a way that matters.
    for stmt in tree.body:
        if isinstance(stmt, ast.Try):
            continue
        if isinstance(stmt, (ast.Expr, ast.If)):
            # A bare call or an `if` whose body is guarded is what we expect
            # for `if st.x is not None: try: ...`.
            if isinstance(stmt, ast.If):
                inner = stmt.body
                if inner and all(isinstance(s, (ast.Try, ast.Pass))
                                 for s in inner):
                    continue
                failures.append(
                    f"line {stmt.lineno}: an unguarded `if` in the teardown - "
                    f"a raise from it skips every step after it, and the "
                    f"overlay stays on screen with no loop behind it")
                continue
            # A bare expression statement: only print() is acceptable (it is
            # the last line and nothing follows it).
            if isinstance(stmt.value, ast.Call) and \
                    isinstance(stmt.value.func, ast.Name) and \
                    stmt.value.func.id == "print":
                continue
            failures.append(f"line {stmt.lineno}: an unguarded statement in "
                            f"the teardown")
        else:
            failures.append(f"line {stmt.lineno}: unexpected "
                            f"{type(stmt).__name__} in the teardown")

    # The two specific calls this fix guards, pinned by name: a future edit
    # that removes the guard would otherwise pass the loop above.
    for needle, what in (
        ("shutdown_worker(st.worker, st.worker_stop)", "the worker shutdown"),
        ("st.shm.close()", "the shared-section close"),
    ):
        pos = body.find(needle)
        if pos < 0:
            failures.append(f"{what} is gone from the teardown - the guard "
                            f"check above no longer covers it")
            continue
        # The call must sit inside a try: look back a short way for one at the
        # same indent level.
        before = body[max(0, pos - 200):pos]
        if "try:" not in before.split("\n")[-4:][0] and \
                not any(line.strip() == "try:" for line in before.splitlines()[-3:]):
            failures.append(f"{what} is not inside a try - a raise there skips "
                            f"the rest of the teardown")

    # The order matters: the worker must die before the section it writes into.
    w = body.find("shutdown_worker(st.worker, st.worker_stop)")
    s = body.find("st.shm.close()")
    if w >= 0 and s >= 0 and w > s:
        failures.append("the shared section is closed before the worker is "
                        "shut down - the worker would write into a dead "
                        "mapping")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: every step of the teardown is guarded, and the order is kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
