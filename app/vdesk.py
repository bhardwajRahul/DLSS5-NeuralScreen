"""Virtual-desktop placement for the overlay windows.

Windows 11 lets the user move a program to another virtual desktop through
Task View. The 1x1 taskbar window travels with it, but the overlay windows
(the HUD layer and the picture surface) are borderless tool windows and can
stay behind on the original desktop - the menu is then drawn where the user
is not looking, which reads as "the app does not expand" (issue #93, user
Saymoin).

The documented interface for this is IVirtualDesktopManager (CLSID_
VirtualDesktopManager, IID_IVirtualDesktopManager, Windows 10+): it can tell
which desktop a window is on and move a window onto a given desktop. It is
not part of the public SDK headers but it is a stable, documented COM API -
no undocumented internals are touched here.

Everything degrades quietly: if the interface is unavailable (very old
Windows, a hardened COM setup), the helpers report "unknown" and the caller
simply does not move anything.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

ole32 = ctypes.windll.ole32

ole32.CoInitialize.argtypes = [ctypes.c_void_p]
ole32.CoInitialize.restype = ctypes.c_long
ole32.CoCreateInstance.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_ulong, ctypes.c_void_p,
                                   ctypes.POINTER(ctypes.c_void_p)]
ole32.CoCreateInstance.restype = ctypes.c_long

CLSCTX_INPROC_SERVER = 1


class GUID(ctypes.Structure):
    """A COM GUID (the desktop id is one too - it is compared, not parsed)."""
    _fields_ = [("Data1", ctypes.c_ulong),
                ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort),
                ("Data4", ctypes.c_ubyte * 8)]

    def __eq__(self, other):
        if not isinstance(other, GUID):
            return NotImplemented
        return bytes(self) == bytes(other)

    def __hash__(self):
        return hash(bytes(self))

    def __str__(self):
        h = "".join(f"{b:02X}" for b in self.Data4)
        return (f"{self.Data1:08X}-{self.Data2:04X}-{self.Data3:04X}"
                f"-{h[:4]}-{h[4:]}")


def _guid(d1: int, d2: int, d3: int, tail) -> GUID:
    return GUID(d1, d2, d3, (ctypes.c_ubyte * 8)(*tail))


_CLSID_VDM = _guid(0xAA509086, 0x5CA9, 0x4C25,
                   (0x8F, 0x95, 0x58, 0x9D, 0x3C, 0x07, 0xB4, 0x8A))
_IID_IVDM = _guid(0xA5CD92FF, 0x29BE, 0x454C,
                  (0x8D, 0x04, 0xD8, 0x28, 0x79, 0xFB, 0x3F, 0x1B))

_manager = None          # cached interface pointer
_manager_failed = False  # the interface was unavailable: do not retry


def _get_manager():
    """The IVirtualDesktopManager pointer, or None when unavailable.

    CoInitialize is called first: the caller is the render thread, which does
    not otherwise touch COM in a way that guarantees an apartment. The result
    is cached per process - the vtable never changes, and a failed
    CoCreateInstance is not worth retrying on every menu show.
    """
    global _manager, _manager_failed
    if _manager is not None:
        return _manager
    if _manager_failed:
        return None
    try:
        ole32.CoInitialize(None)
        obj = ctypes.c_void_p()
        hr = ole32.CoCreateInstance(ctypes.byref(_CLSID_VDM), None,
                                    CLSCTX_INPROC_SERVER,
                                    ctypes.byref(_IID_IVDM),
                                    ctypes.byref(obj))
        if hr != 0 or not obj.value:
            _manager_failed = True
            return None
        _manager = obj
        return _manager
    except Exception:
        _manager_failed = True
        return None


def _vtable(obj):
    return ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents


# IUnknown: 0 QueryInterface, 1 AddRef, 2 Release;
# IVirtualDesktopManager: 3 IsWindowOnCurrentVirtualDesktop,
# 4 GetWindowDesktopId, 5 MoveWindowToDesktop
_IsOnCurrent = None
_GetId = None
_Move = None


def _calls(obj):
    global _IsOnCurrent, _GetId, _Move
    if _IsOnCurrent is None:
        vtbl = _vtable(obj)
        _IsOnCurrent = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wt.HWND,
            ctypes.POINTER(wt.BOOL))(vtbl[3])
        _GetId = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wt.HWND,
            ctypes.POINTER(GUID))(vtbl[4])
        _Move = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, wt.HWND,
            ctypes.POINTER(GUID))(vtbl[5])
    return _IsOnCurrent, _GetId, _Move


def desktop_id(hwnd) -> GUID | None:
    """The virtual-desktop id a window lives on, or None if unknown."""
    if not hwnd:
        return None
    obj = _get_manager()
    if obj is None:
        return None
    try:
        _, get_id, _ = _calls(obj)
        g = GUID()
        if get_id(obj, hwnd, ctypes.byref(g)) != 0:
            return None
        return g
    except Exception:
        return None


def move_to_desktop(hwnd, guid: GUID) -> bool:
    """Move a window onto the given virtual desktop; True when accepted."""
    if not hwnd or guid is None:
        return False
    obj = _get_manager()
    if obj is None:
        return False
    try:
        _, _, move = _calls(obj)
        return move(obj, hwnd, ctypes.byref(guid)) == 0
    except Exception:
        return False


def follow_window(hwnd, reference) -> bool:
    """Put `hwnd` on the same virtual desktop as `reference`.

    No-op (and True) when the two already agree, so the caller can call this
    freely on every menu show. False means "could not tell" - the caller
    leaves the windows alone rather than guessing a desktop.
    """
    ref_id = desktop_id(reference)
    if ref_id is None:
        return False
    if desktop_id(hwnd) == ref_id:
        return True
    return move_to_desktop(hwnd, ref_id)
