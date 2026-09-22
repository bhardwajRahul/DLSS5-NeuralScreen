"""Tracked defaults and user-config schema migration.

Pure filesystem tests: every config.json lives in a temporary directory.  The
developer/user config in the project root is never read or written.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))

import settings_io  # noqa: E402


def _write(path: Path, value: dict) -> bytes:
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return raw


def main() -> int:
    failures = []
    defaults_path = BASE / "config.default.json"
    defaults = json.loads(defaults_path.read_text(encoding="utf-8"))

    # 1. The shipped file identifies the schema understood by this build.
    if defaults.get("schema_version") != settings_io.CONFIG_SCHEMA_VERSION:
        failures.append(
            "config.default.json schema does not match CONFIG_SCHEMA_VERSION")

    root = Path(tempfile.mkdtemp(prefix="ns-config-schema-"))
    try:
        # 2. A fresh install creates the local user file atomically from the
        #    tracked defaults; the default file itself is only read.
        fresh = root / "fresh" / "config.json"
        fresh.parent.mkdir()
        default_before = defaults_path.read_bytes()
        loaded = settings_io.load_config(fresh)
        if json.loads(fresh.read_text(encoding="utf-8")) != defaults:
            failures.append("first launch did not create config.json from defaults")
        if loaded.get("schema_version") != settings_io.CONFIG_SCHEMA_VERSION:
            failures.append("first-launch config has the wrong schema version")
        if defaults_path.read_bytes() != default_before:
            failures.append("loading a fresh config changed config.default.json")
        if list(fresh.parent.glob("*.tmp")):
            failures.append("first-launch creation left a temp file")

        # 3. A pre-schema (v0) file gets every missing known field, but its
        #    own values and unknown extension data survive byte-for-value.
        legacy = root / "legacy" / "config.json"
        legacy.parent.mkdir()
        extension = {"enabled": True, "nested": [1, {"future": "value"}]}
        _write(legacy, {
            "monitor": 2,
            "profile": "Natural",
            "theme": "dark",
            "hotkeys": {"toggle": "F8"},
            "third_party_extension": extension,
        })
        migrated = settings_io.load_config(legacy)
        on_disk = json.loads(legacy.read_text(encoding="utf-8"))
        if on_disk.get("schema_version") != settings_io.CONFIG_SCHEMA_VERSION:
            failures.append("legacy config was not advanced to the current schema")
        if on_disk.get("third_party_extension") != extension:
            failures.append("legacy migration lost an unknown user key")
        if on_disk.get("monitor") != 2 or on_disk.get("theme") != "dark":
            failures.append("legacy migration replaced user values with defaults")
        if on_disk.get("hotkeys") != {"toggle": "F8"}:
            failures.append("legacy migration replaced the user's hotkeys")
        if migrated.get("width") != defaults.get("width"):
            failures.append("legacy migration did not fill a missing default")

        # 4. A current-schema partial file is healed the same way.  Defaults
        #    are the source of known fields, not a one-time install template.
        partial = root / "partial" / "config.json"
        partial.parent.mkdir()
        partial_extension = {"keep": ["all", "of", "this"]}
        _write(partial, {
            "schema_version": settings_io.CONFIG_SCHEMA_VERSION,
            "profile": "Natural",
            "unknown": partial_extension,
        })
        settings_io.load_config(partial)
        partial_disk = json.loads(partial.read_text(encoding="utf-8"))
        if partial_disk.get("unknown") != partial_extension:
            failures.append("default merge lost an unknown current-schema key")
        if partial_disk.get("height") != defaults.get("height"):
            failures.append("default merge did not fill a current-schema field")

        # 5. A newer application may know meanings this one does not.  Refuse
        #    to downgrade it and leave the original bytes untouched.
        future = root / "future" / "config.json"
        future.parent.mkdir()
        future_before = _write(future, {
            "schema_version": settings_io.CONFIG_SCHEMA_VERSION + 1,
            "future_only": {"do_not_drop": True},
        })
        try:
            settings_io.load_config(future)
            failures.append("a future schema version was accepted")
        except ValueError as exc:
            if "newer" not in str(exc):
                failures.append(f"future-schema error is unclear: {exc}")
        if future.read_bytes() != future_before:
            failures.append("a rejected future config was modified")

        # 6. Validation happens before persistence: an invalid old file is
        #    not half-migrated merely because defaults could fill its fields.
        invalid = root / "invalid" / "config.json"
        invalid.parent.mkdir()
        invalid_before = _write(invalid, {
            "width": "broken",
            "unknown": {"must": "remain"},
        })
        try:
            settings_io.load_config(invalid)
            failures.append("an invalid legacy config was accepted")
        except ValueError:
            pass
        if invalid.read_bytes() != invalid_before:
            failures.append("a failed migration modified the invalid config")
        if list(invalid.parent.glob("*.tmp")):
            failures.append("a failed validation left a temp file")

        # 7. The migration path itself uses the atomic writer.  A simulated
        #    replace failure must preserve the old bytes and clean the temp.
        interrupted = root / "interrupted" / "config.json"
        interrupted.parent.mkdir()
        interrupted_before = _write(interrupted, {"profile": "Natural"})
        real_replace = os.replace

        def fail_replace(src, dst):
            raise OSError("simulated migration replace failure")

        os.replace = fail_replace
        try:
            try:
                settings_io.load_config(interrupted)
                failures.append("an interrupted migration reported success")
            except OSError:
                pass
        finally:
            os.replace = real_replace
        if interrupted.read_bytes() != interrupted_before:
            failures.append("an interrupted migration changed the original file")
        if list(interrupted.parent.glob("*.tmp")):
            failures.append("an interrupted migration left a temp file")

        # 8. Distribution carries only the tracked defaults.  The local user
        #    config is explicitly excluded even in an older checkout where it
        #    may still appear in `git ls-files`.
        builder = (BASE / "build_release_zip.py").read_text(encoding="utf-8")
        if '"config.default.json"' not in builder:
            failures.append("release builder does not include config.default.json")
        if 'if norm == "config.json":' not in builder:
            failures.append("release builder does not exclude config.json")
        if "HEAD:config.json" in builder:
            failures.append("release builder still substitutes HEAD config.json")
    finally:
        shutil.rmtree(root, ignore_errors=True)

    for failure in failures:
        print("FAIL:", failure)
    if failures:
        return 1
    print("OK: defaults, schema migration and release config boundaries hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
