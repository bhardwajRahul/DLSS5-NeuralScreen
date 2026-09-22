r"""No language's interface shows another language's words.

THE BUG (found while rendering the 2.0 screenshots, 20.09.2026)

The Program tab in the ENGLISH interface said "Тема" over the light/dark
switch, and the RUSSIAN one said "Theme". The two values had been swapped
between the `en` and `ru` blocks, and both had shipped that way: every other
test asks whether a key EXISTS in all twelve languages, and this key existed in
all twelve. Nothing asked whether the value belonged to the language it was
filed under.

WHAT THIS LOCKS

1. The English block carries no Cyrillic. That is the exact shape of this bug
   and it costs nothing to check. The language NAMES are exempt: a language
   picker shows each language in its own script, so `lang_ru` is "Русский" in
   the English interface on purpose.

2. A key that nearly every language bothers to translate must not be left at
   the English string in Russian or Ukrainian. That catches the mirror image of
   the same swap, and the threshold keeps the genuinely untranslated terms -
   HDR, Spout2, NVOFA, FG - out of it: when a term stays English everywhere,
   most languages do not differ from English and the key is not flagged.

Run:  runtime\python.exe tests\test_i18n_not_mixed.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

from i18n import STRINGS  # noqa: E402

CYRILLIC = re.compile(r"[Ѐ-ӿ]")
#: The language picker names every language in its own script - that is the
#: point of it, and it is the one place Cyrillic belongs in the English UI.
EXEMPT = re.compile(r"^lang_")
#: How many of the other nine languages must translate a key before leaving it
#: in English counts as a mistake rather than a term of art.
TRANSLATED_BY = 6


def main() -> int:
    failures: list[str] = []
    en = STRINGS["en"]

    for key, value in en.items():
        if not isinstance(value, str) or EXEMPT.match(key):
            continue
        if CYRILLIC.search(value):
            failures.append(
                f"en/{key} is Cyrillic ({value!r}) - the English interface "
                f"would show it as it stands")

    others = [l for l in STRINGS if l not in ("en", "ru", "uk")]
    for key, value in en.items():
        if not isinstance(value, str) or not value.strip() or EXEMPT.match(key):
            continue
        translated = sum(1 for l in others
                         if isinstance(STRINGS[l].get(key), str)
                         and STRINGS[l][key] != value)
        if translated < TRANSLATED_BY:
            continue            # a term everyone keeps in English
        for lang in ("ru", "uk"):
            if STRINGS[lang].get(key) == value:
                failures.append(
                    f"{lang}/{key} is still the English {value!r}, and "
                    f"{translated} of {len(others)} other languages translate "
                    f"it - the two blocks were probably swapped")

    # And the key that started it, by name, because a regression here is
    # invisible in a screenshot taken in the other language.
    if STRINGS["en"].get("theme") != "Theme":
        failures.append(f"en/theme is {STRINGS['en'].get('theme')!r}, not 'Theme'")
    if CYRILLIC.search(str(STRINGS["ru"].get("theme", ""))) is None:
        failures.append(f"ru/theme is {STRINGS['ru'].get('theme')!r} - not Russian")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print(f"OK: {len(STRINGS)} languages, none wearing another's words")
    return 0


if __name__ == "__main__":
    sys.exit(main())
