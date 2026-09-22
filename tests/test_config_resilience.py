"""The config survives what users and updates do to it (22.09 audit).

Through the real settings_io and main's startup helpers, no GPU:

* a config.json saved "with BOM" loads; a broken one names the file;
* `gpu: null` ("let the worker choose") survives a save - it made every save
  raise, and the layout, theme and presets were lost on each close;
* the string "false" is not True, a junk `split` / `nr_passes: 1e999` /
  a huge `residual_strength` fall back instead of aborting the launch;
* autostart that points at another copy of the program reads as off here;
* a release unpacked over another throws the compiled-module cache away once
  (the zip dates every file 1980-01-01, so a same-size module kept running
  the old .pyc - 2.1.1 over 2.1.0 called itself 2.1.0).

Run:  runtime\\python.exe tests\\test_config_resilience.py
"""
import json
import os
import sys
import tempfile
import types
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import settings_io  # noqa: E402
import startup  # noqa: E402


def main() -> int:
    failures = []
    root = Path(tempfile.mkdtemp(prefix="ns-config-res-"))

    # 1. BOM, and a JSON error that names the file
    bom = root / "bom" / "config.json"
    bom.parent.mkdir()
    bom.write_bytes(b"\xef\xbb\xbf" + json.dumps(
        {"schema_version": 1, "theme": "dark"}).encode("utf-8"))
    try:
        cfg = settings_io.load_config(bom)
        if cfg.get("theme") != "dark":
            failures.append("a BOM config lost its values")
    except Exception as exc:
        failures.append(f"a config saved with a BOM does not load: {exc!r}")
    broken = root / "broken" / "config.json"
    broken.parent.mkdir()
    broken.write_text('{"theme": "dark",, }', encoding="utf-8")
    try:
        settings_io.load_config(broken)
        failures.append("a broken config.json loaded")
    except ValueError as exc:
        if "config.json" not in str(exc) or "line" not in str(exc):
            failures.append(f"the JSON error does not name the file and place: {exc}")

    # 2. gpu: null survives a save
    path = root / "save" / "config.json"
    path.parent.mkdir()
    cfg = settings_io.load_config(path)
    cfg["gpu"] = None
    cfg["gpu_no_nr"] = [1, "junk", None, 2]
    menu = types.SimpleNamespace(offset=[0, 0], user_scale=1.0, user_height=None,
                                 visible=False, state={"theme": "light"})
    st = types.SimpleNamespace(
        cfg=cfg, cfg_path=path, params=settings_io.resolve_params(cfg),
        monitor=0, lang="en", work_scale=0.65, split_pos=0.0,
        startup_menu=True, nr_small=True,
        display=types.SimpleNamespace(menu=menu))
    try:
        saved = settings_io.save_menu_layout(st)
    except Exception as exc:
        saved = False
        failures.append(f"save_menu_layout raised: {exc!r}")
    if not saved:
        failures.append("a config with gpu: null cannot be saved - every menu "
                        "close loses the layout")
    else:
        on_disk = json.loads(path.read_text(encoding="utf-8"))
        if on_disk.get("gpu", "missing") is not None:
            failures.append(f"gpu: null came back as {on_disk.get('gpu')!r}")
        if on_disk.get("gpu_no_nr") != [1, 2]:
            failures.append(f"gpu_no_nr junk was not dropped: {on_disk.get('gpu_no_nr')}")

    # 3. hostile values fall back instead of aborting
    hostile = root / "hostile" / "config.json"
    hostile.parent.mkdir()
    hostile.write_text(
        '{"schema_version": 1, "hdr": "false", "spout": "yes", '
        '"fullscreen": {"x": 1}, "split": "left", "nr_passes": 1e999}',
        encoding="utf-8")
    try:
        cfg = settings_io.load_config(hostile)
        if cfg["hdr"] is not False:
            failures.append(f'"hdr": "false" read as {cfg["hdr"]!r}')
        if cfg["spout"] is not True:
            failures.append(f'"spout": "yes" read as {cfg["spout"]!r}')
        if not isinstance(cfg["fullscreen"], bool):
            failures.append(f"a dict for fullscreen was kept: {cfg['fullscreen']!r}")
        if cfg["split"] != 0.0:
            failures.append(f"a junk split was kept: {cfg['split']!r}")
        if cfg["nr_passes"] != 1:
            failures.append(f"nr_passes 1e999 was kept: {cfg['nr_passes']!r}")
    except Exception as exc:
        failures.append(f"hostile values aborted the load: {exc!r}")
    os.environ.pop("NS_NR_RESIDUAL_STRENGTH", None)
    try:
        startup._apply_residual_env({"residual_strength": 10 ** 400})
    except Exception as exc:
        failures.append(f"a huge residual_strength aborted the launch: {exc!r}")
    os.environ.pop("NS_NR_RESIDUAL_STRENGTH", None)

    # 4. autostart of another copy reads as off
    import winreg
    real = (winreg.OpenKey, winreg.QueryValueEx, winreg.CloseKey)
    answer = {"value": ""}
    winreg.OpenKey = lambda *a, **k: object()
    winreg.QueryValueEx = lambda key, name: (answer["value"], 1)
    winreg.CloseKey = lambda key: None
    try:
        answer["value"] = r'wscript.exe "D:\old\NeuralScreen\NeuralScreen.vbs"'
        if settings_io._autostart_enabled():
            failures.append("autostart pointing at another copy reads as on here")
        answer["value"] = f'wscript.exe "{settings_io.BASE_DIR / "NeuralScreen.vbs"}"'
        if not settings_io._autostart_enabled():
            failures.append("autostart pointing at this copy reads as off")
    finally:
        winreg.OpenKey, winreg.QueryValueEx, winreg.CloseKey = real

    # 5. a release over another drops the compiled cache once
    import main as ns_main
    app = root / "app"
    cache = app / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "settings_io.cpython-313.pyc").write_bytes(b"old code")
    ns_main._drop_stale_bytecode(app)            # no VERSION.txt: a dev tree
    if not (cache / "settings_io.cpython-313.pyc").exists():
        failures.append("a development tree lost its compiled cache")
    (app / "VERSION.txt").write_text("NeuralScreen 2.1.2\ncommit: abc\n")
    ns_main._drop_stale_bytecode(app)
    if (cache / "settings_io.cpython-313.pyc").exists():
        failures.append("a new release kept the previous release's .pyc")
    (cache / "fresh.cpython-313.pyc").write_bytes(b"compiled by this release")
    ns_main._drop_stale_bytecode(app)            # same release, second launch
    if not (cache / "fresh.cpython-313.pyc").exists():
        failures.append("the cache is thrown away on every launch, not once")

    import shutil
    shutil.rmtree(root, ignore_errors=True)
    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: BOM and broken files, gpu: null, hostile values, another copy's "
          "autostart and a release unpacked over another are all handled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
