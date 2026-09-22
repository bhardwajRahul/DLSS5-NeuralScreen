"""Multipass at native resolution: the work size steps aside so the cascade runs.

Reported in #110: with the processing-resolution slider at native, NR passes
did nothing. The cascade ping-pongs between two work-resolution buffers and
lands in the one the residual composite reads; at 1:1 none of that exists, the
network writes the full-size output directly, and the worker runs one pass
whatever the panel says. So `_work_size` steps a native work size down by the
smallest even amount (2 px) whenever more than one pass is asked for - which
also gains detail: the composite keeps the native frame as its anchor.

Checked here, without a GPU:

1. native + more than one pass -> a work size below the frame on BOTH axes,
   even, and no more than 2 px below it;
2. one pass, or a work size already below native (a scale under 1, the NGX
   cap), is left exactly as it was;
3. a frame too small to step down from keeps its size (the floor wins);
4. every call that sizes a worker hands the pass count in. A call that drops
   it is a path - a monitor switch, one-window mode, a conversion - where
   multipass would quietly do nothing again;
5. ...and only the count Boost allows. The cascade runs only under Boost (the
   panel hides the count with the switch off), so with Boost off a saved
   count of four must size the work like one: stepping aside there bought
   nothing and moved Boost-off users off the 1:1 path. The live callers go
   through `cascade_passes(st)`; the converter's `processing_size` is checked
   by what it returns.

tests/test_cascade_native.py checks the worker's end of it on the GPU.

Run:  runtime\\python.exe tests\\test_cascade_work_size.py
"""
import ast
import sys
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

from media_convert import processing_size  # noqa: E402
from settings_io import _work_size, cascade_passes  # noqa: E402

#: The modules that size a worker. A new one belongs here too.
CALLERS = ("commands.py", "media_convert.py", "pipeline.py", "startup.py")
#: The ones sizing from the app state, where the count must come from
#: cascade_passes(st) rather than straight from the saved setting.
LIVE_CALLERS = ("commands.py", "pipeline.py", "startup.py")


def main() -> int:
    failures = []

    # 1-2. The sizes that matter: common monitors, a window with odd sides,
    # and frames past the NGX cap (their work size is below native already).
    for width, height in ((2560, 1440), (1920, 1080), (1280, 720), (960, 540),
                          (901, 539), (3440, 1440), (3840, 2160)):
        one = _work_size(width, height, 1.0, 1)
        for passes in (2, 4):
            many = _work_size(width, height, 1.0, passes)
            if one == (width, height):
                w, h = many
                if not (w < width and h < height):
                    failures.append(f"{width}x{height}, {passes} passes: work "
                                    f"{many} is not below the frame - the "
                                    f"cascade would not run")
                elif w % 2 or h % 2 or width - w > 3 or height - h > 3:
                    failures.append(f"{width}x{height}, {passes} passes: work "
                                    f"{many} is not the smallest even step down")
            elif many != one:
                failures.append(f"{width}x{height}: already below native at "
                                f"{one}, but {passes} passes changed it to {many}")
        for scale in (0.5, 0.65, 0.85):
            if _work_size(width, height, scale, 4) != _work_size(width, height, scale, 1):
                failures.append(f"{width}x{height} at {scale}: the pass count "
                                f"changed a work size that was already below native")
    if _work_size(1920, 1080, 1.0, 1) != (1920, 1080):
        failures.append("one pass at native no longer runs at native")

    # 3. The floor.
    if _work_size(64, 64, 1.0, 4) != (64, 64):
        failures.append(f"a 64x64 frame stepped below the floor: "
                        f"{_work_size(64, 64, 1.0, 4)}")

    # 4-5. Every caller passes the count, and the live ones the count Boost
    # allows.
    for name in CALLERS:
        tree = ast.parse((BASE / "app" / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_work_size"):
                continue
            passes = node.args[3] if len(node.args) >= 4 else next(
                (k.value for k in node.keywords if k.arg == "nr_passes"), None)
            if passes is None:
                failures.append(f"{name}:{node.lineno}: _work_size without "
                                f"the pass count - multipass at native does "
                                f"nothing on that path")
            elif name in LIVE_CALLERS and not (
                    isinstance(passes, ast.Call)
                    and getattr(passes.func, "id",
                                getattr(passes.func, "attr", "")) == "cascade_passes"):
                failures.append(f"{name}:{node.lineno}: _work_size takes the "
                                f"saved count, not cascade_passes(st) - with "
                                f"Boost off it steps aside for passes that "
                                f"cannot run")

    # 5. What Boost allows, from both ends.
    for boost, saved, expect in ((False, 4, 1), (False, 2, 1), (False, None, 1),
                                 (True, 4, 4), (True, 1, 1), (True, None, 1)):
        st = SimpleNamespace(nr_small=boost)
        if saved is not None:
            st.nr_passes = saved
        if cascade_passes(st) != expect:
            failures.append(f"Boost {'on' if boost else 'off'}, {saved} saved: "
                            f"cascade_passes gave {cascade_passes(st)}, not {expect}")
    for width, height in ((1920, 1080), (1280, 720)):
        off = processing_size(width, height, 1.0, False, 4)
        if off != (width, height):
            failures.append(f"a {width}x{height} conversion with Boost off and "
                            f"4 passes runs at {off}, not the frame")
        on = processing_size(width, height, 1.0, True, 4)
        if on != _work_size(width, height, 1.0, 4):
            failures.append(f"a {width}x{height} conversion with Boost on and "
                            f"4 passes runs at {on}, not the live size")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: native + passes steps the work size 2 px aside under Boost, "
          "nothing else moves, and every caller passes the count Boost allows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
