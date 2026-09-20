r"""Every shipped setting is either in the diagnostic report or excluded on purpose.

THE BUG (#96, found 20.09.2026 in a reporter's v2.0.0 package)

The settings section of a diagnostic package exists because v1.16.0 could not
answer "how was the program configured" from a report. It is built from an
explicit allow-list, `diagnostics._SETTINGS_KEYS` - and nothing ever checked
that list against the settings the product actually ships. Four releases of
new controls went in without it: the reporter's 2.0.0 package came back with
`nr_passes: None`, `fps_overlay: None`, `menu_scale: None`, so a bug report
from a 2.0 user could not say whether the cascade was running, where the
on-screen counter sat, or how big the panel had been made. `nr_direct` had
been missing for longer than that.

The section had quietly stopped doing the one job it was added for, and there
was no way to notice except by reading a package by hand.

WHAT THIS LOCKS

Every key in `config.default.json` is either reported or listed below as
deliberately withheld, with the reason. A new setting fails this test until
somebody decides which it is - which is the whole point: the decision is
cheap, forgetting is not.

The exclusions are a privacy rule, not an oversight. Binding choices and
paths do not belong in a bundle even scrubbed, and a product snapshot that
quietly grew a personal field would be a privacy bug rather than a feature.

Run:  runtime\python.exe tests\test_diag_settings_complete.py
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import diagnostics  # noqa: E402

#: Withheld on purpose, and why. Personal data or noise, never "we forgot".
WITHHELD = {
    "hotkeys": "the user's own key bindings",
    "presets": "the user's own saved parameter sets",
    "recording_dir": "a filesystem path",
    "screenshot_dir": "a filesystem path",
    "menu_offset": "where the user dragged the panel - geometry, not config",
    "menu_height": "the same",
    "width": "the output resolution, already in the log header and `monitor`",
    "height": "the same",
    "schema_version": "the config contract, reported in its own field",
}


def main() -> int:
    failures: list[str] = []
    cfg = json.loads(io.open(BASE / "config.default.json",
                             encoding="utf-8").read())
    allow = set(diagnostics._SETTINGS_KEYS)
    shipped = set(cfg)

    missing = sorted(shipped - allow - set(WITHHELD))
    for key in missing:
        failures.append(
            f"`{key}` ships in config.default.json but is neither reported "
            f"nor listed as withheld: a bug report cannot say what it was "
            f"set to. Add it to diagnostics._SETTINGS_KEYS, or to WITHHELD "
            f"here with the reason it is personal")

    # A key withheld that nobody ships any more is dead paperwork.
    stale = sorted(set(WITHHELD) - shipped)
    for key in stale:
        failures.append(f"`{key}` is listed as withheld but is not shipped")

    # And the allow-list must not name settings that do not exist: a typo
    # there is silent, the key simply never appears in a package.
    KNOWN_ABSENT = {
        # Real parameters that live under `params`, not at the config root.
        "auto_mask", "ui_correction",
        # An override read from the environment, not from the config file.
        "nr_dll",
    }
    ghosts = sorted(allow - shipped - KNOWN_ABSENT)
    for key in ghosts:
        failures.append(
            f"the allow-list names `{key}`, which is not a shipped setting - "
            f"a typo here is silent, the field just never appears")

    # The settings that made this test necessary, by name.
    for key in ("nr_passes", "fps_overlay", "tray_on_minimise",
                "tray_on_close", "menu_scale", "nr_direct", "worker_present"):
        if key not in allow:
            failures.append(f"`{key}` is missing from the report again")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print(f"OK: {len(shipped)} shipped settings - {len(shipped & allow)} "
          f"reported, {len(WITHHELD)} withheld on purpose")
    return 0


if __name__ == "__main__":
    sys.exit(main())
