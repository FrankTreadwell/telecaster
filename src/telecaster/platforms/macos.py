"""macOS platform layer, via PyObjC (Quartz window list + AppKit windows/events).

Coordinates are in points with the origin at the top-left of the main
display — the same global coordinate space Qt uses on macOS — so overlays are
positioned with plain Qt geometry.

Permissions:
* Window titles in the picker need Screen Recording permission; without it
  windows are listed by app name only.
* Hotkeys while another app is focused need Accessibility permission.
"""

from __future__ import annotations

import sys

if sys.platform != "darwin":  # pragma: no cover - selected by platforms/__init__
    raise ImportError("telecaster.platforms.macos is only available on macOS")

import objc
from AppKit import (
    NSEvent,
    NSEventMaskKeyDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSStatusWindowLevel,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowCollectionBehaviorIgnoresCycle,
    NSWindowCollectionBehaviorStationary,
    NSWindowSharingNone,
    NSWindowSharingReadOnly,
    NSWorkspace,
)
from ApplicationServices import AXIsProcessTrusted, AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
from PySide6.QtCore import QObject, QRect, Signal
from PySide6.QtWidgets import QWidget
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowBounds,
    kCGWindowIsOnscreen,
    kCGWindowLayer,
    kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionIncludingWindow,
    kCGWindowListOptionOnScreenOnly,
    kCGWindowName,
    kCGWindowNumber,
    kCGWindowOwnerName,
    kCGWindowOwnerPID,
)

from ..geometry import Rect
from .base import WindowInfo

MIN_SIZE = 60


def _info(wid: int):
    if not wid:
        return None
    for entry in CGWindowListCopyWindowInfo(kCGWindowListOptionIncludingWindow, wid) or []:
        if int(entry[kCGWindowNumber]) == wid:
            return entry
    return None


def _bounds(entry) -> Rect:
    b = entry[kCGWindowBounds]
    return Rect(round(b["X"]), round(b["Y"]), round(b["Width"]), round(b["Height"]))


def _onscreen_windows():
    options = kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    return CGWindowListCopyWindowInfo(options, kCGNullWindowID) or []


def list_windows(exclude_pid: int | None = None) -> list[WindowInfo]:
    """Normal app windows on screen, front to back."""
    found = []
    for entry in _onscreen_windows():
        if entry.get(kCGWindowLayer, 0) != 0:
            continue  # menu bar, dock, status items…
        if exclude_pid is not None and entry.get(kCGWindowOwnerPID) == exclude_pid:
            continue
        rect = _bounds(entry)
        if rect.w < MIN_SIZE or rect.h < MIN_SIZE:
            continue
        title = entry.get(kCGWindowName) or ""
        if not title:
            title = f"window {rect.w}×{rect.h}"
        found.append(WindowInfo(int(entry[kCGWindowNumber]), title, entry.get(kCGWindowOwnerName) or ""))
    return found


def window_exists(wid: int) -> bool:
    return _info(wid) is not None


def target_rect(wid: int) -> Rect | None:
    # Screen-sharing apps capture the whole macOS window, title bar included,
    # so the overlay covers the full window frame.
    entry = _info(wid)
    if entry is None or not entry.get(kCGWindowIsOnscreen):
        return None
    rect = _bounds(entry)
    return None if rect.empty else rect


def window_title(wid: int) -> str:
    entry = _info(wid)
    return (entry.get(kCGWindowName) or "") if entry is not None else ""


def window_app(wid: int) -> str:
    entry = _info(wid)
    return (entry.get(kCGWindowOwnerName) or "") if entry is not None else ""


def window_pid(wid: int) -> int:
    entry = _info(wid)
    return int(entry.get(kCGWindowOwnerPID, 0)) if entry is not None else 0


def _frontmost_pid() -> int:
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app is not None else 0


def frontmost_window(exclude_pid: int) -> int:
    pid = _frontmost_pid()
    if not pid or pid == exclude_pid:
        return 0
    for entry in _onscreen_windows():
        if entry.get(kCGWindowOwnerPID) == pid and entry.get(kCGWindowLayer, 0) == 0:
            return int(entry[kCGWindowNumber])
    return 0


def is_in_front(wid: int, own_pid: int) -> bool:
    pid = _frontmost_pid()
    return not pid or pid == own_pid or pid == window_pid(wid)


def _nswindow(widget: QWidget):
    view = objc.objc_object(c_void_p=int(widget.winId()))
    return view.window()


def prepare_overlay(widget: QWidget) -> None:
    window = _nswindow(widget)
    if window is None:
        return
    # Qt::Tool windows are NSPanels that vanish when the app loses focus,
    # which is exactly when the overlay matters.
    window.setHidesOnDeactivate_(False)
    window.setHasShadow_(False)
    window.setLevel_(NSStatusWindowLevel)
    window.setCollectionBehavior_(
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorIgnoresCycle
    )


def place_overlay(widget: QWidget, rect: Rect) -> None:
    target = QRect(rect.x, rect.y, rect.w, rect.h)
    if widget.geometry() != target:
        widget.setGeometry(target)


def overlay_click_through(widget: QWidget, enabled: bool) -> None:
    window = _nswindow(widget)
    if window is not None:
        window.setIgnoresMouseEvents_(enabled)


def overlay_exclude_from_capture(widget: QWidget, enabled: bool) -> bool:
    window = _nswindow(widget)
    if window is None:
        return False
    window.setSharingType_(NSWindowSharingNone if enabled else NSWindowSharingReadOnly)
    return True


def hotkey_label(letter: str) -> str:
    return f"⌃⌥{letter.upper()}"  # Control+Option


# Virtual key codes (kVK_ANSI_*) so hotkeys follow key position, not layout.
_KEYCODES = {"A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7, "C": 8, "V": 9}
_MODS = NSEventModifierFlagControl | NSEventModifierFlagOption | NSEventModifierFlagShift | NSEventModifierFlagCommand
_WANT = NSEventModifierFlagControl | NSEventModifierFlagOption


class GlobalHotkeys(QObject):
    """Control+Option+<letter> hotkeys, including while other apps have focus."""

    activated = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keys: dict[int, int] = {}
        # Shows the system "allow Accessibility" prompt the first time.
        AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        self._global = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, self._on_global)
        self._local = NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, self._on_local)

    def _match(self, event) -> int | None:
        if (event.modifierFlags() & _MODS) != _WANT:
            return None
        return self._keys.get(int(event.keyCode()))

    def _on_global(self, event) -> None:
        hotkey_id = self._match(event)
        if hotkey_id is not None:
            self.activated.emit(hotkey_id)

    def _on_local(self, event):
        hotkey_id = self._match(event)
        if hotkey_id is None:
            return event
        self.activated.emit(hotkey_id)
        return None  # swallow it

    def register(self, hotkey_id: int, letter: str) -> bool:
        code = _KEYCODES.get(letter.upper())
        if code is None:
            return False
        self._keys[code] = hotkey_id
        return True

    def unregister_all(self) -> None:
        self._keys.clear()
        for monitor in (self._global, self._local):
            if monitor is not None:
                NSEvent.removeMonitor_(monitor)
        self._global = self._local = None

    def problem(self) -> str | None:
        if not AXIsProcessTrusted():
            return ("To use hotkeys while another app is in front, allow Telecaster in System Settings → "
                    "Privacy & Security → Accessibility, then restart it.")
        return None
