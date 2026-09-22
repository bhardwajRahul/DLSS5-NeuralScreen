"""The status line names the card that really runs (issue #81).

NVAPI and DXGI enumerate adapters in their own orders, and on a machine
with two cards the orders disagree. Issue #81: a 4070 Super + 3050 box
ran the pipeline on the 4070 (NS_GPU=0 names DXGI's first NVIDIA card -
Task Manager showed the 4070 doing the work), while the panel and the
About block said "RTX 3050 · Ampere" - the first card NVAPI enumerated.
The displayed card was picked by position, not by the card the worker
runs on.

The fix: every caller of gpuinfo.probe passes the DXGI NAME of the
adapter the worker will really land on (startup._working_card_name, the
same fallback rule the worker applies: an unusable NS_GPU falls back to
the first NVIDIA adapter), and probe describes the card with that name.

Checked:
1. _choose_index matches the hint case-insensitively and falls back to
   the first card for a missing/None/unknown hint.
2. _working_card_name mirrors the worker fallback: exact index, first
   card for an index this machine does not have, first card for junk.
3. _log_environment passes the hint into gpuinfo.probe (monkeypatched).
4. The bring_up call site passes it too (source check - running it needs
   a full hardware bring-up).
5. Real nvapi on this machine: probing by this machine's own adapter
   name answers identically to the no-hint probe (single-card case).

Run:  runtime\\python.exe tests\\test_gpu_identity.py
"""
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import gpuinfo  # noqa: E402
import startup  # noqa: E402

# The two-card shape from issue #81, in DXGI order (the worker's world).
FAKE = [(0, "NVIDIA GeForce RTX 4070 SUPER"), (2, "NVIDIA GeForce RTX 3050")]


def main() -> int:
    failures = []

    # 1. _choose_index: the hint beats position; junk keeps the first card.
    names = ["NVIDIA GeForce RTX 3050", "NVIDIA GeForce RTX 4070 SUPER"]
    if gpuinfo._choose_index(names, "nvidia geforce rtx 4070 super") != 1:
        failures.append("the hint did not match case-insensitively")
    if gpuinfo._choose_index(names, "NVIDIA GeForce RTX 4070 SUPER") != 1:
        failures.append("the exact hint did not match")
    for hint in (None, "", "RTX 9000"):
        if gpuinfo._choose_index(names, hint) != 0:
            failures.append(f"hint {hint!r} should keep the first card")
    if gpuinfo._choose_index([], "anything") != 0:
        failures.append("an empty list should answer 0")

    # 2. _working_card_name mirrors the worker's fallback rule.
    real_list = startup.list_adapters
    startup.list_adapters = lambda: FAKE
    try:
        cases = ((0, "NVIDIA GeForce RTX 4070 SUPER"),
                 (2, "NVIDIA GeForce RTX 3050"),
                 (1, "NVIDIA GeForce RTX 4070 SUPER"),   # not on this machine
                 ("junk", "NVIDIA GeForce RTX 4070 SUPER"),
                 (None, "NVIDIA GeForce RTX 4070 SUPER"))
        for gpu, expect in cases:
            got = startup._working_card_name({"gpu": gpu})
            if got != expect:
                failures.append(f"gpu={gpu!r}: {got!r}, expected {expect!r}")
        if startup._working_card_name({}) != FAKE[0][1]:
            failures.append("a config without a gpu key should name the first")

        # 3. _log_environment passes the hint into gpuinfo.probe.
        seen = []
        real_probe = gpuinfo.probe
        gpuinfo.probe = lambda hint=None: (seen.append(hint), {})[1]
        try:
            startup._log_environment({"lang": "en", "gpu": 2})
            startup._log_environment({"lang": "en", "gpu": 0})
        finally:
            gpuinfo.probe = real_probe
        if seen != [FAKE[1][1], FAKE[0][1]]:
            failures.append(f"the probe hints were {seen!r}")
    finally:
        startup.list_adapters = real_list

    # 4. bring_up passes the hint too (textual: the call needs the whole
    #    hardware bring-up to run).
    src = (BASE / "app" / "startup.py").read_text(encoding="utf-8")
    if "gpu_probe(_working_card_name(st.cfg))" not in src:
        failures.append("bring_up does not pass the hint to gpu_probe")

    # 5. The real nvapi on this machine answers the same either way
    #    (one NVIDIA card here, so the hint cannot change the pick - but
    #    the refactor must not break the answer).
    plain = gpuinfo.probe()
    if plain.get("name"):
        hinted = gpuinfo.probe(FAKE[0][1])
        if hinted != plain:
            failures.append(
                f"the hinted probe diverged on this machine: {hinted} vs {plain}")
    else:
        print("    nvapi did not answer on this machine - check 5 skipped")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the displayed card follows the adapter the worker runs on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
