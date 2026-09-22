"""The window list filter: only real taskbar windows.

The list used to include background-process helpers (owned/tool windows,
DWM-cloaked shells like TextInputHost) - the user reported "сторонние
процессы попадают в список". The filter now keeps only windows that
would show in the taskbar: top-level, unowned, not a tool window, not
DWM-cloaked, with a title.

Checked: the live list is well-formed and every entry passes the filter
invariants (taskbar window, not the desktop, non-empty title, unique).
"""
import os
import sys
import ctypes
import importlib.util
import inspect

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))  # the modules live in app/

# main.py is a script with a `main()` entry point - import it under an
# explicit name so `import main` cannot pick up a different module.
_spec = importlib.util.spec_from_file_location("ns_main", os.path.join(ROOT, "main.py"))
ns_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ns_main)
import winapi


def _cloaked_with(value: int):
    """A stand-in for DwmGetWindowAttribute(hwnd, 14, ...) writing `value`."""
    def call(hwnd, attr, ptr, size):
        if attr == 14:
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int))[0] = value
        return 0    # S_OK
    return call


_cloaked_ok = _cloaked_with(1)      # DWM_CLOAKED_SHELL: hidden from the taskbar
_cloaked_clear = _cloaked_with(0)   # a normal visible window


def main() -> int:
    failures = []

    # The native presenter is ours even though it runs in the worker process.
    # WindowFromPoint can return it through the layered click-through surface;
    # without this identity rule Num5 captures NeuralScreen itself.
    saved_pid = winapi._window_pid
    saved_class = winapi._window_class_name
    try:
        winapi._window_pid = lambda _hwnd: os.getpid() + 100
        winapi._window_class_name = lambda _hwnd: "NeuralScreenPresent"
        if not winapi._is_our_window(1):
            failures.append("the native presenter is not recognised as our window")
        winapi._window_class_name = lambda _hwnd: "SomeGameWindow"
        if winapi._is_our_window(1):
            failures.append("a foreign window is recognised as ours")
        winapi._window_pid = lambda _hwnd: os.getpid()
        if not winapi._is_our_window(1):
            failures.append("the Python UI process is not recognised as ours")
    finally:
        winapi._window_pid = saved_pid
        winapi._window_class_name = saved_class

    for fn, target in ((winapi.foreign_foreground, "hwnd"),
                       (winapi.window_under_cursor, "top")):
        if f"_is_our_window({target})" not in inspect.getsource(fn):
            failures.append(f"{fn.__name__} bypasses the shared own-window filter")

    wins = ns_main.list_capturable_windows()
    print(f"live window list: {len(wins)} entries")
    if not isinstance(wins, list):
        failures.append("list_capturable_windows should return a list")
        print("FAIL: not a list")
        return 1

    seen = set()
    for hwnd, title in wins:
        if not isinstance(hwnd, int) or not isinstance(title, str):
            failures.append(f"malformed entry: {hwnd!r}, {title!r}")
            continue
        if not title.strip():
            failures.append(f"0x{hwnd:X}: empty title slipped through")
        if not ns_main._is_taskbar_window(hwnd):
            failures.append(f"0x{hwnd:X} ({title}): not a taskbar window")
        if ns_main._is_desktop_window(hwnd):
            failures.append(f"0x{hwnd:X} ({title}): the desktop slipped through")
        if winapi._is_our_window(hwnd):
            failures.append(f"0x{hwnd:X} ({title}): a NeuralScreen window slipped through")
        if hwnd in seen:
            failures.append(f"0x{hwnd:X}: duplicate hwnd")
        seen.add(hwnd)

    for hwnd, title in wins:
        print(f"  {hwnd:X}: {title[:60]}")

    # The list is already filtered by the product, so walking it can only
    # confirm the filter AGREES with itself - it says nothing about whether
    # the filter rejects anything. These drive the rule directly, through the
    # real user32/dwmapi calls the product makes (audit: WEAK).
    # The module's own user32 - winapi declares its prototypes on a private
    # instance, so that is the one the rule actually calls.
    user32 = winapi._user32
    dwmapi = ctypes.windll.dwmapi

    real_getwindow = user32.GetWindow
    real_getlong = user32.GetWindowLongW
    real_dwm = dwmapi.DwmGetWindowAttribute
    try:
        # An owned window (a background helper's) must be refused.
        user32.GetWindow = lambda hwnd, what: 0x1234 if what == 4 else 0
        user32.GetWindowLongW = lambda hwnd, what: 0
        dwmapi.DwmGetWindowAttribute = lambda *a: 1          # not success
        if winapi._is_taskbar_window(1):
            failures.append("an OWNED window passed the taskbar filter - that "
                            "is the \"сторонние процессы\" report")
        # A tool window must be refused.
        user32.GetWindow = lambda hwnd, what: 0
        user32.GetWindowLongW = lambda hwnd, what: 0x00000080  # WS_EX_TOOLWINDOW
        if winapi._is_taskbar_window(1):
            failures.append("a TOOL window passed the taskbar filter")
        # A DWM-cloaked window (TextInputHost and friends) must be refused.
        user32.GetWindowLongW = lambda hwnd, what: 0
        dwmapi.DwmGetWindowAttribute = _cloaked_ok
        if winapi._is_taskbar_window(1):
            failures.append("a DWM-CLOAKED window passed the taskbar filter")
        # And a plain top-level unowned window is accepted.
        dwmapi.DwmGetWindowAttribute = _cloaked_clear
        if not winapi._is_taskbar_window(1):
            failures.append("a plain top-level window was refused by the "
                            "taskbar filter")
        print("    the filter: owned/tool/cloaked refused, plain accepted")
    finally:
        user32.GetWindow = real_getwindow
        user32.GetWindowLongW = real_getlong
        dwmapi.DwmGetWindowAttribute = real_dwm

    print("=" * 60)
    if failures:
        print(f"FAIL: {len(failures)} - {failures}")
        return 1
    print("OK: the window list holds only real taskbar windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
