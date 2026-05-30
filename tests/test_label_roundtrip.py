"""V0.3 + V0.4 — label-write → render-back lands on the feature, and
region↔source coordinate consistency.

A label written at source pixel (x,y) must render its marker at the
output pixel the grid's coordinate frame predicts — both for a full-image
render and for a region crop. This proves the loop the agent actually
uses: read a coord off the grid → upsert_label at that coord → verify
view shows the marker on the feature.
(labeling-correctness-verification-tracker V0.3, V0.4)
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.grid_render import compute_output_size  # noqa: E402
from api.label_render import render_grid_with_labels  # noqa: E402

# The blue dot a height_mark draws: fill _HEIGHT_MARK_COLOR = (30, 30, 200).
_BLUE = np.array([30, 30, 200])


def _solid_blue_mask(out: Image.Image) -> np.ndarray:
    """Boolean mask of SOLID blue dot pixels (fill (30,30,200,255)).

    Excludes: the faint full-width horizontal line a height_mark also
    draws (alpha ~60 → composites to a light blue with r>150), and
    LANCZOS colour-fringe pixels near downscaled grid intersections."""
    o = np.asarray(out.convert("RGB")).astype(int)
    r, g, b = o[..., 0], o[..., 1], o[..., 2]
    return (b > 150) & (r < 110) & (g < 110)


def _blue_dot_centroid_near(
    out: Image.Image, exp_x: float, exp_y: float, win: int = 40
) -> tuple[float, float]:
    """(x, y) centroid of the solid blue dot WITHIN ±win of the expected
    output pixel. A placement check: confirms the marker appears where the
    coordinate frame predicts. (V0.4's out-of-region test independently
    confirms it does NOT appear where it shouldn't, so this windowing is
    not circular — the two together pin the marker.)"""
    mask = _solid_blue_mask(out)
    h, w = mask.shape
    x0, x1 = max(0, int(exp_x - win)), min(w, int(exp_x + win) + 1)
    y0, y1 = max(0, int(exp_y - win)), min(h, int(exp_y + win) + 1)
    sub = mask[y0:y1, x0:x1]
    ys, xs = np.where(sub)
    assert len(xs) > 0, f"no solid blue dot within {win}px of ({exp_x},{exp_y})"
    return x0 + xs.mean(), y0 + ys.mean()


def _height_mark(anchor):
    return {
        "type": "height_mark",
        "geometry": {"anchor": list(anchor)},
        "attributes": {"value_mm": 0, "datum": "ok_ffb"},
    }


def test_v0_3_label_renders_at_source_pixel_full():
    """Full-image: a height_mark at source (cx,cy) renders its marker at
    (cx,cy) (1:1) within a few px."""
    w, h = 1200, 900
    cx, cy = 640, 470
    src = Image.new("RGB", (w, h), (255, 255, 255))
    out = render_grid_with_labels(src, [_height_mark((cx, cy))], max_dim=2000)
    assert out.size == (w, h)  # 1:1
    bx, by = _blue_dot_centroid_near(out, cx, cy)
    assert abs(bx - cx) <= 3, f"x off: {bx} vs {cx}"
    assert abs(by - cy) <= 3, f"y off: {by} vs {cy}"


def test_v0_3_label_renders_at_source_pixel_downscaled():
    """Downscaled: marker lands at the scaled output pixel."""
    w, h = 2400, 1600
    cx, cy = 1500, 900
    src = Image.new("RGB", (w, h), (255, 255, 255))
    out = render_grid_with_labels(src, [_height_mark((cx, cy))], max_dim=900)
    ow, oh = out.size
    assert (ow, oh) == compute_output_size(w, h, 900)
    exp_x, exp_y = cx * ow / w, cy * oh / h
    bx, by = _blue_dot_centroid_near(out, exp_x, exp_y)
    assert abs(bx - exp_x) <= 3
    assert abs(by - exp_y) <= 3


def test_v0_4_region_source_consistency():
    """A label at source (cx,cy) rendered through a REGION crop appears at
    the region-local output pixel — i.e. the (x,y) the agent reads on a
    region zoom is the SAME source (x,y) the un-cropped upsert expects."""
    w, h = 2000, 1500
    cx, cy = 1400, 900
    src = Image.new("RGB", (w, h), (255, 255, 255))
    region = (1000, 600, 1800, 1200)  # 800x600, contains the anchor
    out = render_grid_with_labels(
        src, [_height_mark((cx, cy))], region=region, max_dim=2000
    )
    rw, rh = region[2] - region[0], region[3] - region[1]
    assert out.size == (rw, rh)  # region size, 1:1, no margin
    exp_x, exp_y = cx - region[0], cy - region[1]
    bx, by = _blue_dot_centroid_near(out, exp_x, exp_y)
    assert abs(bx - exp_x) <= 3
    assert abs(by - exp_y) <= 3


def test_v0_4_label_outside_region_not_drawn():
    """A label whose anchor is OUTSIDE the region must not bleed into the
    crop (no false blue marker)."""
    w, h = 2000, 1500
    src = Image.new("RGB", (w, h), (255, 255, 255))
    region = (0, 0, 400, 400)
    out = render_grid_with_labels(
        src, [_height_mark((1500, 1200))], region=region, max_dim=2000
    )
    o = np.asarray(out.convert("RGB")).astype(int)
    dist = np.abs(o - _BLUE).sum(axis=2)
    # The triangle is drawn at the mapped (negative/out-of-range) anchor;
    # with a 7px marker far outside, no blue should appear inside the crop.
    assert (dist < 60).sum() < 20, "marker bled into a region it's outside of"
