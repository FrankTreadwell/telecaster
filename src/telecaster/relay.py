"""Tiny WebSocket relay that connects the person sharing a screen with their viewers.

Screen-sharing apps have no side channel for third-party data, so Telecaster
clients meet in a "room" on this relay. It only validates and forwards messages; it keeps
no history except the host's window size, so late joiners can scale lines.

Run standalone (only needs the ``websockets`` package):

    python -m telecaster.relay --port 8765

The desktop app can also run it in a background thread ("Host on this PC").
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import logging
import threading
import time
from dataclasses import dataclass, field

from websockets.asyncio.server import ServerConnection, broadcast, serve
from websockets.exceptions import ConnectionClosed

from .protocol import (
    MAX_MESSAGE_BYTES,
    MAX_PEERS_PER_ROOM,
    PROTOCOL_VERSION,
    sanitize,
    sanitize_join,
)

log = logging.getLogger("telecaster.relay")

DEFAULT_PORT = 8765
JOIN_TIMEOUT = 10.0


@dataclass
class Peer:
    id: str
    ws: ServerConnection
    name: str
    color: str
    role: str
    # Token bucket so one client can't flood a room.
    tokens: float = 600.0
    last_refill: float = field(default_factory=time.monotonic)

    RATE = 300.0
    BURST = 600.0

    def take_token(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.BURST, self.tokens + (now - self.last_refill) * self.RATE)
        self.last_refill = now
        if self.tokens < 1.0:
            return False
        self.tokens -= 1.0
        return True

    def info(self) -> dict:
        return {"id": self.id, "name": self.name, "color": self.color, "role": self.role}


@dataclass
class Room:
    peers: dict[str, Peer] = field(default_factory=dict)
    source: dict | None = None

    def host(self) -> Peer | None:
        return next((p for p in self.peers.values() if p.role == "host"), None)


def _loads(raw: str | bytes) -> object:
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _dumps(msg: dict) -> str:
    return json.dumps(msg, separators=(",", ":"))


class Relay:
    def __init__(self) -> None:
        self.rooms: dict[str, Room] = {}
        self._ids = itertools.count(1)

    def _send_room(self, room: Room, msg: dict, exclude: Peer | None = None) -> None:
        targets = [p.ws for p in room.peers.values() if p is not exclude]
        if targets:
            broadcast(targets, _dumps(msg))

    async def _reject(self, ws: ServerConnection, reason: str) -> None:
        try:
            await ws.send(_dumps({"t": "error", "msg": reason}))
            await ws.close(code=4000, reason=reason[:100])
        except ConnectionClosed:
            pass

    async def handler(self, ws: ServerConnection) -> None:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=JOIN_TIMEOUT)
        except (asyncio.TimeoutError, ConnectionClosed):
            return

        join = sanitize_join(_loads(raw))
        if join is None:
            await self._reject(ws, "Invalid join message (room codes are 3-32 letters/digits).")
            return
        if join["v"] != PROTOCOL_VERSION:
            await self._reject(ws, f"Protocol mismatch: relay speaks v{PROTOCOL_VERSION}, client v{join['v']}.")
            return

        room_code = join["room"]
        room = self.rooms.setdefault(room_code, Room())
        if len(room.peers) >= MAX_PEERS_PER_ROOM:
            await self._reject(ws, "Room is full.")
            return
        if join["role"] == "host" and room.host() is not None:
            await self._reject(ws, "This room already has a host.")
            return

        peer = Peer(str(next(self._ids)), ws, join["name"], join["color"], join["role"])
        room.peers[peer.id] = peer
        log.info("room %s: %s joined as %s (%d peers)", room_code, peer.name, peer.role, len(room.peers))

        try:
            await ws.send(_dumps({
                "t": "welcome",
                "id": peer.id,
                "room": room_code,
                "peers": [p.info() for p in room.peers.values() if p is not peer],
                "source": room.source,
            }))
            self._send_room(room, {"t": "peer_join", **peer.info()}, exclude=peer)

            async for raw in ws:
                if not peer.take_token():
                    continue
                msg = sanitize(_loads(raw))
                if msg is None:
                    continue
                if msg["t"] == "source":
                    if peer.role != "host":
                        continue
                    room.source = msg
                elif msg["t"] == "clear" and msg["scope"] == "all" and peer.role != "host":
                    continue
                msg["from"] = peer.id
                self._send_room(room, msg, exclude=peer)
        except ConnectionClosed:
            pass
        finally:
            room.peers.pop(peer.id, None)
            log.info("room %s: %s left (%d peers)", room_code, peer.name, len(room.peers))
            if room.peers:
                self._send_room(room, {"t": "peer_leave", **peer.info()})
            else:
                self.rooms.pop(room_code, None)

    async def serve(self, host: str, port: int, stop: asyncio.Event) -> None:
        async with serve(self.handler, host, port, max_size=MAX_MESSAGE_BYTES):
            log.info("relay listening on ws://%s:%d", host, port)
            await stop.wait()


class RelayThread(threading.Thread):
    """Runs a Relay on its own asyncio loop so the GUI can host a room."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
        super().__init__(name="telecaster-relay", daemon=True)
        self.host = host
        self.port = port
        self.error: Exception | None = None
        self.ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        try:
            async with serve(Relay().handler, self.host, self.port, max_size=MAX_MESSAGE_BYTES):
                self.ready.set()
                await self._stop.wait()
        except OSError as exc:
            self.error = exc
            self.ready.set()

    def stop(self) -> None:
        if self._loop and self._stop:
            self._loop.call_soon_threadsafe(self._stop.set)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Telecaster relay server")
    parser.add_argument("--host", default="0.0.0.0", help="interface to bind (default: all)")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(Relay().serve(args.host, args.port, asyncio.Event()))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
