"""The release contract's own lists must not be editable in silence (audit H6).

MANDATORY_FILES and REQUIRED_RUNTIME_ARTIFACTS drive two gates that are
self-referential:

* membership is what makes an untracked file acceptable to
  _assert_mandatory_inputs, and
* membership is what keeps a file that _skip() would drop inside the package
  (`rel not in mandatory` in _package_paths).

Nothing else records them. runtime-manifest.json carries the resulting
inventory, so after a regeneration everything agrees - the digest, the file
count, the ZIP, both SHA256SUMS, the verifier's inventory check and the Git
binding. A check of the manifest at HEAD shows no key mentioning "mandatory",
and verify_github.py has no copy of either list to cross-check the builder
against (grep MANDATORY: 0 hits).

So removing one line ships a release without an input the contract calls
mandatory, with a self-consistent and fully verified manifest. The concrete
candidate is already in the list: native/libraries/README.md is dropped by
_skip() and enters the package only because of its mandatory entry - delete
that entry and the file leaves the release and the inventory, and every gate
still passes. The fixture tests in tests/test_release_contract.py build the
contract from those same two lists, so they cannot notice either.

This test holds the lists to their expected content. It is deliberately a
literal second copy: the point is that changing the contract has to be a
deliberate edit in two places, and the review sees it.

Checked here:

* the exact set of MANDATORY_FILES, including the entry that only the list
  keeps in the package;
* the exact set of REQUIRED_RUNTIME_ARTIFACTS;
* every mandatory path is really in the repository (a typo would otherwise
  fail only at release time, on a clean checkout);
* the two lists agree about the runtime binaries they both name;
* THIRD_PARTY_NOTICES and RUNTIME_MANIFEST keep the names the builder pins.

Run:  runtime\\python.exe tests\\test_release_lists_pinned.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import build_release_zip as builder  # noqa: E402

#: The contract, by hand. Adding a file to a release means adding it here too;
#: that is the point (a silent removal is what this exists to catch).
EXPECTED_MANDATORY = {
    "config.default.json",
    "native/libraries/README.md",
    "native/nvngx_dlssg.dll",
    "app/resolution_limits.py",
    "NeuralScreen.exe",
    "NeuralScreen.vbs",
    "NeuralScreen-diag.vbs",
    "README.md",
    "README.ru.md",
    "LICENSE",
    "native/nvngx.dll",
    "native/nvngx.dll_ns-forwarder.dll",
    "native/nvngx_dlssnr.dll",
    "native/Spout.dll",
    "native/SpoutDX.dll",
    "runtime-manifest.json",
    "THIRD-PARTY-NOTICES.md",
}

EXPECTED_RUNTIME_ARTIFACTS = {
    "NeuralScreen.exe",
    "native/nvngx.dll",
    "native/nvngx.dll_ns-forwarder.dll",
    "native/nvngx_dlssg.dll",
    "native/nvngx_dlssnr.dll",
    "native/Spout.dll",
    "native/SpoutDX.dll",
    "runtime/python.exe",
    "runtime/pythonw.exe",
}

#: Entries that exist as a build input rather than as a tracked file: they are
#: produced by the build (the worker, the launcher, the pinned manifest).
GENERATED = {"NeuralScreen.exe", "runtime-manifest.json"}


def main() -> int:
    failures = []

    mandatory = set(builder.MANDATORY_FILES)
    if mandatory != EXPECTED_MANDATORY:
        missing = EXPECTED_MANDATORY - mandatory
        added = mandatory - EXPECTED_MANDATORY
        if missing:
            failures.append(
                f"MANDATORY_FILES lost {sorted(missing)} - a file the contract "
                f"calls mandatory can now leave the release with a manifest "
                f"that still verifies")
        if added:
            failures.append(
                f"MANDATORY_FILES gained {sorted(added)} - the contract "
                f"changed; update this list in the same commit so the change "
                f"is deliberate")

    artifacts = set(builder.REQUIRED_RUNTIME_ARTIFACTS)
    if artifacts != EXPECTED_RUNTIME_ARTIFACTS:
        missing = EXPECTED_RUNTIME_ARTIFACTS - artifacts
        added = artifacts - EXPECTED_RUNTIME_ARTIFACTS
        if missing:
            failures.append(
                f"REQUIRED_RUNTIME_ARTIFACTS lost {sorted(missing)} - the "
                f"runtime tree digest no longer pins it")
        if added:
            failures.append(
                f"REQUIRED_RUNTIME_ARTIFACTS gained {sorted(added)}")

    # A mandatory entry that is not in the repository and is not build output
    # would fail only at release time, on a clean checkout.
    for rel in sorted(mandatory - GENERATED):
        if not (BASE / rel).is_file():
            failures.append(f"a mandatory entry is not in the repository: "
                            f"{rel} - the release would fail on a clean tree")

    # The runtime binaries both lists name must match, or one of them is a typo.
    overlap = mandatory & artifacts
    for rel in sorted(overlap):
        if rel not in EXPECTED_MANDATORY or rel not in EXPECTED_RUNTIME_ARTIFACTS:
            failures.append(f"the two lists disagree about {rel}")

    # The names the builder pins elsewhere must keep agreeing with the lists.
    for name, value, where in (
        ("RUNTIME_MANIFEST", builder.RUNTIME_MANIFEST, "runtime-manifest.json"),
        ("THIRD_PARTY_NOTICES", builder.THIRD_PARTY_NOTICES,
         "THIRD-PARTY-NOTICES.md"),
        ("CHECKSUMS", builder.CHECKSUMS, "SHA256SUMS"),
    ):
        if value != where:
            failures.append(f"{name} is {value!r}, expected {where!r}")
        if value in mandatory and value != where:
            failures.append(f"{name} is mandatory under a different name")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the release contract's lists are pinned, and every mandatory "
          "input is present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
