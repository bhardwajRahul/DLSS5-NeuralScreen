"""The automatic worker revive must survive the NR-OFF transition.

A transient worker failure arms one automatic revive: worker_failed=True and
next_auto_revive=+30 s. The revive itself is read at the top of main's loop,
guarded by an earlier call in the same iteration:

    low_cost_off = pipeline.sync_low_cost_off(st)
    if st.worker_failed:
        if st.next_auto_revive and time.monotonic() >= st.next_auto_revive:

Entering the OFF branch with the worker already shut down, sync_low_cost_off
re-asserted worker_failed and ZEROED next_auto_revive. The deadline read one
line later was 0, the loop took the low-cost branch and continued for the rest
of the session: the revive never fired. In the default configuration (FG off,
no recording) that is every time - the path that exists precisely for "leave
the user with NR off until they press Num1" was the one path it never worked
on, while the log announced "auto-revive in 30s".

Checked here against the real pipeline.sync_low_cost_off:

* the arm survives the OFF transition in the default configuration, so
  main's revive still reads it;
* a worker that really did die is still marked failed - the OFF transition
  must not claim a healthy worker either;
* the arm is cleared where it is consumed, and nowhere else in the OFF path.

Run:  runtime\\python.exe tests\\test_revive_not_disarmed.py
"""
import sys
import types
from pathlib import Path
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import channels  # noqa: E402
import pipeline  # noqa: E402

BACKOFF = pipeline.AUTO_REVIVE_BACKOFF


class _Worker:
    """A worker process that is already gone (the failure handler shut it)."""

    def poll(self):
        return 1

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return 0


class _Display:
    def set_hud_only(self, value):
        pass


def _state(**kw):
    st = types.SimpleNamespace(
        paused=True,                     # NR OFF, the default after a failure
        cfg={"frame_generation": False},  # FG off: the default configuration
        recorder=None,
        pending_shot=None,
        worker=_Worker(),
        worker_stop=None,
        worker_failed=True,
        # The arm the failure handler set. Not time.monotonic() + BACKOFF,
        # because this test reads the field, not the clock.
        next_auto_revive=0.0,
        off_suspended=False,
        work_frame=None,
        output_rgba=None,
        guides=types.SimpleNamespace(previous_gray=None),
        display=_Display(),
    )
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def main() -> int:
    failures = []

    # 1. The bug. A transient failure armed the revive; the OFF transition
    #    runs before the loop reads the deadline. The arm must still be there.
    st = _state()
    armed_at = 1234.5
    st.next_auto_revive = armed_at
    with mock.patch.object(channels, "forget_present", lambda *a: None), \
            mock.patch.object(channels, "forget_dda", lambda *a: None), \
            mock.patch.object(channels, "forget_out", lambda *a: None):
        pipeline.sync_low_cost_off(st)
    if st.next_auto_revive != armed_at:
        failures.append(
            f"the OFF transition disarmed the revive: next_auto_revive "
            f"{armed_at} -> {st.next_auto_revive!r}. main reads it one line "
            f"later, so the automatic revive never fires in the default "
            f"configuration while the log promises one in {BACKOFF:.0f}s")

    # 2. A worker that really did die is still a dead worker. Dropping the arm
    #    must not be replaced by pretending the worker is alive.
    if not st.worker_failed:
        failures.append("the OFF transition cleared worker_failed for a worker "
                        "whose poll() says it exited")

    # 3. A live worker entering NR OFF is not a failure at all: nothing arms,
    #    nothing is marked failed, and the arm stays where it was.
    class _Alive:
        def poll(self):
            return None

    st2 = _state(worker=_Alive(), worker_failed=False, off_suspended=False)
    with mock.patch.object(channels, "suspend_for_off", lambda *a: None), \
            mock.patch.object(channels, "forget_present", lambda *a: None), \
            mock.patch.object(channels, "forget_dda", lambda *a: None):
        pipeline.sync_low_cost_off(st2)
    if st2.worker_failed:
        failures.append("a healthy worker entering NR OFF was marked failed")
    if st2.next_auto_revive:
        failures.append("a healthy worker entering NR OFF had a revive armed")

    # 4. The arm is cleared where it is consumed (main's revive) and the OFF
    #    path no longer touches it. Read the sources: this is the invariant the
    #    bug violated, and a future edit could reintroduce it silently.
    def _without_docstring(body: str) -> str:
        """The function's code, with its docstring dropped.

        The docstring here explains WHY the deadline is not touched, so
        looking for the name alone would flag the explanation itself.
        """
        head = body.find('"""')
        if head < 0:
            return body
        tail = body.find('"""', head + 3)
        return body[:head] + body[tail + 3:] if tail > 0 else body

    pipe_src = (BASE / "app" / "pipeline.py").read_text(encoding="utf-8")
    start = pipe_src.find("def sync_low_cost_off(")
    end = pipe_src.find("\ndef ", start + 1)
    off_body = _without_docstring(pipe_src[start:end if end > 0 else len(pipe_src)])
    for line in off_body.splitlines():
        code = line.split("#", 1)[0]          # a trailing comment is not code
        if "next_auto_revive" in code:
            failures.append("sync_low_cost_off still writes next_auto_revive - "
                            "the OFF handshake must not own the revive deadline")
            break

    main_src = (BASE / "main.py").read_text(encoding="utf-8")
    revive = main_src.find("if st.next_auto_revive and time.monotonic() >= st.next_auto_revive:")
    if revive < 0:
        failures.append("main's auto-revive read is gone - this test no longer "
                        "covers the path it exists for")
    else:
        after = main_src[revive:revive + 400]
        if "st.next_auto_revive = 0.0" not in after:
            failures.append("main's auto-revive no longer clears the deadline "
                            "when it consumes it - it would fire repeatedly")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: a transient failure's revive survives NR OFF and fires once")
    return 0


if __name__ == "__main__":
    sys.exit(main())
