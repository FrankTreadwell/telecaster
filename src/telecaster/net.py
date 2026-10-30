"""Relay client built on QWebSocket so it runs on the Qt event loop."""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtWebSockets import QWebSocket

from .protocol import PROTOCOL_VERSION, sanitize

RECONNECT_MS = 3000


class Session(QObject):
    status = Signal(str)
    joined = Signal(dict)  # the relay's welcome message
    message = Signal(dict)  # a sanitized drawing/state message, with "from"
    peers_changed = Signal()
    peer_left = Signal(str)
    left = Signal()  # connection lost or closed

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ws = QWebSocket()
        self._ws.connected.connect(self._on_connected)
        self._ws.disconnected.connect(self._on_disconnected)
        self._ws.textMessageReceived.connect(self._on_text)
        self._retry = QTimer(self, singleShot=True, interval=RECONNECT_MS)
        self._retry.timeout.connect(self._open)
        self._url = ""
        self._join: dict = {}
        self._wanted = False
        self._refused = False
        self.my_id: str | None = None
        self.peers: dict[str, dict] = {}

    @property
    def is_joined(self) -> bool:
        return self.my_id is not None

    @property
    def wants_connection(self) -> bool:
        return self._wanted

    def open(self, url: str, room: str, name: str, color: str, role: str) -> None:
        self._url = url
        self._join = {"t": "join", "room": room, "name": name, "color": color, "role": role,
                      "v": PROTOCOL_VERSION}
        self._wanted = True
        self._refused = False
        self._retry.stop()
        self._ws.abort()
        self._open()

    def set_color(self, color: str) -> None:
        """Use ``color`` in the join message from now on, so a reconnect keeps it."""
        if self._join:
            self._join["color"] = color

    def close(self) -> None:
        self._wanted = False
        self._retry.stop()
        self._ws.close()

    def send(self, msg: dict) -> None:
        if self.is_joined:
            self._ws.sendTextMessage(json.dumps(msg, separators=(",", ":")))

    def _open(self) -> None:
        self.status.emit(f"Connecting to {self._url}…")
        self._ws.open(QUrl(self._url))

    def _on_connected(self) -> None:
        self._ws.sendTextMessage(json.dumps(self._join))

    def _on_disconnected(self) -> None:
        self.my_id = None
        self.peers.clear()
        self.peers_changed.emit()
        if self._wanted:
            reason = self._ws.errorString() or "connection closed"
            self.status.emit(f"Disconnected ({reason}). Retrying in {RECONNECT_MS // 1000}s…")
            self._retry.start()
        elif not self._refused:
            self.status.emit("Not connected")
        self.left.emit()

    def _on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except ValueError:
            return
        if not isinstance(msg, dict):
            return
        t = msg.get("t")

        if t == "welcome":
            self.my_id = str(msg.get("id"))
            self.peers = {str(p["id"]): p for p in msg.get("peers", []) if isinstance(p, dict) and "id" in p}
            self.status.emit(f"Connected to room {msg.get('room')}")
            self.peers_changed.emit()
            self.joined.emit(msg)
            return
        if t == "error":
            # Relay rejections (room has a host, protocol mismatch…) won't fix themselves.
            self._wanted = False
            self._refused = True
            self.status.emit(f"Relay refused: {msg.get('msg', 'unknown error')}")
            return
        if t == "peer_join" and "id" in msg:
            self.peers[str(msg["id"])] = msg
            self.peers_changed.emit()
            return
        if t == "peer_leave" and "id" in msg:
            peer_id = str(msg["id"])
            self.peers.pop(peer_id, None)
            self.peers_changed.emit()
            self.peer_left.emit(peer_id)
            return

        clean = sanitize(msg)
        if clean is not None and "from" in msg:
            clean["from"] = str(msg["from"])
            self.message.emit(clean)
