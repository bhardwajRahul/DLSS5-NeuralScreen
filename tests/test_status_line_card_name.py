"""The status line must be able to say which card runs the network (audit M4).

The readings hug the right edge and the card name took whatever was left in the
middle. The remainder measured 72 px at 1080p and 74 px at 4K - nearly the same,
because the reading block grows with the font while the panel scale stops at
user_scale 1.2 - so even "NVIDIA GeForce RTX 5070 Ti" was elided to about 63-72
px, i.e. "NVIDIA…", for every card tried. In German at 4K the room was 38 px,
below the old 40-unit floor, so the name was not drawn at all.

The card the network is running on is the one value in that line that cannot be
guessed from anywhere else, so it now gets its room first, capped at a third of
the bar, and the readings are laid out into what remains - a reading that does
not fit is dropped rather than clipped, because a half-number reads as a wrong
number.

Checked here with the real faces and the real layout, at the sizes and card
names the probe used, in the languages that produced the failures: the name
must be drawn, it must be wide enough to identify the card rather than a bare
"NVIDIA…", and every reading that IS drawn must fit inside the bar.

Run:  runtime\\python.exe tests\\test_status_line_card_name.py
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import overlay_ui  # noqa: E402
import pygame  # noqa: E402

CARDS = (
    "NVIDIA GeForce RTX 5070 Ti",
    "NVIDIA GeForce RTX 5070 Ti Laptop GPU",
    "NVIDIA GeForce RTX 4090 Laptop GPU",
    "NVIDIA GeForce RTX 3080 Ti Laptop GPU",
)
SIZES = ((1920, 1080), (2560, 1440), (3840, 2160))
LANGS = ("en", "de", "ru")


def _state(width: int, height: int, lang: str, card: str) -> dict:
    return {
        "lang": lang, "nr": True, "profile": "Natural",
        "profiles": ["Natural"], "params": {},
        "split": 0.0, "work_scale": 0.65, "work_scale_cap": 0.65,
        "work_scale_min": 0.1, "nr_small": True,
        "screen_size": f"{width}x{height}", "theme": "light",
        "gpu_text": card, "gpu_ok": True,
        "hdr": False, "spout": False, "rec_indicator": True,
        "skip_static": True, "windows": [], "window_current": "",
        "monitors": [], "monitor": "", "gpus": [], "gpu": "",
        "version": "1.13.1", "channel": "@perseval_BLR",
        "autostart": True, "open_on_start": True,
        "recording": False, "screenshot_dir": "", "recording_dir": "",
    }


class _FontSpy:
    """A font that records every string rendered through it.

    pygame makes `Font.render` read-only, so the object is wrapped instead of
    patched: the menu holds its fonts as attributes, and swapping them for this
    proxy means the test sees exactly what the drawer asked a font to draw -
    not what the test itself believes the drawer does.
    """

    __slots__ = ("_font", "_seen")

    def __init__(self, font, seen: list):
        object.__setattr__(self, "_font", font)
        object.__setattr__(self, "_seen", seen)

    def render(self, text, *a, **kw):
        self._seen.append(str(text))
        return self._font.render(text, *a, **kw)

    def __getattr__(self, name):
        return getattr(self._font, name)


def _drawn_readings(menu, w: int, h: int) -> dict:
    """What the status block actually PUT ON SCREEN, with where.

    `render()` is not proof of visibility: the drawer builds an image for every
    reading and only then decides whether it fits, so a string can be rendered
    and still never appear. The honest capture is the blit - the moment a value
    lands on the surface - scoped to `_draw_stats` so values drawn elsewhere on
    the page (the source row also prints the resolution) cannot be mistaken for
    status-line survivors.

    Returns {text: (x, y)} for every image the status block blitted.
    """
    _ = (w, h)
    placed: dict[str, tuple[int, int]] = {}
    texts_by_id: dict[int, str] = {}
    active = {"on": False}

    real_mono = menu._mono_small

    class _TaggedFont:
        """Wraps the mono face and remembers which image came from which text."""

        def __init__(self, font, sink):
            self._font = font
            self._sink = sink

        def render(self, text, *a, **kw):
            img = self._font.render(text, *a, **kw)
            self._sink[id(img)] = str(text)
            return img

        def __getattr__(self, name):
            return getattr(self._font, name)

    class _WatchSurface(pygame.Surface):
        __slots__ = ()

        def blit(self, source, dest, *a, **kw):
            if active["on"]:
                label = texts_by_id.get(id(source))
                if label is not None:
                    placed[label] = (int(dest[0]), int(dest[1]))
            return super().blit(source, dest, *a, **kw)

    object.__setattr__(menu, "_mono_small", _TaggedFont(real_mono, texts_by_id))
    real_draw_stats = menu._draw_stats

    def scoped_draw_stats(surface, s):
        active["on"] = True
        try:
            return real_draw_stats(surface, s)
        finally:
            active["on"] = False

    menu._draw_stats = scoped_draw_stats
    try:
        surface = _WatchSurface((w, h), pygame.SRCALPHA)
        menu.draw(surface)
    finally:
        menu._draw_stats = real_draw_stats
        object.__setattr__(menu, "_mono_small", real_mono)
    return placed


def _rendered_texts(menu, w: int, h: int, width: int, height: int) -> set[str]:
    return set(_drawn_readings(menu, w, h))


def main() -> int:
    import pygame
    pygame.init()
    pygame.display.set_mode((64, 64))
    import fonts
    from overlay_ui import OverlayMenu

    failures = []
    checked = 0

    for (w, h) in SIZES:
        for lang in LANGS:
            for card in CARDS:
                menu = OverlayMenu(1.0, lambda size=14, mono=False, bold=False,
                                   L=lang: fonts.load(size, mono=mono,
                                                      bold=bold, lang=L))
                menu.lang = lang
                menu.set_state(_state(w, h, lang, card))
                menu.visible = True
                menu.page = "main"
                menu.layout(w, h)
                menu.stats = {"fps": 144.0, "display_fps": 144.0,
                              "skipped_static": 12, "resolution": f"{w}x{h}"}

                drawn = []
                real_clip = menu._clip

                def spy(font, text, color, max_w, _d=drawn, _r=real_clip):
                    img = _r(font, text, color, max_w)
                    _d.append((str(text), max_w, img.get_width()))
                    return img

                menu._clip = spy
                surface = pygame.Surface((w, h), pygame.SRCALPHA)
                try:
                    menu.draw(surface)
                except Exception as exc:
                    failures.append(f"{w}x{h}/{lang}/{card[:28]}: draw raised "
                                    f"{exc!r}")
                    menu._clip = real_clip
                    continue
                finally:
                    menu._clip = real_clip
                checked += 1

                name_hits = [d for d in drawn if d[0] == card]
                if not name_hits:
                    failures.append(
                        f"{w}x{h}/{lang}/{card[:28]}: the card name never "
                        f"reached _clip - it is not drawn on the status line")
                    continue
                max_w, width = name_hits[0][1], name_hits[0][2]
                if max_w <= 0 or width <= 0:
                    failures.append(
                        f"{w}x{h}/{lang}/{card[:28]}: the card name is drawn "
                        f"at {width}px (max_w={max_w}) - it reads as nothing")
                # "NVIDIA…" is 63 px at the smallest; require more than that,
                # so the name has to carry at least a model hint.
                floor = menu._small_font.size("NVIDIA")[0] + menu._u(6)
                if width <= floor:
                    failures.append(
                        f"{w}x{h}/{lang}/{card[:28]}: the name is only "
                        f"{width}px (floor {floor}) - it says 'NVIDIA…' and "
                        f"not which card")

                # Every reading that was drawn must fit inside the bar. Their
                # max_w is not used (they are rendered whole or skipped), so
                # measure the rendered width against what the code allowed:
                # nothing may start left of the name's end.
                for text, _mw, tw in drawn:
                    if text == card or not text:
                        continue
                    if tw > menu._stats_rect.w:
                        failures.append(
                            f"{w}x{h}/{lang}/{card[:28]}: the reading "
                            f"{text[:16]!r} renders {tw}px in a "
                            f"{menu._stats_rect.w}px bar")

    # The reading the reporter could not find: a frame counter. It was removed
    # from the line in 2caf312 and the main loop kept handing it to the panel,
    # so it reached no font at all. It has to be on screen now, and so does
    # NR - the rate that used to be the FIRST casualty of the old right-to-left
    # layout (measured at 4K: NR and FG dropped, "SKIP 0 3840x2160" kept).
    for (w, h) in SIZES:
        for card in CARDS:
            menu = None
            menu = OverlayMenu(1.0, lambda size=14, mono=False, bold=False,
                               L="en": fonts.load(size, mono=mono,
                                                  bold=bold, lang=L))
            menu.lang = "en"
            menu.set_state(_state(w, h, "en", card))
            menu.visible = True
            menu.page = "main"
            menu.layout(w, h)
            menu.stats = {"fps": 98.8, "display_fps": 167.3,
                          "skipped_static": 0, "resolution": f"{w}x{h}",
                          "frames": 19704}
            placed = _drawn_readings(menu, w, h)
            texts = set(placed)
            # The readings must reach the right edge of their line: a run of
            # numbers bunched against the left edge reads as a half-drawn line
            # (the reporter's exact complaint about the first version). The
            # layout spreads them, so the last one ends where the line ends.
            if placed:
                widest_end = max(
                    pos[0] + menu._mono_small.size(t)[0]
                    for t, pos in placed.items())
                want = menu._stats_line2.right - menu._u(overlay_ui.STAT_PAD)
                slack = max(3, menu._u(4))
                if widest_end < want - slack:
                    failures.append(
                        f"{w}x{h}/{card[:28]}: the readings stop at "
                        f"{widest_end}px but the line runs to {want}px - they "
                        f"are bunched at the left instead of spread across it")
            # Both lines must live INSIDE the status block: squeeze the block
            # back to one line and the readings line overflows it, drawing over
            # the section below while still "rendering" - which is why the
            # geometry is checked and not just the strings.
            block = menu._stats_rect
            for line, which in ((getattr(menu, "_stats_line1", None), "first"),
                                (getattr(menu, "_stats_line2", None), "second")):
                if line is None or line.h <= 0:
                    failures.append(f"{w}x{h}/{card[:28]}: the {which} status "
                                    f"line was not laid out")
                elif not block.contains(line):
                    failures.append(
                        f"{w}x{h}/{card[:28]}: the {which} status line "
                        f"{tuple(line)} is outside the status block "
                        f"{tuple(block)} - the readings draw over the section "
                        f"below")
            # ALL of them at once, not one at a time: a single line physically
            # cannot carry NR + FG + frames + resolution beside a real card
            # name, which is exactly why the readings get a line of their own.
            # Requiring them together is what makes a merge back to one line
            # fail here instead of at a user's 4K screen.
            for needle, what in (("NR 98.8", "NR rate"),
                                 ("FR 19704", "frame counter"),
                                 ("FG 167", "FG rate"),
                                 (f"{w}x{h}", "resolution")):
                if needle not in texts:
                    failures.append(
                        f"{w}x{h}/{card[:28]}: the {what} ({needle!r}) never "
                        f"reached a font - it is not on screen together with "
                        f"the others. Rendered: {sorted(t for t in texts if any(ch.isdigit() for ch in t))[:8]}")

    # Priority, locked by a case where the line genuinely runs out of room.
    # At the sizes above everything fits, so the order is never exercised -
    # and the order is the whole point: NR must survive, the resolution is the
    # one allowed to go. The fit is measured first, so the check only fires at
    # a width where a choice is actually forced (measured: scale 0.4 fits
    # NR+FG+FR in 184px and must drop the rest; 0.6 fits everything).
    for scale in (0.4, 0.5, 0.6, 0.75):
        menu = OverlayMenu(scale, lambda size=14, mono=False, bold=False,
                           L="en": fonts.load(size, mono=mono, bold=bold, lang=L))
        menu.lang = "en"
        menu.set_state(_state(1920, 1080, "en",
                              "NVIDIA GeForce RTX 5070 Ti Laptop GPU"))
        menu.visible = True
        menu.page = "main"
        menu.layout(1920, 1080)
        menu.stats = {"fps": 98.8, "display_fps": 167.3, "skipped_static": 9999,
                      "resolution": "3840x2160", "frames": 197045678}

        # What the line can hold at this width, by the drawer's own rule.
        line2 = menu._stats_line2
        pad = menu._u(overlay_ui.STAT_PAD)
        room = (line2.right - pad) - (line2.x + pad)
        values = [f"NR 98.8", "FG 167", "FR 197045678", "SKIP 9999", "3840x2160"]
        need_all = sum(menu._mono_small.size(v)[0] for v in values) \
            + menu._u(18) * (len(values) - 1)

        texts = _rendered_texts(menu, 1920, 1080, 1920, 1080)
        if "NR 98.8" not in texts:
            failures.append(
                f"scale {scale}: NR was dropped to make room for something "
                f"else - the rate is the last thing that may go. Rendered: "
                f"{sorted(t for t in texts if any(ch.isdigit() for ch in t))[:8]}")
        if "FR 197045678" not in texts:
            failures.append(
                f"scale {scale}: the frame counter was dropped while the "
                f"resolution stayed - the priority is inverted. Rendered: "
                f"{sorted(t for t in texts if any(ch.isdigit() for ch in t))[:8]}")
        # Where a choice is forced, the value that must pay is the resolution:
        # it is the only reading that also appears elsewhere on the page.
        if need_all > room and "3840x2160" in texts:
            failures.append(
                f"scale {scale}: the line cannot hold every reading "
                f"({need_all}px needed, {room}px available) yet nothing was "
                f"dropped - the overflow would overlap. Rendered: "
                f"{sorted(t for t in texts if any(ch.isdigit() for ch in t))[:8]}")

    if not checked:
        print("FAIL: no status line was drawn - this test no longer covers "
              "what it exists for")
        return 1

    for f in failures[:15]:
        print("FAIL:", f)
    if len(failures) > 15:
        print(f"... and {len(failures) - 15} more")
    if failures:
        return 1
    print(f"OK: the card name is readable and the readings fit, on "
          f"{checked} combinations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
