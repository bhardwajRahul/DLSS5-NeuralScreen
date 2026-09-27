"""The hotkeys master switch: off really releases every key (#134).

The request (Koaxz, #134): the numpad keys NeuralScreen binds belong to it
while it runs, and people lose them in games, in Blender, in a calculator.
The menu already had a way to give the keyboard back for a moment -
HotkeyController.suspend(), used while a field waits for a rebind key - but
nothing gave it back for good, and nothing survived a restart.

Checked by FACT, the way test_hotkey_rebind checks suspend/resume: after the
switch is off, registering one of OUR combinations FROM THIS THREAD must
succeed (so the key really is back in the hands of every other program), and
after switching it back on it must fail again (we hold it once more).

Three more things this locks, each of them a way the switch could silently
not work:

  * the shipped default is ON - a config that never mentions the key must
    leave the hotkeys alone;
  * a resume() (the menu closing after a rebind) must NOT put the keys back
    when the switch is off, or the switch would quietly undo itself the next
    time somebody opened and closed the menu;
  * the choice is a setting, not a moment: it is written into the config.

Run:  runtime\\python.exe tests\\test_hotkeys_master_switch.py
"""
import ctypes
import json
import os
import queue
import sys
import time
from pathlib import Path
from types import SimpleNamespace

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ (autocheck)
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from hotkeys import HotkeyController, MOD_ALT, MOD_CONTROL, MOD_NOREPEAT  # noqa: E402

user32 = ctypes.windll.user32
#: A combination nothing else uses, so the probe cannot fight the machine.
PROBE_ID = 901
PROBE_MODS = MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
VK_F6 = 0x75


def can_grab(mods: int, vk: int) -> bool:
    """Could THIS thread register the combination? Then nobody holds it."""
    if user32.RegisterHotKey(None, PROBE_ID, mods, vk):
        user32.UnregisterHotKey(None, PROBE_ID)
        return True
    return False


def _settings_io():
    import settings_io
    return settings_io


def check_shipped_default(failures: list) -> None:
    """A config that never heard of the key must mean ON."""
    cfg = json.loads((BASE / "config.default.json").read_text(encoding="utf-8"))
    if "hotkeys_enabled" not in cfg:
        failures.append("config.default.json does not ship hotkeys_enabled - "
                        "a fresh install would have no switch to read")
    elif cfg["hotkeys_enabled"] is not True:
        failures.append(f"the shipped default is {cfg['hotkeys_enabled']!r}, "
                        f"not True: hotkeys would be off on a fresh install")
    # And the validator has to keep a hand-edited value honest without
    # turning a missing one into "off". _validate_config demands a whole
    # config, so the shipped file is the base for each case. A word that is
    # not a boolean falls back to the SHIPPED default, which is True - the
    # point here is that it does not silently read as False.
    sio = _settings_io()
    base = json.loads((BASE / "config.default.json").read_text(encoding="utf-8"))
    for raw, want in ((True, True), (False, False), ("false", False),
                      ("true", True), ("junk", True)):
        probe = dict(base)
        probe["hotkeys_enabled"] = raw
        got = sio._validate_config(probe).get("hotkeys_enabled", "absent")
        if got is not want:
            failures.append(f"a config with hotkeys_enabled={raw!r} validates "
                            f"to {got!r}, expected {want}")
    for word in ("0", "no", "off", ""):
        probe = dict(base)
        probe["hotkeys_enabled"] = word
        if sio._validate_config(probe).get("hotkeys_enabled") is not False:
            failures.append(f"hotkeys_enabled={word!r} should read as off")
    # A config that never mentions it stays ON: an old config.json from
    # before this setting existed must not mute the hotkeys on upgrade.
    probe = dict(base)
    probe.pop("hotkeys_enabled", None)
    if sio._validate_config(probe).get("hotkeys_enabled", True) is not True:
        failures.append("a config without the key does not read as ON")


def check_switch_releases(failures: list) -> None:
    """The heart of it: off really gives the key back, on takes it again."""
    if not can_grab(PROBE_MODS, VK_F6):
        print("SKIP: Ctrl+Alt+F6 is taken - the probe cannot answer")
        return
    commands: queue.Queue = queue.Queue()
    bindings = {1: (PROBE_MODS, VK_F6, "toggle", "Ctrl+Alt+F6")}
    ctl = HotkeyController(commands, bindings)
    if not hasattr(ctl, "set_enabled"):
        failures.append("HotkeyController has no set_enabled - there is no "
                        "master switch to test (#134)")
        return
    ctl.start()
    try:
        if "Ctrl+Alt+F6" not in ctl.registered:
            failures.append("it did not register at startup - nothing to switch")
            return
        if can_grab(PROBE_MODS, VK_F6):
            failures.append("the key is free although the controller took it")

        # OFF: the key must be back in everyone else's hands.
        ctl.set_enabled(False)
        if not ctl.wait_enabled(0.5):
            failures.append("set_enabled(False) was not acted on in 0.5 s")
        if ctl.registered:
            failures.append(f"the switch is off but it still reports holding "
                            f"{ctl.registered}")
        if not can_grab(PROBE_MODS, VK_F6):
            failures.append("the key is STILL taken after switching off - the "
                            "numpad never goes back to the game (#134)")

        # A resume must not undo the switch: the menu closes after a rebind
        # and would otherwise put the keys straight back on.
        ctl.resume()
        time.sleep(0.3)
        if not can_grab(PROBE_MODS, VK_F6):
            failures.append("closing the menu put the hotkeys back on although "
                            "the master switch is off (#134)")

        # And a rebind while off must not sneak one in either.
        ctl.rebind({1: (PROBE_MODS, VK_F6, "toggle", "Ctrl+Alt+F6")})
        ctl.wait_rebound(0.5)
        time.sleep(0.2)
        if not can_grab(PROBE_MODS, VK_F6):
            failures.append("a rebind registered a hotkey while the switch is off")

        # ON again: the controller takes the key back.
        ctl.set_enabled(True)
        if not ctl.wait_enabled(0.5):
            failures.append("set_enabled(True) was not acted on in 0.5 s")
        if "Ctrl+Alt+F6" not in ctl.registered:
            failures.append(f"switching back on did not register the key "
                            f"(registered={ctl.registered}, failed={ctl.failed})")
        if can_grab(PROBE_MODS, VK_F6):
            failures.append("the key is free although the switch is back on")
    finally:
        ctl.stop()
        time.sleep(0.3)


