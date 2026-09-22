"""A slow scroll survives the noise filter, on content with structure.

The upstream test drove DIS with a mock over FLAT frames and expected a
uniform one-pixel scroll to survive everywhere. Two of its premises do
not hold in this codebase, both deliberately:

- MV validation (A2) drops a vector where it explains the pixel no better
  than standing still - flat content is exactly that case, and
  test_motion_trust pins the behaviour;
- the flow grid is 320x180 at every source size, so one REAL pixel is
  0.125 flow pixels at 4K, below the 0.5-pixel noise floor by design.

So this variant scrolls a pattern with real structure by two flow pixels
(above the floor at every size) and asserts what the guards guarantee:
the moving region keeps its vector scaled to real pixels, the subpixel
noise stays at zero, and a genuinely static frame reports none.
"""
from pathlib import Path
import sys
from unittest.mock import Mock
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))  # the modules live in app/
from guides import TemporalGuideGenerator


def main():
    for width, height in ((640, 360), (2560, 1440), (1440, 2560)):
        guide = TemporalGuideGenerator(width, height, emit_small=True)
        shape = (guide.flow_height, guide.flow_width)
        flow = np.zeros((*shape, 2), np.float32)
        # Two flow pixels of horizontal motion on the left half - in real
        # pixels that is 2 * width / flow_width at every size.
        flow[:, :shape[1] // 2, 0] = 2.0
        flow[:, shape[1] // 2:, 1] = .1
        guide.dis = Mock()
        guide.dis.calc.return_value = flow
        # Texture: period-8 vertical stripes. Rolling by two pixels keeps
        # the scene score small (only the boundary columns change) while
        # giving the static hypothesis something to be wrong about.
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
        texture = np.where((xx % 8) < 4, 80, 160).astype(np.uint8)
        first = texture.copy()
        guide.process(gray=first)
        # The stripes travel left by two flow pixels (== the flow handed in).
        second = np.roll(first, -2, axis=1)
        result = guide.process(gray=second)
        assert not result.reset
        moving = result.motion[:, :shape[1] // 2, 0]
        expected = 2.0 * width / guide.flow_width
        assert np.any(moving != 0), "the scrolled region lost its motion"
        assert np.allclose(moving[moving != 0], expected, atol=1e-3)
        assert not np.any(result.motion[:, shape[1] // 2:])
        static = guide.process(gray=second)
        assert not np.any(static.motion)
    print('PASS: slow scrolling preserved; subpixel noise and static motion rejected')


if __name__ == '__main__':
    main()