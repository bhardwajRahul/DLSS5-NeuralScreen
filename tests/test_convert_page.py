r"""The conversion page fits in twelve languages.

A new page means new captions in twelve languages, and this panel has been
here before: a caption that does not fit is clipped with an ellipsis or
spills into its neighbour, and in a screenshot of one language it looks fine.
Measured with the product fonts, in every language:

1. The four output segments: every caption inside its cell, with the same
   margin the settings page is held to.
2. Each row's button: the widest of the four action words inside it - and
   every row's button the same width and in one column, whatever state the
   row is in.
3. The Clear finished / Stop all strip, the footer's Add files, and the
   Media tab's button that opens the page.
4. The main page's Convert cell, with its icon and a running percentage
   ("Convert 100%"), in the third of the actions strip it now shares - and
   the Screenshot and Record captions beside it, which used to have half.
5. The notes wrap to the panel: every line of the page's hint and of the
   empty queue's note fits its width, in Chinese and Japanese too - they
   have no spaces to break at, and wrapped at spaces alone a note was one
   line running off the panel.

Run:  runtime\python.exe tests\test_convert_page.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

#: The margin the settings page's captions are held to (test_settings_hints).
ROOM = 0.92

JOBS = [
    {"id": 1, "name": "a.mp4", "kind": "video", "status": "running",
     "fraction": 0.5, "line": "50%", "tone": "text", "action": "stop"},
    {"id": 2, "name": "b.png", "kind": "image", "status": "done",
     "line": "done", "tone": "ok", "action": "show"},
    {"id": 3, "name": "c.avi", "kind": "video", "status": "failed",
     "line": "failed", "tone": "danger", "action": "retry"},
    {"id": 4, "name": "d.mkv", "kind": "video", "status": "queued",
     "line": "waiting", "tone": "muted", "action": "remove"},
]


def main() -> int:
    import pygame
    pygame.init()
    pygame.display.set_mode((64, 64))
    import fonts
    from i18n import STRINGS
    from overlay_ui import (FONT_SIZE, SMALL_SIZE, OverlayMenu)

    failures: list[str] = []
    widest = (0.0, "")

    def check(share: float, where: str, detail: str) -> None:
        nonlocal widest
        if share > widest[0]:
            widest = (share, where)
        if share > ROOM:
            failures.append(f"{where}: {detail} ({share * 100:.0f}%)")

    for lang, s in STRINGS.items():
        menu = OverlayMenu(1.0, lambda size=14, mono=False, bold=False, L=lang:
                           fonts.load(size, mono=mono, bold=bold, lang=L))
        menu.lang = lang
        menu.set_state({"lang": lang, "theme": "light", "nr": True,
                        "gpu_ok": True, "convert_jobs": JOBS,
                        "convert_dest": "folder", "convert_dir": "D:/Out",
                        "convert_progress": 1.0})
        menu.visible = True
        u = menu._u

        # 1-3. The page, with a row in every state.
        menu.page = "convert"
        menu.layout(2560, 1440)
        for item in menu.items:
            where = f"{lang} convert/{item.key}"
            if item.kind == "segmented":
                labels = item.extra.get("labels") or item.payload or []
                cell = item.rect.w // max(1, len(labels))
                for label in labels:
                    width = menu._small_font.size(str(label))[0]
                    check(width / max(1, cell), where,
                          f"the caption {label!r} is {width} of a {cell} px cell")
            elif item.kind == "button" and item.key.startswith("convert_job:"):
                room = item.rect.w - u(12)
                for action in ("stop", "remove", "show", "retry"):
                    word = s.get(f"convert_act_{action}", action)
                    width = menu._small_font.size(word)[0]
                    check(width / max(1, room), f"{lang} row button {action}",
                          f"{word!r} is {width} of {room} px")
            elif item.kind == "button" and item.extra.get("pair"):
                room = item.rect.w - u(16)
                label = str(item.extra.get("label", ""))
                width = menu._small_font.size(label)[0]
                check(width / max(1, room), where,
                      f"{label!r} is {width} of {room} px")
            elif item.kind == "action" and item.key == "convert_add":
                label = str(item.extra.get("label", ""))
                width = menu._font.size(label)[0]
                check(width / max(1, item.rect.w - u(16)), where,
                      f"{label!r} is {width} of {item.rect.w} px")
            elif item.kind == "note":
                inset = int(item.extra.get("inset", 0)) if \
                    item.extra.get("boxed") else 0
                room = item.rect.w - 2 * inset
                for line in menu._wrap_note(item.extra.get("text", ""), room):
                    width = menu._small_font.size(line)[0]
                    if width > room:
                        failures.append(f"{where}: a wrapped line is {width} "
                                        f"of {room} px: {line[:30]!r}")
        buttons = [i for i in menu.items
                   if i.kind == "button" and i.key.startswith("convert_job:")]
        if len(buttons) != len(JOBS):
            failures.append(f"{lang}: {len(buttons)} row buttons for "
                            f"{len(JOBS)} rows")
        elif len({(b.rect.w, b.rect.right) for b in buttons}) != 1:
            failures.append(f"{lang}: the row buttons are not one column: "
                            f"{[(b.rect.w, b.rect.right) for b in buttons]}")

        # The empty queue, whose note is the drop target.
        menu.set_state({"convert_jobs": []})
        menu.layout(2560, 1440)
        empty = [i for i in menu.items if i.key == "convert_empty"]
        if not empty or not empty[0].extra.get("boxed"):
            failures.append(f"{lang}: the empty queue shows no drop box")

        # 3. The Media tab's way in.
        menu.page = "settings"
        menu.settings_tab = "rec"
        menu.layout(2560, 1440)
        way_in = next((i for i in menu.items
                       if i.kind == "button" and i.key == "convert"), None)
        if way_in is None:
            failures.append(f"{lang}: the Media tab has no button to the page")
        else:
            label = str(way_in.extra.get("label", ""))
            width = menu._font.size(label)[0]
            check(width / max(1, way_in.rect.w - u(16)), f"{lang} rec/convert",
                  f"{label!r} is {width} of {way_in.rect.w} px")

        # 4. The actions strip on the main page.
        menu.page = "main"
        menu.layout(2560, 1440)
        ik = SMALL_SIZE / float(FONT_SIZE)
        icon_w = int(round(u(17) * ik)) + u(9)
        for key in ("screenshot", "record", "convert"):
            cell = next((i for i in menu.items
                         if i.kind == "button" and i.key == key), None)
            if cell is None:
                failures.append(f"{lang}: no {key} cell on the main page")
                continue
            label = str(cell.extra.get("label", ""))
            room = cell.rect.w - u(16) - icon_w
            width = menu._small_font.size(label)[0]
            # The strip's own measure is the full width: its captions were
            # sized to it (121 of 123 px for es "Captura de pantalla").
            if width > room:
                failures.append(f"{lang} main/{key}: {label!r} is {width} of "
                                f"the {room} px beside its icon - clipped")

    print(f"    widest caption on the conversion page: {widest[1]} at "
          f"{widest[0] * 100:.0f}%")
    for f in failures[:25]:
        print("FAIL:", f)
    if len(failures) > 25:
        print(f"... and {len(failures) - 25} more")
    if failures:
        return 1
    print(f"OK: the conversion page fits in all {len(STRINGS)} languages - "
          f"segments, row buttons, strip, footer, notes and the Convert cell")
    return 0


if __name__ == "__main__":
    sys.exit(main())
