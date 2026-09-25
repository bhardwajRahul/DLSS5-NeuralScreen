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


def _drive(st, size, ticks, rebuilds, step=0.1, origin=(0, 0),
           live=None, switches=None, by_idx=None):
    clock = {"t": 500.0}
    real = (pipeline.monitor_size, pipeline.teardown_pipeline,
            pipeline.rebuild_pipeline, pipeline.ScreenCapture,
            pipeline._refresh_dxcam_factory, pipeline.time,
            pipeline.monitor_origin, pipeline.resolve_output_idx,
            startup._apply_monitor_env, pipeline.list_monitors,
            pipeline.switch_monitor)
    # The corner is the test's, not this machine's monitor layout.
    pipeline.monitor_origin = lambda name: origin
    pipeline.resolve_output_idx = lambda name: 0
    startup._apply_monitor_env = lambda capture: origin
    pipeline.teardown_pipeline = lambda s: None
    pipeline._refresh_dxcam_factory = lambda: None
    pipeline.time = types.SimpleNamespace(monotonic=lambda: clock["t"])

    if by_idx is not None:
        # A monitor LAYOUT: index -> (devicename, size or None when the
        # display is gone). The capture is resolved through it, exactly as
        # ScreenCapture resolves a real one, so a switch really moves the
        # pipeline to the new monitor - the stub must not keep pointing at
        # the dead one, or it would measure its own lie.
        pipeline.monitor_size = lambda name: next(
            (sz for _dn, sz in by_idx.values() if _dn == name), None)
        pipeline.list_monitors = lambda: [
            (i, (sz or (0, 0))[0], (sz or (0, 0))[1], dn)
            for i, (dn, sz) in sorted(by_idx.items())]
        pipeline.ScreenCapture = lambda monitor_idx=0: types.SimpleNamespace(
            devicename=by_idx[int(monitor_idx)][0],
            resolution=by_idx[int(monitor_idx)][1],
            monitor_idx=monitor_idx, close=lambda: None)

        def _switch(s, target):
            switches.append(target)
            i = next(i for i, (dn, _sz) in by_idx.items() if dn == target)
            s.monitor = i
            s.capture = types.SimpleNamespace(
                devicename=target, resolution=by_idx[i][1],
                monitor_idx=i, close=lambda: None)
            s.width, s.height = by_idx[i][1]
        pipeline.switch_monitor = _switch
    else:
        pipeline.monitor_size = (lambda name: size(clock["t"])
                                 if callable(size) else size)
        # What Windows reports as connected, for the "the monitor is gone" path.
        pipeline.list_monitors = lambda: list(live or [])
        if switches is not None:
            pipeline.switch_monitor = lambda s, target: switches.append(target)
        # A fresh capture opens at the size the monitor has at that moment.
        pipeline.ScreenCapture = lambda monitor_idx=0: types.SimpleNamespace(
            devicename=r"\\.\DISPLAY1",
            resolution=size(clock["t"]) if callable(size) else size,
            monitor_idx=monitor_idx, close=lambda: None)

    pipeline.rebuild_pipeline = lambda s, note: rebuilds.append((s.width, s.height))
    try:
        for _ in range(ticks):
            clock["t"] += step
            pipeline.follow_monitor(st)
    finally:
        (pipeline.monitor_size, pipeline.teardown_pipeline,
         pipeline.rebuild_pipeline, pipeline.ScreenCapture,
         pipeline._refresh_dxcam_factory, pipeline.time,
         pipeline.monitor_origin, pipeline.resolve_output_idx,
         startup._apply_monitor_env, pipeline.list_monitors,
         pipeline.switch_monitor) = real


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

    # 7. The captured monitor is GONE (#128): unplugged, a dock removed, the
    #    display switched off in Windows. monitor_size answers None, and the
    #    pipeline used to simply return - it kept running against a display
    #    DXGI no longer exposes, while the worker refused the output on its
    #    next reopen, until the user quit the program. It must move to a
    #    monitor that IS there, once, after the same half-second debounce.
    st = _state()
    rebuilds, switches = [], []
    _drive(st, None, 30, rebuilds, switches=switches, by_idx={
        0: (r"\\.\DISPLAY1", None),          # unplugged
        1: (r"\\.\DISPLAY2", (1920, 1080)),
    })
    if switches != [r"\\.\DISPLAY2"]:
        failures.append(f"a vanished monitor switched {switches}, expected "
                        f"['\\\\\\\\.\\\\DISPLAY2'] once")
    if (st.width, st.height) != (1920, 1080):
        failures.append(f"the pipeline did not move to the live monitor: "
                        f"{st.width}x{st.height}")

    # 8. ... and it does NOT switch while the display is only briefly absent:
    #    a mode change hides the monitor for a moment, and a rebuild there
    #    would be paid for nothing. Three ticks of 0.1 s are inside the
    #    half-second debounce.
    st = _state()
    rebuilds, switches = [], []
    _drive(st, None, 3, rebuilds, step=0.1, switches=switches, by_idx={
        0: (r"\\.\DISPLAY1", None),
        1: (r"\\.\DISPLAY2", (1920, 1080)),
    })
    if switches:
        failures.append(f"a momentary absence switched monitors: {switches}")

    # 9. Nothing else is connected: there is no live monitor to move to, and
    #    the honest answer is to say so rather than switch to a dead index.
    st = _state()
    rebuilds, switches = [], []
    _drive(st, None, 30, rebuilds, switches=switches, by_idx={
        0: (r"\\.\DISPLAY1", None),
    })
    if switches:
        failures.append(f"switched to a monitor that does not exist: {switches}")
    if st.capture.devicename != r"\\.\DISPLAY1":
        failures.append(f"the capture left the dead monitor anyway: "
                        f"{st.capture.devicename}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: a resolution or position change rebuilds once, after it settles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
