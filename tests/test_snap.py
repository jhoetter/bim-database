"""Unit tests for api/snap.py (issue #10).

Pure-function tests for:
- local-crop → source coordinate mapping (1:1 and downscaled crops)
- nearest-ink feature detection / snap
- the combined resolve_point flow
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.snap import (  # noqa: E402
    map_crop_to_source,
    nearest_ink_feature,
    resolve_point,
)


# ── local-crop → source mapping ───────────────────────────────────────────


def test_map_no_region_is_identity():
    assert map_crop_to_source([12.0, 34.0], None, 1600) == (12.0, 34.0)


def test_map_native_crop_is_translation():
    # 300x300 crop at origin (200,150), under max_dim -> 1:1.
    region = (200, 150, 500, 450)
    assert map_crop_to_source([10, 20], region, 1600) == (210.0, 170.0)


def test_map_downscaled_crop_scales_back_up():
    # 3200-wide crop downscaled to max_dim=1600 -> scale factor 2.
    region = (0, 0, 3200, 1600)
    # local (800, 400) in the 1600x800 output maps to source (1600, 800).
    sx, sy = map_crop_to_source([800, 400], region, 1600)
    assert round(sx) == 1600 and round(sy) == 800


# ── nearest-ink feature / snap ────────────────────────────────────────────


def _blank(size=(100, 100), shade=255) -> Image.Image:
    return Image.new("L", size, shade).convert("RGB")


def test_nearest_feature_found_returns_offset():
    img = _blank()
    # Single black pixel at (60, 40).
    img.putpixel((60, 40), (0, 0, 0))
    res = nearest_ink_feature(img, [55, 40], radius_px=14)
    assert res["found"]
    assert res["point"] == [60.0, 40.0]
    assert res["offset_px"] == [5.0, 0.0]
    assert res["distance_px"] == pytest.approx(5.0)


def test_nearest_feature_picks_closest_of_many():
    img = _blank()
    for p in [(40, 40), (52, 50), (80, 80)]:
        img.putpixel(p, (0, 0, 0))
    res = nearest_ink_feature(img, [50, 50], radius_px=20)
    assert res["found"]
    assert res["point"] == [52.0, 50.0]  # nearest


def test_nearest_feature_none_on_blank():
    res = nearest_ink_feature(_blank(), [50, 50], radius_px=10)
    assert res["found"] is False
    assert res["offset_px"] == [0.0, 0.0]
    assert res["distance_px"] is None
    assert res["point"] == [50.0, 50.0]


def test_nearest_feature_respects_radius():
    img = _blank()
    img.putpixel((70, 50), (0, 0, 0))  # 20px away
    assert nearest_ink_feature(img, [50, 50], radius_px=10)["found"] is False
    assert nearest_ink_feature(img, [50, 50], radius_px=25)["found"] is True


# ── combined resolve_point ────────────────────────────────────────────────


def test_resolve_source_frame_with_snap():
    img = _blank()
    img.putpixel((62, 50), (0, 0, 0))
    out = resolve_point(img, [58, 50], frame="source", snap=True, snap_radius_px=14)
    assert out["snapped"]
    assert out["source_point"] == [62.0, 50.0]
    assert out["offset_px"] == [4.0, 0.0]
    assert out["mapped_point"] == [58.0, 50.0]


def test_resolve_crop_frame_maps_then_snaps():
    img = _blank((400, 400))
    # ink at source (260, 175)
    img.putpixel((260, 175), (0, 0, 0))
    region = (200, 150, 360, 310)  # 160x160 crop, 1:1
    # local (62, 27) -> source (262, 177); snaps to (260, 175)
    out = resolve_point(
        img, [62, 27], region=region, frame="crop", snap=True, snap_radius_px=10,
    )
    assert out["mapped_point"] == [262.0, 177.0]
    assert out["snapped"]
    assert out["source_point"] == [260.0, 175.0]


def test_resolve_snap_off_returns_mapped():
    img = _blank()
    img.putpixel((62, 50), (0, 0, 0))
    out = resolve_point(img, [58, 50], frame="source", snap=False)
    assert out["snapped"] is False
    assert out["source_point"] == [58.0, 50.0]


def test_resolve_crop_requires_region():
    with pytest.raises(ValueError):
        resolve_point(_blank(), [1, 2], frame="crop", region=None)


def test_resolve_bad_frame_raises():
    with pytest.raises(ValueError):
        resolve_point(_blank(), [1, 2], frame="nonsense")
