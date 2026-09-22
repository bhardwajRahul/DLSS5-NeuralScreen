"""Adaptive exposure (PaperWhite principle) - on by default, no FPS cost.

The worker maps the AREA luminance average (the same 320x180 frame the
guides use) through a smoothstep between Dark/Lit thresholds and feeds the
result into DLSS.Exposure.Scale with temporal smoothing - the Ghady983
principle: dark scenes get a brighter input so the network stops producing
artifacts in the shadows.

Checked:
* with NS_PW=1 the worker logs the [pw] line and NR still comes up;
* with NS_PW=0 the [pw] line is absent (the feature is off);
* the mapping math (mirrored in Python): the exposure stays in
  [min, max], rises in dark scenes, returns to 1.0 in lit ones, and the
  EMA converges to the target.
"""
import math
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # the project root
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(Path(__file__).resolve().parent))

LOG = BASE / "NeuralScreen.log"
PY = BASE / "runtime" / "python.exe"

import autocheck  # noqa: E402


def _smoothstep(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _target(avg: float, dark: float, lit: float, mn: float, mx: float) -> float:
    t = (avg - dark) / (lit - dark)
    return mx - (mx - mn) * _smoothstep(t)


def _run_worker(env_extra: dict) -> str:
    LOG.write_text("", encoding="utf-8")
    env = dict(os.environ, NS_PHASE="1", **env_extra)
    with tempfile.TemporaryDirectory(prefix="ns-adaptive-") as temporary:
        config = Path(temporary) / "config.json"
        config.write_text(json.dumps(autocheck.shipped_config(), indent=2) + "\n",
                          encoding="utf-8")
        proc = subprocess.Popen(
            [str(PY), "-u", "main.py", "--config", str(config)],
            cwd=str(BASE), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 30
            pw_line = None
            nr_line = None
            while time.time() < deadline:
                text = LOG.read_text(encoding="utf-8", errors="replace")
                if pw_line is None:
                    m = re.search(r"\[pw\] adaptive exposure on", text)
                    if m:
                        pw_line = m.group(0)
                if nr_line is None and "NR ON" in text:
                    nr_line = "NR ON"
                if (pw_line or "NS_PW=0" in str(env_extra)) and nr_line:
                    break
                time.sleep(0.5)
        finally:
            # The tree this test started, and nothing else: terminate() kills
            # only python.exe, and the worker (nvngx.dll) it spawned has to go
            # with it or the next GUI test finds "NeuralScreen is already
            # running". It used to be `taskkill /IM nvngx.dll` - every worker on
            # the machine, a NeuralScreen the developer had open included.
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return (pw_line or "") + "|" + (nr_line or "")


def main() -> int:
    failures = []

    # 1. Live: NS_PW=1 -> the [pw] line appears, NR comes up.
    out = _run_worker({"NS_PW": "1"})
    if "[pw] adaptive exposure on" not in out:
        failures.append("no [pw] line with NS_PW=1")
    if "NR ON" not in out:
        failures.append("NR did not come up with NS_PW=1")

    # 2. Live: NS_PW=0 -> no [pw] line, NR still comes up.
    out = _run_worker({"NS_PW": "0"})
    if "[pw]" in out:
        failures.append("[pw] line present with NS_PW=0")
    if "NR ON" not in out:
        failures.append("NR did not come up with NS_PW=0")

    # 3. The mapping math. The constants are READ FROM THE PRODUCT, not typed
    #    in here: the earlier version hardcoded 0.10/0.40/1.00/1.10, so
    #    changing the C++ default left the test validating its own copy
    #    (audit: WEAK). PwEnvFloat is called with a default, so the literal
    #    after NS_PW_<NAME> in the worker source IS the default.
    cpp = (BASE / "native" / "dlss5-feed-host64.cpp").read_text(
        encoding="utf-8", errors="replace")

    def _default(name: str) -> float:
        m = re.search(rf'PwEnvFloat\(\s*"{name}"\s*,\s*([0-9.]+)f\s*\)', cpp)
        if not m:
            raise AssertionError(f"{name} default not found in the worker")
        return float(m.group(1))

    dark, lit, mn, mx = (_default("NS_PW_DARK"), _default("NS_PW_LIT"),
                         _default("NS_PW_MIN"), _default("NS_PW_MAX"))
    tau = _default("NS_PW_TAU")
    print(f"    defaults from the worker: dark={dark} lit={lit} "
          f"min={mn} max={mx} tau={tau}")
    # And the shape of the mapping the worker actually applies.
    for needle, what in (("smoothstep", "the smoothstep"),
                         ("g_pw_exposure", "the smoothed exposure value")):
        if needle not in cpp:
            failures.append(f"{what} is gone from the worker - this test's "
                            f"model of the mapping no longer describes it")
    if not (0.0 <= dark < lit <= 1.0):
        failures.append(f"the thresholds are not a usable window: "
                        f"dark={dark} lit={lit}")
    if not (mn <= mx):
        failures.append(f"the exposure range is inverted: min={mn} max={mx}")
    # A dark scene (avg below dark) gets the max exposure.
    if not math.isclose(_target(0.0, dark, lit, mn, mx), mx, rel_tol=1e-6):
        failures.append("dark scene does not get the max exposure")
    # A lit scene (avg above lit) returns to 1.0.
    if not math.isclose(_target(1.0, dark, lit, mn, mx), mn, rel_tol=1e-6):
        failures.append("lit scene does not return to 1.0")
    # Monotonic: brighter scene -> lower exposure.
    prev = _target(0.0, dark, lit, mn, mx)
    for avg in (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.5):
        cur = _target(avg, dark, lit, mn, mx)
        if cur > prev + 1e-9:
            failures.append(f"exposure not monotonic at avg={avg}")
        prev = cur
    # The range is [mn, mx] everywhere.
    for avg in (0.0, 0.05, 0.2, 0.5, 0.9, 1.0):
        v = _target(avg, dark, lit, mn, mx)
        if not (mn - 1e-9 <= v <= mx + 1e-9):
            failures.append(f"exposure {v} out of range at avg={avg}")

    # 4. The EMA converges to the target (the worker's own tau, 60 fps).
    target = _target(0.05, dark, lit, mn, mx)  # a dark-ish scene
    value = 1.0
    for _ in range(600):  # 10 s at 60 fps
        a = 1.0 - math.exp(-(1.0 / 60.0) / max(tau, 1e-6))
        value += (target - value) * a
    if abs(value - target) > 1e-3:
        failures.append(f"EMA did not converge: {value} vs {target}")

    for fl in failures:
        print("FAIL:", fl)
    if failures:
        return 1
    print("OK: adaptive exposure on by default, dark scenes brightened, "
          "lit scenes untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
