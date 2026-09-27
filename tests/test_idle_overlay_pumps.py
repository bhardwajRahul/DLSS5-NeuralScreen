"""The idle overlay keeps pumping its window's messages (#135).

MEASURED, on this bench, with the real program (v2.1.7, commit 40247f0):

    state                      window   window answers   Save As dialog
    NR ON,  menu open          shown    2 ms, not hung    appears at once
    NR OFF, menu closed        hidden   NO answer, HUNG   never appears (100 s)
    NR OFF, menu open          hidden   NO answer, HUNG   never appears
    NR OFF, FG on (busy)       shown    alive             appears at once

The split is not NR on/off - in the last row NR is off and the dialog is
immediate. It tracks whether the window's message queue is being drained. With
NR off and nothing consuming frames, main takes the idle branch
(main.py:667-672): it HIDES the window and sleeps, and `pygame.event.get()` -
which lives only inside `if st.display.menu.visible:` (main.py:646) - is never
reached. Windows then marks the window "Not Responding", and
`SendMessageTimeoutW(WM_NULL)` to it returns 0.

That matters because the native Save As dialog is given this very window as its
OWNER (`open_save_dialog` -> `dialogs.ask_save_path(st.display.get_hwnd(), ...)`,
commands.py:210). Windows talks to an owner window while the dialog is coming
up and while it is answered; a window whose thread never takes messages off the
queue blocks it. The reporter's own log is that, to the second: the dialog was
opened at 18:36:23.545 and answered at 18:37:22.643 - 59193 ms later - and the
only event in between is `overlay menu opened` at 18:37:06.273, which is what
started draining the queue again.

WHAT THIS CHECKS. The program is driven into the idle state (NR off, menu
closed) and then asked two things, both of which a user can see:

1. does the window answer? - `SendMessageTimeoutW(WM_NULL)` and
   `IsHungAppWindow`, Windows' own verdict. Neither blocks.
2. does the Save As dialog appear? - the reporter's actual complaint. The
   dialog is a `#32770` window owned by our process.

Both fail on the code before the fix and pass after it. Nothing here is a
source scan: the window is a real one and the answer comes from Windows.

Needs NeuralScreen not to be running. ~25 seconds.

Run:  runtime\\python.exe tests\\test_idle_overlay_pumps.py
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent  # the project root
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/ (autocheck)

# Physical pixels, before pygame loads anything (SDL freezes DPI awareness).
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

import autocheck  # noqa: E402  - launch/log/send_key/quit_app live there

u = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

WM_NULL = 0x0000
SMTO_ABORTIFHUNG = 0x0002
PROBE_TIMEOUT_MS = 1500
DIALOG_WAIT_S = 15.0
SUSPEND_MARK = "capture, processing and presentation suspended"

u.SendMessageTimeoutW.restype = ctypes.c_void_p
u.SendMessageTimeoutW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                  ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                  ctypes.POINTER(ctypes.c_void_p)]


def app_pid() -> int | None:
    """The running program's pid, from the same list running_instances reads."""
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "CSV"],
                         capture_output=True).stdout.decode("cp1251", errors="replace")
    for line in out.splitlines()[1:]:
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) >= 2 and cells[1].isdigit():
            return int(cells[1])
    return None


def windows_of(pid: int, only_class: str | None = None) -> list:
    """Top-level windows of our process, optionally filtered by class."""
    rows = []

    @WNDENUMPROC
    def cb(hwnd, _l):
        p = ctypes.wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(64)
        u.GetClassNameW(hwnd, cls, 64)
        if only_class and cls.value != only_class:
            return True
        rows.append({"hwnd": int(hwnd), "cls": cls.value,
                     "visible": bool(u.IsWindowVisible(hwnd))})
        return True

    u.EnumWindows(cb, None)
    return rows


def overlay_hwnd(pid: int) -> int:
    """The overlay's own window - the one handed to the dialog as its owner."""
    for w in windows_of(pid):
        if "pygame" in w["cls"].lower():
            return w["hwnd"]
    return 0


def answers(hwnd: int) -> dict:
    """Does this window's thread take messages off its queue?

    SendMessageTimeoutW(WM_NULL) returns non-zero only when the target thread
    answered inside the timeout, and WM_NULL is the documented no-op probe, so
    the program is not disturbed either way. IsHungAppWindow is Windows' own
    answer to the same question.
    """
    res = ctypes.c_void_p(0)
    t0 = time.monotonic()
    ok = u.SendMessageTimeoutW(ctypes.c_void_p(hwnd), WM_NULL, None, None,
                               SMTO_ABORTIFHUNG, PROBE_TIMEOUT_MS,
                               ctypes.byref(res))
    return {
        "answered": bool(ok),
        "probe_ms": round((time.monotonic() - t0) * 1000.0, 1),
        "hung": bool(u.IsHungAppWindow(ctypes.c_void_p(hwnd))),
    }


