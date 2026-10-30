"""Wire protocol shared by the relay server and the desktop client.

Every message is a JSON object with a ``t`` (type) field. All coordinates are
normalized to the range [0, 1] relative to the shared area, so a line drawn
on a 1280x720 view of a shared screen lands in the same spot on the 2560x1440
window being shared. Pen widths are normalized to the height of that area.

Client -> relay
    join    {room, name, color, role: "host"|"viewer", v}
    source  {aspect, w, h}                       (host only; cached for late joiners)
    begin   {sid, color, width, ttl, border, border_width, p: [[x, y], ...]}
                                                 (color/width are the fill; border/border_width
                                                 outline it on each side and are optional)
    pts     {sid, p: [[x, y], ...]}
    end     {sid}
    ping    {p: [x, y], color}                   (a "look here" ripple)
    clear   {scope: "mine"|"all"}                ("all" is host only)

Relay -> client
    welcome {id, room, peers: [{id, name, color, role}], source}
    peer_join / peer_leave {id, name, color, role}
    error   {msg}
    ...and every client message above, with a ``from`` peer id added.
"""

from __future__ import annotations

import math
import re
from typing import Any

PROTOCOL_VERSION = 1

MAX_POINTS_PER_MESSAGE = 256
MAX_MESSAGE_BYTES = 64 * 1024
MAX_PEERS_PER_ROOM = 64

ROLES = ("host", "viewer")
_ROOM_RE = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_SID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Message types a client may send after joining, which the relay forwards.
RELAYED_TYPES = ("source", "begin", "pts", "end", "ping", "clear")


def _finite(value: Any, lo: float, hi: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return min(max(value, lo), hi)


def _color(value: Any) -> str | None:
    if isinstance(value, str) and _COLOR_RE.match(value):
        return value.lower()
    return None


def _sid(value: Any) -> str | None:
    if isinstance(value, str) and _SID_RE.match(value):
        return value
    return None


def _point(value: Any) -> list[float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    x = _finite(value[0], 0.0, 1.0)
    y = _finite(value[1], 0.0, 1.0)
    if x is None or y is None:
        return None
    return [round(x, 5), round(y, 5)]


def _points(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list) or len(value) > MAX_POINTS_PER_MESSAGE:
        return None
    out = []
    for raw in value:
        pt = _point(raw)
        if pt is None:
            return None
        out.append(pt)
    return out


def _name(value: Any) -> str:
    if not isinstance(value, str):
        return "anon"
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    return cleaned[:32] or "anon"


def valid_room(room: Any) -> bool:
    return isinstance(room, str) and bool(_ROOM_RE.match(room))


def sanitize_join(msg: Any) -> dict | None:
    """Validate a join message. Returns a cleaned copy or None."""
    if not isinstance(msg, dict) or msg.get("t") != "join":
        return None
    if not valid_room(msg.get("room")):
        return None
    role = msg.get("role")
    if role not in ROLES:
        return None
    return {
        "t": "join",
        "room": msg["room"],
        "name": _name(msg.get("name")),
        "color": _color(msg.get("color")) or "#ff3b30",
        "role": role,
        "v": msg.get("v") if isinstance(msg.get("v"), int) else 0,
    }


def sanitize(msg: Any) -> dict | None:
    """Validate a drawing/state message, rebuilding it from known fields only.

    Used by the relay on input (so arbitrary payloads are never forwarded) and
    by the client on receipt. Unknown or malformed messages return None.
    """
    if not isinstance(msg, dict):
        return None
    t = msg.get("t")

    if t == "source":
        aspect = _finite(msg.get("aspect"), 0.1, 10.0)
        w = msg.get("w")
        h = msg.get("h")
        if aspect is None or type(w) is not int or type(h) is not int:
            return None
        if not (0 < w <= 16384 and 0 < h <= 16384):
            return None
        return {"t": t, "aspect": aspect, "w": w, "h": h}

    if t == "begin":
        sid = _sid(msg.get("sid"))
        pts = _points(msg.get("p", []))
        width = _finite(msg.get("width"), 0.0005, 0.2)
        ttl = _finite(msg.get("ttl", 0), 0.0, 600.0)
        color = _color(msg.get("color"))
        border = _color(msg.get("border", "#000000"))
        border_width = _finite(msg.get("border_width", 0), 0.0, 0.1)
        if None in (sid, pts, width, ttl, color, border, border_width):
            return None
        return {"t": t, "sid": sid, "color": color, "width": width, "ttl": ttl,
                "border": border, "border_width": border_width, "p": pts}

    if t == "pts":
        sid = _sid(msg.get("sid"))
        pts = _points(msg.get("p"))
        if sid is None or not pts:
            return None
        return {"t": t, "sid": sid, "p": pts}

    if t == "end":
        sid = _sid(msg.get("sid"))
        if sid is None:
            return None
        return {"t": t, "sid": sid}

    if t == "ping":
        pt = _point(msg.get("p"))
        color = _color(msg.get("color"))
        if pt is None or color is None:
            return None
        return {"t": t, "p": pt, "color": color}

    if t == "clear":
        scope = msg.get("scope")
        if scope not in ("mine", "all"):
            return None
        return {"t": t, "scope": scope}

    return None
