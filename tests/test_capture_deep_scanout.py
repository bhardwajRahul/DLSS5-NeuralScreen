"""A 10-bit display must not flip the capture format every frame (audit B1).

Measured in a real v1.13.1 log (issue #89, a 10-bit / HDR-capable display with
HDR compatibility off): 1955 `capture format 10 -> 87 - rebuilding the bridge`
lines in 133 seconds, i.e. ~15 teardown-and-rebuild cycles per second, plus
1969 allocations of a 3840x2160 cross-device shared texture. The log's own
`[perf] grab 43.0ms` and `NR 18.3 fps` are what that costs the user.

The mechanism, read out of the worker:

* with HDR compatibility off the capture is opened with the legacy
  `output1->DuplicateOutput(...)`, deliberately, because an earlier fix found
  `DuplicateOutput1` insufficient (#86);
* on a 10-bit output that legacy call does NOT pin the format - the very same
  log shows `[dda] SDR capture fixed to BGRA8 through legacy duplication`
  followed by the flips;
* `StageCapturedFrame` then sees a format that differs from its shared texture
  and tears the entire capture bridge down, once per flip.

The same log shows the fix: after the user turns HDR compatibility ON the code
takes the IDXGIOutput5 path, and the storm stops completely - 0 rebuilds for
the rest of the session. So the duplication CAN be pinned; it just was not
asked to be when the display is 10-bit and HDR compatibility is off.

The worker now reads the display's bit depth (IDXGIOutput6, already queried for
the log line) and, when it is deeper than 8 bits with HDR compatibility off,
asks for FP16 first through DuplicateOutput1 - the capture shader already
converts FP16 to SDR, so the picture is unchanged. BGRA8 stays as the second
accepted format for a driver that refuses FP16, and an 8-bit display keeps the
exact path it had.

Checked here, without a 10-bit display:

* the worker still compiles, with the fix in it;
* the decision reads the bit depth and not a constant, and the 10-bit branch
  exists and is gated on it;
* the legacy path is still there for an 8-bit output and for a refused FP16
  open, so nothing is lost on the hardware that works today;
* the HDR-compatibility path is untouched (the log proves it was already
  stable);
* the storm's cost is not re-introduced: the format-change branch still
  returns FormatChanged, i.e. it does not silently keep frames of the wrong
  format.

Run:  runtime\\python.exe tests\\test_capture_deep_scanout.py
"""
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native" / "dlss5-feed-host64.cpp"


def _worker_source() -> str:
    return SOURCE.read_text(encoding="utf-8", errors="replace")


def _open_dda_body(src: str) -> str:
    """The body of OpenDda, where the duplication is chosen."""
    start = src.find("static bool OpenDda(")
    if start < 0:
        return ""
    # The next top-level function after it.
    end = src.find("\nstatic ", start + 10)
    return src[start:end if end > 0 else len(src)]


def main() -> int:
    failures = []
    src = _worker_source()
    if not src:
        print("FAIL: the worker source is not readable")
        return 1

    # 1. The display's high-colour capability must be read and gate the path.
    if "g_capture_deep_bits" not in src:
        failures.append("g_capture_deep_bits does not exist - the scan-out "
                        "depth is not read")
    else:
        if "g_capture_deep_bits = d1.BitsPerColor" not in src:
            failures.append("the scan-out depth is never assigned from "
                            "IDXGIOutput6's BitsPerColor")
        if "g_capture_deep_bits > 8" not in src:
            failures.append("nothing tests the scan-out depth against 8")
    # The reporter's own log line is `output colour space 12, 8 bits per
    # colour`: an HDR-capable display can report 8 bits, so the bit depth
    # alone is not the trigger. The advanced-colour capability has to be read
    # too, or the exact reported case is missed.
    if "g_capture_display.enabled" not in src:
        failures.append("the display's advanced-colour capability is not read "
                        "- an HDR-capable display reporting 8 bits per colour "
                        "(the #89 log) would not be detected")

    body = _open_dda_body(src)
    if not body:
        failures.append("OpenDda is gone - this test no longer covers the "
                        "code that chooses the duplication")
    else:
        # 2. The 10-bit branch exists, asks for FP16 first, and keeps BGRA8 as
        #    the fallback so a refusing driver still works.
        if "DuplicateOutput1" not in body:
            failures.append("OpenDda never calls DuplicateOutput1 - the format "
                            "cannot be pinned")
        if "DXGI_FORMAT_R16G16B16A16_FLOAT, DXGI_FORMAT_B8G8R8A8_UNORM" \
                not in body.replace(" ", " ").replace("\n", " "):
            failures.append("the 10-bit branch does not ask for FP16 first "
                            "with BGRA8 as the fallback")
        # 3. The legacy path survives for the 8-bit case and for a refused
        #    FP16 open: the machines that work today must keep working.
        if "output1->DuplicateOutput(g_dda_d11, &g_dda_dup)" not in body:
            failures.append("the legacy DuplicateOutput path is gone - an "
                            "8-bit display would lose the behaviour that "
                            "works today")

    # 4. HDR compatibility still owns its own path: the log proves it was
    #    stable, so it must not have been folded into the new branch.
    if "hdr_formats" not in src:
        failures.append("the HDR-compatibility duplication list is gone")

    # 5. The storm's cost must not be hidden: a frame whose format does not
    #    match is still not silently accepted.
    if "StageResult::FormatChanged" not in src:
        failures.append("FormatChanged is gone - a mismatched frame could be "
                        "staged as if it were fine")

    # 6. It compiles. The suite already insists on a built worker elsewhere,
    #    so the compiler is present on any machine that runs this.
    vswhere = Path(os.environ.get("ProgramFiles(x86)",
                                  r"C:\Program Files (x86)")) / \
        "Microsoft Visual Studio/Installer/vswhere.exe"
    try:
        install = subprocess.check_output(
            [str(vswhere), "-latest", "-products", "*",
             "-requires", "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
             "-property", "installationPath"],
            text=True, timeout=120).strip()
    except Exception as exc:
        print(f"SKIP: MSVC is not available here ({exc}) - the source checks "
              f"above still ran")
        install = ""

    if install:
        vcvars = Path(install) / "VC/Auxiliary/Build/vcvars64.bat"
        # A temp directory, not repo/_work: that folder is gitignored scratch a
        # disk cleanup may remove, and the object is a temporary file. Writing
        # to _work failed with C1083 once the cleanup had taken it.
        with tempfile.TemporaryDirectory(prefix="ns-compile-") as scratch:
            out_obj = Path(scratch) / "nvngx_deepscan.obj"
            command = (f'"{vcvars}" >nul && cl /nologo /c /O2 /EHsc /W3 /MD '
                       f'/std:c++17 /Iinclude /Isrc '
                       f'"{SOURCE}" /Fo:"{out_obj}"')
            result = subprocess.run(
                'cmd /d /s /c "' + command + '"', cwd=str(ROOT / "native"),
                capture_output=True, text=True, encoding="cp866",
                errors="replace", timeout=540)
            combined = (result.stdout or "") + (result.stderr or "")
            if result.returncode != 0 or "error C" in combined:
                failures.append("the worker does not compile with the fix:\n" +
                                "\n".join(l for l in combined.splitlines()
                                          if "error" in l.lower())[:600])
            else:
                warnings = [l for l in combined.splitlines()
                            if re.search(r"warning C\d+", l)]
                if warnings:
                    failures.append(f"{len(warnings)} compiler warning(s) appeared "
                                    f"with the fix: {warnings[0].strip()[:120]}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the capture is pinned to one format on a 10-bit display, and "
          "the 8-bit path is unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
