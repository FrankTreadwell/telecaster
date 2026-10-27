"""Pure geometry helpers for mapping between windows and the shared video area.

Rects here are in physical screen pixels (what Win32 reports). Strokes never
use pixels on the wire: they are normalized against whichever rect the
overlay covers, which is what makes differing resolutions line up.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def aspect(self) -> float:
        return self.w / self.h if self.h else 0.0

    @property
    def empty(self) -> bool:
        return self.w <= 0 or self.h <= 0


@dataclass(frozen=True)
class Insets:
    """Pixels trimmed from each edge of a window's client area.

    Used on the viewer side to cut away the viewing app's UI around the shared screen.
    Stored in pixels (not fractions) because app chrome stays a fixed size
    when the window is resized.
    """

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    def as_list(self) -> list[int]:
        return [self.left, self.top, self.right, self.bottom]

    @classmethod
    def from_list(cls, values) -> "Insets":
        try:
            left, top, right, bottom = (max(0, int(v)) for v in values)
        except (TypeError, ValueError):
            return cls()
        return cls(left, top, right, bottom)


def apply_insets(rect: Rect, insets: Insets) -> Rect:
    w = rect.w - insets.left - insets.right
    h = rect.h - insets.top - insets.bottom
    if w <= 0 or h <= 0:
        return rect  # insets no longer fit (window shrank); fall back to the full area
    return Rect(rect.x + insets.left, rect.y + insets.top, w, h)


def fit_aspect(rect: Rect, aspect: float) -> Rect:
    """Largest rect of the given aspect ratio centered inside ``rect``.

    Mirrors how video and screen-sharing apps letterbox or pillarbox a
    stream whose aspect ratio differs from the space it is shown in.
    """
    if rect.empty or aspect <= 0:
        return rect
    if rect.aspect > aspect:
        # Too wide: pillarbox (bars left/right).
        w = round(rect.h * aspect)
        return Rect(rect.x + (rect.w - w) // 2, rect.y, w, rect.h)
    h = round(rect.w / aspect)
    return Rect(rect.x, rect.y + (rect.h - h) // 2, rect.w, h)


def insets_from_selection(full: Rect, sel: Rect) -> Insets:
    """Convert a rectangle selected inside ``full`` into edge insets.

    ``sel`` is relative to ``full``'s top-left corner.
    """
    left = max(0, min(sel.x, full.w))
    top = max(0, min(sel.y, full.h))
    right = max(0, full.w - (sel.x + sel.w))
    bottom = max(0, full.h - (sel.y + sel.h))
    return Insets(left, top, right, bottom)


def normalize(x: float, y: float, w: float, h: float) -> tuple[float, float]:
    if w <= 0 or h <= 0:
        return 0.0, 0.0
    return min(max(x / w, 0.0), 1.0), min(max(y / h, 0.0), 1.0)


def denormalize(nx: float, ny: float, w: float, h: float) -> tuple[float, float]:
    return nx * w, ny * h
