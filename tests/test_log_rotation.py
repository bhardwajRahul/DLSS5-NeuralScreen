"""NeuralScreen.log is moved aside at startup once it has grown too large.

It was appended to by every run and never trimmed: one user's log reached
7.8 MB in two and a half days, most of it the NR and FG heartbeats. A start
now moves an oversized log to NeuralScreen.log.1 (replacing the previous one)
and begins a new file. A second copy of the program reaches the rotation too,
while the first copy holds the log open - Windows refuses the rename then,
and the log must simply be kept, not lost or half-moved.

Everything runs on files in a temporary folder.

Run:  runtime\\python.exe tests\\test_log_rotation.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import startup  # noqa: E402


def main() -> int:
    failures = []
    limit = 1000
    with tempfile.TemporaryDirectory(prefix="ns-log-") as temporary:
        log = Path(temporary) / "NeuralScreen.log"
        old = Path(temporary) / "NeuralScreen.log.1"

        log.write_text("x" * (limit - 1), encoding="utf-8")
        if startup._rotate_log(log, limit) or not log.exists():
            failures.append("a log under the limit was moved")

        log.write_text("new " * limit, encoding="utf-8")
        old.write_text("the previous old one", encoding="utf-8")
        if not startup._rotate_log(log, limit):
            failures.append("a log over the limit was not moved")
        elif log.exists() or not old.read_text(encoding="utf-8").startswith("new "):
            failures.append("the oversized log did not become NeuralScreen.log.1")

        log.write_text("held " * limit, encoding="utf-8")
        with open(log, "a", encoding="utf-8"):     # the first copy's handle
            moved = startup._rotate_log(log, limit)
        if not log.exists():
            failures.append("a log another copy holds open was lost")
        elif moved:
            print("    (this filesystem allowed the rename of an open file)")

        if startup._rotate_log(Path(temporary) / "missing.log", limit):
            failures.append("a missing log was reported as rotated")

    if startup.LOG_ROTATE_BYTES < 1024 * 1024:
        failures.append("the rotation limit is below 1 MB - a support bundle "
                        "would lose the session it was made for")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: an oversized log is moved to NeuralScreen.log.1 at startup, "
          "and a log another copy holds open is kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
