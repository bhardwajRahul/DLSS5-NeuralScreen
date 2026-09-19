"""--test must evaluate through the same runtime that created the feature.

`--test` drives the worker with no client and reports "N/300 evaluates
succeeded". It reported **0/300** for a long time while the live pipeline was
perfectly healthy, because the two halves of the self-test disagreed:

* the feature was created through the DLSSNR runtime (`g_nr_create`, which is
  `NVSDK_NGX_D3D12_CreateFeature` from nvngx_dlssnr.dll);
* it was evaluated through the NGX CORE wrapper (`NGX_D3D12_EVALUATE_DLSS_EXT`
  from the NGX headers) - a different implementation that knows nothing about a
  handle the runtime owns, so every evaluate answered 0xBAD00004
  (FeatureNotFound).

The live path never had this problem: `EvaluateVideo` has always called
`g_nr_evaluate`. The self-test now does too, and this checks the source so the
two cannot drift apart again:

1. `Evaluate` calls `g_nr_evaluate` and does NOT call the core wrapper;
2. the core wrappers (`SafeEvaluateDLSS`/`SafeCreateDLSS`) are gone - their
   only caller was the broken pair, and leaving them invites the same mistake;
3. the parameter block is shared (`ApplyNrEvalParams`), so the self-test sets
   the same names the live path sets instead of its own subset;
4. `--test` fills the shipped profile (`SetTestVideoParams`) - the read-back
   check compares against real values, and an all-zero profile would report a
   contract failure that exists only in the self-test.

Provable by mutation: point `Evaluate` back at the core wrapper, or drop the
profile call, and the matching check goes red.

Run:  runtime\\python.exe tests\\test_self_test_uses_runtime.py
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native" / "dlss5-feed-host64.cpp"


def without_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def body_after(text: str, marker: str) -> str:
    """The function body from the DEFINITION of `marker`.

    Forward declarations appear before the definition, and taking the first hit
    lands in a declaration whose next `{` belongs to a different function. The
    definition is the last occurrence.
    """
    start = text.rfind(marker)
    if start < 0:
        return ""
    brace = text.find("{", start)
    if brace < 0:
        return ""
    rest = text[brace:]
    nxt = re.search(r"\nstatic [A-Za-z_].*\(", rest)
    return rest[:nxt.start()] if nxt else rest


def main() -> int:
    src = without_comments(SOURCE.read_text(encoding="utf-8", errors="replace"))
    if not src:
        print("FAIL: the worker source is not readable")
        return 1
    failures = []

    evaluate = body_after(src, "static bool Evaluate(ID3D12Resource *color")
    if not evaluate:
        failures.append("Evaluate() is gone - the self-test path cannot be checked")
    else:
        if "g_nr_evaluate" not in evaluate:
            failures.append("--test evaluates through something other than the "
                            "runtime that created the feature: it will answer "
                            "FeatureNotFound while the live path is fine")
        if "NGX_D3D12_EVALUATE_DLSS_EXT" in evaluate:
            failures.append("Evaluate() calls the NGX CORE wrapper again - that "
                            "is the 0/300 bug: the handle belongs to the DLSSNR "
                            "runtime, not to the core")
        if "ApplyNrEvalParams" not in evaluate:
            failures.append("the self-test builds its own parameter block "
                            "instead of the shared one: it can silently test a "
                            "different contract than the program runs")

    for gone in ("SafeEvaluateDLSS", "SafeCreateDLSS"):
        if re.search(r"\b" + gone + r"\s*\(", src):
            failures.append(f"{gone} is back: the core wrapper has no caller "
                            "on any working path and invites the same mistake")

    shared = body_after(src, "static void ApplyNrEvalParams(")
    if not shared:
        failures.append("ApplyNrEvalParams() is gone: the live and self-test "
                        "parameter blocks are no longer the same code")
    else:
        for name in ("DLSSNR.Color", "DLSSNR.Output", "DLSSNR.MVec",
                     "DLSSNR.ColorSubrectWidth", "DLSSNR.Reset"):
            if name not in shared:
                failures.append(f"the shared parameter block lost {name}")

    test_body = body_after(src, "static int RunTest()")
    if not test_body:
        failures.append("RunTest() is gone")
    elif "SetTestVideoParams()" not in test_body:
        failures.append("--test no longer arms the shipped profile: the "
                        "read-back check compares against zeros and reports a "
                        "failure that exists only in the self-test")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: --test creates and evaluates through the same runtime as the "
          "live path, with the shared parameter block and a real profile")
    return 0


if __name__ == "__main__":
    sys.exit(main())
