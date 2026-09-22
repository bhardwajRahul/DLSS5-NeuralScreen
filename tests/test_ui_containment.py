"""The program's reactions to a failure stay local (22.09 audit).

No GPU and no real windows, except where a source rule is the check:

* a menu action or command that raises is logged and shown, and the main
  loop goes on - it used to close the program (run_contained);
* a hotkey rebind onto a key another action holds is refused with a message
  instead of silently leaving one of them dead;
* a per-pass control moved after the worker failed does not write into its
  closed stdin;
* a window probe that fails puts the capture back the way it was: the desktop
  pipeline gets the desktop (and its DACK is awaited), a window pipeline gets
  its window again, and a window that cannot be captured any more ends
  window mode;
* a rebuild made while the worker stood failed ends the failed state;
* showing or re-layering our window never activates it, the taskbar module and
  winapi state their own ctypes prototypes, and the support bundle does not
  carry other programs' window titles.

Run:  runtime\\python.exe tests\\test_ui_containment.py
"""
import queue
import re
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import channels  # noqa: E402
import commands  # noqa: E402
import diagnostics  # noqa: E402
import hotkeys  # noqa: E402
import pipeline  # noqa: E402


def _src(name: str) -> str:
    return (BASE / name).read_text(encoding="utf-8")


def _function(src: str, name: str) -> str:
    m = re.search(rf"\n    def {name}\(.*?(?=\n    def |\Z)", src, re.S) or \
        re.search(rf"\ndef {name}\(.*?(?=\ndef |\Z)", src, re.S)
    return m.group(0) if m else ""


