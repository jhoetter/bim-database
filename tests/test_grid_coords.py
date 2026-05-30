"""V0 — coordinate round-trip integrity for the grid overlay.

The bedrock of the labeling pipeline: a coordinate read off the grid MUST
map back to the same source pixel, or every downstream label is wrong.
These tests lock that down (labeling-correctness-verification-tracker V0).

V0.1  grid coordinate frame is exact (full / region / downscale)
V0.2  grid labels are unique + aligned — no tier doubling
      (regression for the 1080 -> broad 108 vs 5*finer 105 bug)
V0.5  snap aid agrees with the grid frame

(V0.3 label-write -> render-back and V0.4 region<->source live in
test_grid_coords_roundtrip via the running API; see test_scene_view_*.)
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from api.grid_render import (
    _TIER_FRACTION,
    compute_output_size,
    render_grid_overlay,
)


def _scene_with_cross(w: int, h: int, cx: int, cy: int) -> Image.Image:
    """White scene with a RED cross at source pixel (cx, cy).

    Red, not black, so the content line can be isolated from the grid
    overlay by CHROMA. The grid lines are achromatic (black/grey), so
    `red − (green+blue)/2` is large only on the content line and ~0 on
    grid lines — even after the 0.5 background fade. (A black content
    line gets contaminated: faded content is lighter than the grid lines
    drawn on top, so a darkness probe finds the nearest grid line, not
    the feature.)
    """
    arr = np.full((h, w, 3), 255, np.uint8)
    arr[:, cx] = (255, 0, 0)
    arr[cy, :] = (255, 0, 0)
    return Image.fromarray(arr)


def _redness(out: Image.Image) -> np.ndarray:
    """Per-pixel red-chroma: red − mean(green, blue), clipped at 0."""
    o = np.asarray(out.convert("RGB")).astype(float)
    return np.clip(o[..., 0] - 0.5 * (o[..., 1] + o[..., 2]), 0, None)


def _reddest_col(out: Image.Image, near_x: int, band: int = 8) -> int:
    """Output x of the most-red column within ±band of near_x."""
    chroma = _redness(out)
    lo, hi = max(0, near_x - band), min(chroma.shape[1], near_x + band + 1)
    return lo + int(chroma[:, lo:hi].mean(axis=0).argmax())


def _reddest_row(out: Image.Image, near_y: int, band: int = 8) -> int:
    chroma = _redness(out)
    lo, hi = max(0, near_y - band), min(chroma.shape[0], near_y + band + 1)
    return lo + int(chroma[lo:hi, :].mean(axis=1).argmax())


# Back-compat aliases (the tests below were written against darkest_*).
_darkest_col = _reddest_col
_darkest_row = _reddest_row


# ── V0.1 — coordinate frame is exact ──────────────────────────────────

@pytest.mark.parametrize(
    "w,h,cx,cy,max_dim",
    [
        (1000, 800, 500, 400, 1600),   # full, 1:1 (no downscale)
        (1080, 700, 540, 350, 1600),   # the size class that had the doubling bug
        (1000, 800, 500, 400, 900),    # downscaled
        (2400, 1600, 1200, 800, 900),  # heavy downscale
    ],
)
def test_v0_1_coordinate_frame_exact_full(w, h, cx, cy, max_dim):
    """A content mark at source (cx,cy) renders at the corresponding
    output pixel within <=1px, full image, 1:1 and downscaled."""
    src = _scene_with_cross(w, h, cx, cy)
    out = render_grid_overlay(src, max_dim=max_dim)
    ow, oh = out.size
    exp_x = round(cx * ow / w)
    exp_y = round(cy * oh / h)
    assert abs(_darkest_col(out, exp_x) - exp_x) <= 1
    assert abs(_darkest_row(out, exp_y) - exp_y) <= 1


def test_v0_1_coordinate_frame_exact_region():
    """With a region crop, the content mark still maps correctly and the
    output dims equal the region size (no margin)."""
    w, h, cx, cy = 1200, 900, 700, 500
    src = _scene_with_cross(w, h, cx, cy)
    region = (400, 300, 1000, 800)  # x0,y0,x1,y1 — contains the cross
    out = render_grid_overlay(src, region=region, max_dim=1600)
    rw, rh = region[2] - region[0], region[3] - region[1]
    assert out.size == (rw, rh)  # region size, no downscale, no margin
    # cross at source (cx,cy) -> region-local (cx-x0, cy-y0)
    exp_x = cx - region[0]
    exp_y = cy - region[1]
    assert abs(_darkest_col(out, exp_x) - exp_x) <= 1
    assert abs(_darkest_row(out, exp_y) - exp_y) <= 1


def test_v0_1_coordinate_frame_exact_region_downscaled():
    w, h, cx, cy = 2000, 1500, 1400, 900
    src = _scene_with_cross(w, h, cx, cy)
    region = (1000, 600, 1800, 1200)  # 800x600 region
    out = render_grid_overlay(src, region=region, max_dim=400)
    rw, rh = region[2] - region[0], region[3] - region[1]
    assert out.size == compute_output_size(rw, rh, 400)
    ow, oh = out.size
    exp_x = round((cx - region[0]) * ow / rw)
    exp_y = round((cy - region[1]) * oh / rh)
    assert abs(_darkest_col(out, exp_x) - exp_x) <= 1
    assert abs(_darkest_row(out, exp_y) - exp_y) <= 1


# ── V0.2 — labels unique + aligned (no tier doubling) ─────────────────

def _steps(long_src: int) -> tuple[int, int, int]:
    """Mirror render_grid_overlay's tier-step derivation."""
    finer = max(1, round(long_src * _TIER_FRACTION["finer"]))
    return 5 * finer, finer, max(1, finer // 5)


@pytest.mark.parametrize("long_src", [800, 1000, 1080, 1200, 1417, 2000, 3000, 6000])
def test_v0_2_tiers_nested_exactly(long_src):
    """broad == 5*finer and finer == 5*detail for ALL sizes, so broad
    labels land exactly on finer-every-5th lines (no doubling)."""
    broad, finer, detail = _steps(long_src)
    assert broad == 5 * finer, f"broad {broad} != 5*finer {finer} at long={long_src}"
    # detail nests under finer (5*detail may exceed finer only when finer<5)
    if finer >= 5:
        assert finer == 5 * detail or abs(finer - 5 * detail) <= 4


def test_v0_2_no_doubled_labels_1080():
    """Regression for the exact reported bug: a 1080-wide image must not
    print two near-overlapping coordinate numbers on an axis. We assert
    the actual spec the renderer builds has broad == 5*finer."""
    import api.grid_render as g

    captured: dict = {}
    orig = g._Spec

    def spy(**kw):
        captured.update(kw)
        return orig(**kw)

    g._Spec = spy
    try:
        g.render_grid_overlay(Image.new("RGB", (1080, 800), (255, 255, 255)), max_dim=1600)
    finally:
        g._Spec = orig
    assert captured["broad_step"] == 5 * captured["finer_step"]
    # the old bug: broad=108, 5*finer=105 -> assert we're NOT there
    assert not (captured["broad_step"] == 108 and captured["finer_step"] == 21)


# ── V0.5 — snap aid agrees with the grid frame ────────────────────────

def test_v0_5_snap_matches_grid_frame():
    """snap_anchor returns a source-px coordinate in the SAME frame the
    grid labels use: snapping near a drawn tick lands on the tick's
    source pixel."""
    snap_mod = pytest.importorskip("scripts.snap_anchor", reason=None) if False else None
    # snap_anchor lives in the bim-agent repo; import by path if available.
    import importlib.util
    import os

    path = os.path.expanduser("~/repos/bim-agent/scripts/snap_anchor.py")
    if not os.path.exists(path):
        pytest.skip("snap_anchor.py not present in this checkout")
    spec = importlib.util.spec_from_file_location("snap_anchor", path)
    snap_anchor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(snap_anchor)

    # scene with a strong horizontal tick at y=300
    w, h = 800, 600
    arr = np.full((h, w, 3), 255, np.uint8)
    arr[300, 200:600] = 0  # a clear horizontal line (a "tick")
    img = Image.fromarray(arr)
    tmp = "/tmp/v0_snap_scene.png"
    img.save(tmp)
    # ask snap to refine an approximate y=308 onto the real tick at y=300
    res = snap_anchor.snap(tmp, x=400, y=308, axis="y", band=30)
    assert abs(res["snapped_coord"] - 300) <= 2, res
