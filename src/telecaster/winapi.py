"""Thin ctypes wrappers over the Win32 calls Telecaster needs.

Everything here works in physical pixels. Qt 6 makes the process per-monitor
DPI aware, so Win32 reports real pixel coordinates on every monitor.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass

from .geometry import Rect

if sys.platform != "win32":  # pragma: no cover - guarded by the app entry point
    raise ImportError("telecaster.winapi is only available on Windows")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = wintypes.HWND(-1)
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_NOOWNERZORDER = 0x0200

GA_ROOT = 2
DWMWA_CLOAKED = 14
WDA_NONE = 0x0
WDA_EXCLUDEFROMCAPTURE = 0x11
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND
user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
]
user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _GetWindowLong = user32.GetWindowLongPtrW
    _SetWindowLong = user32.SetWindowLongPtrW
    _LONG = ctypes.c_ssize_t
else:  # pragma: no cover - 32-bit Python
    _GetWindowLong = user32.GetWindowLongW
    _SetWindowLong = user32.SetWindowLongW
    _LONG = ctypes.c_long
_GetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int]
_GetWindowLong.restype = _LONG
_SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, _LONG]
_SetWindowLong.restype = _LONG

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    exe: str

    @property
    def label(self) -> str:
        return f"{self.title}  —  {self.exe}" if self.exe else self.title


def window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_exe(hwnd: int) -> str:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, window_pid(hwnd))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _is_cloaked(hwnd: int) -> bool:
    # UWP apps and windows on other virtual desktops are "visible" but cloaked.
    cloaked = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return cloaked.value != 0


def list_windows(exclude_pid: int | None = None) -> list[WindowInfo]:
    """Visible, titled top-level windows, in Z order (front to back)."""
    found: list[WindowInfo] = []

    @WNDENUMPROC
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
            return True
        if get_ex_style(hwnd) & WS_EX_TOOLWINDOW:
            return True
        title = window_title(hwnd)
        if not title:
            return True
        if exclude_pid is not None and window_pid(hwnd) == exclude_pid:
            return True
        found.append(WindowInfo(int(hwnd), title, window_exe(hwnd)))
        return True

    user32.EnumWindows(callback, 0)
    return found


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def client_rect(hwnd: int) -> Rect | None:
    """The window's client area (no title bar or borders) in screen pixels."""
    if not is_window(hwnd) or is_minimized(hwnd) or not user32.IsWindowVisible(hwnd):
        return None
    rc = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rc)):
        return None
    origin = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    rect = Rect(origin.x, origin.y, rc.right - rc.left, rc.bottom - rc.top)
    return None if rect.empty else rect


def window_rect(hwnd: int) -> Rect | None:
    rc = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rc)):
        return None
    return Rect(rc.left, rc.top, rc.right - rc.left, rc.bottom - rc.top)


def foreground_window() -> int:
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return 0
    return int(user32.GetAncestor(hwnd, GA_ROOT) or hwnd)


def place_topmost(hwnd: int, rect: Rect) -> None:
    user32.SetWindowPos(
        hwnd, HWND_TOPMOST, rect.x, rect.y, rect.w, rect.h, SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def get_ex_style(hwnd: int) -> int:
    return int(_GetWindowLong(hwnd, GWL_EXSTYLE))


def set_ex_style_bits(hwnd: int, bits: int, enabled: bool) -> None:
    style = get_ex_style(hwnd)
    new = (style | bits) if enabled else (style & ~bits)
    if new != style:
        _SetWindowLong(hwnd, GWL_EXSTYLE, new)
        user32.SetWindowPos(
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER | SWP_FRAMECHANGED | SWP_NOZORDER,
        )


def make_overlay_style(hwnd: int) -> None:
    """Never steal focus from the window underneath, and stay out of Alt+Tab."""
    set_ex_style_bits(hwnd, WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, True)


def set_click_through(hwnd: int, enabled: bool) -> None:
    # A layered + transparent window is skipped by hit-testing entirely, so
    # clicks reach whatever is underneath (the shared window, or the viewing app).
    if enabled:
        set_ex_style_bits(hwnd, WS_EX_LAYERED | WS_EX_TRANSPARENT, True)
    else:
        set_ex_style_bits(hwnd, WS_EX_TRANSPARENT, False)


def set_exclude_from_capture(hwnd: int, enabled: bool) -> bool:
    """Hide a window from screen capture (Windows 10 2004+). Returns success."""
    return bool(user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE))


def register_hotkey(hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
    return bool(user32.RegisterHotKey(hwnd, hotkey_id, modifiers | MOD_NOREPEAT, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    user32.UnregisterHotKey(hwnd, hotkey_id)


def msg_from_address(address: int) -> wintypes.MSG:
    return wintypes.MSG.from_address(address)
