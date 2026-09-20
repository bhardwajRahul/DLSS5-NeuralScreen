r"""Every DLSSNR parameter the worker sets must exist in the runtime.

An NGX parameter block is a map keyed by the name string: the runtime looks
a parameter up by the literal we passed, so a name that does not appear
anywhere in nvngx_dlssnr.dll cannot be read. Setting one is not an error
that anything reports - the value simply goes into the map and is never
fetched, and the code around it reads as if the network had been told
something.

That is how eight of them accumulated in CreateFeature: InputWidth,
InputHeight, OutputWidth, OutputHeight, Output.Width, Output.Height,
Upscaling and Scale. The code read as though the feature took a full-res
input and scaled it down itself; the network was never told any such thing,
which is the same fact TECHNICAL.md reaches by measurement from the other
end (the network enhances, it does not upscale).

This scans the runtime for every "DLSSNR.*" literal it carries and the
worker source for every one it sets, and fails on a name the runtime does
not have. It is a spelling check with teeth: "DLSSNR.Output.Width" and
"DLSSNR.OutputSubrectWidth" differ by a plausible typo, and only one of
them is real.

Skipped, not failed, when the runtime is not in the tree: a source checkout
without the 158 MB DLL is a normal state, and a test that cannot look must
say so rather than pass quietly.

Run:  runtime\python.exe tests\test_ngx_params_exist.py
"""
import io
import re
import sys
from pathlib import Path


def _repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "main.py").is_file():
            return p
    return start


BASE = _repo_root(Path(__file__).resolve().parent)
RUNTIME = BASE / "native" / "nvngx_dlssnr.dll"
SOURCES = (BASE / "native" / "dlss5-feed-host64.cpp",)

#: A name the worker sets deliberately although the runtime has no literal
#: for it. Empty, and it should stay that way - an entry here needs the
#: measurement that shows the parameter does something anyway.
EXPECTED_ABSENT: dict[str, str] = {}


def main() -> int:
    if not RUNTIME.is_file():
        print(f"SKIP: {RUNTIME.relative_to(BASE).as_posix()} is not in the "
              f"tree - nothing to compare the names against")
        return 0

    blob = RUNTIME.read_bytes()
    in_runtime = set(m.group(0).decode("ascii") for m in
                     re.finditer(rb"DLSSNR\.[A-Za-z0-9_.]{1,40}", blob))
    if len(in_runtime) < 20:
        print(f"FAIL: only {len(in_runtime)} DLSSNR names found in "
              f"{RUNTIME.name} ({len(blob)} bytes) - the scan itself looks "
              f"broken, and a broken scan would pass everything")
        return 1

    failures = []
    for path in SOURCES:
        src = io.open(path, encoding="utf-8").read()
        used = {}
        for m in re.finditer(r'Set\("(DLSSNR\.[A-Za-z0-9_.]{1,40})"', src):
            used.setdefault(m.group(1), src.count(m.group(0)))
        if not used:
            failures.append(
                f"{path.name}: no DLSSNR parameters found at all - the "
                f"pattern this test reads the source with has gone stale")
            continue
        for name in sorted(used):
            if name in in_runtime or name in EXPECTED_ABSENT:
                continue
            failures.append(
                f"{path.name} sets {name!r}, which does not appear anywhere "
                f"in {RUNTIME.name}: the runtime looks parameters up by that "
                f"string, so nothing ever reads it")
        print(f"    {path.name}: {len(used)} DLSSNR parameters set, "
              f"{len(in_runtime)} carried by the runtime")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: every DLSSNR parameter the worker sets exists in the runtime")
    return 0


if __name__ == "__main__":
    sys.exit(main())
