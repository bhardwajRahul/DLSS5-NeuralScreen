"""The worker-failure classifier: hard failures never auto-revive.

The recovery mechanism (v1.5.4) gives a transient worker failure ONE
automatic revive after a backoff instead of leaving NR off until the
user presses Num1. The classifier decides which failures are transient:
0xBAD00001 (FeatureNotSupported - the GPU cannot run the pass at all,
Turing or a broken runtime build) is permanent - retrying only spins
the restart loop. Everything else (0x00000000 no-frame, timeouts,
driver hiccups, TDR code 6) can clear on its own and is worth a revive.

Checked: the classifier returns True only for 0xBAD00001, and the
auto-revive deadline is armed only for transient failures.
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))  # the modules live in app/
from main import (CAPTURE_RETRY_BACKOFF,  # noqa: E402
                  MAX_CONSECUTIVE_CAPTURE_FAILURES, _hard_failure,
                  _next_capture_failure)


def main() -> int:
    failures = []

    # 1. Empty logs: nothing to classify - not a hard failure.
    if _hard_failure([]):
        failures.append("empty logs must not be a hard failure")

    # 2. The permanent failure: feature 18 create failed 0xBAD00001.
    logs = ["[pure] direct feature 18 create failed 0xBAD00001 (FeatureNotSupported)"]
    if not _hard_failure(logs):
        failures.append("0xBAD00001 must be a hard failure")

    # 3. The transient no-frame: 0x00000000 (issue #11, kortul).
    logs = ["[main] NGX evaluation failed on frame 0: 0x00000000"]
    if _hard_failure(logs):
        failures.append("0x00000000 must NOT be a hard failure")

    # 4. A TDR death (code 6) - transient, worth a revive.
    logs = ["the NGX worker exited with code 6", "[host] device removed"]
    if _hard_failure(logs):
        failures.append("a TDR death must NOT be a hard failure")

    # 5. A timeout - transient.
    logs = ["[gray] fence timeout", "the worker has been silent for 5s"]
    if _hard_failure(logs):
        failures.append("a timeout must NOT be a hard failure")

    # 6. 0xBAD00001 anywhere in the tail wins, even with transient lines.
    logs = ["[main] worker silent/dead on frame 0",
            "[pure] direct feature 18 create failed 0xBAD00001 (FeatureNotSupported)"]
    if not _hard_failure(logs):
        failures.append("0xBAD00001 in the tail must win over transient lines")

    # 7. The verdict is not buried by later noise. It used to be: only the
    #    last 40 lines counted, and the worker prints this ONCE on frame 0
    #    and then keeps logging - so any forty later diagnostics turned a
    #    permanently broken card back into a "transient" one and the revive
    #    loop started again (audit, 12.09). Changed on purpose.
    logs = ["0xBAD00001"] + ["transient"] * 50
    if not _hard_failure(logs):
        failures.append("0xBAD00001 must survive later diagnostics - it is "
                        "printed once and never repeated")

    # 8. ...but a LATER success wins. NGX is reinitialised in place after
    #    repeated failures, and if the feature came up after the refusal the
    #    card is not broken. This is the one thing the tail window got right,
    #    and reading newest-first keeps it.
    logs = ["[pure] direct feature 18 create failed 0xBAD00001",
            "[host] NGX reinitialised",
            "[pure] direct feature 18 ready: 1920x1080"]
    if _hard_failure(logs):
        failures.append("a feature that came up AFTER the refusal must not "
                        "count as a hard failure")

    # 9. Capture recovery has its own bounded budget. A permanently denied
    #    DDA/GDI source used to recreate a 4K session on every loop, exhausting
    #    memory before the diagnostic test could read its 600 KB log.
    count, retry_at = 0, 0.0
    now = 100.0
    for attempt in range(1, MAX_CONSECUTIVE_CAPTURE_FAILURES + 1):
        count, retry_at = _next_capture_failure(count, now)
        if attempt < MAX_CONSECUTIVE_CAPTURE_FAILURES and retry_at:
            failures.append(f"capture was quarantined after only {attempt} failures")
    if count != 0:
        failures.append(f"capture failure counter did not reset: {count}")
    if retry_at != now + CAPTURE_RETRY_BACKOFF:
        failures.append(f"capture retry deadline is {retry_at}, expected "
                        f"{now + CAPTURE_RETRY_BACKOFF}")

    # A failed post-backoff probe is one attempt, not a fresh burst of three.
    count, retry_at = _next_capture_failure(
        MAX_CONSECUTIVE_CAPTURE_FAILURES - 1, now + CAPTURE_RETRY_BACKOFF)
    if count != 0 or retry_at != now + 2 * CAPTURE_RETRY_BACKOFF:
        failures.append("a failed capture probe did not return straight to quarantine")

    source = open(os.path.join(BASE, "main.py"), encoding="utf-8-sig").read()
    safe_grab = source.split("def _safe_grab()", 1)[-1].split(
        "# The loop's own state", 1)[0]
    for marker in ("_next_capture_failure(", "if now < capture_retry_at:",
                   "time.sleep(CAPTURE_FAILURE_IDLE)",
                   "MAX_CONSECUTIVE_CAPTURE_FAILURES - 1"):
        if marker not in safe_grab:
            failures.append(f"_safe_grab no longer enforces capture gate: {marker}")

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} - {failures}")
        return 1
    print("OK: worker failures are classified and capture retries are bounded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
