"""The release document set has one meaning in two programs.

`build_release_zip.py` emits four artifacts; the four documents (README and
TECHNICAL, EN and RU) are payload inside the archive but have to be attached
to the GitHub release as separate assets as well. Two independent lists drive
that - `RELEASE_DOCUMENTS` in the builder and in the verifier - and they only
work if they agree:

* if the verifier expects a document the builder does not name, the release
  command in RELEASING.md is short one asset and the verifier fails with
  "release vX is missing asset README.md" AFTER the release is published;
* if the builder names one the verifier does not expect, a correctly published
  release is reported as "carries N asset(s) beyond the release set".

Neither program imports the other (the verifier is deliberately standalone:
it must run against a checkout where the builder may have moved on), so the
agreement is checked here instead of at runtime.

Also checked: the documents are tracked files (an untracked document cannot be
in the tagged blob the verifier compares against), and they are part of the
package payload - the archive the verifier downloads must contain them.

Run:  runtime\\python.exe tests\\test_release_documents_agree.py
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))  # the modules live in app/

import build_release_zip as builder  # noqa: E402
import verify_github as verifier  # noqa: E402


def main() -> int:
    failures = []

    built = tuple(builder.RELEASE_DOCUMENTS)
    checked = tuple(verifier.RELEASE_DOCUMENTS)
    if built != checked:
        failures.append(
            f"the two document lists disagree: builder={built}, verifier={checked} "
            "- a release cut from one of them is missing an asset the other expects")

    # The four documents are exactly the four bilingual pairs; a fifth entry
    # would silently widen the asset set the verifier allows.
    if len(set(built)) != len(built):
        failures.append(f"the document list has duplicates: {built}")
    if len(built) != 4:
        failures.append(f"the document list is {len(built)} entries, expected 4")

    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace").stdout.split()
    for name in built:
        if name not in tracked:
            failures.append(f"{name} is listed but not tracked: the verifier "
                            "compares it against the tagged blob and would "
                            "report 'missing from local tag'")
        if not (ROOT / name).is_file():
            failures.append(f"{name} is listed but missing from the working tree")

    # The documents must also travel inside the archive (they are payload), and
    # the manifest's source inventory is what proves it: every tracked shipped
    # file is listed there with its blob. A document that is tracked but absent
    # from the inventory would be a payload gap. (They are not all in
    # MANDATORY_FILES: TECHNICAL.md is a shipped document but not a file whose
    # absence must stop the build, which is a different contract.)
    import json
    manifest = ROOT / "runtime-manifest.json"
    if manifest.is_file():
        inventory = {r["path"] for r in json.loads(
            manifest.read_text(encoding="utf-8"))["source_inventory"]}
        for name in built:
            if name not in inventory:
                failures.append(f"{name} is not in the manifest source inventory: "
                                "it would not be in the archive payload either")
    else:
        failures.append("runtime-manifest.json is missing; the packaged document "
                        "set cannot be checked")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the builder and the verifier name the same four release "
          "documents, and all four are tracked, present and inventoried")
    return 0


if __name__ == "__main__":
    sys.exit(main())
