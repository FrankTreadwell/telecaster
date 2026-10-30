from telecaster.autocrop import Gray, find_content
from telecaster.geometry import Rect


def canvas(w, h, fill=0):
    return [bytearray([fill]) * w for _ in range(h)]


def paint(rows, rect: Rect, value):
    for y in range(rect.y, rect.y + rect.h):
        rows[y][rect.x : rect.x + rect.w] = bytes([value]) * rect.w


def to_gray(rows) -> Gray:
    return Gray(len(rows[0]), len(rows), b"".join(bytes(r) for r in rows))


def sampled(img: Gray, factor: int) -> Gray:
    """Nearest-neighbour downscale, like Qt.FastTransformation."""
    w, h = img.w // factor, img.h // factor
    data = bytes(img.data[(y * factor) * img.stride + x * factor] for y in range(h) for x in range(w))
    return Gray(w, h, data)


def detect(rows, factor=4, aspect=None):
    full = to_gray(rows)
    return find_content(sampled(full, factor), full, aspect)


def viewing_app(w=800, h=400):
    """A grey sidebar and top bar around a black stage, like Discord's stream view."""
    rows = canvas(w, h)
    paint(rows, Rect(0, 0, 90, h), 40)    # sidebar
    paint(rows, Rect(0, 0, w, 24), 30)    # top bar, joined to the sidebar
    paint(rows, Rect(700, 360, 60, 30), 120)  # a control button below the stage
    return rows


def test_finds_video_between_bars():
    rows = viewing_app()
    video = Rect(203, 41, 481, 271)
    paint(rows, video, 25)
    paint(rows, Rect(300, 100, 50, 40), 0)  # pure black inside the video doesn't split it
    paint(rows, Rect(390, 318, 90, 40), 100)  # thumbnail just below, 7 px gap
    assert detect(rows) == video


def test_dark_content_edges_are_kept():
    rows = viewing_app()
    video = Rect(150, 50, 480, 270)
    paint(rows, video, 8)  # very dark, but not letterbox black
    assert detect(rows) == video


def test_prefers_the_shared_aspect_ratio():
    rows = viewing_app()
    wide = Rect(120, 40, 400, 225)   # 16:9
    square = Rect(560, 40, 230, 230)  # 1:1, and smaller
    paint(rows, wide, 60)
    paint(rows, square, 60)
    assert detect(rows, aspect=16 / 9) == wide
    assert detect(rows, aspect=1.0) == square


def test_no_bars_means_no_crop():
    rows = canvas(400, 200, 50)
    assert detect(rows) is None


def test_nothing_playing():
    assert detect(viewing_app()) is None
