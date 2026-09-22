"""Motion vectors where nothing moved are dropped (A2).

The desktop is mostly still, and optical flow is worst exactly where the
picture matters most: on text, icons, window borders. When a window slides
across a page of text, DIS finds "motion" on the text as well - text beside
a moving edge looks explainable by a shift - and NGX, handed those vectors,
smears the text it should have left alone.

The noise floor that was already there is a test of LENGTH. It cannot see a
wrong vector that happens to be long, which is this whole class of error.

What guards it now is the static hypothesis: warp the previous frame by the
vector and keep the vector only if it explains the pixel better than
standing still does. Measured against the usual alternative - a second DIS
pass and a forward/backward consistency check - it is three times cheaper
and catches far more (the numbers are in guides.py, beside the constants).

This pins the behaviour on synthetic frames, so it needs no captures and no
GPU:

1. Video playing inside a window leaves the page around it alone.
2. What really moves still gets its vectors - the guard must not simply
   zero everything.
3. A verdict that changes does not change straight back. A guard
   re-decided from scratch every frame makes threshold cells alternate, and
   the motion field alternates with them - the guard becomes a source of
   the shimmer it exists to remove. Measured before the memory was added:
   8.10% of the live cells changed verdict between frames on a game scene
   and 49.2% of those flipped straight back.
4. The cost stays near the measured budget.

Negative control: with the static hypothesis disabled the same scene puts
false vectors on 5.2% of the still page instead of 0.3%, so the test is
measuring the guard and not the scene.

Run:  runtime\\python.exe tests\\test_motion_trust.py
"""
import sys
import time
from pathlib import Path

import cv2
import numpy as np

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

from guides import TemporalGuideGenerator  # noqa: E402

# A 4K screen with Boost at 0.65 - the grid the flow really runs on.
WORK_W, WORK_H = 2496, 1404
SHIFT = 90           # how far the content inside the window travels
REPEAT = 20
# The static hypothesis was measured at +0.67..+0.72 ms on this grid. The
# ceiling is generous on purpose: this runs on whatever machine the suite
# runs on, and the point is to catch a change of order, not jitter.
BUDGET_MS = 3.0


def text_page(w: int, h: int) -> np.ndarray:
    """Rows of small marks - a stand-in for a page of text.

    Low contrast on purpose (60 on 40), and dense. That is where flow goes
    wrong: bright text on white is easy to match, and DIS leaves it alone.
    Dark-themed body copy is ambiguous at the flow grid, so the motion of
    the window next to it propagates straight into it - measured, this
    scene puts false vectors on 5.2% of the still page without the guard
    and 0.15% with high-contrast bars instead, which would not have
    reproduced the problem at all.
    """
    img = np.full((h, w), 40, dtype=np.uint8)
    rng = np.random.default_rng(4)
    for y in range(40, h - 20, 14):
        x = 30
        while x < w - 40:
            word = int(rng.integers(30, 90))
            img[y:y + 5, x:x + word] = 60
            x += word + int(rng.integers(8, 18))
    return img


