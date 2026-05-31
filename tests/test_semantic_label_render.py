"""Semantic audit renderer parity checks.

These tests pin the render contract that MCP/agent QA must not regress to
minimal centerlines/outlines. They use synthetic labels and broad color-family
checks rather than brittle pixel-perfect screenshots.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.label_render import render_grid_with_labels  # noqa: E402


def _blank() -> Image.Image:
    return Image.new("RGB", (520, 360), (255, 255, 255))


def _rgb(img: Image.Image) -> np.ndarray:
    return np.asarray(img.convert("RGB")).astype(int)


def test_floorplan_door_renders_swing_arc_and_missing_parent_warning():
    label = {
        "id": "door-1",
        "type": "floorplan_opening",
        "geometry": {"quad": [[100, 100], [190, 100], [190, 130], [100, 130]]},
        "attributes": {"opening_kind": "door", "swing": "in", "swing_side": "left"},
        "status": "readable",
    }
    out = render_grid_with_labels(_blank(), [label], clean=True, max_dim=1000)
    arr = _rgb(out)
    teal = (arr[..., 1] > 110) & (arr[..., 2] > 90) & (arr[..., 0] < 90)
    red_warning = (arr[..., 0] > 160) & (arr[..., 1] < 100) & (arr[..., 2] < 120)
    assert int(teal.sum()) > 120
    assert int(red_warning.sum()) > 20


def test_floorplan_window_renders_internal_sash_lines():
    label = {
        "id": "window-1",
        "type": "floorplan_opening",
        "geometry": {"quad": [[80, 140], [240, 140], [240, 180], [80, 180]]},
        "attributes": {"opening_kind": "window"},
        "relations": [{"kind": "belongs_to", "other_id": "wall-1"}],
        "status": "readable",
    }
    out = render_grid_with_labels(_blank(), [label], clean=True, max_dim=1000)
    arr = _rgb(out)
    blue = (arr[..., 2] > 130) & (arr[..., 1] > 80) & (arr[..., 0] < 80)
    # The sash lines add substantial blue signal inside the opening body.
    assert int(blue[148:174, 90:230].sum()) > 250


def test_component_line_polyline_variant_renders_closed_region():
    label = {
        "id": "comp-1",
        "type": "component_line",
        "geometry": {"polyline": [[300, 80], [440, 100], [420, 220], [280, 200]]},
        "attributes": {"line_kind": "dachschraege"},
        "status": "readable",
    }
    out = render_grid_with_labels(_blank(), [label], clean=True, max_dim=1000)
    arr = _rgb(out)
    orange = (arr[..., 0] > 160) & (arr[..., 1] > 60) & (arr[..., 1] < 140) & (arr[..., 2] < 90)
    assert int(orange.sum()) > 300


def test_reference_dimension_uses_amber_marker_not_plain_line_only():
    label = {
        "id": "dim-1",
        "type": "dimensioned_distance",
        "geometry": {"start": [80, 300], "end": [420, 300]},
        "attributes": {"value_mm": 3000, "target_orientation": "horizontal", "is_reference": True},
        "status": "readable",
    }
    out = render_grid_with_labels(_blank(), [label], clean=True, max_dim=1000)
    arr = _rgb(out)
    amber = (arr[..., 0] > 180) & (arr[..., 1] > 90) & (arr[..., 1] < 190) & (arr[..., 2] < 90)
    # Includes dashed line, caps, and star marker.
    assert int(amber.sum()) > 300


def test_missing_and_not_readable_statuses_are_visible():
    labels = [
        {
            "id": "missing-wall",
            "type": "wall",
            "geometry": {"start": [80, 80], "end": [80, 260]},
            "attributes": {"thickness_mm": 240},
            "status": "missing",
        },
        {
            "id": "unreadable-dim",
            "type": "dimension_number",
            "geometry": {"anchor": [300, 220]},
            "attributes": {"text": "?"},
            "status": "not_readable",
        },
    ]
    out = render_grid_with_labels(_blank(), labels, clean=True, max_dim=1000)
    arr = _rgb(out)
    red = (arr[..., 0] > 150) & (arr[..., 1] < 90) & (arr[..., 2] < 120)
    purple = (arr[..., 0] > 90) & (arr[..., 2] > 130) & (arr[..., 1] < 100)
    assert int(red.sum()) > 50
    assert int(purple.sum()) > 50


def test_opening_parent_wall_relation_is_visible_when_attached():
    labels = [
        {
            "id": "wall-1",
            "type": "wall",
            "geometry": {"start": [80, 130], "end": [320, 130]},
            "attributes": {"thickness_mm": 240},
            "status": "readable",
        },
        {
            "id": "door-1",
            "type": "floorplan_opening",
            "geometry": {"quad": [[160, 110], [220, 110], [220, 150], [160, 150]]},
            "attributes": {"opening_kind": "door", "swing": "in", "swing_side": "left"},
            "relations": [{"kind": "belongs_to", "other_id": "wall-1"}],
            "status": "readable",
        },
    ]
    out = render_grid_with_labels(_blank(), labels, clean=True, max_dim=1000)
    arr = _rgb(out)
    cyan_relation = (arr[..., 1] > 80) & (arr[..., 2] > 150) & (arr[..., 0] < 90)
    red_warning = (arr[..., 0] > 160) & (arr[..., 1] < 100) & (arr[..., 2] < 120)
    assert int(cyan_relation.sum()) > 30
    assert int(red_warning.sum()) == 0


def test_view_opening_circle_variants_and_frame_visible_render():
    labels = [
        {
            "id": "nested-circle",
            "type": "view_opening",
            "geometry": {"circle": {"center": [130, 110], "radius_px": 28}},
            "attributes": {"opening_kind": "window", "frame_visible": True},
            "status": "readable",
        },
        {
            "id": "flat-circle",
            "type": "view_opening",
            "geometry": {"shape": "circle", "center": [260, 110], "radius_px": 28},
            "attributes": {"opening_kind": "dormer", "frame_visible": True},
            "status": "readable",
        },
    ]
    out = render_grid_with_labels(_blank(), labels, clean=True, max_dim=1000)
    arr = _rgb(out)
    blue = (arr[..., 2] > 130) & (arr[..., 1] > 80) & (arr[..., 0] < 90)
    orange = (arr[..., 0] > 160) & (arr[..., 1] > 55) & (arr[..., 1] < 140) & (arr[..., 2] < 100)
    assert int(blue.sum()) > 120
    assert int(orange.sum()) > 120


def test_dimension_number_bbox_and_relation_to_distance_render():
    labels = [
        {
            "id": "dim-1",
            "type": "dimensioned_distance",
            "geometry": {"start": [80, 280], "end": [320, 280]},
            "attributes": {"value_mm": 2400, "target_orientation": "horizontal"},
            "status": "readable",
        },
        {
            "id": "num-1",
            "type": "dimension_number",
            "geometry": {"bbox": [[185, 230], [245, 230], [245, 255], [185, 255]]},
            "attributes": {"text": "2.40", "parsed_value_mm": 2400},
            "relations": [{"kind": "labels", "other_id": "dim-1"}],
            "status": "readable",
        },
    ]
    out = render_grid_with_labels(_blank(), labels, clean=True, max_dim=1000)
    arr = _rgb(out)
    purple = (arr[..., 0] > 100) & (arr[..., 2] > 130) & (arr[..., 1] < 110)
    cyan_relation = (arr[..., 1] > 80) & (arr[..., 2] > 150) & (arr[..., 0] < 90)
    assert int(purple.sum()) > 180
    assert int(cyan_relation.sum()) > 30
