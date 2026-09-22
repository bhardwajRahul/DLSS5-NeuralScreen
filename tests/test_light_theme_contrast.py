r"""WCAG contrast contract for the warm-clay light menu theme.

Run: runtime\python.exe tests\test_light_theme_contrast.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

from overlay_ui import THEMES  # noqa: E402


def _luminance(colour: str) -> float:
    channels = [int(colour[pos:pos + 2], 16) / 255.0 for pos in (1, 3, 5)]
    linear = [value / 12.92 if value <= 0.04045
              else ((value + 0.055) / 1.055) ** 2.4
              for value in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    hi, lo = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    failures = []
    light = THEMES["light"]

    # All colours used to render ordinary text meet 4.5:1 on every surface
    # they can appear on. The filled clay controls use the cream background
    # as their label colour, so that pair is included too.
    text_pairs = [
        ("text", "bg"), ("text", "surface"),
        ("muted", "bg"), ("muted", "surface"),
        ("accent", "bg"), ("accent", "surface"),
        ("ok", "bg"), ("ok", "surface"),
        ("danger", "bg"), ("danger", "surface"),
        ("bg", "accent"),
    ]
    for foreground, background in text_pairs:
        ratio = _contrast(light[foreground], light[background])
        print(f"text {foreground}/{background}: {ratio:.2f}:1")
        if ratio < 4.5:
            failures.append(f"{foreground}/{background} is {ratio:.2f}:1, "
                            "below 4.5:1")

    # Field boundaries and the keyboard focus indicator are non-text UI.
    for foreground, background in (
            ("border", "bg"), ("border", "surface"),
            ("focus", "bg"), ("focus", "surface")):
        ratio = _contrast(light[foreground], light[background])
        print(f"UI {foreground}/{background}: {ratio:.2f}:1")
        if ratio < 3.0:
            failures.append(f"{foreground}/{background} is {ratio:.2f}:1, "
                            "below 3:1")

    # Focus must remain obvious in the dark theme too; this is independent
    # of the light-theme WCAG floor but part of the keyboard contract.
    dark = THEMES["dark"]
    for background in ("bg", "surface"):
        ratio = _contrast(dark["focus"], dark[background])
        print(f"dark focus/{background}: {ratio:.2f}:1")
        if ratio < 3.0:
            failures.append(f"dark focus/{background} is only {ratio:.2f}:1")

    # Keep the visual language: the accent is recognisably red-clay, not a
    # generic blue accessibility patch.
    accent = tuple(int(light["accent"][pos:pos + 2], 16) for pos in (1, 3, 5))
    if not (accent[0] > accent[1] > accent[2]):
        failures.append(f"light accent lost the warm-clay hue: {accent}")

    for failure in failures:
        print("FAIL:", failure)
    if failures:
        return 1
    print("OK: light text, controls and focus meet WCAG contrast thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
