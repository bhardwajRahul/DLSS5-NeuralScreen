r"""The surround of a window-sized frame is painted, every frame.

In one-window mode the layer is the whole screen while the frame is the
size of the captured window, and show() blits the frame where the window
is. The swap chain is flip-discard: what was on screen last time is not
in the back buffer, so anything show() does not paint is undefined - in
practice the frame from two flips ago. Left alone it reads as a still
photograph of the desktop pinned over the real one, and nothing in it
reacts (user, 13.09).

Three things are pinned here, because two earlier attempts each got one
wrong (d10cfd4/e46a24b blinked - two writers of the layered attributes;
ca1801d stayed magenta - a keyed fill on a layer that was not keyed):

  * every pixel outside the frame is CHROMA_KEY and the frame survives;
  * the layer is ACTUALLY keyed when the frame is smaller than it - the
    missing check that let the magenta screen through;
  * the request is idempotent: a second frame with the same geometry
    must not write the attributes again, and a frame that covers the
    layer must go back to a plain alpha with no key.

The attribute writes are observed through a spy over user32, so "the key
is on" is checked as the call the user's screen would actually obey.
[#60 window mode]

Run:  runtime\python.exe tests\test_window_surround.py
"""
import os
import sys
import time
from pathlib import Path


def _repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "main.py").is_file():
            return p
    return start


BASE = _repo_root(Path(__file__).resolve().parent)
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np  # noqa: E402
import pygame  # noqa: E402


