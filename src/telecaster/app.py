"""Telecaster control window: pick a role, a window, a room — then draw."""

from __future__ import annotations

import os
import secrets
import socket
import sys
import time

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .geometry import Insets, Rect, apply_insets, fit_aspect, insets_from_selection
from .protocol import sanitize, valid_room
from .relay import DEFAULT_PORT, RelayThread
from .strokes import StrokeStore

if sys.platform == "win32":
    from . import winapi
    from .net import Session
    from .overlay import LOCAL, Overlay

TRACK_MS = 16
PICK_SECONDS = 3
ROOM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

HOTKEY_DRAW, HOTKEY_CLEAR_MINE, HOTKEY_CLEAR_ALL = 1, 2, 3
HOTKEYS = {
    HOTKEY_DRAW: (0x44, "Ctrl+Alt+D"),  # D
    HOTKEY_CLEAR_MINE: (0x43, "Ctrl+Alt+C"),  # C
    HOTKEY_CLEAR_ALL: (0x58, "Ctrl+Alt+X"),  # X
}


def new_room_code() -> str:
    return "".join(secrets.choice(ROOM_ALPHABET) for _ in range(6))


def lan_ip() -> str:
    # Connecting a UDP socket sends nothing; it just picks the outbound interface.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class MainWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Telecaster {__version__}")
        self.settings = QSettings("Telecaster", "Telecaster")
        self.pid = os.getpid()

        self.store = StrokeStore()
        self.overlay = Overlay(self.store)
        self.session = Session(self)
        self.relay: RelayThread | None = None

        self.target_hwnd = 0
        self.source_aspect: float | None = None
        self.source_size: tuple[int, int] | None = None
        self.insets = Insets.from_list(self.settings.value("viewer/insets", [0, 0, 0, 0]))
        self._sent_size: tuple[int, int] | None = None
        self._last_region: Rect | None = None
        self._pick_left = 0

        self._build_ui()
        self._load_settings()
        self._wire()
        self._register_hotkeys()
        self.refresh_windows(restore=True)

        self._track_timer = QTimer(self, interval=TRACK_MS)
        self._track_timer.timeout.connect(self._tick)
        self._track_timer.start()

    # -- UI ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        role_box = QGroupBox("I am…")
        role_row = QHBoxLayout(role_box)
        self.host_radio = QRadioButton("Streaming (host)")
        self.viewer_radio = QRadioButton("Watching (viewer)")
        self.role_group = QButtonGroup(self)
        self.role_group.addButton(self.host_radio)
        self.role_group.addButton(self.viewer_radio)
        role_row.addWidget(self.host_radio)
        role_row.addWidget(self.viewer_radio)
        root.addWidget(role_box)

        conn_box = QGroupBox("Room")
        conn = QFormLayout(conn_box)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("ws://host-ip:8765")
        conn.addRow("Relay server", self.url_edit)
        self.relay_check = QCheckBox("Run the relay on this PC, port")
        self.port_spin = QSpinBox(minimum=1024, maximum=65535, value=DEFAULT_PORT)
        relay_row = QHBoxLayout()
        relay_row.addWidget(self.relay_check)
        relay_row.addWidget(self.port_spin)
        relay_row.addStretch()
        self.relay_row_widget = QWidget()
        self.relay_row_widget.setLayout(relay_row)
        relay_row.setContentsMargins(0, 0, 0, 0)
        conn.addRow("", self.relay_row_widget)
        self.room_edit = QLineEdit()
        self.room_edit.setMaxLength(32)
        self.new_room_btn = QPushButton("New")
        room_row = QHBoxLayout()
        room_row.addWidget(self.room_edit)
        room_row.addWidget(self.new_room_btn)
        conn.addRow("Room code", room_row)
        self.name_edit = QLineEdit()
        self.name_edit.setMaxLength(32)
        conn.addRow("Your name", self.name_edit)
        self.color_btn = QPushButton()
        conn.addRow("Pen color", self.color_btn)
        self.connect_btn = QPushButton("Connect")
        conn.addRow(self.connect_btn)
        self.status_label = QLabel("Not connected")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        conn.addRow(self.status_label)
        root.addWidget(conn_box)

        win_box = QGroupBox("Window to draw on")
        win = QVBoxLayout(win_box)
        self.window_hint = QLabel()
        self.window_hint.setWordWrap(True)
        win.addWidget(self.window_hint)
        self.window_combo = QComboBox()
        self.window_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.window_combo.setMinimumContentsLength(30)
        win.addWidget(self.window_combo)
        win_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh list")
        self.pick_btn = QPushButton(f"Pick by focusing it ({PICK_SECONDS}s)")
        win_row.addWidget(self.refresh_btn)
        win_row.addWidget(self.pick_btn)
        win.addLayout(win_row)
        self.track_label = QLabel("No window selected")
        win.addWidget(self.track_label)
        root.addWidget(win_box)

        self.video_box = QGroupBox("Stream video area")
        video = QVBoxLayout(self.video_box)
        self.fit_check = QCheckBox("Match the streamer's aspect ratio (handles black bars)")
        video.addWidget(self.fit_check)
        video_row = QHBoxLayout()
        self.calibrate_btn = QPushButton("Mark video area…")
        self.reset_area_btn = QPushButton("Use whole window")
        video_row.addWidget(self.calibrate_btn)
        video_row.addWidget(self.reset_area_btn)
        video.addLayout(video_row)
        self.insets_label = QLabel()
        video.addWidget(self.insets_label)
        root.addWidget(self.video_box)

        pen_box = QGroupBox("Drawing")
        pen = QFormLayout(pen_box)
        self.width_spin = QSpinBox(minimum=1, maximum=60, value=6, suffix=" px @1080p")
        pen.addRow("Line width", self.width_spin)
        self.ttl_spin = QDoubleSpinBox(minimum=0, maximum=600, value=4, decimals=1, suffix=" s")
        self.ttl_spin.setSpecialValueText("never")
        pen.addRow("Fade after", self.ttl_spin)
        self.draw_btn = QPushButton(f"Draw mode  [{HOTKEYS[HOTKEY_DRAW][1]}]")
        self.draw_btn.setCheckable(True)
        pen.addRow(self.draw_btn)
        clear_row = QHBoxLayout()
        self.clear_mine_btn = QPushButton(f"Clear mine  [{HOTKEYS[HOTKEY_CLEAR_MINE][1]}]")
        self.clear_all_btn = QPushButton(f"Clear everyone's  [{HOTKEYS[HOTKEY_CLEAR_ALL][1]}]")
        clear_row.addWidget(self.clear_mine_btn)
        clear_row.addWidget(self.clear_all_btn)
        pen.addRow(clear_row)
        pen.addRow(QLabel("Left-drag draws, right-click pings. Draw mode blocks clicks to the window underneath."))
        root.addWidget(pen_box)

        opt_box = QGroupBox("Options")
        opt = QVBoxLayout(opt_box)
        self.hide_unfocused_check = QCheckBox("Hide overlay while the window isn't in front")
        self.exclude_capture_check = QCheckBox("Keep the overlay out of screen captures")
        opt.addWidget(self.hide_unfocused_check)
        opt.addWidget(self.exclude_capture_check)
        root.addWidget(opt_box)
        root.addStretch()

    def _wire(self) -> None:
        self.host_radio.toggled.connect(self._on_role_changed)
        self.new_room_btn.clicked.connect(lambda: self.room_edit.setText(new_room_code()))
        self.color_btn.clicked.connect(self._choose_color)
        self.connect_btn.clicked.connect(self._toggle_connection)
        self.relay_check.toggled.connect(self._update_role_ui)
        self.refresh_btn.clicked.connect(lambda: self.refresh_windows())
        self.pick_btn.clicked.connect(self._start_pick)
        self.window_combo.activated.connect(self._on_combo_activated)
        self.fit_check.toggled.connect(lambda _: self._update_insets_label())
        self.calibrate_btn.clicked.connect(self._start_calibration)
        self.reset_area_btn.clicked.connect(lambda: self._set_insets(Insets()))
        self.width_spin.valueChanged.connect(self._apply_pen)
        self.ttl_spin.valueChanged.connect(self._apply_pen)
        self.draw_btn.toggled.connect(self._on_draw_toggled)
        self.clear_mine_btn.clicked.connect(self.clear_mine)
        self.clear_all_btn.clicked.connect(self.clear_all)
        self.exclude_capture_check.toggled.connect(self.overlay.set_exclude_from_capture)

        self.overlay.outgoing.connect(self.session.send)
        self.overlay.calibrated.connect(self._on_calibrated)
        self.overlay.calibration_cancelled.connect(lambda: self.overlay.set_mode("passive"))

        self.session.status.connect(self.status_label.setText)
        self.session.joined.connect(self._on_joined)
        self.session.message.connect(self._on_message)
        self.session.peers_changed.connect(self._update_peers)
        self.session.peer_left.connect(lambda pid: self.store.end_all(pid, time.monotonic()))
        self.session.left.connect(self._update_connect_button)

    # -- settings ------------------------------------------------------------

    def _load_settings(self) -> None:
        s = self.settings
        role = s.value("role", "viewer")
        (self.host_radio if role == "host" else self.viewer_radio).setChecked(True)
        self.url_edit.setText(s.value("url", f"ws://127.0.0.1:{DEFAULT_PORT}"))
        self.relay_check.setChecked(s.value("host/run_relay", True, type=bool))
        self.port_spin.setValue(s.value("host/port", DEFAULT_PORT, type=int))
        self.room_edit.setText(s.value("room", "") or new_room_code())
        self.name_edit.setText(s.value("name", "") or os.environ.get("USERNAME", "player"))
        self._set_color(s.value("color", "#ff3b30"))
        self.width_spin.setValue(s.value("pen/width", 6, type=int))
        self.ttl_spin.setValue(s.value("pen/ttl", 4.0, type=float))
        self.fit_check.setChecked(s.value("viewer/fit", True, type=bool))
        self.hide_unfocused_check.setChecked(s.value("hide_unfocused", True, type=bool))
        self.exclude_capture_check.setChecked(s.value("exclude_capture", True, type=bool))
        self.overlay.set_exclude_from_capture(self.exclude_capture_check.isChecked())
        self._apply_pen()
        self._update_role_ui()
        self._update_insets_label()

    def _save_settings(self) -> None:
        s = self.settings
        s.setValue("role", self.role)
        s.setValue("url", self.url_edit.text().strip())
        s.setValue("host/run_relay", self.relay_check.isChecked())
        s.setValue("host/port", self.port_spin.value())
        s.setValue("room", self.room_edit.text().strip())
        s.setValue("name", self.name_edit.text().strip())
        s.setValue("color", self.overlay.pen_color)
        s.setValue("pen/width", self.width_spin.value())
        s.setValue("pen/ttl", self.ttl_spin.value())
        s.setValue("viewer/fit", self.fit_check.isChecked())
        s.setValue("viewer/insets", self.insets.as_list())
        s.setValue("hide_unfocused", self.hide_unfocused_check.isChecked())
        s.setValue("exclude_capture", self.exclude_capture_check.isChecked())

    # -- role / pen ----------------------------------------------------------

    @property
    def role(self) -> str:
        return "host" if self.host_radio.isChecked() else "viewer"

    def _on_role_changed(self, _checked: bool) -> None:
        self._sent_size = None
        self._update_role_ui()

    def _update_role_ui(self) -> None:
        host = self.role == "host"
        self.relay_row_widget.setVisible(host)
        self.url_edit.setEnabled(not (host and self.relay_check.isChecked()))
        self.video_box.setVisible(not host)
        self.clear_all_btn.setVisible(host)
        if host:
            self.window_hint.setText(
                "Choose the game window you are sharing in Discord. "
                "Run games in borderless/windowed mode: overlays can't draw over exclusive fullscreen.")
        else:
            self.window_hint.setText(
                "Choose the Discord window (or popped-out stream window) showing the stream. "
                "Full-screening the stream in Discord gives the most accurate lines.")

    def _set_color(self, color: str) -> None:
        if not QColor.isValidColorName(color):
            color = "#ff3b30"
        color = QColor(color).name()
        self.overlay.pen_color = color
        swatch = QPixmap(16, 16)
        swatch.fill(QColor(color))
        self.color_btn.setIcon(QIcon(swatch))
        self.color_btn.setText(color)

    def _choose_color(self) -> None:
        color = QColorDialog.getColor(QColor(self.overlay.pen_color), self, "Pen color")
        if color.isValid():
            self._set_color(color.name())

    def _apply_pen(self) -> None:
        self.overlay.pen_width = self.width_spin.value() / 1080
        self.overlay.pen_ttl = float(self.ttl_spin.value())

    def _on_draw_toggled(self, on: bool) -> None:
        if self.overlay.mode == "calibrate":
            self.draw_btn.setChecked(False)
            return
        self.overlay.set_mode("draw" if on else "passive")

    def toggle_draw(self) -> None:
        self.draw_btn.setChecked(not self.draw_btn.isChecked())

    def clear_mine(self) -> None:
        self.store.clear(LOCAL)
        self.session.send({"t": "clear", "scope": "mine"})
        self.overlay.refresh()

    def clear_all(self) -> None:
        if self.role != "host":
            return
        self.store.clear()
        self.session.send({"t": "clear", "scope": "all"})
        self.overlay.refresh()

    # -- connection ----------------------------------------------------------

    def _toggle_connection(self) -> None:
        if self.session.is_joined or self.connect_btn.text() == "Disconnect":
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        room = self.room_edit.text().strip()
        if not valid_room(room):
            QMessageBox.warning(self, "Telecaster", "Room codes are 3-32 letters, digits, - or _.")
            return
        url = self.url_edit.text().strip()
        if self.role == "host" and self.relay_check.isChecked():
            port = self.port_spin.value()
            if self.relay is None or self.relay.port != port or not self.relay.is_alive():
                self._stop_relay()
                self.relay = RelayThread("0.0.0.0", port)
                self.relay.start()
                self.relay.ready.wait(3)
                if self.relay.error is not None:
                    QMessageBox.critical(self, "Telecaster", f"Couldn't start the relay on port {port}:\n{self.relay.error}")
                    self.relay = None
                    return
            url = f"ws://127.0.0.1:{port}"
        elif not url.startswith(("ws://", "wss://")):
            QMessageBox.warning(self, "Telecaster", "The relay address should start with ws:// or wss://")
            return
        self._save_settings()
        self.session.open(url, room, self.name_edit.text().strip(), self.overlay.pen_color, self.role)
        self.connect_btn.setText("Disconnect")
        for widget in (self.host_radio, self.viewer_radio, self.room_edit, self.url_edit,
                       self.relay_check, self.port_spin, self.name_edit, self.color_btn, self.new_room_btn):
            widget.setEnabled(False)

    def _disconnect(self) -> None:
        self.session.close()
        self._stop_relay()
        self._update_connect_button()

    def _update_connect_button(self) -> None:
        if self.session.wants_connection:
            return
        self.connect_btn.setText("Connect")
        for widget in (self.host_radio, self.viewer_radio, self.room_edit, self.relay_check,
                       self.port_spin, self.name_edit, self.color_btn, self.new_room_btn):
            widget.setEnabled(True)
        self._update_role_ui()

    def _stop_relay(self) -> None:
        if self.relay is not None:
            self.relay.stop()
            self.relay.join(2)
            self.relay = None

    def _on_joined(self, welcome: dict) -> None:
        source = sanitize(welcome.get("source")) if welcome.get("source") else None
        if source and self.role == "viewer":
            self._set_source(source)
        self._sent_size = None  # host re-announces its window size
        if self.role == "host" and self.relay is not None:
            port = self.relay.port
            self.status_label.setText(
                f"Hosting room {welcome.get('room')}. Viewers on your network use ws://{lan_ip()}:{port}. "
                f"Over the internet, forward TCP port {port} and share ws://<your public IP>:{port}.")
        self._update_peers()

    def _on_message(self, msg: dict) -> None:
        if msg["t"] == "source":
            self._set_source(msg)
            return
        self.store.apply(msg["from"], msg, time.monotonic())
        self.overlay.refresh()

    def _set_source(self, source: dict) -> None:
        self.source_aspect = source["aspect"]
        self.source_size = (source["w"], source["h"])
        self._update_insets_label()

    def _update_peers(self) -> None:
        if not self.session.is_joined:
            return
        names = [p.get("name", "?") + (" (host)" if p.get("role") == "host" else "")
                 for p in self.session.peers.values()]
        base = self.status_label.text().split("\n")[0]
        self.status_label.setText(base + "\n" + (f"With: {', '.join(names)}" if names else "Nobody else here yet."))

    # -- target window -------------------------------------------------------

    def refresh_windows(self, restore: bool = False) -> None:
        windows = winapi.list_windows(exclude_pid=self.pid)
        self.window_combo.clear()
        self.window_combo.addItem("— choose a window —", 0)
        for info in windows:
            self.window_combo.addItem(info.label, info.hwnd)
        if restore:
            want_title = self.settings.value(f"{self.role}/target_title", "")
            want_exe = self.settings.value(f"{self.role}/target_exe", "")
            match = next((w for w in windows if w.title == want_title and w.exe == want_exe), None)
            if match is None and want_exe:
                same_exe = [w for w in windows if w.exe == want_exe]
                match = same_exe[0] if len(same_exe) == 1 else None
            if match:
                self._set_target(match.hwnd)
        self._sync_combo()

    def _sync_combo(self) -> None:
        index = self.window_combo.findData(self.target_hwnd) if self.target_hwnd else 0
        if index < 0 and self.target_hwnd:
            self.window_combo.addItem(winapi.window_title(self.target_hwnd) or "(untitled)", self.target_hwnd)
            index = self.window_combo.count() - 1
        self.window_combo.setCurrentIndex(max(index, 0))

    def _on_combo_activated(self, index: int) -> None:
        self._set_target(int(self.window_combo.itemData(index) or 0))

    def _set_target(self, hwnd: int) -> None:
        self.target_hwnd = hwnd
        self._sent_size = None
        self._last_region = None
        if hwnd:
            self.settings.setValue(f"{self.role}/target_title", winapi.window_title(hwnd))
            self.settings.setValue(f"{self.role}/target_exe", winapi.window_exe(hwnd))
        else:
            self.overlay.hide()
            self.track_label.setText("No window selected")

    def _start_pick(self) -> None:
        self._pick_left = PICK_SECONDS
        self.pick_btn.setEnabled(False)
        self._pick_step()

    def _pick_step(self) -> None:
        if self._pick_left > 0:
            self.pick_btn.setText(f"Click the window now… {self._pick_left}")
            self._pick_left -= 1
            QTimer.singleShot(1000, self._pick_step)
            return
        self.pick_btn.setText(f"Pick by focusing it ({PICK_SECONDS}s)")
        self.pick_btn.setEnabled(True)
        hwnd = winapi.foreground_window()
        if not hwnd or winapi.window_pid(hwnd) == self.pid:
            self.track_label.setText("Picked Telecaster itself — click the target window during the countdown.")
            return
        self._set_target(hwnd)
        self._sync_combo()
        self.raise_()
        self.activateWindow()

    # -- viewer video area ---------------------------------------------------

    def _start_calibration(self) -> None:
        if not self.target_hwnd:
            QMessageBox.information(self, "Telecaster", "Choose the Discord window first.")
            return
        self.draw_btn.setChecked(False)
        self.overlay.set_mode("calibrate")

    def _on_calibrated(self, fx: float, fy: float, fw: float, fh: float) -> None:
        self.overlay.set_mode("passive")
        client = winapi.client_rect(self.target_hwnd)
        if client is None:
            return
        sel = Rect(round(fx * client.w), round(fy * client.h), round(fw * client.w), round(fh * client.h))
        self._set_insets(insets_from_selection(client, sel))

    def _set_insets(self, insets: Insets) -> None:
        self.insets = insets
        self.settings.setValue("viewer/insets", insets.as_list())
        self._update_insets_label()

    def _update_insets_label(self) -> None:
        area = "whole window" if self.insets == Insets() else (
            "trimmed {} / {} / {} / {} px (left/top/right/bottom)".format(*self.insets.as_list()))
        if self.source_size:
            src = f"streamer's window is {self.source_size[0]}×{self.source_size[1]}"
        else:
            src = "waiting for the streamer's window size"
        self.insets_label.setText(f"Area: {area}; {src}.")

    # -- tracking loop -------------------------------------------------------

    def _region_for(self, client: Rect) -> Rect:
        if self.role == "host" or self.overlay.mode == "calibrate":
            return client
        region = apply_insets(client, self.insets)
        if self.fit_check.isChecked() and self.source_aspect:
            region = fit_aspect(region, self.source_aspect)
        return region

    def _tick(self) -> None:
        hwnd = self.target_hwnd
        if not hwnd:
            return
        if not winapi.is_window(hwnd):
            self._set_target(0)
            self._sync_combo()
            self.track_label.setText("The selected window was closed.")
            return
        client = winapi.client_rect(hwnd)
        if client is None:
            self.overlay.hide()
            return
        if self.hide_unfocused_check.isChecked() and self.overlay.mode == "passive":
            fg = winapi.foreground_window()
            if fg and fg != hwnd and winapi.window_pid(fg) != self.pid:
                self.overlay.hide()
                return

        if self.role == "host" and self.session.is_joined and self._sent_size != (client.w, client.h):
            self._sent_size = (client.w, client.h)
            self.session.send({"t": "source", "aspect": client.w / client.h, "w": client.w, "h": client.h})

        region = self._region_for(client)
        self.overlay.place(region)
        if region != self._last_region:
            self._last_region = region
            self.track_label.setText(
                f"Tracking {client.w}×{client.h} window; overlay {region.w}×{region.h} at ({region.x}, {region.y})")

    # -- hotkeys -------------------------------------------------------------

    def _register_hotkeys(self) -> None:
        hwnd = int(self.winId())
        failed = []
        for hotkey_id, (vk, label) in HOTKEYS.items():
            if not winapi.register_hotkey(hwnd, hotkey_id, winapi.MOD_CONTROL | winapi.MOD_ALT, vk):
                failed.append(label)
        if failed:
            self.status_label.setText(f"Hotkeys already in use by another app: {', '.join(failed)}")

    def nativeEvent(self, event_type, message):
        if bytes(event_type) == b"windows_generic_MSG":
            msg = winapi.msg_from_address(int(message))
            if msg.message == winapi.WM_HOTKEY:
                action = {HOTKEY_DRAW: self.toggle_draw, HOTKEY_CLEAR_MINE: self.clear_mine,
                          HOTKEY_CLEAR_ALL: self.clear_all}.get(int(msg.wParam))
                if action:
                    action()
                return True, 0
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event) -> None:
        self._save_settings()
        hwnd = int(self.winId())
        for hotkey_id in HOTKEYS:
            winapi.unregister_hotkey(hwnd, hotkey_id)
        self.session.close()
        self._stop_relay()
        self.overlay.close()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Telecaster")
    if sys.platform != "win32":
        QMessageBox.critical(None, "Telecaster", "Telecaster's overlay currently supports Windows only.\n"
                             "The relay server (telecaster-relay) runs anywhere.")
        sys.exit(1)
    window = MainWindow()
    window.show()
    app.setQuitOnLastWindowClosed(True)
    sys.exit(app.exec())