def check_off_from_the_start(failures: list) -> None:
    """Startup with the switch off must never register anything at all.

    The bench case behind it: a user who plays a numpad game turns hotkeys
    off, and the keys must not be held even for the first seconds of a launch.
    """
    if not can_grab(PROBE_MODS, VK_F6):
        print("SKIP: Ctrl+Alt+F6 is taken - the startup case cannot be probed")
        return
    commands: queue.Queue = queue.Queue()
    bindings = {1: (PROBE_MODS, VK_F6, "toggle", "Ctrl+Alt+F6")}
    ctl = HotkeyController(commands, bindings)
    ctl.set_enabled(False)          # as startup does, before start()
    ctl.start()
    try:
        if ctl.registered:
            failures.append(f"it registered {ctl.registered} although the "
                            f"switch was off before the thread started")
        if not can_grab(PROBE_MODS, VK_F6):
            failures.append("the key was taken at launch with the switch off")
    finally:
        ctl.stop()
        time.sleep(0.3)


PARAMS = {"intensity": 1.0, "local_tone": 0.5, "local_structure": 1.0,
          "skin_structure": -1.0, "style": 1}


def check_menu_row(failures: list) -> None:
    """The row exists, is drawn from the payload, and the click flips it.

    The click is driven through the real apply_menu_action on a real config
    file in a temporary directory, because half of what the switch promises is
    that the choice survives a restart: "set and forget" is the whole point of
    the request.
    """
    import commands
    import tempfile
    from overlay_ui import OverlayMenu
    import pygame

    if not pygame.font.get_init():
        pygame.font.init()
    menu = OverlayMenu(1.0, lambda size=14: pygame.font.Font(None, size))
    menu.lang = "en"
    if "hotkeys_enabled" not in menu.state:
        failures.append("the menu has no hotkeys_enabled in its state - "
                        "set_state would drop the value in silence and the "
                        "toggle would always draw as off")
        return

    tmp = Path(tempfile.mkdtemp(prefix="ns134-"))
    cfg_path = tmp / "config.json"
    cfg_path.write_text(json.dumps({"hotkeys_enabled": True, "profile": "Natural",
                                    "params": {}, "monitor": 0, "theme": "light"}),
                        encoding="utf-8")
    # The click: the real dispatch, on the real config object.
    st = SimpleNamespace(
        cfg={"hotkeys_enabled": True, "profile": "Natural", "theme": "light"},
        hotkeys=_FakeHotkeys(),
        display=SimpleNamespace(menu=menu, alert=lambda *a, **k: None),
        lang="en", params=dict(PARAMS), cfg_path=cfg_path, presets={},
        monitor=0, work_scale=1.0, split_pos=0.0, startup_menu=False,
        nr_small=True, window_hwnd=None, width=1920, height=1080,
        work_w=1920, work_h=1080,
    )
    for expect in (False, True):
        commands.apply_menu_action(st, ("toggle", "hotkeys_enabled"))
        if bool(st.cfg.get("hotkeys_enabled")) is not expect:
            failures.append(f"the toggle left hotkeys_enabled="
                            f"{st.cfg.get('hotkeys_enabled')!r}, expected "
                            f"{expect}")
        if st.hotkeys.last is not expect:
            failures.append(f"the toggle did not tell the controller "
                            f"({st.hotkeys.last!r}, expected {expect})")
        saved = json.loads(cfg_path.read_text(encoding="utf-8"))
        if bool(saved.get("hotkeys_enabled")) is not expect:
            failures.append(f"the choice did not reach config.json "
                            f"({saved.get('hotkeys_enabled')!r}, expected "
                            f"{expect}) - it would not survive a restart")

    # And the payload really carries it, so the toggle draws the real state
    # instead of always looking off.
    payload = _settings_io()._menu_layout_payload(
        {"hotkeys_enabled": False, "profile": "Natural", "menu_scale_auto": True},
        dict(PARAMS), 0, "en", 1.0, 0.0, False, True, menu)
    if payload.get("hotkeys_enabled") is not False:
        failures.append(f"the menu payload reports hotkeys_enabled="
                        f"{payload.get('hotkeys_enabled')!r} for a config with "
                        f"it off")


class _FakeHotkeys:
    """The controller's observable surface, without spawning a thread."""

    def __init__(self):
        self.last = None
        self.registered = []

    def set_enabled(self, on):
        self.last = bool(on)

    def wait_enabled(self, timeout=0.5):
        return True


def main() -> int:
    failures: list = []
    check_shipped_default(failures)
    print("shipped default and the validator: checked")
    check_switch_releases(failures)
    print("the switch releases the key, a resume does not undo it: checked")
    check_off_from_the_start(failures)
    print("launching with the switch off holds nothing: checked")
    check_menu_row(failures)
    print("the menu row and its click: checked")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: the master switch really releases the keys (#134)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
