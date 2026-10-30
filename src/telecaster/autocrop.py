"""Find the shared video inside a screenshot of the viewing app by its black bars.

Viewing apps (Discord, Zoom, a browser player…) letterbox a stream on a pure
black background, while their own UI is drawn in greys. So the shared image is
the big, solid block of non-black pixels that is surrounded by black.

Works on 8-bit greyscale images so it needs nothing beyond the standard
library: a downscaled copy finds the block quickly, then the full-resolution
copy pins each edge down to the exact pixel. The downscaled copy should be
sampled (nearest neighbour), not smoothed, so thin black gaps between the
video and neighbouring UI stay black.
"""

from __future__ import annotations

from dataclasses import dataclass

from .geometry import Rect

BLACK_MAX = 6        # luma at or below this counts as letterbox black
MIN_FILL = 0.5       # share of a candidate's bounding box that must be non-black
SPARSE_FILL = 0.25   # blobs thinner than this (app chrome wrapping the view) never merge
MIN_AREA = 0.02      # candidates smaller than this share of the image are ignored
ASPECT_TOLERANCE = 0.15
SAMPLE_SIZE = 640    # longest side of the downscaled copy (a few px per sample)


@dataclass(frozen=True)
class Gray:
    """An 8-bit greyscale image; rows may be padded (``stride`` >= ``w``)."""

    w: int
    h: int
    data: bytes
    stride: int = 0

    def __post_init__(self) -> None:
        if not self.stride:
            object.__setattr__(self, "stride", self.w)

    def row(self, y: int) -> bytes:
        return self.data[y * self.stride : y * self.stride + self.w]


def _components(img: Gray) -> list[tuple[int, int, int, int, int]]:
    """Bounding boxes (x0, y0, x1, y1, pixel count) of 4-connected non-black blobs."""
    w, h = img.w, img.h
    content = bytearray(w * h)
    for y in range(h):
        row = img.row(y)
        for x in range(w):
            if row[x] > BLACK_MAX:
                content[y * w + x] = 1

    found = []
    for start in range(w * h):
        if not content[start]:
            continue
        content[start] = 0
        stack = [start]
        x0 = x1 = start % w
        y0 = y1 = start // w
        count = 0
        while stack:
            i = stack.pop()
            count += 1
            x, y = i % w, i // w
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for n in (i - 1 if x else -1, i + 1 if x < w - 1 else -1, i - w, i + w):
                if 0 <= n < w * h and content[n]:
                    content[n] = 0
                    stack.append(n)
        found.append((x0, y0, x1 + 1, y1 + 1, count))
    return found


def _merge_overlapping(boxes: list[tuple[int, int, int, int, int]]) -> list[tuple[int, int, int, int, int]]:
    """Join blobs whose boxes overlap: dark content can split one video into pieces.

    Sparse blobs are dropped first: a sidebar joined to a top bar has a box
    covering the whole window, which would swallow everything.
    """
    boxes = [b for b in boxes if b[4] >= SPARSE_FILL * (b[2] - b[0]) * (b[3] - b[1])]
    merged = True
    while merged:
        merged = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                a, b = boxes[i], boxes[j]
                if a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]:
                    boxes[i] = (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]), a[4] + b[4])
                    del boxes[j]
                    merged = True
                    break
            if merged:
                break
    return boxes


def _pick(boxes, img: Gray, aspect: float | None):
    candidates = []
    for x0, y0, x1, y1, count in boxes:
        area = (x1 - x0) * (y1 - y0)
        if area < MIN_AREA * img.w * img.h or count < MIN_FILL * area:
            continue  # specks, or app chrome (a sidebar + top bar spans a big, mostly black box)
        candidates.append((area, (x0, y0, x1, y1)))
    if not candidates:
        return None
    if aspect:
        matching = [c for c in candidates
                    if abs((c[1][2] - c[1][0]) / (c[1][3] - c[1][1]) / aspect - 1) <= ASPECT_TOLERANCE]
        candidates = matching or candidates
    return max(candidates)[1]


def _line_has_content(img: Gray, horizontal: bool, pos: int, lo: int, hi: int) -> bool:
    if horizontal:
        pixels = img.row(pos)[lo:hi]
    else:
        pixels = bytes(img.data[y * img.stride + pos] for y in range(lo, hi))
    if not pixels:
        return False
    bright = sum(1 for p in pixels if p > BLACK_MAX)
    return bright > len(pixels) * 0.01


def _refine_edge(img: Gray, horizontal: bool, coarse: int, step: int, inward: int, lo: int, hi: int) -> int:
    """Walk outward from just inside ``coarse`` until the first all-black line.

    ``step`` is -1 for a left/top edge, +1 for a right/bottom edge (the
    outward direction); returned positions are inclusive pixel lines. The
    walk stays within ``inward`` pixels of ``coarse`` either way.
    """
    limit = img.h if horizontal else img.w
    pos = min(max(coarse - step * inward, 0), limit - 1)
    if not _line_has_content(img, horizontal, pos, lo, hi):
        return coarse
    for _ in range(2 * inward):
        if not (0 <= pos + step < limit and _line_has_content(img, horizontal, pos + step, lo, hi)):
            break
        pos += step
    return pos


def find_content(small: Gray, full: Gray, aspect: float | None = None) -> Rect | None:
    """The shared image's rect in ``full``'s pixels, or None if no black bars frame it.

    ``small`` is a downscaled copy of ``full`` (the same picture); ``aspect``
    is the shared window's width / height when known, to prefer a match.
    """
    if small.w <= 0 or small.h <= 0 or full.w <= 0 or full.h <= 0:
        return None
    box = _pick(_merge_overlapping(_components(small)), small, aspect)
    if box is None:
        return None
    sx, sy = full.w / small.w, full.h / small.h
    left, top = round(box[0] * sx), round(box[1] * sy)
    right, bottom = min(round(box[2] * sx), full.w) - 1, min(round(box[3] * sy), full.h) - 1
    if (left, top, right + 1, bottom + 1) == (0, 0, full.w, full.h):
        return None  # nothing but content: no bars to crop

    # Sample the middle of each edge so neighbouring UI (thumbnails, buttons)
    # next to a corner doesn't extend it.
    mx, my = (right - left) // 10, (bottom - top) // 10
    inx, iny = max(2, round(2 * sx)), max(2, round(2 * sy))
    left = _refine_edge(full, False, left, -1, inx, top + my, bottom - my)
    right = _refine_edge(full, False, right, 1, inx, top + my, bottom - my)
    top = _refine_edge(full, True, top, -1, iny, left + mx, right - mx)
    bottom = _refine_edge(full, True, bottom, 1, iny, left + mx, right - mx)
    rect = Rect(left, top, right - left + 1, bottom - top + 1)
    return None if rect.empty else rect
