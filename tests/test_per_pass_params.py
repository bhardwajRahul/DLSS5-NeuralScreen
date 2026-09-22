"""The per-pass parameter set: it reaches the worker, and it stays honest.

The cascade runs 1-4 passes and every one of them used to read the same
parameters, so pass 2 repeated pass 1 exactly - a third of the frame rate for
no change. The set this file covers gives passes 2..N their own numbers.
Measured 22.09.2026: pass 2 on Faithful against a Strong main set moves 100% of
pixels; sending the same numbers through the same command moves none, so the
difference is the numbers and not the command.

Four things can break silently, and none of them shows up as an error:

1. THE WIRE. The two ends were written by hand, separately. The command is a
   fixed 40 bytes with an int64 last, and C++ pads a struct before an int64
   when the four-byte fields above it do not add up to a multiple of eight -
   four bytes of padding shifts every field after the gap by one slot, and the
   worker would read `style` out of the low half of a float. The offsets are
   checked here against the worker's own constants.

2. THE DEFAULT. "No set" has to mean "every pass uses the main set". A build
   that quietly invented a set would change the picture for every user who
   never asked for one.

3. THE SURVIVAL. A resize rebuilds the cascade, and a rebuilt cascade reads
   the main set. So every RNSZ has to be followed by the set again - which is
   the same class of bug the pass count itself had (the panel said four, the
   worker ran one, nothing in the log).

4. THE SWITCH. A set that is off must not linger in the config: on is a set,
   off is the absence of one, and those are different states.

Run:  runtime\\python.exe tests\\test_per_pass_params.py
"""
from __future__ import annotations

import io
import re
import struct
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import protocol  # noqa: E402
import settings_io  # noqa: E402

PARAMS = {"intensity": 0.70, "local_tone": 0.25,
          "local_structure": 0.75, "skin_structure": -1.0}
SET = {"style": 0, **PARAMS}


class _Sink:
    """A worker that only remembers what was written to it."""

    def __init__(self):
        self.blob = b""
        self.stdin = self

    def write(self, data):
        self.blob += data

    def flush(self):
        pass


