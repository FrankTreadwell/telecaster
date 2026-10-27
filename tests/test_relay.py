import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect

from telecaster.relay import Relay


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def join(port, role, room="ROOM1", name=None):
    ws = await connect(f"ws://127.0.0.1:{port}")
    await ws.send(json.dumps({"t": "join", "room": room, "name": name or role, "color": "#00ff00",
                              "role": role, "v": 1}))
    return ws, json.loads(await ws.recv())


async def recv_type(ws, t, timeout=2):
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg["t"] == t:
            return msg


@pytest.fixture
def relay_port():
    return free_port()


def run(coro_fn, port):
    async def main():
        stop = asyncio.Event()
        server = asyncio.create_task(Relay().serve("127.0.0.1", port, stop))
        await asyncio.sleep(0.1)
        try:
            await coro_fn(port)
        finally:
            stop.set()
            await server

    asyncio.run(main())


def test_strokes_are_relayed_with_sender_id(relay_port):
    async def scenario(port):
        host, host_welcome = await join(port, "host")
        viewer, welcome = await join(port, "viewer")
        assert welcome["t"] == "welcome"
        assert [p["role"] for p in welcome["peers"]] == ["host"]
        await recv_type(host, "peer_join")

        await viewer.send(json.dumps({"t": "begin", "sid": "1", "color": "#ff0000", "width": 0.01,
                                      "ttl": 3, "p": [[0.5, 0.5]], "junk": 1}))
        msg = await recv_type(host, "begin")
        assert msg["from"] == welcome["id"]
        assert "junk" not in msg
        await host.close()
        await viewer.close()

    run(scenario, relay_port)


def test_source_is_host_only_and_cached_for_late_joiners(relay_port):
    async def scenario(port):
        host, _ = await join(port, "host")
        early, _ = await join(port, "viewer")
        await early.send(json.dumps({"t": "source", "aspect": 1.0, "w": 100, "h": 100}))  # ignored
        await host.send(json.dumps({"t": "source", "aspect": 16 / 9, "w": 2560, "h": 1440}))
        msg = await recv_type(early, "source")
        assert (msg["w"], msg["h"]) == (2560, 1440)

        late, welcome = await join(port, "viewer")
        assert welcome["source"]["w"] == 2560
        for ws in (host, early, late):
            await ws.close()

    run(scenario, relay_port)


def test_second_host_and_viewer_clear_all_rejected(relay_port):
    async def scenario(port):
        host, _ = await join(port, "host")
        viewer, _ = await join(port, "viewer")
        _, refused = await join(port, "host")
        assert refused["t"] == "error"

        await viewer.send(json.dumps({"t": "clear", "scope": "all"}))
        await viewer.send(json.dumps({"t": "clear", "scope": "mine"}))
        msg = await recv_type(host, "clear")
        assert msg["scope"] == "mine"
        await host.close()
        await viewer.close()

    run(scenario, relay_port)


def test_rooms_are_isolated(relay_port):
    async def scenario(port):
        a, _ = await join(port, "host", room="AAAA")
        b, _ = await join(port, "viewer", room="BBBB")
        await b.send(json.dumps({"t": "ping", "p": [0.5, 0.5], "color": "#ffffff"}))
        with pytest.raises(asyncio.TimeoutError):
            await recv_type(a, "ping", timeout=0.3)
        await a.close()
        await b.close()

    run(scenario, relay_port)
