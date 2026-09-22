"""User presets: save, load, apply, and survive a broken config.

The config is user-editable - a hand-typed preset must not take the
program down. The contract:

* load_presets drops anything that is not a full params snapshot
  (the four sliders + the NGX plumbing ints in range);
* a config whose profile points at a deleted preset falls back to
  Natural instead of raising;
* resolve_params applies a preset like a profile;
* _next_preset_name always finds a free "Preset N" name.

Pure unit test - no worker, no window.
"""
import json
import sys
import tempfile
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

from settings_io import (  # noqa: E402
    PROFILES, load_config, load_presets, resolve_params, _next_preset_name,
)


def _write_cfg(data: dict) -> Path:
    fd, path = tempfile.mkstemp(suffix=".json")
    with open(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Path(path)


def _base_cfg() -> dict:
    return {
        "monitor": 0, "width": 3840, "height": 2160, "fullscreen": True,
        "warmup": 30, "profile": "Natural",
        "intensity": None, "local_tone": None,
        "local_structure": None, "skin_structure": None,
    }


def main() -> int:
    failures = []

    # 1. A preset written by THIS build loads and applies. It carries the
    # four sliders plus style and the auto mask - the three dead fields are
    # gone from presets since the ranges were measured.
    good = {
        "My Game": {"intensity": 0.9, "local_tone": 0.8,
                    "local_structure": 1.1, "skin_structure": 0.5,
                    "style": 2, "auto_mask": 1},
    }
    cfg = _base_cfg()
    cfg["presets"] = good
    cfg["profile"] = "My Game"
    presets = load_presets(cfg)
    if presets != good:
        failures.append(f"a well-formed preset must load as-is, got {presets}")
    params = resolve_params(cfg)
    if params["intensity"] != 0.9 or params["style"] != 2:
        failures.append(f"resolve_params must apply the preset, got {params}")

    # 1b. A preset written by an OLDER build loads too: the three dead
    # fields are ignored, and a value wider than today's range is pulled in
    # rather than dropped. Losing someone's saved look over a number that
    # never did anything would be the wrong trade.
    old = {
        "From 1.8.2": {"intensity": 2.5, "local_tone": 0.8,
                       "local_structure": 2.0, "skin_structure": 0.5,
                       "profile": 2, "preset": 2, "style": 2,
                       "auto_mask": 1, "ui_correction": 0},
    }
    cfg_old = _base_cfg()
    cfg_old["presets"] = old
    loaded = load_presets(cfg_old).get("From 1.8.2", {})
    if loaded.get("intensity") != 1.0 or loaded.get("local_structure") != 2.0:
        failures.append(f"an old preset must be pulled into range, got {loaded}")
    if "ui_correction" in loaded or "profile" in loaded:
        failures.append(f"the dead fields must not survive the load, got {loaded}")
    if loaded.get("style") != 2 or loaded.get("auto_mask") != 1:
        failures.append(f"style and the auto mask must survive, got {loaded}")

    # 2. Broken entries are dropped, the rest survive.
    broken = {
        "Bad Type": "not a dict",
        "Bad Float": {"intensity": "abc", "local_tone": 1.0,
                      "local_structure": 1.0, "skin_structure": 0.0,
                      "profile": 1, "preset": 0, "style": 1,
                      "auto_mask": 0, "ui_correction": 0},
        # No "out of range" entry here any more: that is a migration, not
        # a breakage, and case 1b covers it. What IS broken is a style
        # nobody ever wrote.
        "Bad Style": {"intensity": 1.0, "local_tone": 1.0,
                      "local_structure": 1.0, "skin_structure": 0.0,
                      "style": 9, "auto_mask": 0},
        "Missing Key": {"intensity": 1.0, "local_tone": 1.0,
                        "local_structure": 1.0,
                        "profile": 1, "preset": 0, "style": 1,
                        "auto_mask": 0, "ui_correction": 0},
        "Bad Mask": {"intensity": 1.0, "local_tone": 1.0,
                     "local_structure": 1.0, "skin_structure": 0.0,
                     "style": 1, "auto_mask": 7},
        "": {"intensity": 1.0, "local_tone": 1.0,
             "local_structure": 1.0, "skin_structure": 0.0,
             "profile": 1, "preset": 0, "style": 1,
             "auto_mask": 0, "ui_correction": 0},
        "Good One": {"intensity": 1.0, "local_tone": 1.0,
                     "local_structure": 1.0, "skin_structure": 0.0,
                     "profile": 1, "preset": 0, "style": 1,
                     "auto_mask": 0, "ui_correction": 0},
    }
    cfg2 = _base_cfg()
    cfg2["presets"] = broken
    presets2 = load_presets(cfg2)
    if set(presets2) != {"Good One"}:
        failures.append(f"only the well-formed preset must survive, got "
                        f"{sorted(presets2)}")

    # 3. A config whose profile is a deleted preset falls back to Natural.
    cfg3 = _base_cfg()
    cfg3["presets"] = {}
    cfg3["profile"] = "Deleted Preset"
    path3 = _write_cfg(cfg3)
    try:
        loaded = load_config(path3)
        if loaded["profile"] != "Natural":
            failures.append("a stale preset reference must fall back to "
                            f"Natural, got {loaded['profile']!r}")
    finally:
        path3.unlink(missing_ok=True)

    # 4. A config whose profile is a live preset loads without raising.
    cfg4 = _base_cfg()
    cfg4["presets"] = good
    cfg4["profile"] = "My Game"
    path4 = _write_cfg(cfg4)
    try:
        loaded4 = load_config(path4)
        if loaded4["profile"] != "My Game":
            failures.append("a live preset reference must be kept, got "
                            f"{loaded4['profile']!r}")
    finally:
        path4.unlink(missing_ok=True)

    # 5. _next_preset_name skips the taken names.
    if _next_preset_name({}) != "Preset 1":
        failures.append("the first free name must be 'Preset 1'")
    if _next_preset_name({"Preset 1": {}, "Preset 2": {}}) != "Preset 3":
        failures.append("taken names must be skipped")

    # 6. The built-in profiles are untouched by the preset machinery.
    if "Natural" not in PROFILES or "My Game" in PROFILES:
        failures.append("built-in profiles must not be polluted by presets")

    # 7. The model is NOT carried by built-in profiles any more (user rule
    #    15.09): switching to a profile leaves the chosen model alone, and
    #    a fresh config opens on Natural.
    import settings_io as _sio
    cfg7 = _base_cfg()
    cfg7["profile"] = "Strong / Cinematic"
    no_key = _sio.resolve_params(cfg7)
    if no_key["style"] != 1:
        failures.append(f"a fresh config must open on Natural (style 1), "
                        f"got {no_key['style']}")
    cfg7["style"] = 2
    kept = _sio.resolve_params(cfg7)
    if kept["style"] != 2:
        failures.append(f"the live model must survive a profile switch, "
                        f"got {kept['style']}")
    # ...while a preset still carries its own model.
    cfg7b = _base_cfg()
    cfg7b["presets"] = good
    cfg7b["profile"] = "My Game"
    _sio.PROFILES  # sanity: import resolved
    preset_params = _sio.resolve_params(cfg7b)
    if preset_params["style"] != 2:
        failures.append(f"a preset must keep the model it was saved with, "
                        f"got {preset_params['style']}")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: presets load, apply, and survive a broken config")
    return 0


if __name__ == "__main__":
    sys.exit(main())
