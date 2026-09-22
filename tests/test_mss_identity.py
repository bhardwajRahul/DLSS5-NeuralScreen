"""The mss fallback must not fabricate a monitor identity.

ScreenCapture._open_mss used to set self.devicename from the POSITIONAL
index - "\\\\.\\DISPLAY{monitor_idx + 1}". DXGI output order and DISPLAYn
numbering are not guaranteed to agree (the module's own docstring says so),
so on an Optimus machine the guessed name can point at ANOTHER monitor: the
overlay origin, NS_OUTPUT and the identity saved to the config all follow
the guess while the capture itself is on the right display.

Expected: the reported devicename is the one the opened monitor really has,
or an empty name (which _apply_monitor_env handles). [audit ui-display]

The earlier version checked the SOURCE TEXT for the synthesis line. Both of
its guards were defeated by a formatting change: writing the same bug as
`DISPLAY{monitor_idx+1}` (no spaces) passed, because the literal it looked
for was gone and it fell back to a "likely fixed" NOTE (audit: WEAK). This
version drives the real _open_mss with a fake mss module and asserts on the
behaviour - the devicename and the resolution the object ends up with.

Run:  runtime\\python.exe tests\\test_mss_identity.py
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

import capture  # noqa: E402


class _MssSession:
    """A fake mss session: monitors[0] is the virtual screen, 1..n physical."""

    def __init__(self, monitors):
        self.monitors = monitors


def _run_open_mss(cap, monitor_idx, wanted_name, monitors, devicename_at):
    """Drive the real _open_mss with the fake session and a fake lookup."""
    import mss as real_mss

    saved_mss = getattr(real_mss, "MSS", None)
    saved_lower = getattr(real_mss, "mss", None)
    saved_lookup = capture._devicename_at
    saved_resolve = capture.resolve_output_idx

    real_mss.MSS = lambda: _MssSession(monitors)
    if saved_lower is not None:
        real_mss.mss = lambda: _MssSession(monitors)
    capture._devicename_at = devicename_at
    capture.resolve_output_idx = lambda name: {
        "\\\\.\\DISPLAY2": 1}.get(name)
    try:
        cap._open_mss(monitor_idx, wanted_name)
    finally:
        real_mss.MSS = saved_mss
        if saved_lower is not None:
            real_mss.mss = saved_lower
        capture._devicename_at = saved_lookup
        capture.resolve_output_idx = saved_resolve


def _fresh():
    cap = capture.ScreenCapture.__new__(capture.ScreenCapture)
    cap._camera = None
    cap._mss = None
    cap.devicename = None
    cap.monitor_idx = 0
    return cap


def main() -> int:
    failures = []

    # The topology the bug is about: the FLAT dxcam index 0 points at the
    # monitor Windows calls DISPLAY2. A positional guess ("DISPLAY1") names
    # the wrong display; the identity lookup names the right one.
    monitors = [
        {"left": 0, "top": 0, "width": 5760, "height": 1080},      # virtual
        {"left": 0, "top": 0, "width": 2560, "height": 1440},      # DISPLAY1
        {"left": 2560, "top": 0, "width": 3840, "height": 2160},   # DISPLAY2
    ]

    def lookup(x, y):
        # Windows' own answer for the corner of that rectangle.
        return "\\\\.\\DISPLAY2" if x >= 2560 else "\\\\.\\DISPLAY1"

    cap = _fresh()
    _run_open_mss(cap, 0, "\\\\.\\DISPLAY2", monitors, lookup)
    print(f"    asked for \\\\.\\DISPLAY2 -> devicename={cap.devicename!r} "
          f"monitor_idx={cap.monitor_idx} "
          f"resolution={getattr(cap, 'resolution', None)}")
    if cap.devicename != "\\\\.\\DISPLAY2":
        failures.append(
            f"the mss fallback reported {cap.devicename!r} for a session "
            f"opened on \\\\.\\DISPLAY2 - a positional guess names another "
            f"monitor, and the overlay origin and the saved config follow it")
    if getattr(cap, "resolution", None) != (3840, 2160):
        failures.append(f"the resolution followed the wrong monitor: "
                        f"{getattr(cap, 'resolution', None)}")

    # No identity available (a point on no monitor): the name must be EMPTY,
    # not a guess that looks like knowledge.
    cap2 = _fresh()
    _run_open_mss(cap2, 0, "", monitors, lambda x, y: "")
    print(f"    no identity available -> devicename={cap2.devicename!r}")
    if cap2.devicename:
        failures.append(
            f"with no identity lookup result the fallback produced "
            f"{cap2.devicename!r} - an empty name is the honest answer")

    # And the synthesis shape must not come back in the source, in ANY
    # spacing: that is the guard the old literal-string check could be
    # formatted around.
    src = (BASE / "app" / "capture.py").read_text(encoding="utf-8")
    if re.search(r"DISPLAY\s*\{\s*monitor_idx", src):
        failures.append(
            "capture.py builds a devicename from the positional index again - "
            "DXGI order != DISPLAYn numbering makes that name another monitor")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the mss fallback reports the identity it really opened")
    return 0


if __name__ == "__main__":
    sys.exit(main())
