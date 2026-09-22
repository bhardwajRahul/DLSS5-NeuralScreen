"""The save dialog must open where the user's folder is, or say why not (M2).

The folder picker returns a Path and drain_save_dialog stores it as a string in
st.cfg, which save_menu_layout writes back into config.json. _validate_config
accepts a str and coerces anything else to "", so the value survives as long as
it is a string.

The half that is directly readable in the source is the dialog's start point:
it was `str(shot_dir) if ... else None`, with no existence check. A folder that
has since been deleted, or that lives on a drive which is not connected right
now, was handed to the dialog anyway - and the library then falls back to its
own default location without telling anybody, so the user's chosen folder looks
like it was forgotten.

Checked here: a folder that exists is used as the start point; one that does
not exist (or is a file, or an empty string) is not, and the fallback is what
the dialog gets.

The other half of M2 - st.cfg and config.json diverging so that a save writes
"" - is UNCONFIRMED in the audit itself and needs two writers (or a hand edit
between a folder pick and the next save) to reproduce; nothing here claims it.

Run:  runtime\\python.exe tests\\test_shot_dir_exists_check.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import commands  # noqa: E402


class _Display:
    def get_hwnd(self):
        return 1234

    def alert(self, *a, **k):
        pass


def _state(shot_dir):
    return SimpleNamespace(
        cfg={"screenshot_dir": shot_dir, "screenshot_format": "png"},
        display=_Display(),
        shot_paths=__import__("queue").Queue(),
        shot_dialog_open=False,
        lang="en",
        shot_rgba=None,
    )


def _initial_dir_for(shot_dir) -> tuple:
    """Run the real open_save_dialog and capture what it hands the dialog."""
    seen = {}

    def fake_ask(hwnd, name, initial_dir, fallback_dir=None):
        seen["initial_dir"] = initial_dir
        seen["fallback"] = str(fallback_dir) if fallback_dir else None
        return None

    st = _state(shot_dir)
    with mock.patch.object(commands.dialogs, "ask_save_path", fake_ask), \
            mock.patch.object(commands.threading, "Thread",
                              lambda **kw: SimpleNamespace(
                                  start=lambda: kw["target"](), daemon=None)):
        commands.open_save_dialog(st)
    return seen.get("initial_dir"), seen.get("fallback")


def main() -> int:
    failures = []
    root = Path(tempfile.mkdtemp(prefix="ns-shotdir-"))
    real = root / "shots"
    real.mkdir()

    # 1. A folder that exists is the start point.
    initial, fallback = _initial_dir_for(str(real))
    if initial != str(real):
        failures.append(f"an existing folder was not used as the dialog's "
                        f"start point: {initial!r} != {str(real)!r}")
    if not fallback:
        failures.append("the dialog was given no fallback folder")

    # 2. A folder that was deleted must not be handed to the dialog: the
    #    library then silently opens somewhere else.
    gone = root / "deleted-since"
    initial, _ = _initial_dir_for(str(gone))
    if initial == str(gone):
        failures.append(f"a missing folder {gone} was still used as the start "
                        f"point - the dialog silently falls back elsewhere")
    if initial is not None and not Path(initial).is_dir():
        failures.append(f"the start point {initial!r} is not a directory")

    # 3. A path that exists but is a file is not a folder either.
    a_file = root / "note.txt"
    a_file.write_text("x", encoding="utf-8")
    initial, _ = _initial_dir_for(str(a_file))
    if initial == str(a_file):
        failures.append("a FILE was handed to the dialog as its folder")

    # 4. Empty and whitespace values mean "no preference".
    for value in ("", "   ", None):
        initial, _ = _initial_dir_for(value)
        if initial not in (None, ""):
            failures.append(f"shot_dir={value!r} produced a start point "
                            f"{initial!r} instead of 'no preference'")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the dialog opens in the user's folder when it is there, and "
          "falls back rather than pretending when it is not")
    return 0


if __name__ == "__main__":
    sys.exit(main())