def dialogs_of(pid: int) -> list:
    return windows_of(pid, "#32770")


def main() -> int:
    busy = autocheck.running_instances()
    if busy:
        print(f"FAIL: NeuralScreen is already running ({busy}) - stop it first")
        return 1

    failures: list[str] = []
    # The idle state under test is NR OFF with the menu CLOSED - launched that
    # way rather than closed afterwards, because toggling the panel is itself
    # what pumps the queue (that is how the reporter unblocked his dialog).
    offset = autocheck.launch({"open_menu_on_start": False})
    try:
        if not autocheck.wait_for(offset, autocheck.NR_FRAME_MARKER, 30.0):
            print("FAIL: NeuralScreen did not start processing")
            return 1
        if "overlay menu opened" in autocheck.log_since(offset):
            failures.append("the panel opened on start - the idle state under "
                            "test has it closed")

        # Into the state the reporter was in: NR off, menu closed.
        autocheck.send_key(*autocheck.binding_keys("toggle"))     # Num1
        text = autocheck.wait_for(offset, SUSPEND_MARK, 20.0)
        if not text:
            print("FAIL: NR OFF never suspended the pipeline")
            print(autocheck.log_since(offset)[-600:])
            return 1
        time.sleep(2.0)   # let it settle into the idle branch

        pid = app_pid()
        if pid is None:
            print("FAIL: the program's process was not found")
            return 1
        hwnd = overlay_hwnd(pid)
        if not hwnd:
            print("FAIL: the overlay window was not found")
            return 1

        probe = answers(hwnd)
        print(f"    idle overlay 0x{hwnd:X}: answered={probe['answered']} "
              f"in {probe['probe_ms']} ms, IsHungAppWindow={probe['hung']}, "
              f"visible={bool(u.IsWindowVisible(ctypes.c_void_p(hwnd)))}")
        if not probe["answered"] or probe["hung"]:
            failures.append(
                f"the overlay window does not take its messages while the "
                f"program is idle (no answer in {PROBE_TIMEOUT_MS} ms, "
                f"IsHungAppWindow={probe['hung']}) - Windows reports it as Not "
                f"Responding and any dialog owned by it hangs (#135)")

        # The reporter's own symptom: the Save As dialog, in this state.
        before = len(dialogs_of(pid))
        t0 = time.monotonic()
        seen = []
        autocheck.send_key(*autocheck.binding_keys("screenshot_menu"))  # Num3
        while time.monotonic() - t0 < DIALOG_WAIT_S:
            seen = dialogs_of(pid)
            if seen:
                break
            time.sleep(0.1)
        print(f"    Save As dialog in the idle state after "
              f"{time.monotonic() - t0:.2f} s: {seen or 'none'}")
        if not seen:
            failures.append(
                f"no Save As dialog appeared within {DIALOG_WAIT_S:.0f} s with "
                f"the program idle (was {before} before) - the dialog is owned "
                f"by a window nobody is pumping (#135: the reporter waited "
                f"59193 ms and it only came once he opened the menu)")
        else:
            # Answer it so the run ends cleanly and the write is exercised.
            u.SetForegroundWindow(ctypes.c_void_p(seen[0]["hwnd"]))
            time.sleep(0.2)
            u.keybd_event(0x1B, 0, 0, 0)      # Esc -> cancel, no file written
            u.keybd_event(0x1B, 0, 0, 2)
            time.sleep(0.5)

        # And the queue must stay pumped as long as the program is idle, not
        # merely once: the dialog above is answered seconds later.
        probe2 = answers(hwnd)
        print(f"    after the exchange: answered={probe2['answered']} "
              f"in {probe2['probe_ms']} ms, IsHungAppWindow={probe2['hung']}")
        if not probe2["answered"] or probe2["hung"]:
            failures.append("the overlay stopped answering its messages again")
    finally:
        left = autocheck.quit_app()
        if left:
            failures.append(f"processes left behind: {left}")

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print("OK: the idle overlay answers its messages and the Save As dialog "
          "opens while NR is off with the menu closed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
