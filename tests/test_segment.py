"""Unit tests for api/segment.py (issue #11) — the scene-pixel → PDF-unit
mapping used by split_scene."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.segment import (  # noqa: E402
    scene_px_dims,
    scene_px_to_pdf,
    validate_region_px,
)


def test_scene_px_dims_matches_raster_scale():
    # A4 landscape-ish bbox at 300 dpi: 600pt * 300/72 = 2500 px.
    assert scene_px_dims([0, 0, 600, 300], 300) == (2500, 1250)


def test_px_to_pdf_origin_offset_and_scale():
    # Parent bbox starts at (100,50) pt, rendered at 144 dpi (f = 0.5).
    # Scene pixel (200,100) -> PDF (100 + 100, 50 + 50) = (200,100).
    out = scene_px_to_pdf([200, 100, 400, 300], [100, 50, 700, 500], 144)
    assert out == [200.0, 100.0, 300.0, 200.0]


def test_px_to_pdf_clamps_to_parent_bbox():
    # A region running past the parent's right/bottom edge is clamped.
    out = scene_px_to_pdf([0, 0, 100000, 100000], [0, 0, 600, 400], 72)
    assert out == [0.0, 0.0, 600.0, 400.0]


def test_validate_region_ok():
    assert validate_region_px([10, 10, 100, 100], (200, 200)) is None


def test_validate_region_bad_shape():
    assert validate_region_px([1, 2, 3], (200, 200)) is not None


def test_validate_region_non_positive_area():
    assert "non-positive" in validate_region_px([100, 100, 100, 50], (200, 200))


def test_validate_region_outside_parent():
    assert "outside" in validate_region_px([0, 0, 300, 50], (200, 200))


def test_roundtrip_two_side_by_side_regions():
    # 1000x400 px parent at 100 dpi (f=0.72). Left + right halves map to
    # disjoint PDF sub-boxes that tile the parent width.
    bbox = [0, 0, 720, 288]  # 720pt*100/72=1000px, 288*100/72=400px
    dims = scene_px_dims(bbox, 100)
    assert dims == (1000, 400)
    left = scene_px_to_pdf([0, 0, 500, 400], bbox, 100)
    right = scene_px_to_pdf([500, 0, 1000, 400], bbox, 100)
    assert left[2] == pytest.approx(right[0])  # they meet at the seam
    assert left[0] == 0 and right[2] == pytest.approx(720)