def main() -> int:
    failures: list[str] = []

    # 1. The wire, field by field, against the worker's own constants.
    cpp = io.open(BASE / "native" / "dlss5-feed-host64.cpp",
                  encoding="utf-8", errors="surrogateescape").read()

    def _const(name: str) -> int | None:
        m = re.search(r"%s\s*=\s*(0x[0-9A-Fa-f]+|\d+)u?;" % name, cpp)
        return int(m.group(1), 0) if m else None

    for py_name, c_name in (("PER_PASS_MAGIC", "PER_PASS_MAGIC"),
                            ("PER_PASS_ACK_MAGIC", "PER_PASS_ACK_MAGIC"),
                            ("PER_PASS_FLAG_ENABLED", "PER_PASS_FLAG_ENABLED")):
        py, c = getattr(protocol, py_name, None), _const(c_name)
        if c is None:
            failures.append(f"the worker has no {c_name} constant")
        elif py != c:
            failures.append(f"{py_name}: worker 0x{c:08X}, protocol.py "
                            f"0x{py:08X}")

    # The message numbers, which is how the dispatcher routes it at all.
    if not re.search(r"if \(fh\.magic == PER_PASS_MAGIC\)", cpp):
        failures.append("the worker's dispatcher does not read PER_PASS_MAGIC, "
                        "so a PPRM falls through to frame handling")
    if not re.search(r"if \(msg == 11\)", cpp):
        failures.append("the worker handles no message 11 - PPRM never lands")
    if "return 11;" not in cpp:
        failures.append("the dispatcher never returns 11 for PPRM")

    sent = _Sink()
    protocol.send_per_pass(sent, SET, enabled=True)
    size = struct.calcsize(protocol.PER_PASS_FMT)
    if len(sent.blob) != size:
        failures.append(f"send_per_pass wrote {len(sent.blob)} bytes, the "
                        f"format is {size}")
    fields = struct.unpack(protocol.PER_PASS_FMT, sent.blob[:size])
    if fields[0] != protocol.PER_PASS_MAGIC:
        failures.append("the magic did not go out first")
    if fields[1] & protocol.PER_PASS_FLAG_ENABLED == 0:
        failures.append("the enabled flag did not go out")
    if fields[2] != 0:
        failures.append(f"style went out as {fields[2]}, not 0")
    for got, want, name in zip(fields[4:8], PARAMS.values(), PARAMS):
        if abs(got - want) > 1e-6:
            failures.append(f"{name} went out as {got}, not {want}")
    # The int64 is last and must not be read as two floats: this is the offset
    # the padding bug would move.
    if size != 40:
        failures.append(f"the command is {size} bytes; the worker declares a "
                        f"40-byte struct with int64_t pts last")

    clear = _Sink()
    protocol.send_per_pass(clear, SET, enabled=False)
    if struct.unpack(protocol.PER_PASS_FMT,
                     clear.blob[:size])[1] & protocol.PER_PASS_FLAG_ENABLED:
        failures.append("enabled=False still sent the enabled bit, so a set "
                        "could not be cleared")

    # A worker that never receives this command must behave as before.
    quiet = _Sink()
    protocol.send_resize(quiet, {"style": 1, "auto_mask": 1, **PARAMS},
                         1920, 1080, 1)
    if protocol.PER_PASS_FMT in (len(quiet.blob),):
        failures.append("send_resize now emits a per-pass set on its own")

    # 2. The default, and what a half-written set does.
    if settings_io.clean_per_pass(None) is not None:
        failures.append("a missing set did not come back as None")
    if settings_io.clean_per_pass({}) is not None:
        failures.append("an empty dict became a set - every pass would then "
                        "read an invented set instead of the main one")
    good = settings_io.clean_per_pass(SET)
    if good is None or good["style"] != 0:
        failures.append(f"a well-formed set did not survive: {good}")
    # Clamped, not dropped: a hand-edited config with 9.0 is still a set.
    hot = settings_io.clean_per_pass({"style": 1, **PARAMS,
                                      "intensity": 9.0})
    if hot is None or hot["intensity"] > 1.0:
        failures.append(f"an out-of-range strength was not clamped: {hot}")
    for broken in ({"style": 7, **PARAMS},          # no such model
                   {"style": 1},                    # three numbers missing
                   {"style": 1, **PARAMS, "intensity": "loud"},
                   {"style": 1, **PARAMS, "local_tone": None},
                   "not a dict"):
        if settings_io.clean_per_pass(broken) is not None:
            failures.append(f"a broken set was accepted: {broken!r} - a set "
                            f"repaired from part of the numbers changes the "
                            f"picture in a way nobody asked for")

    # 3. It survives every path that rebuilds the cascade. Read as source: the
    # send lives inside do_restart, and no unit test can watch a running worker
    # rebuild its features.
    pipe = io.open(BASE / "app" / "pipeline.py", encoding="utf-8").read()
    if "_send_per_pass_if_any" not in pipe:
        failures.append("pipeline.py has no per-pass hand-off")
    calls = [m for m in re.finditer(
        r"send_resize\((?:[^()]|\([^()]*\))*\)", pipe, re.S)]
    if not calls:
        failures.append("no send_resize call found - this test looks at nothing")
    for m in calls:
        # The window has to clear the ack wait and whatever comment sits
        # between the two commands - an earlier version used 400 characters
        # and failed on a call whose explanation was longer than that.
        window = pipe[m.end():m.end() + 900]
        if "_send_per_pass_if_any" not in window:
            line = pipe[:m.start()].count("\n") + 1
            failures.append(
                f"pipeline.py:{line}: this RNSZ is not followed by the "
                f"per-pass set, so a resize drops passes 2+ back to the main "
                f"set with nothing in the log")
    if "nr_pass_params" not in pipe:
        failures.append("pipeline.py never reads the per-pass set")

    # 4. The switch, and the config key.
    if "PER_PASS_KEYS" not in dir(settings_io):
        failures.append("settings_io has no PER_PASS_KEYS")
    else:
        if set(settings_io.PER_PASS_KEYS) != set(PARAMS):
            failures.append(f"PER_PASS_KEYS is {settings_io.PER_PASS_KEYS}, "
                            f"which is not the four strengths")
    io_src = io.open(BASE / "app" / "settings_io.py", encoding="utf-8").read()
    if "nr_pass_params" not in io_src:
        failures.append("the config never carries nr_pass_params, so the set "
                        "is lost on the next launch")
    cmds = io.open(BASE / "app" / "commands.py", encoding="utf-8").read()
    if '"pass_params"' not in cmds:
        failures.append("commands.py handles no pass_params toggle")
    if 'st.cfg.pop("nr_pass_params"' not in cmds:
        failures.append("turning the set off does not clear the config key - "
                        "off and 'a set equal to the main values' are "
                        "different states, and only one of them survives a "
                        "profile change")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the per-pass set has a matched 40-byte layout, no set means the "
          "main set, every resize re-states it, and off clears the key")
    return 0


if __name__ == "__main__":
    sys.exit(main())
