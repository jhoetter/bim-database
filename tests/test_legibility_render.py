"""WS-A (legibility-first-quality tracker): render pipeline must not
self-inflict illegibility on faint scans.

Covers:
- A1  enhance runs on the NATIVE crop before the downscale (stronger lift).
- A2  the lossless `format=png` read path round-trips faint grays exactly.
- A3  draw_grid=False yields a clean crop, pixel-identical to the source
      (no grid lines, labels, legend, or fade).
- A4  the coordinate_multicolor mesh can never fully occlude ink (alpha<=140).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.grid_render import (  # noqa: E402
    _enhance_image,
    _multicolor_line,
    compute_output_size,
    render_grid_overlay,
)
from api.main import _save_grid_png  # noqa: E402


def _faint_lines(w: int, h: int, *, value: int, step: int, width: int) -> Image.Image:
    """White canvas with faint vertical pencil-like lines."""
    arr = np.full((h, w), 255, np.uint8)
    for x in range(step, w, step):
        arr[:, x : x + width] = value
    return Image.fromarray(arr, "L").convert("RGB")


# ── A1 ──────────────────────────────────────────────────────────────────
def test_a1_enhance_runs_on_native_crop_before_downscale():
    """The render output must equal enhance(native)->downscale, and must
    NOT equal downscale->enhance. CLAHE/threshold are local-neighborhood
    operators: running them on full-resolution pixels (before LANCZOS blends
    faint strokes toward the background) is the materially stronger lift.
    This pins the ordering deterministically, independent of any contrast
    metric."""
    W, H, MAXD = 3000, 400, 1000
    src = _faint_lines(W, H, value=215, step=30, width=2)
    ow, oh = compute_output_size(W, H, MAXD)

    out = render_grid_overlay(
        src, draw_grid=False, tiers=(), enhance="threshold", max_dim=MAXD,
        background_opacity=1.0,
    )
    got = np.asarray(out.convert("RGB"))

    native_first = np.asarray(
        _enhance_image(src, "threshold").resize((ow, oh), Image.LANCZOS).convert("RGB")
    )
    downscale_first = np.asarray(
        _enhance_image(src.resize((ow, oh), Image.LANCZOS), "threshold").convert("RGB")
    )

    assert np.array_equal(got, native_first), "enhance must run before downscale"
    assert not np.array_equal(got, downscale_first), (
        "output matches the weaker downscale-first ordering — A1 regressed"
    )


# ── A2 + A3 ─────────────────────────────────────────────────────────────
def test_a3_clean_crop_is_pixel_identical_to_source(tmp_path):
    """draw_grid=False + opacity 1.0 + enhance none => the output is the
    source crop untouched. This is the read-tier contract: a value is read
    off real pixels, not a grid-occluded, faded composite."""
    src = _faint_lines(800, 600, value=235, step=40, width=1)
    region = (100, 50, 500, 350)
    out = render_grid_overlay(
        src, region=region, draw_grid=False, tiers=(),
        background_opacity=1.0, enhance="none",
    )
    expected = np.asarray(src.crop(region).convert("RGB"))
    got = np.asarray(out.convert("RGB"))
    assert got.shape == expected.shape
    assert np.array_equal(got, expected), "clean crop must equal the source crop"


def test_a2_png_read_path_is_lossless(tmp_path):
    """format=png round-trips faint grays exactly; png8 is the lossy survey
    path (indexed palette). The fidelity gate (WS-C) relies on png being
    lossless so a read value is trustworthy."""
    src = _faint_lines(400, 300, value=247, step=20, width=1)
    overlay = render_grid_overlay(
        src, draw_grid=False, tiers=(), background_opacity=1.0, enhance="none",
    )
    png = tmp_path / "read.png"
    png8 = tmp_path / "survey.png"
    _save_grid_png(overlay, png, "png")
    _save_grid_png(overlay, png8, "png8")

    # png is lossless: the faint 247 level survives.
    back = np.asarray(Image.open(png).convert("L"))
    assert (back == 247).any(), "lossless png must keep the faint gray level"
    # png8 is an indexed-palette image (the cheap survey path).
    assert Image.open(png8).mode == "P"


# ── A4 ──────────────────────────────────────────────────────────────────
def test_a4_multicolor_mesh_never_opaque():
    """No coordinate_multicolor gridline may fully occlude the ink under it."""
    for tier in ("broad", "finer", "detail"):
        for orient in ("vertical", "horizontal"):
            rgba = _multicolor_line(120, 24, tier=tier, orientation=orient)
            assert rgba[3] <= 140, f"{tier}/{orient} alpha {rgba[3]} > 140"


def test_a3_no_grid_allows_empty_tiers():
    """A clean crop needs no tiers — draw_grid=False must not require them."""
    src = Image.new("RGB", (300, 200), (255, 255, 255))
    # Would raise 'at least one tier required' if the guard ignored draw_grid.
    render_grid_overlay(src, draw_grid=False, tiers=(), background_opacity=1.0)
