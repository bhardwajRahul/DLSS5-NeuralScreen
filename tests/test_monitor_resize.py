"""The desktop resolution changes under a running pipeline - rebuild once.

Everything downstream of the frame size is built once: the worker's NGX
feature, the shared memory, the overlay window. Nothing watched the monitor
itself, so switching the desktop from 1440p to 4K left the program
processing a 2560x1440 island in the corner of a 4K screen, with the overlay
stuck at its old bounds (user report, 11.09).

st.capture.resolution cannot answer this - it is what the monitor was when
the capture session opened - so the size is asked of Windows every 30 frames
and has to hold still for half a second before anything is rebuilt: a mode
change goes through intermediate sizes, and rebuilding on each one would
mean several worker restarts for one switch.
"""
import sys
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import pipeline  # noqa: E402
import startup  # noqa: E402

OLD = (2560, 1440)
NEW = (3840, 2160)


def _state():
    st = types.SimpleNamespace(
        window_hwnd=None, worker_failed=False, running=True,
        width=OLD[0], height=OLD[1], mon_w=OLD[0], mon_h=OLD[1],
        mon_resize=None, monitor=0, work_scale=0.65, work_w=0, work_h=0,
        mon_origin=(0, 0), cfg={}, lang="en", origins=[],
        capture=types.SimpleNamespace(devicename=r"\\.\DISPLAY1",
                                      resolution=NEW, close=lambda: None))
    st.display = types.SimpleNamespace(
        alert=lambda *a, **kw: None,
        set_origin=lambda x, y: st.origins.append((x, y)))
    return st


def _drive(st, size, ticks, rebuilds, step=0.1, origin=(0, 0)):
    clock = {"t": 500.0}
    real = (pipeline.monitor_size, pipeline.teardown_pipeline,
            pipeline.rebuild_pipeline, pipeline.ScreenCapture,
            pipeline._refresh_dxcam_factory, pipeline.time,
            pipeline.monitor_origin, pipeline.resolve_output_idx,
            startup._apply_monitor_env)
    pipeline.monitor_size = lambda name: size(clock["t"]) if callable(size) else size
    # The corner is the test's, not this machine's monitor layout.
    pipeline.monitor_origin = lambda name: origin
    pipeline.resolve_output_idx = lambda name: 0
    startup._apply_monitor_env = lambda capture: origin
    pipeline.teardown_pipeline = lambda s: None
    pipeline._refresh_dxcam_factory = lambda: None
    # A fresh capture opens at the size the monitor has at that moment.
    pipeline.ScreenCapture = lambda monitor_idx=0: types.SimpleNamespace(
        devicename=r"\\.\DISPLAY1",
        resolution=size(clock["t"]) if callable(size) else size,
        monitor_idx=monitor_idx, close=lambda: None)
    pipeline.rebuild_pipeline = lambda s, note: rebuilds.append((s.width, s.height))
    pipeline.time = types.SimpleNamespace(monotonic=lambda: clock["t"])
    try:
        for _ in range(ticks):
            clock["t"] += step
            pipeline.follow_monitor(st)
    finally:
        (pipeline.monitor_size, pipeline.teardown_pipeline,
         pipeline.rebuild_pipeline, pipeline.ScreenCapture,
         pipeline._refresh_dxcam_factory, pipeline.time,
         pipeline.monitor_origin, pipeline.resolve_output_idx,
         startup._apply_monitor_env) = real


def main() -> int:
    failures = []

    # 1. The monitor has not changed: nothing happens, ever.
    st = _state()
    rebuilds = []
    _drive(st, OLD, 30, rebuilds)
    if rebuilds:
        failures.append(f"an unchanged monitor rebuilt {len(rebuilds)} times")

    # 2. 1440p -> 4K: exactly one rebuild, at the new size.
    st = _state()
    rebuilds = []
    _drive(st, NEW, 30, rebuilds)
    if rebuilds != [NEW]:
        failures.append(f"expected one rebuild at {NEW}, got {rebuilds}")
    if (st.width, st.height) != NEW or (st.mon_w, st.mon_h) != NEW:
        failures.append(f"the state kept the old size: {st.width}x{st.height}")

    # 3. A mode change passing through intermediate sizes rebuilds once, for
    #    the size it settles on - not once per step.
    st = _state()
    rebuilds = []
    steps = [(2560, 1440), (1024, 768), (1920, 1080), (3840, 2160)]

    def moving(t):
        idx = min(int((t - 500.0) / 0.2), len(steps) - 1)
        return steps[idx]

    _drive(st, moving, 30, rebuilds)
    if rebuilds != [NEW]:
        failures.append(f"a mode change in progress produced {rebuilds}")

    # 4. Window mode is not this function's business - follow_window owns it.
    st = _state()
    st.window_hwnd = 0x1234
    rebuilds = []
    _drive(st, NEW, 30, rebuilds)
    if rebuilds:
        failures.append("window mode was rebuilt by the monitor watcher")

    # 5. A dead worker is not revived from here: the overlay stays hidden
    #    (issue #3) until the user turns NR back on.
    st = _state()
    st.worker_failed = True
    rebuilds = []
    _drive(st, NEW, 30, rebuilds)
    if rebuilds:
        failures.append("a failed worker was rebuilt by the monitor watcher")

    # 6. Another display made the main one (Windows 11's way of moving the
    #    taskbar): this monitor keeps its size but its corner moves on the
    #    virtual desktop. One rebuild, and the new corner reaches the overlay
    #    - it used to be ignored, and the picture stayed at the old corner.
    st = _state()
    st.capture.resolution = OLD
    rebuilds = []
    _drive(st, OLD, 30, rebuilds, origin=(-2560, 0))
    if len(rebuilds) != 1:
        failures.append(f"a moved monitor produced {len(rebuilds)} rebuilds, expected 1")
    if tuple(st.mon_origin) != (-2560, 0) or st.origins[-1:] != [(-2560, 0)]:
        failures.append(f"the new corner did not reach the state/overlay: "
                        f"{st.mon_origin} {st.origins}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: a resolution or position change rebuilds once, after it settles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
