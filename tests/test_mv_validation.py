"""MV validation: noise-floor vectors are zeroed, real motion survives.

DIS on a static desktop reports small noise vectors (capture noise,
cursor jitter, UI shimmer). NGX would treat them as real motion and
smear text/UI. The validation zeroes vectors below the noise floor
(0.5 px in flow space) before the upscale.

Checked: a synthetic shift of 2 px in flow space survives (magnitude
above the floor); a sub-floor shift (0.2 px) is zeroed; a static frame
produces no motion at all.
"""
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "app"))  # the modules live in app/

import numpy as np  # noqa: E402
from guides import TemporalGuideGenerator  # noqa: E402

W, H = 1280, 720
FW, FH = 320, 180


def _frame(shift: int = 0) -> np.ndarray:
    """A synthetic scene: a large bright bar on a dark background, shifted.

    The bar covers a third of the frame so the shift moves enough pixels
    to clear the static-screen threshold (scene_score > 0.001).
    """
    img = np.zeros((H, W, 4), dtype=np.uint8)
    img[100:500, 200 + shift:800 + shift, 0] = 200
    img[100:500, 200 + shift:800 + shift, 1] = 200
    img[100:500, 200 + shift:800 + shift, 2] = 200
    img[..., 3] = 255
    return img


def main() -> int:
    failures = []
    g = TemporalGuideGenerator(W, H, flow_width=FW, emit_small=True)

    # 1. A static frame: no motion at all.
    f0 = _frame(0)
    g.process(f0)
    f1 = _frame(0)
    out = g.process(f1)
    if out.reset:
        failures.append("a static frame must not reset")
    if np.abs(out.motion).max() > 0:
        failures.append(f"a static frame produced motion: {np.abs(out.motion).max():.3f}")

    # 2. A real shift (4 px in flow space ~ 16 px at 4K): survives.
    g2 = TemporalGuideGenerator(W, H, flow_width=FW, emit_small=True)
    g2.process(_frame(0))
    out2 = g2.process(_frame(4))
    mag = np.hypot(out2.motion[..., 0], out2.motion[..., 1])
    if mag.max() < 1.0:
        failures.append(f"a 4 px shift was zeroed: max {mag.max():.3f}")

    # 3. The noise floor itself. The old version injected a 1 px shift and
    #    could not fail: a 1 px move of that bar is below the scene gate
    #    (guides.py:308), so DIS was never called, and even when it was, the
    #    static-hypothesis test dropped the vector before the floor could be
    #    reached (audit: WEAK). The floor is tested here in isolation:
    #      - a gray pair that passes the scene gate, so the flow path runs;
    #      - dis.calc stubbed to return ONE known vector;
    #      - _moved stubbed to say "yes, it explains the pixel", so the
    #        static hypothesis cannot be what drops it.
    #    Only the floor is left, and it has to be what decides.
    from types import SimpleNamespace

    # The product's OWN floor, read from the source: if it is turned off
    # (0.0) the feature is gone, and the case below would otherwise still
    # pass because it sets its own value (audit: WEAK - the old version could
    # not fail on any mutation of the rule it names).
    src = (Path(BASE) / "app" / "guides.py").read_text(encoding="utf-8")
    mfloor = re.search(r"self\._flow_noise_floor\s*=\s*([0-9.]+)", src)
    if not mfloor:
        failures.append("guides.py no longer sets _flow_noise_floor - the "
                        "noise floor is not configured at all")
        product_floor = 0.5
    else:
        product_floor = float(mfloor.group(1))
        print(f"    the product's noise floor: {product_floor}")
        if product_floor <= 0.0:
            failures.append(
                f"the product's noise floor is {product_floor} - DIS noise "
                f"vectors are no longer zeroed and NGX smears text/UI")

    # The threshold has to be READ from the attribute, not hardcoded: use the
    # product's own value, not one this test hands it.
    def _floor_case(vec, floor):
        g = TemporalGuideGenerator(W, H, flow_width=FW, emit_small=True)
        if floor is not None:
            g._flow_noise_floor = floor
        # Two gray frames a little apart: above the 0.001 gate, well below
        # the 0.24 reset. Gray is flow-sized, so it is used as-is.
        a = np.full((FH, FW), 40, np.uint8)
        b = np.full((FH, FW), 60, np.uint8)
        g.process(gray=a)
        flow = np.zeros((FH, FW, 2), np.float32)
        flow[..., 0] = vec[0]
        flow[..., 1] = vec[1]
        g.dis = SimpleNamespace(calc=lambda *_: flow.copy())
        g._moved = lambda *a, **k: np.ones((FH, FW), bool)
        out = g.process(gray=b)
        return float(np.hypot(out.motion[..., 0], out.motion[..., 1]).max())

    # A vector under the PRODUCT's floor is zeroed (floor=None keeps its value)...
    half = max(product_floor / 2.0, 1e-6)
    sub = _floor_case((half, 0.0), None)
    print(f"    |v|={half:.3f} with the product floor -> max motion {sub:.3f}")
    if sub > 1e-6:
        failures.append(f"a sub-floor vector survived: {sub:.3f}")
    # ...and the SAME vector survives when the floor is switched off, so the
    # case above cannot be passing for some other reason.
    sub_open = _floor_case((half, 0.0), 0.0)
    if sub_open <= 1e-6:
        failures.append("with the floor off the same vector is still zeroed - "
                        "something else is dropping it, so the floor is not "
                        "what this case measures")
    # And a vector clearly above the floor is kept.
    over = _floor_case((product_floor + 3.0, 0.0), None)
    print(f"    |v|={product_floor + 3.0:.3f} with the product floor "
          f"-> max motion {over:.3f}")
    if over <= 1e-6:
        failures.append(f"an above-floor vector was zeroed: {over:.3f}")

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} - {failures}")
        return 1
    print("OK: noise-floor vectors are zeroed, real motion survives")
    return 0


if __name__ == "__main__":
    sys.exit(main())
