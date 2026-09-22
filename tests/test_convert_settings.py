r"""The conversion page's settings, from the config file to the converter.

The output choices live in four places - config.default.json, the validator
in settings_io, the panel's segments in overlay_ui, and the converter that
acts on them - and a value added to one and forgotten in another fails
quietly: a codec the panel offers but the validator resets, a folder that
never reaches the queue. Checked here, without a GPU:

1. The four value lists agree everywhere (settings_io keeps its own copy,
   because importing the converter from there would be an import cycle).
2. The validator: an unknown value falls back to the first choice, and the
   audio switch is off only when the config really says false.
3. Every conversion setting is saved with the panel's layout, and the
   shipped defaults are the first choices.
4. The multi-file picker: its answer is parsed in both of Windows' shapes,
   and its struct asks for several files with room for their names.
5. The settings a queued file gets are a COPY, taken on the main thread: a
   slider moved afterwards does not reach it, and "One folder" hands the
   converter that folder.
6. The converter's own rules: which container a video goes to, which audio
   is copied and which re-encoded, the codec fallbacks, and output names
   that never take a name a crashed run left behind.

Run:  runtime\python.exe tests\test_convert_settings.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import commands  # noqa: E402
import convert_jobs  # noqa: E402
import dialogs  # noqa: E402
import media_convert  # noqa: E402
import overlay_ui  # noqa: E402
import settings_io  # noqa: E402


def main() -> int:
    failures: list[str] = []

    # 1. One list of values, wherever it is spelled out.
    pairs = (
        ("convert_dest", settings_io.CONVERT_DESTS, convert_jobs.DESTINATIONS),
        ("convert_codec", settings_io.CONVERT_CODECS, media_convert.CODEC_CHOICES),
        ("convert_quality", settings_io.CONVERT_QUALITIES,
         tuple(media_convert.QUALITY_CQ)),
        ("convert_image_format", settings_io.CONVERT_IMAGE_FORMATS,
         media_convert.IMAGE_FORMATS),
    )
    for key, validator, engine in pairs:
        if tuple(validator) != tuple(engine):
            failures.append(f"{key}: settings_io allows {validator}, the "
                            f"converter knows {engine}")
        panel = overlay_ui._CONVERT_SEGMENTS.get(key)
        if tuple(panel or ()) != tuple(validator):
            failures.append(f"{key}: the panel offers {panel}, settings_io "
                            f"allows {validator}")
        if commands._CONVERT_CHOICES.get(key) != validator:
            failures.append(f"{key}: commands accepts "
                            f"{commands._CONVERT_CHOICES.get(key)}")

    # 2. The validator.
    base = json.loads((BASE / "config.default.json").read_text(encoding="utf-8"))
    hostile = dict(base, convert_dest="cloud", convert_codec="vp9",
                   convert_quality=7, convert_image_format="gif",
                   convert_dir=["not", "a", "path"], convert_audio="no")
    cfg = settings_io._validate_config(dict(hostile))
    for key, allowed in (("convert_dest", settings_io.CONVERT_DESTS),
                         ("convert_codec", settings_io.CONVERT_CODECS),
                         ("convert_quality", settings_io.CONVERT_QUALITIES),
                         ("convert_image_format",
                          settings_io.CONVERT_IMAGE_FORMATS)):
        if cfg[key] != allowed[0]:
            failures.append(f"{key}: {hostile[key]!r} validated to "
                            f"{cfg[key]!r}, not the default {allowed[0]!r}")
    if cfg["convert_dir"] != "":
        failures.append(f"a list as the folder validated to {cfg['convert_dir']!r}")
    if cfg["convert_audio"] is not True:
        failures.append("a truthy string turned the audio off - only false may")
    off = settings_io._validate_config(dict(base, convert_audio=False))
    if off["convert_audio"] is not False:
        failures.append("convert_audio false did not stay false")
    fine = settings_io._validate_config(dict(base, convert_codec="HEVC",
                                             convert_dest="folder"))
    if fine["convert_codec"] != "hevc" or fine["convert_dest"] != "folder":
        failures.append(f"valid values were not kept: "
                        f"{fine['convert_codec']!r}, {fine['convert_dest']!r}")

    # 3. Saved with the layout; shipped with the first choices.
    for key, allowed in (("convert_dest", settings_io.CONVERT_DESTS),
                         ("convert_codec", settings_io.CONVERT_CODECS),
                         ("convert_quality", settings_io.CONVERT_QUALITIES),
                         ("convert_image_format",
                          settings_io.CONVERT_IMAGE_FORMATS)):
        if base.get(key) != allowed[0]:
            failures.append(f"config.default.json ships {key}="
                            f"{base.get(key)!r}, not {allowed[0]!r}")
    if base.get("convert_audio") is not True or base.get("convert_dir") != "":
        failures.append("config.default.json: convert_audio/convert_dir defaults")
    menu = SimpleNamespace(user_scale=1.0, user_height=None, offset=[0, 0],
                           state={"theme": "dark"})
    saved_cfg = dict(cfg, convert_dest="folder", convert_dir="D:/Out",
                     convert_codec="h264", convert_quality="small",
                     convert_image_format="png", convert_audio=False)
    params = {"intensity": 1.0, "local_tone": 0.5, "local_structure": 1.0,
              "skin_structure": -1.0, "style": 1}
    original = settings_io.devicename_for_output_idx
    settings_io.devicename_for_output_idx = lambda _idx: None
    try:
        saved = settings_io._menu_layout_payload(
            saved_cfg, params, 0, "en", 0.65, 0.0, True, True, menu)
    finally:
        settings_io.devicename_for_output_idx = original
    for key, want in (("convert_dest", "folder"), ("convert_dir", "D:/Out"),
                      ("convert_codec", "h264"), ("convert_quality", "small"),
                      ("convert_image_format", "png"), ("convert_audio", False)):
        if saved.get(key) != want:
            failures.append(f"the saved layout has {key}={saved.get(key)!r}, "
                            f"not {want!r} - it would be lost on restart")

    # 4. The multi-file picker.
    nul = chr(0)
    one = dialogs.split_multiselect(f"C:\\clips\\a.mp4{nul}{nul}garbage")
    if one != [Path("C:/clips/a.mp4")]:
        failures.append(f"a single pick parsed as {one}")
    many = dialogs.split_multiselect(
        f"C:\\clips{nul}a.mp4{nul}b c.mkv{nul}d.png{nul}{nul}old{nul}")
    if many != [Path("C:/clips/a.mp4"), Path("C:/clips/b c.mkv"),
                Path("C:/clips/d.png")]:
        failures.append(f"a pick of three parsed as {many}")
    if dialogs.split_multiselect(nul * 8) != []:
        failures.append("an empty answer is not an empty list")
    ofn, buf = dialogs._open_dialog_struct(0, None, "t", multi=True)
    if not (ofn.Flags & 0x200 and ofn.Flags & 0x80000):
        failures.append(f"the multi picker's flags {ofn.Flags:#x} do not ask "
                        f"for several files in the Explorer shape")
    if ofn.nMaxFile != dialogs.OPEN_BUFFER_CHARS or len(buf) != ofn.nMaxFile:
        failures.append("the multi picker's buffer and nMaxFile disagree")
    ofn1, buf1 = dialogs._open_dialog_struct(0, None, "t")
    if ofn1.Flags & 0x200 or ofn1.nMaxFile != 1024:
        failures.append("the single-file picker changed")

    # 5. The copy a queued file gets.
    with tempfile.TemporaryDirectory() as tmp:
        out_folder = Path(tmp) / "converted here"
        st = SimpleNamespace(
            params={"intensity": 0.8, "style": 2}, work_scale=0.5,
            nr_small=True, nr_passes=2,
            cfg={"flow_preset": "ultrafast", "convert_dest": "folder",
                 "convert_dir": str(out_folder), "convert_codec": "hevc",
                 "convert_quality": "balanced", "convert_image_format": "jpg",
                 "convert_audio": False})
        snap = commands.convert_settings(st)
        st.params["intensity"] = 0.1
        if snap.params.get("intensity") != 0.8:
            failures.append("the queued settings share the live params dict")
        if Path(snap.out_dir or "") != out_folder or not out_folder.is_dir():
            failures.append(f"One folder handed the converter {snap.out_dir!r}")
        if (snap.codec, snap.quality, snap.image_format, snap.copy_audio,
                snap.nr_passes, snap.flow_preset) != (
                "hevc", "balanced", "jpg", False, 2, "ultrafast"):
            failures.append(f"the snapshot lost a choice: {snap}")
        st.cfg["convert_dest"] = "source"
        if commands.convert_settings(st).out_dir is not None:
            failures.append("Beside original still handed a folder")

    # 6. The converter's rules.
    for name, fmt, want in (("a.png", "keep", ".png"), ("a.jpeg", "keep", ".jpeg"),
                            ("a.webp", "png", ".png"), ("a.bmp", "jpg", ".jpg"),
                            ("a.mkv", "keep", ".mkv"), ("a.webm", "keep", ".mkv"),
                            ("a.avi", "keep", ".mp4"), ("a.wmv", "png", ".mp4"),
                            ("a.mov", "keep", ".mp4")):
        got = media_convert.output_suffix(Path(name), fmt)
        if got != want:
            failures.append(f"output_suffix({name}, {fmt}) = {got}, not {want}")
    if media_convert.codec_chain("h264") != ("h264_nvenc",) or \
            media_convert.codec_chain("hevc")[0] != "hevc_nvenc" or \
            media_convert.codec_chain("auto") != media_convert.CODEC_CHAIN:
        failures.append("the codec chains changed")
    for codec, container, want in (("aac", "mp4", "copy"),
                                   ("pcm_s16le", "mp4", "aac"),
                                   ("vorbis", "mp4", "aac"),
                                   ("wmav2", "mp4", "aac"),
                                   ("opus", "mp4", "copy"),
                                   ("pcm_s16le", "matroska", "copy")):
        got = media_convert.audio_plan(codec, container)
        if got != want:
            failures.append(f"audio_plan({codec}, {container}) = {got}, "
                            f"not {want}")
    if media_convert.encoder_options("small")["cq"] != "30" or \
            media_convert.encoder_options("bogus")["cq"] != "16":
        failures.append("the quality steps do not map to the NVENC cq values")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "clip.mkv"
        src.write_bytes(b"x")
        (Path(tmp) / "clip-nr.mp4.partial").write_bytes(b"x")
        name = media_convert.default_output_path(src, None, ".mp4").name
        if name != "clip-nr-2.mp4":
            failures.append(f"a .partial left by a crashed run was reused: "
                            f"{name}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the conversion settings agree from the config to the converter, "
          "survive a restart, and each queued file gets its own copy")
    return 0


if __name__ == "__main__":
    sys.exit(main())