def main() -> int:
    failures = []
    alerts = []
    display = types.SimpleNamespace(alert=lambda text, **kw: alerts.append(text))

    # 1. run_contained
    st = types.SimpleNamespace(display=display, lang="en")

    def boom():
        raise ValueError("write to closed file")
    if commands.run_contained(st, "a test action", boom) is not None:
        failures.append("run_contained did not return None for a failure")
    if not alerts:
        failures.append("a contained failure was not shown")
    if commands.run_contained(st, "a test action", lambda: 7) != 7:
        failures.append("run_contained does not pass the result through")
    main_src = _src("main.py")
    if "commands.apply_menu_action(st, action)\n" in main_src:
        failures.append("main still calls apply_menu_action unguarded")
    if re.search(r"if not commands\.drain_commands\(st\)", main_src):
        failures.append("main still calls drain_commands unguarded")

    # 2. a taken hotkey is refused
    alerts.clear()
    bindings = hotkeys.build_bindings({})
    st = types.SimpleNamespace(
        display=types.SimpleNamespace(alert=lambda text, **kw: alerts.append(text),
                                      menu=types.SimpleNamespace(set_hotkeys=lambda *_: None)),
        lang="en", cfg={"hotkeys": {}}, hotkey_bindings=bindings,
        hotkeys=types.SimpleNamespace(rebind=lambda b: failures.append(
            "a taken combination was registered anyway")),
        tray_commands=queue.Queue())
    toggle_name = next(name for _m, _v, cmd, name in bindings.values() if cmd == "toggle")
    commands.apply_menu_action(st, ("hotkey", "record", toggle_name))
    if st.cfg["hotkeys"]:
        failures.append(f"the taken combination was saved: {st.cfg['hotkeys']}")
    if not any(toggle_name in a for a in alerts):
        failures.append(f"the refusal did not name the key: {alerts}")

    # 3. per-pass into a failed worker
    sent = []
    real_send = pipeline.send_per_pass
    pipeline.send_per_pass = lambda *a, **k: sent.append(a)
    try:
        st = types.SimpleNamespace(worker_failed=True,
                                   worker=types.SimpleNamespace(poll=lambda: None))
        commands._send_per_pass_now(st, {"style": 1}, enabled=True)
        if sent:
            failures.append("the per-pass set was written to a failed worker")
    finally:
        pipeline.send_per_pass = real_send

    # 4. a failed probe restores the source
    calls = []
    real = (pipeline.send_dda, channels.probe_window_capture,
            pipeline.switch_window, pipeline.window_frame_rect)
    pipeline.send_dda = lambda worker, w, h: calls.append(("dda", w, h))
    pipeline.window_frame_rect = lambda hwnd: (0, 0, 800, 600)
    reader = types.SimpleNamespace(wait_dack=lambda timeout: calls.append(("dack",)))
    exits = []
    disp = types.SimpleNamespace(exit_switch_mode=lambda: exits.append(1))
    try:
        # desktop pipeline
        st = types.SimpleNamespace(window_hwnd=None, width=2560, height=1440,
                                   worker=None, reader=reader, display=disp,
                                   follow_size=None)
        pipeline._restore_after_failed_probe(st, 0x123)
        if ("dda", 2560, 1440) not in calls or ("dack",) not in calls:
            failures.append(f"a desktop pipeline did not get the desktop back "
                            f"with its DACK awaited: {calls}")
        # window pipeline, another window refused: the current one comes back
        calls.clear()
        channels.probe_window_capture = lambda s, hwnd: (
            calls.append(("probe", hwnd)) or (1280, 720))
        st = types.SimpleNamespace(window_hwnd=0xA, width=1280, height=720,
                                   worker=None, reader=reader, display=disp,
                                   follow_size=(1280, 720))
        pipeline.switch_window = lambda s, h: calls.append(("switch", h))
        pipeline._restore_after_failed_probe(st, 0xB)
        if ("probe", 0xA) not in calls or any(c[0] == "dda" for c in calls):
            failures.append(f"a window pipeline was not pointed back at its "
                            f"window (or got a desktop crop): {calls}")
        if st.follow_size != (1280, 720):
            failures.append("the processed window's follow size was overwritten "
                            "by the refused window's")
        # the processed window itself is gone: window mode ends
        calls.clear()
        pipeline._restore_after_failed_probe(st, 0xA)
        if ("switch", 0) not in calls:
            failures.append(f"a window that cannot be captured any more did not "
                            f"end window mode: {calls}")
    finally:
        (pipeline.send_dda, channels.probe_window_capture,
         pipeline.switch_window, pipeline.window_frame_rect) = real

    # 5. a rebuild ends a failed state
    rebuild = _function(_src("pipeline.py"), "rebuild_pipeline")
    for token in ("st.worker_failed = False", "st.next_auto_revive = 0.0",
                  "st.paused = False"):
        if token not in rebuild:
            failures.append(f"rebuild_pipeline does not reset the failed state "
                            f"({token})")

    # 6. never activate; own prototypes; no foreign titles
    disp_src = _src("display.py")
    for name, token in (("set_visible", "ShowWindow(hwnd, 8 if visible else 0)"),
                        ("reveal", "ShowWindow(hwnd, 8)"),
                        ("_set_topmost", "0x0001 | 0x0002 | 0x0010")):
        if token not in _function(disp_src, name):
            failures.append(f"Display.{name} can activate the window")
    if "| 0x0010)" not in _function(disp_src, "set_menu_input"):
        failures.append("set_menu_input's frame change can activate the window")
    taskbar_src = _src("taskbar.py")
    if 'ctypes.WinDLL("user32"' not in taskbar_src or "ctypes.windll.user32" in taskbar_src:
        failures.append("taskbar.py shares windll.user32 (and pystray's prototypes)")
    if "FindWindowExW(None, tb, cls, None)" not in taskbar_src:
        failures.append("the taskbar click test stops at the first secondary taskbar")
    if 'ctypes.WinDLL("user32"' not in _src("winapi.py"):
        failures.append("winapi.py reads window handles as a C int")
    line = "[z] foreign-above-hud top=hwnd=0x1 class='x' title='Private chat - Bob' rect=(0,0,1,1)"
    if "Private chat" in diagnostics.sanitize_text(line):
        failures.append("the support bundle carries another program's window title")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: failures stay local, taken keys are refused, a failed probe "
          "restores the source, and our windows never take the focus")
    return 0


if __name__ == "__main__":
    sys.exit(main())
