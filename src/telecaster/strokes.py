"""In-memory model of everyone's strokes and pings, independent of Qt."""

from __future__ import annotations

from dataclasses import dataclass, field

FADE_OUT_SECONDS = 0.6
PING_SECONDS = 1.2
MAX_POINTS_PER_STROKE = 4000
MAX_STROKES = 600

Point = tuple[float, float]


@dataclass
class Stroke:
    owner: str
    color: str
    width: float  # fraction of the video area's height
    ttl: float  # seconds visible after the pen lifts; 0 = until cleared
    points: list[Point] = field(default_factory=list)
    ended_at: float | None = None

    def alpha(self, now: float) -> float:
        if self.ended_at is None or self.ttl <= 0:
            return 1.0
        remaining = self.ended_at + self.ttl - now
        if remaining >= 0:
            return 1.0
        return max(0.0, 1.0 + remaining / FADE_OUT_SECONDS)

    def expired(self, now: float) -> bool:
        return self.ttl > 0 and self.ended_at is not None and now >= self.ended_at + self.ttl + FADE_OUT_SECONDS

    @property
    def animating(self) -> bool:
        return self.ttl > 0 and self.ended_at is not None


@dataclass
class Ping:
    owner: str
    color: str
    x: float
    y: float
    started: float

    def progress(self, now: float) -> float:
        return (now - self.started) / PING_SECONDS


class StrokeStore:
    def __init__(self) -> None:
        self.strokes: dict[tuple[str, str], Stroke] = {}
        self.pings: list[Ping] = []

    def begin(self, owner: str, sid: str, color: str, width: float, ttl: float,
              points: list[Point], now: float) -> None:
        key = (owner, sid)
        self.strokes.pop(key, None)
        self.strokes[key] = Stroke(owner, color, width, ttl, list(points)[:MAX_POINTS_PER_STROKE])
        while len(self.strokes) > MAX_STROKES:
            del self.strokes[next(iter(self.strokes))]

    def extend(self, owner: str, sid: str, points: list[Point]) -> None:
        stroke = self.strokes.get((owner, sid))
        if stroke is None or stroke.ended_at is not None:
            return
        room = MAX_POINTS_PER_STROKE - len(stroke.points)
        if room > 0:
            stroke.points.extend(points[:room])

    def end(self, owner: str, sid: str, now: float) -> None:
        stroke = self.strokes.get((owner, sid))
        if stroke is not None and stroke.ended_at is None:
            stroke.ended_at = now

    def end_all(self, owner: str, now: float) -> None:
        """Finish any strokes left open, e.g. when a peer disconnects mid-line."""
        for stroke in self.strokes.values():
            if stroke.owner == owner and stroke.ended_at is None:
                stroke.ended_at = now

    def ping(self, owner: str, color: str, x: float, y: float, now: float) -> None:
        self.pings.append(Ping(owner, color, x, y, now))

    def clear(self, owner: str | None = None) -> None:
        if owner is None:
            self.strokes.clear()
            self.pings.clear()
            return
        self.strokes = {k: s for k, s in self.strokes.items() if s.owner != owner}
        self.pings = [p for p in self.pings if p.owner != owner]

    def prune(self, now: float) -> None:
        dead = [k for k, s in self.strokes.items() if s.expired(now)]
        for key in dead:
            del self.strokes[key]
        self.pings = [p for p in self.pings if p.progress(now) < 1.0]

    @property
    def animating(self) -> bool:
        return bool(self.pings) or any(s.animating for s in self.strokes.values())

    def apply(self, owner: str, msg: dict, now: float) -> None:
        """Apply a sanitized protocol message from ``owner``."""
        t = msg["t"]
        if t == "begin":
            self.begin(owner, msg["sid"], msg["color"], msg["width"], msg["ttl"],
                       [tuple(p) for p in msg["p"]], now)
        elif t == "pts":
            self.extend(owner, msg["sid"], [tuple(p) for p in msg["p"]])
        elif t == "end":
            self.end(owner, msg["sid"], now)
        elif t == "ping":
            self.ping(owner, msg["color"], msg["p"][0], msg["p"][1], now)
        elif t == "clear":
            self.clear(None if msg["scope"] == "all" else owner)
