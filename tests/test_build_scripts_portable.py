"""Build scripts must not hardcode where a compiler lives.

Why this exists: every `native\\*.bat` used to call

    call "C:\\Program Files (x86)\\Microsoft Visual Studio\\2022\\BuildTools\\
          VC\\Auxiliary\\Build\\vcvars64.bat" >nul

a path that exists only on the machine those scripts were written on. On any
other box - VS Community/Professional/Enterprise, BuildTools on another drive -
the script died with "The system cannot find the path specified." and then
"'cl' is not recognized", several lines away from the actual problem. The
call also discarded its own failure (`>nul`, no check), which is why the error
surfaced as a missing compiler rather than a missing vcvars.

That was fixed by resolving the toolchain with `vswhere` through one shared
`native\\vcvars.bat`. Nothing stopped the next script from reintroducing the
literal path - and it happened once already (test-quality.bat came in from
upstream with the old line still in it).

What this locks:
  * no `native\\*.bat` contains an absolute path into "Program Files";
  * every script that needs the MSVC environment goes through `vcvars.bat`
    and CHECKS the result, instead of calling a path and hoping;
  * the two files that do the resolving exist and are the ones referenced.

Checked by reading the scripts, not by running them: a missing compiler must
not be able to make this test pass.

Run:  runtime\\python.exe tests\\test_build_scripts_portable.py
"""
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
NATIVE = BASE / "native"

#: A literal Windows path into Program Files in a .bat. Build scripts resolve
#: the toolchain instead; a hardcoded one only works on the author's machine.
HARDCODED = re.compile(r"[A-Za-z]:\\Program Files", re.IGNORECASE)

#: Scripts that compile something and therefore need the MSVC environment.
#: build-clang.bat is deliberately NOT here: it locates clang-cl and lld-link
#: itself and needs no vcvars (see the header of that file).
NEEDS_VCVARS = (
    "build-host.bat",
    "build-launcher.bat",
    "build-spout-adapter.bat",
    "build-spout-check.bat",
    "build-spout-test.bat",
    "test-hdr.bat",
    "test-quality.bat",
)


def main() -> int:
    failures = []

    scripts = sorted(NATIVE.glob("*.bat"))
    if not scripts:
        print(f"FAIL: no build scripts found in {NATIVE} - this test no longer "
              f"covers what it exists for")
        return 1

    # ---- no hardcoded Program Files path anywhere --------------------
    for path in scripts:
        text = path.read_text(encoding="utf-8", errors="replace")
        for num, line in enumerate(text.splitlines(), 1):
            if line.lstrip().lower().startswith("rem"):
                continue          # documentation may quote the old path
            if HARDCODED.search(line):
                failures.append(
                    f"{path.name}:{num} hardcodes a compiler path: "
                    f"{line.strip()[:90]} - resolve it with vswhere instead, "
                    f"otherwise the script only builds on one machine")

    # ---- the resolvers exist -----------------------------------------
    for helper in ("vcvars.bat", "vswhere.bat"):
        if not (NATIVE / helper).is_file():
            failures.append(f"{helper} is missing - the scripts below cannot "
                            f"resolve a toolchain")

    # ---- every compiling script goes through vcvars, and checks it ----
    for name in NEEDS_VCVARS:
        path = NATIVE / name
        if not path.is_file():
            failures.append(f"{name} is missing")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        calls = [l for l in text.splitlines()
                 if "vcvars" in l.lower()
                 and not l.lstrip().lower().startswith("rem")]
        if not calls:
            failures.append(
                f"{name} does not call vcvars.bat - it must not resolve the "
                f"toolchain on its own")
            continue
        # The call has to fail the script when the toolchain is missing: with
        # no check, the build continues and dies later with a confusing
        # "'cl' is not recognized".
        checked = any("||" in l and "exit" in l.lower() for l in calls)
        if not checked:
            failures.append(
                f"{name} calls vcvars without checking the result: "
                f"{calls[0].strip()[:80]} - a missing compiler must fail here, "
                f"not several lines later")

    # ---- nothing reaches into a specific Visual Studio edition --------
    # A path built from %VSWHERE% / %VSPATH% is what we want; naming a
    # particular edition (Community/Professional/Enterprise) is the same
    # assumption in a new place.
    for path in scripts:
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.lstrip().lower().startswith("rem"):
                continue
            for edition in ("Community", "Professional", "Enterprise"):
                if re.search(rf"\\{edition}\\", line, re.IGNORECASE):
                    failures.append(
                        f"{path.name} names the {edition} edition: "
                        f"{line.strip()[:80]} - build tools may be installed "
                        f"under any edition")

    for f in failures[:15]:
        print("FAIL:", f)
    if len(failures) > 15:
        print(f"... and {len(failures) - 15} more")
    if failures:
        return 1
    print(f"OK: {len(scripts)} build scripts resolve their toolchain, none "
          f"hardcodes a Visual Studio path, and every compiling script checks "
          f"that vcvars succeeded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
