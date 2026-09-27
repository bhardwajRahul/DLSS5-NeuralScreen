"""The NR cascade has hotkeys: one pass up, one pass down (#126).

#126 was closed as taken, #133 as its duplicate, and the feature fell between
the two: v2.1.6 had no binding, no row in Settings -> Keys, no strings. The
pass count was reachable only through the panel.

Checked by driving the real command dispatch (commands.drain_commands) with
the commands the hotkey thread posts:

* up and down step the count through the same action the panel's control
  takes - st, the config and a settings apply - clamped to 1..4, and a press
  at the edge applies nothing;
* the alert names the count, and the count that really runs where that
  differs: without Boost the cascade does not run, and a frame too small to
  step its work size aside runs one pass (the owner's caveat in #126);
* both commands are default bindings on keys of their own, rebindable like
  every other one, and have a row in Settings -> Keys.

Run:  runtime\\python.exe tests\\test_nr_passes_hotkey.py
"""
import queue
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import commands  # noqa: E402
import hotkeys  # noqa: E402
import pipeline  # noqa: E402
from i18n import STRINGS  # noqa: E402
from overlay_ui import HOTKEY_ROWS  # noqa: E402


def _state(passes=1, boost=True, size=(1920, 1080), scale=0.65, lang="en"):
    alerts, menu_states = [], []
    st = SimpleNamespace(
        nr_passes=passes, nr_small=boost, width=size[0], height=size[1],
        work_scale=scale, lang=lang, params={}, running=True,
        cfg={"profile": "Natural", "nr_passes": passes},
        tray_commands=queue.Queue())
    st.display = SimpleNamespace(
        alert=lambda text, *a, **k: alerts.append(text),
        menu=SimpleNamespace(visible=False,
                             set_state=lambda d: menu_states.append(dict(d))))
    return st, alerts, menu_states


def _press(st, *cmds):
    applies = []
    for cmd in cmds:
        st.tray_commands.put(cmd)
    with patch.object(pipeline, "request_apply",
                      lambda s, scale, profile, params, *a, **k: applies.append(s.nr_passes)):
        commands.drain_commands(st)
    return applies


def main() -> int:
    failures = []
    label = STRINGS["en"]["nr_passes"]

    # 1. Up, up, down: each step through the panel's action.
    st, alerts, menu = _state(passes=1)
    applies = _press(st, "nr_passes_up", "nr_passes_up", "nr_passes_down")
    if st.nr_passes != 2 or st.cfg["nr_passes"] != 2:
        failures.append(f"up, up, down left {st.nr_passes} passes "
                        f"(config {st.cfg['nr_passes']}), expected 2")
    if applies != [2, 3, 2]:
        failures.append(f"the settings applies carried {applies}, expected [2, 3, 2]")
    if menu[-1:] != [{"nr_passes": 2}]:
        failures.append(f"the panel was not told the new count: {menu}")
    if alerts != [f"{label}: 2", f"{label}: 3", f"{label}: 2"]:
        failures.append(f"alerts {alerts}")

    # 2. The edges: nothing to apply, the alert still says where it is.
    st, alerts, _menu = _state(passes=4)
    applies = _press(st, "nr_passes_up")
    if st.nr_passes != 4 or applies or alerts != [f"{label}: 4"]:
        failures.append(f"up at 4: passes {st.nr_passes}, applies {applies}, "
                        f"alerts {alerts}")
    st, alerts, _menu = _state(passes=1)
    applies = _press(st, "nr_passes_down")
    if st.nr_passes != 1 or applies or alerts != [f"{label}: 1"]:
        failures.append(f"down at 1: passes {st.nr_passes}, applies {applies}, "
                        f"alerts {alerts}")

    # 3. The effective state: Boost off runs one pass whatever the count.
    runs_one = STRINGS["en"]["nr_passes_runs"].format(live=1)
    st, alerts, _menu = _state(passes=2, boost=False)
    _press(st, "nr_passes_up")
    if st.nr_passes != 3 or alerts != [f"{label}: 3 ({runs_one})"]:
        failures.append(f"with Boost off the alert says {alerts}, expected "
                        f"the count and that one pass runs")
    # ... and so does a frame too small to step its work size aside.
    st, alerts, _menu = _state(passes=1, size=(64, 64), scale=1.0)
    _press(st, "nr_passes_up")
    if alerts != [f"{label}: 2 ({runs_one})"]:
        failures.append(f"a 64x64 frame at native: the alert says {alerts}, "
                        f"expected that one pass runs")
    # At native on an ordinary frame the work size steps aside and it runs.
    st, alerts, _menu = _state(passes=1, scale=1.0)
    _press(st, "nr_passes_up")
    if alerts != [f"{label}: 2"]:
        failures.append(f"at native on 1920x1080 the alert says {alerts}")
    # Another language: the alert is built from that language's strings.
    st, alerts, _menu = _state(passes=2, boost=False, lang="ru")
    _press(st, "nr_passes_down")
    ru = STRINGS["ru"]
    if alerts != [f"{ru['nr_passes']}: 1"]:
        failures.append(f"the Russian alert is {alerts}")

    # 4. Bindings, rebinding and the Settings -> Keys rows.
    by_cmd = {cmd: (mods, vk) for mods, vk, cmd, _n in hotkeys.DEFAULT_BINDINGS.values()}
    for cmd in ("nr_passes_up", "nr_passes_down"):
        if cmd not in by_cmd:
            failures.append(f"no default binding for {cmd}")
        if cmd not in {c for c, _label in HOTKEY_ROWS}:
            failures.append(f"no row for {cmd} in Settings -> Keys")
    keys = list(by_cmd.values())
    if len(set(keys)) != len(keys):
        failures.append("two default bindings share one key")
    rebound = hotkeys.build_bindings({"nr_passes_up": "F9"})
    if not any(cmd == "nr_passes_up" and vk == 0x78 for _m, vk, cmd, _n in rebound.values()):
        failures.append("nr_passes_up cannot be rebound")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the NR pass count steps up and down from the keyboard, says what "
          "runs, and is rebindable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
