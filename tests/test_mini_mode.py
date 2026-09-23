"""Mini mode: what it keeps, and that choosing can be clicked at all.

The bug this exists for was reported on the first real run: while choosing,
the dots did nothing. `hit()` answers with the CONTROL's own rectangle by
design - "only explicit switches and choices should be clickable", rule
16.09 - so the only thing that reacted was the switch, which is the one
place a click must not land while choosing. The dot in the margin and the
label were dead, and the row's own control both kept the row and flipped
its setting.

So the checks here are about targets, not about state: a click on the dot,
on the label and on the control must all do the same one thing.

Run:  runtime\\python.exe tests\\test_mini_mode.py
"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402

import fonts  # noqa: E402
import overlay_ui  # noqa: E402

W, H = 2560, 1440


def build(mini=False, pick=False):
    menu = overlay_ui.OverlayMenu(
        1.0, lambda size, mono=False, bold=False: fonts.load(size, mono=mono))
    menu.set_state({"theme": "dark", "lang": "en", "nr": True, "boost": True,
                    "profile": "Natural", "framegen": True})
    menu.visible = True
    menu.page = "main"
    menu.mini, menu.mini_pick = mini, pick
    menu.layout(W, H)
    return menu


def click(menu, pos):
    return menu.handle_event(
        pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=pos))


def rows(menu):
    return [i.key for i in menu.items if i.kind in overlay_ui.MINI_PICKABLE]


def main() -> int:
    pygame.init()
    pygame.display.set_mode((64, 64))
    failures = []

    # 1. Mini mode keeps what it was told to and drops the rest.
    full = build()
    mini = build(mini=True)
    full_rows, mini_rows = rows(full), rows(mini)
    if set(mini_rows) != set(overlay_ui.DEFAULT_MINI_ROWS) & set(full_rows):
        failures.append(f"mini mode shows {mini_rows}, expected the default "
                        f"set {overlay_ui.DEFAULT_MINI_ROWS}")
    if len(mini_rows) >= len(full_rows):
        failures.append(f"mini mode did not shorten the page: "
                        f"{len(mini_rows)} of {len(full_rows)} rows")
    if mini.panel_rect.h >= full.panel_rect.h:
        failures.append(f"mini mode did not shorten the panel: "
                        f"{mini.panel_rect.h} vs {full.panel_rect.h}")

    # 2. Choosing shows every row again, so a dropped one can come back.
    picking = build(mini=True, pick=True)
    if set(rows(picking)) != set(full_rows):
        failures.append("choosing does not show every row: a row that was "
                        "dropped cannot be put back")

    # 3. THE REGRESSION: the dot, the label and the control each keep or
    #    drop the row, and none of them touches the control's value.
    for where in ("dot", "label", "control"):
        menu = build(mini=True, pick=True)
        row = next(i for i in menu.items if i.key == "intensity")
        before = "intensity" in menu.mini_rows
        if where == "dot":
            pos = (menu.panel_rect.x + 11, row.rect.y + 8)
        elif where == "label":
            pos = (row.rect.x + 40, row.rect.centery)
        else:
            zone = row.extra.get("hit") or row.rect
            pos = zone.center
        out = click(menu, pos)
        if ("intensity" in menu.mini_rows) == before:
            failures.append(f"a click on the {where} did not keep or drop "
                            f"the row (kept={before})")
        if not any(a[0] == "mini_rows" for a in out):
            failures.append(f"a click on the {where} reported {out}, "
                            f"expected a mini_rows change")
        if any(a[0] in ("param", "toggle", "slider") for a in out):
            failures.append(f"a click on the {where} also operated the "
                            f"control: {out}")

    # 4. Keeping a row is what makes it appear in mini mode.
    menu = build(mini=True, pick=True)
    row = next(i for i in menu.items if i.key == "style")
    click(menu, (menu.panel_rect.x + 11, row.rect.y + 8))
    menu.mini_pick = False
    menu.layout(W, H)
    if "style" not in rows(menu):
        failures.append("a row kept while choosing is not in mini mode")

    # 5. Outside mini mode nothing changes: the click goes to the control.
    menu = build()
    row = next(i for i in menu.items if i.key == "intensity")
    zone = row.extra.get("hit") or row.rect
    out = click(menu, zone.center)
    if any(a[0] == "mini_rows" for a in out):
        failures.append(f"a normal click reported a mini_rows change: {out}")

    # 6. The other pages are untouched - compared against themselves with
    #    mini mode off, because a settings TAB can legitimately be shorter
    #    than the kept set and a count alone proves nothing.
    for page in ("settings", "windows"):
        off, on = build(), build(mini=True)
        for menu in (off, on):
            menu.page = page
            menu.layout(W, H)
        if rows(on) != rows(off):
            failures.append(f"mini mode changed the {page} page: "
                            f"{rows(on)} against {rows(off)}")

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)}")
        for line in failures:
            print("  -", line)
        return 1
    print(f"OK: mini mode keeps {len(mini_rows)} of {len(full_rows)} rows, "
          f"the panel goes {full.panel_rect.h} -> {mini.panel_rect.h} px, and "
          f"the dot, the label and the control all choose")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
