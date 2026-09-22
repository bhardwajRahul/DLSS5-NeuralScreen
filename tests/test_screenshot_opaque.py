r"""A PNG screenshot has no transparent rectangle where the panel is.

THE MEASUREMENT (#107, a reporter's v2.0.1 screenshot)

    alpha=255: 1512092/2073600 (72.92%)     <- the desktop
    not fully opaque: 561508, bounding box (1380, 18, 1920, 1058)
    inside that rectangle: alpha=0 for 99.98% of pixels
    and the panel's colours ARE in the file: (38,38,36) body, (217,119,87) accent

He attached it with "also note the transparent section". In a viewer that
honours alpha the panel area is a hole.

WHY. The frame is drawn onto a pygame surface built with
image.frombuffer(rgba, ..., "RGBX") - the X byte is "unused" for pygame's
renderer, and frombuffer does not copy, so the surface shares the array the file
is written from. The panel is composited through an SRCALPHA scratch, and the
value left in that byte is what the writer picks up. The PNG path keeps the
channel (COLOR_RGBA2BGRA); the JPEG path drops it, which is why the fault only
ever appeared in a PNG - the default format.

WHAT THIS LOCKS

1. The writer's output is opaque over the whole image, including a region drawn
   through an SRCALPHA overlay - not just over the untouched part.
2. The JPEG control stays clean, so this is the alpha handling and not the
   compositing.
3. The caller's frame is NOT modified by the save: forcing the channel opaque
   must work on a copy.

Uses the REAL dialogs.save_image and reads the file back with an independent
reader (cv2 for the pixels, numpy for the channel), so a change in the writer is
what the test sees.

Run:  runtime\python.exe tests\test_screenshot_opaque.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

import dialogs  # noqa: E402

PANEL = (60, 40, 120, 80)      # x, y, w, h - the region drawn through SRCALPHA


def build_frame(w=240, h=160) -> np.ndarray:
    """A capture-sized RGBA frame, then a panel patch composited as the app does.

    The panel is drawn with pygame exactly like Display._draw_menu_at_screen_position:
    an SRCALPHA scratch, filled with the panel colour at BG_ALPHA, blitted onto a
    surface that shares the frame's memory. That is what leaves 0 in the X byte.
    """
    import pygame
    from display import BG_ALPHA

    frame = np.zeros((h, w, 4), np.uint8)
    frame[..., 0:3] = (6, 14, 32)
    frame[..., 3] = 255
    surf = pygame.image.frombuffer(frame, (w, h), "RGBX")
    scratch = pygame.Surface((PANEL[2], PANEL[3]), pygame.SRCALPHA)
    scratch.fill((0, 0, 0, 0))
    pygame.draw.rect(scratch, (38, 38, 36, BG_ALPHA),
                     pygame.Rect(0, 0, PANEL[2], PANEL[3]))
    surf.blit(scratch, (PANEL[0], PANEL[1]))
    return frame


def alpha_inside(path: Path) -> list[int]:
    import cv2
    im = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if im is None:
        return []
    if im.ndim != 3 or im.shape[2] != 4:
        return [-1]                       # no alpha channel at all
    x, y, w, h = PANEL
    return sorted(set(im[y:y + h, x:x + w, 3].ravel().tolist()))


def main() -> int:
    import cv2
    pygame_ok = True
    try:
        import pygame
        pygame.init()
        pygame.display.set_mode((1, 1), pygame.HIDDEN)
    except Exception as exc:
        pygame_ok = False
        print(f"FAIL: pygame unavailable ({exc})")
        return 1

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="ns-shot-opaque-") as temp:
        work = Path(temp)

        frame = build_frame()
        before = frame.copy()
        png = work / "shot.png"
        if not dialogs.save_image(png, frame):
            print("FAIL: the writer refused to write a PNG")
            return 1

        inside = alpha_inside(png)
        print(f"PNG alpha inside the panel region: {inside}")
        if inside == [-1]:
            failures.append("the PNG has no alpha channel to check")
        elif inside and any(a != 255 for a in inside):
            failures.append(
                f"the PNG carries alpha {inside} over the panel - a viewer that "
                f"honours alpha shows a hole there (#107: 'the transparent "
                f"section')")
        if not png.is_file() or png.stat().st_size == 0:
            failures.append("nothing was written")

        # The caller's frame must be untouched: the fix works on a copy.
        if not np.array_equal(frame, before):
            failures.append(
                "the save modified the caller's frame - the alpha is forced "
                "on a copy, not in place")

        # Control: the same bytes as JPEG must stay opaque, proving the fault
        # is the alpha handling and not the compositing.
        jpg = work / "shot.jpg"
        if not dialogs.save_image(jpg, build_frame()):
            failures.append("the writer refused to write a JPEG")
        else:
            jalpha = alpha_inside(jpg)
            print(f"JPEG alpha inside the same region (control): {jalpha}")
            if jalpha and jalpha != [-1] and any(a != 255 for a in jalpha):
                failures.append(
                    f"the JPEG control is not opaque either ({jalpha}) - the "
                    f"fault is in the compositing, not in the alpha handling")

        # And the desktop part stays opaque in both.
        im = cv2.imread(str(png), cv2.IMREAD_UNCHANGED)
        if im is not None and im.ndim == 3 and im.shape[2] == 4:
            outside = sorted(set(im[0:10, 0:10, 3].ravel().tolist()))
            print(f"PNG alpha over the untouched desktop: {outside}")
            if outside != [255]:
                failures.append(
                    f"the desktop part is not opaque ({outside}) - the "
                    f"conversion is wrong, not just the panel region")

    try:
        pygame.quit()
    except Exception:
        pass

    if failures:
        print("FAIL: a PNG screenshot can carry transparency")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: PNG screenshots are opaque everywhere, the JPEG control agrees, "
          "and the caller's frame is untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
