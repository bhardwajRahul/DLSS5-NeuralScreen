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
   multipass would quietly do nothing again.

tests/test_cascade_native.py checks the worker's end of it on the GPU.

Run:  runtime\\python.exe tests\\test_cascade_work_size.py
"""
import ast
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from settings_io import _work_size  # noqa: E402

#: The modules that size a worker. A new one belongs here too.
CALLERS = ("commands.py", "media_convert.py", "pipeline.py", "startup.py")


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

    # 4. Every caller passes the count.
    for name in CALLERS:
        tree = ast.parse((BASE / name).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "_work_size"):
                keywords = {k.arg for k in node.keywords}
                if len(node.args) < 4 and "nr_passes" not in keywords:
                    failures.append(f"{name}:{node.lineno}: _work_size without "
                                    f"the pass count - multipass at native does "
                                    f"nothing on that path")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: native + passes steps the work size 2 px aside, nothing else moves, "
          "and every caller passes the count")
    return 0


if __name__ == "__main__":
    sys.exit(main())
