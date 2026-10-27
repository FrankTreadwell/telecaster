"""Invite links: one string the host shares that holds the relay address and room.

    telecaster://join?relay=ws%3A%2F%2F203.0.113.7%3A8765&room=K7QX2M

Viewers can paste the link on its own or the whole chat message containing it.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit

from .protocol import valid_room

SCHEME = "telecaster"
_LINK_RE = re.compile(rf"{SCHEME}://\S+", re.IGNORECASE)


def make_invite(relay_url: str, room: str) -> str:
    return f"{SCHEME}://join?" + urlencode({"relay": relay_url, "room": room})


def parse_invite(text: str) -> tuple[str, str] | None:
    """Find an invite link in ``text``. Returns (relay_url, room) or None."""
    match = _LINK_RE.search(text or "")
    if not match:
        return None
    parts = urlsplit(match.group(0).rstrip(".,;)>'\""))
    if parts.netloc.lower() != "join":
        return None
    query = parse_qs(parts.query)
    relay = query.get("relay", [""])[0].strip()
    room = query.get("room", [""])[0].strip()
    relay_parts = urlsplit(relay)
    if relay_parts.scheme not in ("ws", "wss") or not relay_parts.hostname:
        return None
    if not valid_room(room):
        return None
    return relay, room


def relay_url_for_host(host: str, port: int) -> str:
    if ":" in host and not host.startswith("["):  # bare IPv6 literal
        host = f"[{host}]"
    return f"ws://{host}:{port}"
