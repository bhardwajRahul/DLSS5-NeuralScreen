"""The worker logs its DRED/device-removed diagnostics at startup.

Issue #1 (Win10 TDR) died with only "code 6" in the log - no reason, no
breadcrumbs. The worker now enables DRED breadcrumbs when the OS supports
them and logs the outcome either way; on a removed device BeginCommands
logs GetDeviceRemovedReason plus breadcrumbs. This test checks the startup
half: the log must contain a DRED line (enabled or unavailable-with-hr),
and the worker must still run NR afterwards.

Run:  runtime\\python.exe test_dred_diag.py
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil

BASE = Path(__file__).resolve().parent.parent  # the project root
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))

LOG = BASE / "NeuralScreen.log"
PY = BASE / "runtime" / "python.exe"

import autocheck  # noqa: E402


def run_worker(config: Path, env_extra: dict) -> tuple[str, str | None, str | None]:
    """Start main.py, watch the log, return (log text, DRED line, NR line).

    The worker is a separate process; the launcher starts it via the VBS.
    For the test we run main.py directly with NS_PHASE=1 so the [host]
    lines reach the log. The log is read incrementally: a persistent
    capture failure used to make the application log/recreate in a tight
    loop, and loading the complete growing log every 500 ms amplified that
    failure and could itself raise MemoryError.
    """
    LOG.write_text("", encoding="utf-8")
    env = dict(os.environ, **env_extra)
    proc = subprocess.Popen(
        [str(PY), "-u", "main.py", "--config", str(config)],
        cwd=str(BASE), env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 30
        dred_line = None
        nr_line = None
        offset = 0
        tail = ""
        while time.time() < deadline:
            with LOG.open("r", encoding="utf-8", errors="replace") as stream:
                stream.seek(offset)
                chunk = stream.read()
                offset = stream.tell()
            text = tail + chunk
            tail = text[-4096:]
            if dred_line is None:
                m = re.search(
                    r"\[host\] DRED (breadcrumbs enabled|settings unavailable"
                    r"|breadcrumbs off)", text)
                if m:
                    dred_line = m.group(0)
            if nr_line is None and "NR ON" in text:
                nr_line = "NR ON"
            if dred_line and nr_line:
                break
            time.sleep(0.5)
    finally:
        # Capture the descendants while the parent still exists. Terminating
        # python.exe first orphaned nvngx.dll, and killing by image name could
        # also stop a worker that did not belong to this test.
        try:
            parent = psutil.Process(proc.pid)
            owned = parent.children(recursive=True) + [parent]
        except (psutil.Error, ProcessLookupError):
            owned = []
        for child in reversed(owned):
            try:
                child.terminate()
            except psutil.Error:
                pass
        _gone, alive = psutil.wait_procs(owned, timeout=5)
        for child in alive:
            try:
                child.kill()
            except psutil.Error:
                pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return LOG.read_text(encoding="utf-8", errors="replace"), dred_line, nr_line


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory(prefix="ns-dred-") as temporary:
        config = Path(temporary) / "config.json"
        config.write_text(json.dumps(autocheck.shipped_config(), indent=2) + "\n",
                          encoding="utf-8")

        # 1. Default: the breadcrumbs are on, and the worker runs.
        _text, dred_line, nr_line = run_worker(config, {"NS_PHASE": "1"})
        if dred_line is None:
            failures.append("no DRED line in the log at all")
        elif "enabled" not in dred_line and "unavailable" not in dred_line:
            failures.append(f"the default run did not report DRED: {dred_line!r}")
        else:
            print(f"DRED line: {dred_line}")
        if nr_line is None:
            failures.append("NR did not come up after the DRED init")
        else:
            print("NR came up after the DRED init")

        # 2. NS_DRED=0: off, and SAID to be off.
        #
        # The switch exists because the breadcrumbs are not free (the runtime
        # inserts one after every render op; Microsoft measures 2-5% on a
        # typical AAA engine, and #129 is a report of exactly that kind of
        # load). A run with the switch set must NOT claim the breadcrumbs are
        # on, and must not be mistaken for the "unavailable" failure path -
        # the two are different facts about the machine and the log has to
        # tell them apart.
        _text, off_line, off_nr = run_worker(config, {"NS_PHASE": "1", "NS_DRED": "0"})
        if off_line is None:
            failures.append("NS_DRED=0 produced no DRED line at all")
        elif "off" not in off_line:
            failures.append(f"NS_DRED=0 still reports DRED as active: {off_line!r}")
        else:
            print(f"NS_DRED=0 line: {off_line}")
        if off_nr is None:
            failures.append("NR did not come up with NS_DRED=0")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: DRED diagnostics logged, the switch is honest, NR runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
