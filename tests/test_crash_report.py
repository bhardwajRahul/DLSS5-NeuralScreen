"""A crash in the main loop must land in the log with its traceback.

A user on an RTX 3060 sent a log whose whole account of the failure was one
line:

    [main] ERROR: <built-in function get> returned a result with an exception set

That is a SystemError - some C call had already left an error indicator set
and the next builtin tripped over it - so the line names the messenger and
not the cause. With no traceback there was nothing to work from, and the
reporter ended up guessing at a fix and sending it as a patch (issue #41,
PR #42). A whole round trip lost to a missing traceback.

So: the loop's handler prints the traceback, and this pins it.

The handler is located by its OWN anchor - the `[main] ERROR` line it is
required to print - not by "the next two-space-indented block after any
`except Exception`". The earlier regex ran from main.py:429 to main.py:1189,
a 43,670-character span containing every other handler in main(), so a
traceback printed anywhere in it satisfied the check (audit: WEAK). Anchor
on the marker, then read outward from it.

Run:  runtime\\python.exe tests\\test_crash_report.py
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/


def _handler_block(src: str) -> str:
    """The handler around the `[main] ERROR` line, and nothing else.

    Anchored on the marker the handler must print, then widened to the
    enclosing statement: back to the `except` that opens it, forward to the
    `return` that closes it. If either boundary is missing the block is
    reported as unreadable rather than silently widened to the whole file.
    """
    marker = '[main] ERROR: {exc}'
    at = src.find(marker)
    if at < 0:
        return ""
    start = src.rfind("except Exception as exc:", 0, at)
    if start < 0:
        return ""
    end = src.find("\n        return ", at)
    if end < 0:
        return ""
    return src[start:end]


def main() -> int:
    failures = []
    src = (BASE / "main.py").read_text(encoding="utf-8")

    body = _handler_block(src)
    if not body:
        failures.append("the main loop's exception handler is gone - "
                        "re-check where a crash is reported now")
    else:
        # The span has to stay small: if the anchor ever moves near the top
        # of main() the check would swallow unrelated handlers again, which
        # is exactly what the earlier version did.
        if len(body) > 3000:
            failures.append(
                f"the located handler spans {len(body)} characters - the "
                f"anchor no longer encloses one handler, so a traceback "
                f"printed anywhere in it would satisfy this check")
        if "format_exc" not in body and "print_exc" not in body:
            failures.append("the main loop reports a crash without its "
                            "traceback - a user's log then names the "
                            "messenger and not the cause (issue #41)")
        if "[main] ERROR" not in body:
            failures.append("the crash line lost its [main] ERROR marker - "
                            "that is what people grep for in the log")
    print(f"    located handler: {len(body)} characters")

    # And the top-level one, for a failure before the loop even starts.
    tail = src[src.rfind('if __name__ == "__main__":'):]
    if "print_exc" not in tail and "format_exc" not in tail:
        failures.append("a failure before the loop starts is reported "
                        "without a traceback")

    # SystemError is the class that actually arrived. It is an Exception, so
    # `except Exception` catches it - if that ever stops being true the
    # handler would miss exactly the case it was written for.
    if not issubclass(SystemError, Exception):
        failures.append("SystemError is no longer an Exception - the handler "
                        "would not catch the case from issue #41")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: a crash reaches the log with its traceback, from one handler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
