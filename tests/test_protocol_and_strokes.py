import math

from telecaster.protocol import MAX_POINTS_PER_MESSAGE, sanitize, sanitize_join
from telecaster.strokes import FADE_OUT_SECONDS, StrokeStore


def test_join_validation():
    ok = sanitize_join({"t": "join", "room": "ABC123", "name": "  Sam\x00 ", "color": "#AABBCC",
                        "role": "viewer", "v": 1})
    assert ok == {"t": "join", "room": "ABC123", "name": "Sam", "color": "#aabbcc", "role": "viewer", "v": 1}
    assert sanitize_join({"t": "join", "room": "a b", "role": "viewer"}) is None
    assert sanitize_join({"t": "join", "room": "ABC123", "role": "admin"}) is None


def test_sanitize_drops_unknown_fields_and_clamps():
    msg = sanitize({"t": "begin", "sid": "1", "color": "#ff0000", "width": 5, "ttl": 2,
                    "p": [[1.5, -2]], "evil": "x"})
    assert msg == {"t": "begin", "sid": "1", "color": "#ff0000", "width": 0.2, "ttl": 2.0,
                   "border": "#000000", "border_width": 0.0, "p": [[1.0, 0.0]]}


def test_begin_border_is_validated_and_stored():
    msg = sanitize({"t": "begin", "sid": "1", "color": "#ff0000", "width": 0.01, "ttl": 0,
                    "border": "#FFFFFF", "border_width": 3, "p": [[0.5, 0.5]]})
    assert msg["border"] == "#ffffff" and msg["border_width"] == 0.1
    assert sanitize({"t": "begin", "sid": "1", "color": "#ff0000", "width": 0.01,
                     "border": "white", "p": []}) is None
    assert sanitize({"t": "begin", "sid": "1", "color": "#ff0000", "width": 0.01,
                     "border_width": math.inf, "p": []}) is None

    store = StrokeStore()
    store.apply("a", msg, now=0)
    stroke = store.strokes[("a", "1")]
    assert (stroke.color, stroke.border, stroke.border_width) == ("#ff0000", "#ffffff", 0.1)


def test_sanitize_rejects_bad_input():
    assert sanitize({"t": "pts", "sid": "1", "p": [[math.nan, 0]]}) is None
    assert sanitize({"t": "pts", "sid": "1", "p": [[0, 0]] * (MAX_POINTS_PER_MESSAGE + 1)}) is None
    assert sanitize({"t": "pts", "sid": "../../", "p": [[0, 0]]}) is None
    assert sanitize({"t": "source", "aspect": 1.7, "w": True, "h": 1080}) is None
    assert sanitize({"t": "clear", "scope": "everything"}) is None
    assert sanitize({"t": "nope"}) is None
    assert sanitize("not a dict") is None


def test_stroke_lifecycle_and_fade():
    store = StrokeStore()
    store.apply("a", {"t": "begin", "sid": "1", "color": "#ff0000", "width": 0.01, "ttl": 2.0,
                      "p": [[0.1, 0.1]]}, now=0)
    store.apply("a", {"t": "pts", "sid": "1", "p": [[0.2, 0.2], [0.3, 0.3]]}, now=0.1)
    stroke = store.strokes[("a", "1")]
    assert len(stroke.points) == 3
    assert stroke.alpha(100) == 1.0  # still being drawn

    store.apply("a", {"t": "end", "sid": "1"}, now=1.0)
    assert stroke.alpha(2.9) == 1.0
    assert 0 < stroke.alpha(3.0 + FADE_OUT_SECONDS / 2) < 1
    store.prune(3.0 + FADE_OUT_SECONDS)
    assert not store.strokes


def test_permanent_strokes_survive_prune():
    store = StrokeStore()
    store.begin("a", "1", "#ffffff", 0.01, 0, [(0, 0)], now=0)
    store.end("a", "1", now=0)
    store.prune(10_000)
    assert ("a", "1") in store.strokes


def test_clear_scopes():
    store = StrokeStore()
    store.begin("a", "1", "#ffffff", 0.01, 0, [(0, 0)], now=0)
    store.begin("b", "1", "#ffffff", 0.01, 0, [(0, 0)], now=0)
    store.apply("a", {"t": "clear", "scope": "mine"}, now=0)
    assert list(store.strokes) == [("b", "1")]
    store.apply("host", {"t": "clear", "scope": "all"}, now=0)
    assert not store.strokes


def test_points_after_end_are_ignored():
    store = StrokeStore()
    store.begin("a", "1", "#ffffff", 0.01, 0, [(0, 0)], now=0)
    store.end_all("a", now=1)
    store.extend("a", "1", [(0.5, 0.5)])
    assert store.strokes[("a", "1")].points == [(0, 0)]
