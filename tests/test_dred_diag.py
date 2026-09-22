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


def main() -> int:
    failures = []
    # The worker is a separate process; the launcher starts it via the VBS.
    # For the test we run main.py directly with NS_PHASE=1 so the [host]
    # lines reach the log.
    LOG.write_text("", encoding="utf-8")
    env = dict(os.environ, NS_PHASE="1")
    with tempfile.TemporaryDirectory(prefix="ns-dred-") as temporary:
        config = Path(temporary) / "config.json"
        config.write_text(json.dumps(autocheck.shipped_config(), indent=2) + "\n",
                          encoding="utf-8")
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
                # Read only what the process appended. A persistent capture
                # failure used to make the application log/recreate in a tight
                # loop; loading the complete growing log every 500 ms amplified
                # that failure and could itself raise MemoryError.
                with LOG.open("r", encoding="utf-8", errors="replace") as stream:
                    stream.seek(offset)
                    chunk = stream.read()
                    offset = stream.tell()
                text = tail + chunk
                tail = text[-4096:]
                if dred_line is None:
                    m = re.search(
                        r"\[host\] DRED (breadcrumbs enabled|settings unavailable)",
                        text)
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

    if dred_line is None:
        failures.append("no DRED line in the log at all")
    else:
        print(f"DRED line: {dred_line}")
    if nr_line is None:
        failures.append("NR did not come up after the DRED init")
    else:
        print("NR came up after the DRED init")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: DRED diagnostics logged, NR runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