class _SpyUser32:
    """user32 with SetLayeredWindowAttributes recorded, everything else real."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def SetLayeredWindowAttributes(self, hwnd, key, alpha, flags):
        self.calls.append((key, alpha, flags))
        return 1

    def __getattr__(self, name):
        return getattr(self._real, name)


def main() -> int:
    failures = []
    pygame.init()
    disp = None
    try:
        import display as display_mod

        key = display_mod.CHROMA_KEY
        key_ref = (key[2] << 16) | (key[1] << 8) | key[0]
        disp = display_mod.Display(1920, 1080, click_through=False)

        # Dirty the layer the way a previous frame would have: this is the
        # stale picture the user was looking at.
        disp.screen.fill((11, 22, 33))

        # A captured window at (400, 200), 640x360, on the primary monitor.
        disp._frame_size = (640, 360)
        disp.set_window_layer(400, 200, 640, 360)

        # The attribute writes go through a spy; get_wm_info is faked so the
        # write path is reached at all under the dummy video driver. Installed
        # AFTER set_window_layer: that call may re-apply the layer through the
        # real user32 (a no-op under dummy), and the counts below measure the
        # writes show() itself makes.
        spy = _SpyUser32(display_mod.user32)
        real_wm_info = display_mod.pygame.display.get_wm_info
        display_mod.user32 = spy
        display_mod.pygame.display.get_wm_info = lambda: {"window": 1}
        probes = []
        try:
            frame = np.empty((360, 640, 4), dtype=np.uint8)
            frame[:, :, 0] = 200
            frame[:, :, 1] = 100
            frame[:, :, 2] = 50
            frame[:, :, 3] = 255
            disp.show(frame)

            # The surround, BEFORE anything else is drawn over the screen.
            # Four probes around the frame, one per rectangle the fill covers.
            for name, pos in (("above", (960, 100)),
                              ("below", (960, 900)),
                              ("left", (100, 380)),
                              ("right", (1500, 380))):
                probes.append((name, tuple(disp.screen.get_at(pos))[:3]))
            probes.append(("frame", tuple(disp.screen.get_at((700, 380)))[:3]))
            first_writes = len(spy.calls)
            first_call = spy.calls[0] if spy.calls else None

            # The same frame again: a per-frame rewrite of the attributes is
            # the blink class this design exists to remove.
            disp.show(frame)
            second_writes = len(spy.calls)

            # A frame that covers the layer: back to a plain alpha, no key.
            # Made larger than any monitor this could run on, so it covers
            # even if the layer was expanded to the real screen.
            big = np.empty((2160, 3840, 4), dtype=np.uint8)
            big[:, :, 3] = 255
            disp.show(big)
            third_writes = len(spy.calls)
            covering_call = (spy.calls[second_writes]
                             if len(spy.calls) > second_writes else None)
            covering_state = disp._layer_state

            # A window as WIDE as the screen but shorter: it does not cover
            # the layer either, so it must get the keyed surround too. The
            # width-only test this guards against missed exactly this shape.
            wide = np.empty((900, 1920, 4), dtype=np.uint8)
            wide[:, :, 3] = 255
            disp.show(wide)
            fourth_writes = len(spy.calls)
            wide_call = (spy.calls[third_writes]
                         if len(spy.calls) > third_writes else None)
            wide_state = disp._layer_state

            # The mode-switch fade-out over a keyed picture: the surround is
            # painted pure key and must STAY exactly the key while the veil
            # dissolves - if the veil's writes drop the key bit, or the dim
            # blends over the surround, the last frames of every window-mode
            # switch show a magenta band (measured (241,3,236) at alpha 248
            # before this was pinned). Runs INSIDE the spy zone: the veil
            # writes its own attributes and they must be observed as the
            # window would really get them.
            disp.enter_switch_mode(frame, disp.width, disp.height)
            disp.exit_switch_mode()
            now = time.monotonic()
            fade_steps = 0
            for i in range(1, 4):
                disp._finish_switch_if_due(
                    now + display_mod.SWITCH_FADE_OUT * i / 4.0)
                if not disp.is_switch_active():
                    break
                disp.show(frame)
                fade_steps += 1
                surround = tuple(disp.screen.get_at((100, 100)))[:3]
                if surround != key:
                    failures.append(
                        f"mid-fade the surround is {surround}, want the "
                        f"pure key {key} - a magenta band around the window")
                    break
                if spy.calls:
                    _, _, fade_flags = spy.calls[-1]
                    if not (fade_flags & display_mod.LWA_COLORKEY):
                        failures.append(
                            "a veil fade-out write dropped the colour key "
                            "while a keyed picture was coming up")
                        break
            if fade_steps == 0:
                failures.append("the fade-out never ran on the keyed picture")
            # Let the fade finish and the teardown land while the spy is
            # still in place (the teardown re-applies the layer itself).
            disp._finish_switch_if_due(
                time.monotonic() + display_mod.SWITCH_FADE_OUT + 0.1)
        finally:
            display_mod.user32 = spy._real
            display_mod.pygame.display.get_wm_info = real_wm_info

        for name, got in probes:
            if name == "frame":
                if got != (200, 100, 50):
                    failures.append(
                        f"the frame itself is {got}, want (200, 100, 50) - "
                        f"the fill ate the picture")
            elif got != key:
                failures.append(
                    f"{name} of the frame is {got}, want the key {key} - the "
                    f"surround was left as whatever the last flip held")

        # The layer must ACTUALLY be keyed while a window-sized frame is up:
        # a keyed fill on a layer without a key is a magenta screen, which is
        # exactly what shipped once because nothing checked this half.
        if first_writes != 1:
            failures.append(
                f"the first window-sized frame wrote the attributes "
                f"{first_writes} time(s), want exactly 1")
        elif first_call is not None:
            call_key, call_alpha, call_flags = first_call
            if not (call_flags & display_mod.LWA_COLORKEY):
                failures.append(
                    f"the layer was not colour-keyed for a window-sized frame "
                    f"(flags {call_flags:#x}) - the surround fill would show "
                    f"as a solid colour")
            if call_alpha != 255:
                failures.append(
                    f"the keyed layer's alpha is {call_alpha}, want 255 - a "
                    f"window-sized frame at BG_ALPHA would be see-through")
            if call_key != key_ref:
                failures.append(
                    f"the colour key is {call_key:#x}, want {key_ref:#x} "
                    f"(CHROMA_KEY {key})")

        if second_writes != first_writes:
            failures.append(
                f"a second identical frame wrote the attributes "
                f"{second_writes - first_writes} more time(s) - per-frame "
                f"rewriting is the blink of d10cfd4/e46a24b")

        if third_writes != second_writes + 1:
            failures.append(
                f"a frame covering the layer wrote "
                f"{third_writes - second_writes} time(s), want 1 (back to "
                f"the plain alpha)")
        elif covering_call is not None:
            _, call_alpha, call_flags = covering_call
            if call_flags & display_mod.LWA_COLORKEY:
                failures.append(
                    "a frame covering the layer kept the colour key")
            if call_alpha != 255:
                failures.append(
                    f"a covering frame's alpha is {call_alpha}, want 255")
            if covering_state != display_mod.LAYER_OPAQUE:
                failures.append(
                    f"the layer state after a covering frame is "
                    f"{covering_state!r}, want "
                    f"{display_mod.LAYER_OPAQUE!r}")

        # The wide-but-short window: not covered either, so the key must
        # come back on it (and the state must not be left OPAQUE).
        if fourth_writes != third_writes + 1:
            failures.append(
                f"a wide-but-short frame wrote the attributes "
                f"{fourth_writes - third_writes} time(s), want 1 (back to "
                f"the keyed picture)")
        elif wide_call is not None:
            _, call_alpha, call_flags = wide_call
            if not (call_flags & display_mod.LWA_COLORKEY):
                failures.append(
                    "a wide-but-short frame (screen-wide, shorter than the "
                    "layer) was not colour-keyed - its bottom strip would "
                    "hold a stale picture")
            if wide_state != display_mod.LAYER_PICTURE_KEYED:
                failures.append(
                    f"the layer state after a wide-but-short frame is "
                    f"{wide_state!r}, want "
                    f"{display_mod.LAYER_PICTURE_KEYED!r}")
    finally:
        try:
            if disp is not None:
                disp.close()
        except Exception:
            pass
        pygame.quit()

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the surround of a window-sized frame is keyed out, the layer "
          "is really keyed, and per-frame shows do not rewrite the attributes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
