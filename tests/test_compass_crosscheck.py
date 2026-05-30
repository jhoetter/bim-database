"""V4.2 — elevation-title vs compass cross-check (pure helpers)."""
from api.fact_derivation import (
    normalize_orientation,
    orientation_from_title,
    compass_titles_agree,
)


def test_normalize_orientation_english_german_abbrev():
    assert normalize_orientation("north") == "north"
    assert normalize_orientation("Nord") == "north"
    assert normalize_orientation(" SÜD ") == "south"
    assert normalize_orientation("sued") == "south"
    assert normalize_orientation("O") == "east"
    assert normalize_orientation("w") == "west"
    assert normalize_orientation("") is None
    assert normalize_orientation(None) is None
    assert normalize_orientation("upstairs") is None


def test_orientation_from_title_tokens_and_compounds():
    assert orientation_from_title("Ansicht Nord") == "north"
    assert orientation_from_title("Westansicht") == "west"
    assert orientation_from_title("Süd-Ansicht") == "south"
    assert orientation_from_title("South Elevation") == "south"
    assert orientation_from_title("Ostfassade") == "east"
    assert orientation_from_title(None) is None
    assert orientation_from_title("Grundriss EG") is None  # no direction


def test_orientation_from_title_conflicting_is_none():
    # a section spanning two directions makes no single orientation claim
    assert orientation_from_title("Schnitt Nord-Süd") is None


def test_compass_agree_true():
    res = compass_titles_agree("Ansicht Nord", "north")
    assert res["agree"] is True
    assert res["title_orientation"] == "north"
    assert res["scene_orientation"] == "north"


def test_compass_agree_false_is_flagged():
    res = compass_titles_agree("Westansicht", "east")
    assert res["agree"] is False
    assert res["title_orientation"] == "west"
    assert res["scene_orientation"] == "east"
    assert "west" in res["reason"] and "east" in res["reason"]


def test_compass_uncheckable_returns_none_not_false():
    # missing scene orientation -> can't check, must not read as disagreement
    res = compass_titles_agree("Ansicht Nord", None)
    assert res["agree"] is None
    assert res["title_orientation"] == "north"
    # missing title direction -> also uncheckable
    res2 = compass_titles_agree("Grundriss EG", "north")
    assert res2["agree"] is None
