"""Audit F8: teardown+rebuild must reset every channel flag the loop reads.

teardown_pipeline closes the shm; rebuild_pipeline resets the per-worker
flags (present/dda/window/out/motion). gray_active is set/cleared by
channels.sync_gray but NOT reset by either - after a rebuild the loop's
`if st.gray_active:` branch reads a gray section of the NEW shm that the
new worker has not opened (returns None -> one frame with no luminance,
or a shape mismatch through guides' guard).

Expected: the set of flags reset in rebuild_pipeline equals the set the
loop's negotiation block reads. [audit python-core F8]

This used to fail on gray_active alone and merely PRINT the other eight, so
a regression in any of them passed (audit: WEAK). The flags are a set: one
of them missing is the same class of bug, and the print is not a check.
The assertion is now over the whole set.

Run:  runtime\\python.exe tests\\test_channel_flags.py
"""
import re
import sys
from pathlib import Path


def _repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "main.py").is_file():
            return p
    return start


BASE = _repo_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

# The flags the main loop negotiates/reads every frame (main.py).
LOOP_FLAGS = ("present_mode", "present_attempted", "dda_mode", "dda_attempted",
              "gray_active", "motion_small", "motion_attempted",
              "out_shm", "out_attempted")


def main() -> int:
    failures = []
    rebuild_src = (BASE / "app" / "pipeline.py").read_text(encoding="utf-8")
    m = re.search(r"def rebuild_pipeline\(st[^)]*\).*?(?=\ndef |\Z)",
                  rebuild_src, re.S)
    body = m.group(0) if m else ""
    reset = set(re.findall(r"st\.([a-z_]+)\s*=\s*False", body))
    # attempt flags count as reset when their mode flag is.
    missing = [f for f in LOOP_FLAGS if f not in reset]
    print(f"    reset in rebuild_pipeline: {sorted(reset & set(LOOP_FLAGS))}")
    print(f"    missing: {missing}")

    # Every flag the loop reads must be reset, not just gray_active. Each
    # missing one leaves the loop reading the NEW worker's channel with a
    # state flag set by the OLD one - gray_active reads a section that was
    # never opened, present_mode/dda_mode make the loop take a channel it
    # has not negotiated yet.
    if missing:
        failures.append(
            "F8: rebuild_pipeline does not reset " + ", ".join(missing) +
            " - the loop's negotiation block reads a flag the old worker "
            "set, on the new worker's channels")

    # attempt flags are the pair of their mode flag: if the mode flag is
    # reset but its attempt flag is not (or the other way round), the loop
    # skips a channel it still has to negotiate.
    for mode, attempt in (("present_mode", "present_attempted"),
                          ("dda_mode", "dda_attempted"),
                          ("motion_small", "motion_attempted"),
                          ("out_shm", "out_attempted")):
        if (mode in reset) != (attempt in reset):
            failures.append(
                f"F8: {mode}/{attempt} were not reset as a pair "
                f"(one of them is missing)")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: rebuild_pipeline resets every channel flag the loop reads")
    return 0


if __name__ == "__main__":
    sys.exit(main())
