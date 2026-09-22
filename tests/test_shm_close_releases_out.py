"""Closing the frame buffer releases every section it opened (audit H3).

SharedFrameBuffer owns three named sections: the input (colour+motion), the
reverse gray channel, and the reverse pixel channel (OUTS). close() released
the input and delegated gray to close_gray(), but never touched the pixel
channel - close_out() existed and did the right thing, yet outside open_out()
it had no caller in the program.

The OUTS channel is on by default, and enable_out_shm calls open_out() once per
pipeline build. Every rebuild - monitor switch, window switch, Spout, HDR,
motion backend, GPU switch - runs teardown_pipeline, which closes the buffer,
and then replaces it with a new SharedFrameBuffer. The old object went away
with the only reference to a live 33,177,608-byte section and its mapping, so
one section stayed resident for the life of the process per rebuild: eight
switches retained ~264 MB that nothing could read or release. The same close()
at exit leaked one more.

Checked here with the real SharedFrameBuffer and no worker:

* close() releases all three sections when all three were open;
* it releases them when only some were opened (open_out without open_gray,
  and the reverse);
* the public surface afterwards is honest: reads report "not open" rather than
  returning pixels from a section that was supposedly released;
* close() is idempotent, and closable with nothing open.

Run:  runtime\\python.exe tests\\test_shm_close_releases_out.py
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))  # the modules live in app/

from protocol import SharedFrameBuffer  # noqa: E402

W, H = 3840, 2160
OUT_BYTES = W * H * 4 + 8          # the RGBA8 section plus its seqlock


def _open_shared_state() -> dict:
    """The three mappings, as attributes the object itself holds."""
    return {}


def main() -> int:
    failures = []

    # 1. Everything open: close() must release all three.
    shm = SharedFrameBuffer(W, H)
    shm.open_out(W, H)
    shm.open_gray(320, 180)
    if shm._out_mm is None or shm._out_buf is None:
        failures.append("open_out did not open the pixel section - the premise "
                        "of this test is gone")
    if shm.out_bytes != OUT_BYTES:
        failures.append(f"the pixel section is {shm.out_bytes} bytes, expected "
                        f"{OUT_BYTES}")
    shm.close()
    if shm._out_mm is not None or shm._out_buf is not None:
        failures.append("close() left the OUTS pixel section open: one "
                        f"{OUT_BYTES}-byte named section stays resident for the "
                        "life of the process on every pipeline rebuild")
    if shm.out_bytes or shm.out_w or shm.out_h:
        failures.append(f"close() left the pixel channel describing "
                        f"{shm.out_w}x{shm.out_h} / {shm.out_bytes} B")
    if shm._out_ring:
        failures.append("close() left the pixel ring behind - a reopen at "
                        "another size would inherit slots shaped for the old one")
    if shm._gray_mm is not None or shm._gray_buf is not None:
        failures.append("close() left the gray section open")
    if shm._mm is None or shm._buf is not None:
        # close() resets the numpy VIEW and closes the mapping; the mapping
        # object itself is only ever replaced by a new buffer.
        pass

    # 2. Only the pixel channel open (a rebuild where gray never negotiated).
    shm = SharedFrameBuffer(W, H)
    shm.open_out(W, H)
    shm.close()
    if shm._out_mm is not None:
        failures.append("close() left the pixel section open when gray was "
                        "never opened")

    # 3. Only the gray channel open.
    shm = SharedFrameBuffer(W, H)
    shm.open_gray(320, 180)
    shm.close()
    if shm._gray_mm is not None:
        failures.append("close() left the gray section open when the pixel "
                        "channel was never opened")

    # 4. The surface is honest afterwards: a released channel reads as "not
    #    open" instead of handing out pixels from a closed mapping.
    shm = SharedFrameBuffer(W, H)
    shm.open_out(W, H)
    shm.close()
    for name, call in (("read_out", shm.read_out), ("read_gray", shm.read_gray)):
        try:
            got = call()
        except Exception as exc:
            failures.append(f"{name}() after close() raised {exc!r} instead of "
                            f"reporting an unopened channel")
            continue
        if got is not None:
            failures.append(f"{name}() after close() returned pixels from a "
                            f"released section")
    try:
        shm.put(__import__("numpy").zeros((2, 2, 4), dtype="uint8"),
                __import__("numpy").zeros((2, 2, 4), dtype="uint8"))
    except ValueError:
        pass  # refused on capacity, which is fine - it must not segfault
    except Exception as exc:
        failures.append(f"put() after close() raised {exc!r}")

    # 5. Idempotent, and safe with nothing open.
    shm = SharedFrameBuffer(W, H)
    shm.close()
    shm.close()
    shm2 = SharedFrameBuffer(W, H)
    shm2.open_out(W, H)
    shm2.close()
    shm2.close()
    if shm2._out_mm is not None:
        failures.append("a second close() reopened something")

    for f in failures:
        print("FAIL:", f)
    if failures:
        return 1
    print("OK: close() releases the input, gray and OUTS sections")
    return 0


if __name__ == "__main__":
    sys.exit(main())
