"""Converted files say what they hold, and HDR sources are refused (22.09 audit).

No GPU: the output stream is built in a throwaway in-memory container, the way
convert_video builds it.

* the output STREAM carries full-range BT.709/sRGB tags - they were on the
  frames only, the encoder and the mp4 `colr` box are fixed at open(), and a
  file that says nothing is played as limited range (crushed blacks);
* the mp4 timescale is capped at 1/90000 s (a microsecond grid overflowed a
  32-bit player after 71 minutes);
* a PQ/HLG source is recognised, and the queue names the refusal;
* the queue's error names: a truncated source is "unreadable" at the decode
  stage, not "could not be written".

Run:  runtime\\python.exe tests\\test_conversion_colour_and_hdr.py
"""
import io
import sys
import types
from fractions import Fraction
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

import av  # noqa: E402

import convert_jobs  # noqa: E402
import media_convert  # noqa: E402
from i18n import STRINGS  # noqa: E402


def main() -> int:
    failures = []

    out = av.open(io.BytesIO(), mode="w", format="mp4")
    try:
        stream = media_convert._add_video_stream(
            out, media_convert.SOFTWARE_CODEC, Fraction(30), 128, 72, "high")
        ctx = stream.codec_context
        got = (int(ctx.color_range), int(ctx.colorspace),
               int(ctx.color_primaries), int(ctx.color_trc))
        if got != (2, 1, 1, 13):
            failures.append(f"the output stream is tagged {got}, not full-range "
                            f"BT.709/sRGB (2, 1, 1, 13)")
    finally:
        try:
            out.close()
        except Exception:
            pass

    fine = types.SimpleNamespace(time_base=Fraction(1, 1_000_000))
    grid = media_convert._encoder_grid(fine, Fraction(60))
    if grid < Fraction(1, 90_000):
        failures.append(f"a microsecond source grid is kept ({grid}): the mp4 "
                        f"timescale overflows a 32-bit player within the hour")

    pq = types.SimpleNamespace(codec_context=types.SimpleNamespace(color_trc=16))
    hlg = types.SimpleNamespace(codec_context=types.SimpleNamespace(color_trc=18))
    sdr = types.SimpleNamespace(codec_context=types.SimpleNamespace(color_trc=13))
    if not media_convert._is_hdr(pq) or not media_convert._is_hdr(hlg):
        failures.append("a PQ or HLG source is not recognised as HDR")
    if media_convert._is_hdr(sdr):
        failures.append("an sRGB source is taken for HDR")

    cases = [
        (media_convert.ConversionError("hdr", "PQ"), "hdr"),
        (media_convert.ConversionError(
            "decode", av.error.InvalidDataError(1094995529, "Invalid data")),
         "unreadable"),
    ]
    for exc, want in cases:
        got = convert_jobs.friendly_error(exc)
        if got != want:
            failures.append(f"friendly_error({exc.stage}) = {got!r}, not {want!r}")
        for lang, strings in STRINGS.items():
            if f"convert_err_{want}" not in strings:
                failures.append(f"{lang} has no convert_err_{want}")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: converted streams carry their colour tags, the timescale is "
          "capped, and HDR sources and unreadable ones are named")
    return 0


if __name__ == "__main__":
    sys.exit(main())
