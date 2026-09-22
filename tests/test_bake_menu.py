"""An open menu ends up in the recorded frame and in the screenshot.

Our window is marked WDA_EXCLUDEFROMCAPTURE, so it cannot appear in a capture
of the desktop - and therefore cannot appear in the frame we save, either.
Everything of ours that must be IN the file is drawn onto the frame by
`display.draw_capture_overlay`, on top of the pixels the capture already
returned. That is what puts the settings panel into a screenshot (and into a
recording) while the HUD and the watermark stay out.

Checked, off-screen with the dummy video driver:

* a CLOSED menu leaves the frame byte-for-byte alone - the old failure mode
  was a panel that lingered in every shot;
* an OPEN menu covers its own panel rect, and nothing outside that rect is
  touched (a bake that bled outside would paint over the picture);
* the "grabbed an edge" hover highlight does not reach the file (it is cursor
  state, and it used to freeze into the frame).

This was `test_bake_menu.py` in the project root, run by nobody - the suite
registry never listed it, so the screenshot behaviour it guards had no
regression check at all (audit: DEAD, same class as test_spout_library).

Run:  runtime\\python.exe tests\\test_bake_menu.py
"""
import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import numpy as np  # noqa: E402
import pygame  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))  # the modules live in app/

from display import Display  # noqa: E402

W, H = 1280, 720
FRAME_RGB = (17, 34, 51)


def make_frame():
    frame = np.zeros((H, W, 4), dtype=np.uint8)
    frame[..., 0], frame[..., 1], frame[..., 2] = FRAME_RGB
    frame[..., 3] = 255
    return np.ascontiguousarray(frame)


def surface_of(frame):
    return pygame.image.frombuffer(frame, (frame.shape[1], frame.shape[0]), "RGBX")


def _menu(disp):
    disp.set_hud({"fps": 60.0, "status": "NR ON", "resolution": f"{W}x{H}",
                  "profile": "Strong / Cinematic", "frames": 10})
    disp.menu.set_state({
        "nr": True, "profile": "Strong / Cinematic",
        "profiles": ["Faithful", "Natural", "Strong / Cinematic"],
        "params": {"intensity": 1.65, "local_tone": 1.40,
                   "local_structure": 1.50, "skin_structure": 1.00},
        "work_size": "832x468", "open_on_start": True,
    })
    return disp


def main() -> int:
    failures = []
    disp = _menu(Display(W, H, fullscreen=False))
    try:
        # 1. The menu is closed - the frame must not change.
        frame = make_frame()
        before = frame.copy()
        disp.menu.visible = False
        disp.draw_capture_overlay(surface_of(frame))
        if not np.array_equal(frame, before):
            failures.append("a closed menu changed the frame - a stale panel "
                            "would appear in every shot")

        # 2. The menu is open - the panel must appear, and only it.
        frame = make_frame()
        disp.menu.visible = True
        disp.draw_capture_overlay(surface_of(frame))
        r = disp.menu.panel_rect
        if r.w == 0 or r.h == 0:
            failures.append("the open menu's panel has zero size - nothing "
                            "would be baked")
        else:
            inside = frame[r.y + 5:r.bottom - 5, r.x + 5:r.right - 5, :3]
            changed = float((inside != np.array(FRAME_RGB, np.uint8))
                            .any(axis=2).mean())
            print(f"    panel {r.w}x{r.h} @ {r.x},{r.y} - "
                  f"pixels changed: {changed:.1%}")
            if changed < 0.9:
                failures.append(f"the panel covered only {changed:.1%} of its "
                                f"own area - the bake is partial")
            outside_ok = True
            for y, x in ((2, 2), (H - 3, W - 3), (2, W - 3)):
                if r.collidepoint(x, y):
                    continue
                if tuple(frame[y, x, :3]) != FRAME_RGB:
                    outside_ok = False
            if not outside_ok:
                failures.append("the frame is damaged outside the panel")

        # 3. The hover highlight does not reach the file.
        frame_hover = make_frame()
        disp.menu.hover = "title"
        disp.draw_capture_overlay(surface_of(frame_hover))
        if not np.array_equal(frame_hover, frame):
            failures.append("the hover state leaked into the file")
        if disp.menu.hover != "title":
            failures.append("the hover state was not restored after the bake")
    finally:
        disp.close()

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: the open menu is baked in, a closed one is not, and the hover "
          "does not leak")
    return 0


if __name__ == "__main__":
    sys.exit(main())