def texture(w: int, h: int) -> np.ndarray:
    """Something with structure at every scale, to stand in for a window."""
    rng = np.random.default_rng(11)
    small = rng.integers(0, 255, size=(h // 8, w // 8)).astype(np.uint8)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def rgba(gray: np.ndarray) -> np.ndarray:
    out = np.empty((gray.shape[0], gray.shape[1], 4), dtype=np.uint8)
    out[..., 0] = out[..., 1] = out[..., 2] = gray
    out[..., 3] = 255
    return np.ascontiguousarray(out)


# A video playing inside a window, the page around it still. This is the
# shape that reproduces the problem: a moving rectangle surrounded on all
# four sides by static text. A window sliding over one half of the screen
# does not - measured, it puts false vectors on 0.6% of the still half
# either way, because the noise floor already catches most of them.
RECT = (WORK_W // 5, WORK_H // 5, WORK_W * 4 // 5, WORK_H * 4 // 5)


def scene(dx: int):
    """A page of text, with a textured rectangle in the middle at dx."""
    frame = text_page(WORK_W, WORK_H)
    win = texture(WORK_W, WORK_H)
    M = np.float32([[1, 0, dx], [0, 1, dx // 3]])
    moved = cv2.warpAffine(win, M, (WORK_W, WORK_H), borderMode=cv2.BORDER_REFLECT)
    x0, y0, x1, y1 = RECT
    frame[y0:y1, x0:x1] = moved[y0:y1, x0:x1]
    return frame


def halves(mag: np.ndarray, fw: int, fh: int):
    """Split the flow grid into "nothing moved" and "something did"."""
    x0, y0, x1, y1 = RECT
    fx0, fy0 = int(x0 * fw / WORK_W), int(y0 * fh / WORK_H)
    fx1, fy1 = int(x1 * fw / WORK_W), int(y1 * fh / WORK_H)
    inside = np.zeros(mag.shape, dtype=bool)
    # A margin around the rectangle: the cells straddling its edge really do
    # see motion, and asking them to be still would be asking for a lie.
    inside[fy0 - 2:fy1 + 2, fx0 - 2:fx1 + 2] = True
    return mag[~inside], mag[fy0 + 2:fy1 - 2, fx0 + 2:fx1 - 2]


def run(disable_trust: bool):
    g = TemporalGuideGenerator(WORK_W, WORK_H, emit_small=True)
    if disable_trust:
        # The negative control: every vector is trusted, which is what the
        # code did before this guard existed.
        g._moved = lambda cur, prev, flow: np.ones(flow.shape[:2], dtype=bool)
    g.process(rgba(scene(0)))                 # first frame: no history
    guide = g.process(rgba(scene(-SHIFT)))
    motion = np.asarray(guide.motion, dtype=np.float32)
    mag = np.hypot(motion[..., 0], motion[..., 1])
    still, moving = halves(mag, g.flow_width, g.flow_height)
    return g, still, moving


def main() -> int:
    failures = []

    g, still, moving = run(disable_trust=False)
    false_pct = 100.0 * float((still > 0).mean())
    kept_pct = 100.0 * float((moving > 0).mean())
    print(f"    vectors on the text that did not move: {false_pct:.2f}%")
    print(f"    vectors on the window that did move:   {kept_pct:.1f}%")
    if false_pct > 1.0:
        failures.append(
            f"{false_pct:.2f}% of the still text got motion vectors - NGX "
            f"smears text on those, which is the whole point of the guard")
    if kept_pct < 40.0:
        failures.append(
            f"only {kept_pct:.1f}% of the moving half kept its vectors - the "
            f"guard is throwing away real motion, not just wrong motion")

    # The negative control has to fail, or the test is measuring nothing.
    _g2, still_off, _moving_off = run(disable_trust=True)
    off_pct = 100.0 * float((still_off > 0).mean())
    print(f"    negative control (guard disabled):     {off_pct:.2f}% on the text")
    if off_pct <= 2.0:
        failures.append(
            f"the negative control barely fails ({off_pct:.2f}%): without the "
            f"guard the text should be covered in false vectors, so this "
            f"scene no longer reproduces the problem and the test is blind")

    # 3. The verdict has memory: nothing may flip and flip straight back.
    g3 = TemporalGuideGenerator(WORK_W, WORK_H, emit_small=True)
    verdicts = []
    prev = None
    for i in range(10):
        cur = cv2.resize(scene(-SHIFT * i // 3), (g3.flow_width, g3.flow_height),
                         interpolation=cv2.INTER_AREA)
        if prev is not None:
            flow = g3.dis.calc(cur, prev, None)
            alive = np.hypot(flow[..., 0], flow[..., 1]) >= g3._flow_noise_floor
            verdicts.append((alive, alive & g3._moved(cur, prev, flow).copy()))
        prev = cur
    flips = backs = live = 0
    for i in range(1, len(verdicts) - 1):
        (aa, a), (ba, b), (ca, c) = verdicts[i - 1], verdicts[i], verdicts[i + 1]
        common = aa & ba
        changed = common & (a != b)
        flips += int(changed.sum())
        live += int(common.sum())
        backs += int((changed & ca & (c == a)).sum())
    flip_pct = 100.0 * flips / max(1, live)
    back_pct = 100.0 * backs / max(1, flips)
    print(f"    verdict changes {flip_pct:.2f}% of live cells, "
          f"{back_pct:.1f}% of them flip straight back")
    if back_pct > 5.0:
        failures.append(
            f"{back_pct:.1f}% of the verdict changes reverse on the very "
            f"next frame - the guard is re-deciding instead of remembering, "
            f"and an alternating mask is itself shimmer")

    # Cost: what the guard adds, on the real grid.
    cur = cv2.resize(scene(-SHIFT), (g.flow_width, g.flow_height),
                     interpolation=cv2.INTER_AREA)
    prev = cv2.resize(scene(0), (g.flow_width, g.flow_height),
                      interpolation=cv2.INTER_AREA)
    flow = g.dis.calc(cur, prev, None)
    g._moved(cur, prev, flow)
    t0 = time.perf_counter()
    for _ in range(REPEAT):
        g._moved(cur, prev, flow)
    ms = (time.perf_counter() - t0) / REPEAT * 1000.0
    print(f"    the guard costs {ms:.2f} ms per moving frame "
          f"at {g.flow_width}x{g.flow_height}")
    if ms > BUDGET_MS:
        failures.append(
            f"the guard costs {ms:.2f} ms, over the {BUDGET_MS:.1f} ms "
            f"ceiling - it was measured at 0.7 ms, so something changed order")

    if failures:
        for f in failures:
            print(f"FAIL: {f}")
        return 1
    print("OK: still text keeps its zero vectors, real motion keeps its own")
    return 0


if __name__ == "__main__":
    sys.exit(main())
