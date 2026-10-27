"""Types shared by the per-OS platform modules.

Each platform module (``windows``, ``macos``) provides the same functions:

Windows to track (``wid`` is an OS window id):
    list_windows(exclude_pid) -> list[WindowInfo]
    window_exists(wid) -> bool
    target_rect(wid) -> Rect | None   area to cover, in the overlay's coordinates;
                                      None while minimized or hidden
    window_title(wid) / window_app(wid) / window_pid(wid)
    frontmost_window(exclude_pid) -> wid or 0
    is_in_front(wid, own_pid) -> bool

The overlay (a QWidget):
    prepare_overlay(widget)
    place_overlay(widget, rect)
    overlay_click_through(widget, enabled)
    overlay_exclude_from_capture(widget, enabled) -> bool

Global hotkeys:
    GlobalHotkeys(parent)  QObject with an ``activated(int)`` signal and
                           ``register(id, letter) -> bool``, ``unregister_all()``,
                           ``problem() -> str | None``
    hotkey_label(letter) -> str
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WindowInfo:
    wid: int
    title: str
    app: str

    @property
    def label(self) -> str:
        if self.title and self.app:
            return f"{self.title}  —  {self.app}"
        return self.title or self.app
