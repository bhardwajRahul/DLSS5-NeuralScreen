"""The apply is debounced: one rebuild at the end of a drag, not one per step.

Issue #115 (fyrestormx): "when setting Boost, perhaps a small delay before
applying so it doesnt seem slow/nonresponsive, also makes it easier to choose
the resolution since it hangs a bit while changing."

The apply cannot be made free - it sends RNSZ and waits for the ack (~0.3 s),
and the frame loop is not pumping the panel's events while it waits. What it
can do is not run on the first step of a movement. Every request lands in one
slot with a deadline APPLY_DEBOUNCE ahead of it; the main loop applies the
slot when the deadline passes, so a drag across the slider produces ONE
feature rebuild at the value the user let go on.

What this pins, without launching anything:

* a request does NOT apply immediately - it goes into the slot with a future
  deadline, which is the whole point of the change;
* a second request pushes the deadline forward and REPLACES the value, so the
  last value wins and the intermediate ones never reach the worker;
* the Boost switch survives a request that does not mention it - a slider
  move while a switch is queued must not quietly drop the switch, or turning
  Boost on and then moving the slider would leave the mode unchanged;
* the slot is a monotonic DEADLINE and not a flag, so nothing applies while
  the deadline is in the future.

Run:  runtime\\python.exe tests\\test_apply_debounce.py
"""
import sys
import time
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import pipeline  # noqa: E402


def _state():
    return types.SimpleNamespace(
        pending_apply=None, pending_apply_due=0.0,
        cfg={"profile": "Natural"}, lang="en",
        params={"intensity": 1.0}, work_scale=0.5, nr_small=False)


def main() -> int:
    failures = []
    real_restart = pipeline.do_restart
    applied = []
    pipeline.do_restart = lambda st, *a, **kw: applied.append((a, kw))
    try:
        # 1. A request must NOT apply immediately - it is queued.
        st = _state()
        pipeline.request_apply(st, 0.5, "Natural", st.params, new_small=True)
        if applied:
            failures.append(f"a single request applied immediately: {applied}")
        if st.pending_apply is None:
            failures.append("the request was not queued at all")
        elif st.pending_apply[3] is not True:
            failures.append(f"the queued Boost flag is {st.pending_apply[3]!r}, "
                            f"expected True")
        if st.pending_apply_due <= time.monotonic():
            failures.append("the deadline is not in the future - the slot "
                            "would apply on the next pass")
        print(f"    queued: {st.pending_apply}, "
              f"due in {st.pending_apply_due - time.monotonic():.2f}s")

        # 2. A drag: many requests, and the LAST value is what stays.
        st = _state()
        for step in (0.40, 0.45, 0.50, 0.55):
            pipeline.request_apply(st, step, "Natural", st.params, new_small=True)
        if st.pending_apply is None or abs(st.pending_apply[0] - 0.55) > 1e-9:
            failures.append(f"the drag kept {st.pending_apply}, expected the "
                            f"last value 0.55")
        if applied:
            failures.append(f"a drag applied {len(applied)} time(s) - the "
                            f"worker rebuilds once per step again")
        print(f"    drag of 4 steps: queued {st.pending_apply[0]}, "
              f"applied {len(applied)} times")

        # 3. Each request pushes the deadline FORWARD: a request made while the
        #    slot is already waiting must move it, or a fast drag would apply
        #    in the middle of the movement.
        st = _state()
        pipeline.request_apply(st, 0.40, "Natural", st.params, new_small=True)
        first_due = st.pending_apply_due
        time.sleep(0.02)
        pipeline.request_apply(st, 0.45, "Natural", st.params, new_small=True)
        if st.pending_apply_due <= first_due:
            failures.append("the second request did not push the deadline "
                            "forward")
        print(f"    deadline moved forward by "
              f"{(st.pending_apply_due - first_due) * 1000:.0f}ms")

        # 4. A request that does not mention Boost must keep the queued switch.
        #    This is the bug the naive version had: turning Boost on and then
        #    moving the slider dropped new_small back to None, and the mode
        #    never changed.
        st = _state()
        pipeline.request_apply(st, 0.5, "Natural", st.params, new_small=True)
        pipeline.request_apply(st, 0.55, "Natural", st.params)   # no new_small
        if st.pending_apply is None or st.pending_apply[3] is not True:
            failures.append(f"the queued Boost flag was lost by a slider "
                            f"request: {st.pending_apply}")
        print(f"    switch kept across a slider move: {st.pending_apply[3]}")

        # 5. And the reverse: a request that HAS new_small wins over the slot.
        st = _state()
        pipeline.request_apply(st, 0.5, "Natural", st.params, new_small=True)
        pipeline.request_apply(st, 0.5, "Natural", st.params, new_small=False)
        if st.pending_apply is None or st.pending_apply[3] is not False:
            failures.append(f"an explicit new_small did not win: "
                            f"{st.pending_apply}")
        print(f"    explicit switch wins: {st.pending_apply[3]}")

        # 6. A fresh state (nothing queued) never applies anything on its own:
        #    the main loop is the only caller of do_restart here.
        st = _state()
        if st.pending_apply is not None:
            failures.append("a fresh state came up with something queued")
    finally:
        pipeline.do_restart = real_restart

    if failures:
        print("\nFAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK: requests are debounced, the last value wins, and a queued "
          "Boost switch survives a slider move")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
