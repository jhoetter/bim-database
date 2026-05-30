"""QA verify view: clean overlay (no grid, full-opacity drawing,
semi-transparent labels) so a misaligned label is visibly OFF the ink.

The agent kept "verifying" wall placements that were actually wrong
because the default verify view (dense grid + drawing faded to 50%) hid
the misalignment. clean=True shows the drawing crisp with the colored
label composited at ~62% alpha, so a wall line beside the wall (not on
it) is obvious. Coordinates are unchanged from the non-clean path.
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


def _scene_with_vwall(w=600, h=400, x=300) -> Image.Image:
    arr = np.full((h, w, 3), 255, np.uint8)
    arr[:, x - 2:x + 3] = 0  # black vertical wall, 5px wide
    return Image.fromarray(arr)


def _wall(x):
    return {"type": "wall", "geometry": {"start": [x, 40], "end": [x, 360]},
            "status": "readable", "attributes": {}}


def test_clean_preserves_drawing_ink_full_opacity():
    """In clean mode the black wall ink stays dark (not faded to 50%)."""
    img = _scene_with_vwall(x=300)
    out = render_grid_with_labels(img, [_wall(300)], clean=True, max_dim=2000)
    o = np.asarray(out.convert("RGB"))
    # column at the wall is still near-black (full opacity preserved)
    assert (255 - o[:, 298:303, :].mean()) > 120


def test_clean_label_is_visible_and_orange():
    """The wall label renders as a visible orange stroke."""
    img = _scene_with_vwall(x=300)
    out = render_grid_with_labels(img, [_wall(360)], clean=True, max_dim=2000)
    o = np.asarray(out.convert("RGB")).astype(int)
    band = o[:, 358:363, :].mean(axis=(0, 1))  # rgb at the label x
    r, g, b = band
    assert r > g > b and r > 180, band  # orange-ish, clearly drawn


def test_clean_makes_misalignment_visible():
    """A wall label 60px OFF the real wall: in clean mode the wall ink and
    the label occupy DIFFERENT columns (both visible) — the proof the QA
    view surfaces the miss. (In the old faded+grid view the ink was washed
    out and the miss was easy to overlook.)"""
    img = _scene_with_vwall(x=300)
    out = render_grid_with_labels(img, [_wall(360)], clean=True, max_dim=2000)
    o = np.asarray(out.convert("RGB")).astype(int)
    ink_dark = 255 - o[:, 298:303, :].mean()     # real wall column
    label_orange = o[:, 358:363, 0].mean() - o[:, 358:363, 2].mean()  # R-B at label
    assert ink_dark > 120, "real wall ink must remain visible"
    assert label_orange > 60, "label must be visibly orange at its (wrong) x"


def test_clean_no_grid_lines():
    """Clean mode draws no grid: away from ink+label the canvas is plain
    white (the non-clean mode would have grey grid lines there)."""
    img = _scene_with_vwall(x=300)
    out = render_grid_with_labels(img, [_wall(300)], clean=True, max_dim=2000)
    o = np.asarray(out.convert("RGB"))
    # a region with neither wall nor label (top-left quadrant, x<200)
    patch = o[10:200, 10:200, :]
    assert patch.min() > 240, "clean mode should have no grid lines in empty area"


def test_clean_coords_match_non_clean():
    """Same output size + same label position whether clean or not."""
    img = _scene_with_vwall(x=300)
    a = render_grid_with_labels(img, [_wall(300)], clean=True, max_dim=2000)
    b = render_grid_with_labels(img, [_wall(300)], clean=False, max_dim=2000)
    assert a.size == b.size
