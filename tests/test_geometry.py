from telecaster.geometry import (
    Insets,
    Rect,
    apply_insets,
    denormalize,
    fit_aspect,
    insets_from_selection,
    normalize,
)


def test_fit_aspect_pillarbox():
    # 16:9 video in an ultrawide space gets bars left and right.
    rect = fit_aspect(Rect(0, 0, 2560, 1080), 16 / 9)
    assert rect == Rect(320, 0, 1920, 1080)


def test_fit_aspect_letterbox():
    # 16:9 video in a 4:3 space gets bars top and bottom.
    rect = fit_aspect(Rect(100, 50, 1024, 768), 16 / 9)
    assert rect == Rect(100, 50 + 96, 1024, 576)


def test_fit_aspect_exact_match_is_identity():
    rect = Rect(10, 20, 1280, 720)
    assert fit_aspect(rect, 16 / 9) == rect


def test_insets_round_trip():
    full = Rect(500, 300, 1600, 900)
    sel = Rect(72, 48, 1280, 720)
    insets = insets_from_selection(full, sel)
    assert insets == Insets(72, 48, 248, 132)
    assert apply_insets(full, insets) == Rect(572, 348, 1280, 720)


def test_insets_that_no_longer_fit_fall_back_to_full_rect():
    rect = Rect(0, 0, 200, 100)
    assert apply_insets(rect, Insets(150, 0, 150, 0)) == rect


def test_insets_from_list_rejects_garbage():
    assert Insets.from_list(["a", 1]) == Insets()
    assert Insets.from_list(["5", -3, 2, 1]) == Insets(5, 0, 2, 1)


def test_resolution_independence():
    # A point drawn on a 1280x720 stream lands on the same spot of a 2560x1440 game.
    nx, ny = normalize(320, 180, 1280, 720)
    assert denormalize(nx, ny, 2560, 1440) == (640, 360)


def test_normalize_clamps():
    assert normalize(-5, 900, 100, 100) == (0.0, 1.0)
