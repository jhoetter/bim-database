"""Visual regression tests for the grid overlay renderer.

Per agentic-labeling-followups-tracker §G2-3. The strict invariants:
- Output dimensions exactly match the source dimensions (or the
  cropped region) — no outer margin.
- max_dim downscales without skewing aspect ratio.
- All requested tiers leave visible signal (i.e. lines that distinguish
  the output from a single-color image).
- Tier subsets produce visually-different outputs (so the per-tier
  checkboxes the user toggles actually do something).
- Coordinate labels along the edges aren't cut off — they sit inside
  the canvas bounds with a readable chip background.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.grid_render import ALL_TIERS, DEFAULT_TIERS, render_grid_overlay  # noqa: E402


@pytest.fixture
def sample_scene() -> Image.Image:
    """Pick the first JPG under data/dataset/, fall back to a synthetic
    image if the corpus is empty."""
    dataset = REPO_ROOT / "data" / "dataset"
    if dataset.exists():
        for p in dataset.rglob("*.jpg"):
            return Image.open(p)
    # Synthetic fallback so the test runs on a fresh clone.
    img = Image.new("RGB", (1800, 1200), (210, 210, 220))
    return img


def test_default_tiers_excludes_detail():
    """Per §8 decision 3: default is {broad, finer}; detail is opt-in."""
    assert set(DEFAULT_TIERS) == {"broad", "finer"}
    assert "detail" not in DEFAULT_TIERS
    assert set(DEFAULT_TIERS) <= set(ALL_TIERS)


def test_output_matches_source_dimensions_no_margin(sample_scene):
    out = render_grid_overlay(sample_scene, tiers=("broad",), max_dim=10000)
    assert out.size == sample_scene.size, (
        f"grid output {out.size} should equal source {sample_scene.size} "
        "— no outer margin per §G2-1"
    )


def test_output_clamped_to_max_dim(sample_scene):
    out = render_grid_overlay(sample_scene, tiers=("broad",), max_dim=400)
    assert max(out.size) <= 400
    # Aspect ratio preserved (within 1 px rounding).
    src_ratio = sample_scene.size[0] / sample_scene.size[1]
    out_ratio = out.size[0] / out.size[1]
    assert abs(src_ratio - out_ratio) < 0.01


def test_cropped_output_matches_region_dims(sample_scene):
    region = (100, 100, 600, 500)
    out = render_grid_overlay(sample_scene, tiers=("broad",), region=region, max_dim=10000)
    assert out.size == (500, 400)


def test_cropped_output_keeps_native_resolution_under_max_dim(sample_scene):
    """H4 (followups-2 tracker): small crops should stay 1:1 — readability
    of small rotated dim text was the original motivation for this."""
    region = (100, 100, 500, 500)  # 400×400 crop, source-pixel
    out = render_grid_overlay(sample_scene, tiers=("broad",), region=region, max_dim=1600)
    # Should NOT have been upscaled to 1600
    assert out.size == (400, 400), (
        f"crop with max_dim=1600 should keep 400x400 native; got {out.size}"
    )


def test_cropped_output_still_clamps_when_crop_exceeds_max_dim(sample_scene):
    """Large crops still get downscaled to max_dim so an agent's context
    doesn't get hit by a multi-megabyte image."""
    sw, sh = sample_scene.size
    # Use a region that's clearly bigger than max_dim if possible.
    if max(sw, sh) < 1200:
        pytest.skip("sample image too small to exercise the cap")
    out = render_grid_overlay(
        sample_scene,
        tiers=("broad",),
        region=(0, 0, min(sw, 1500), min(sh, 1500)),
        max_dim=800,
    )
    assert max(out.size) <= 800


def test_tiers_must_be_valid():
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    with pytest.raises(ValueError, match="unknown tier"):
        render_grid_overlay(img, tiers=("nonexistent",))
    with pytest.raises(ValueError, match="at least one tier"):
        render_grid_overlay(img, tiers=())


def test_region_must_be_inside_image():
    img = Image.new("RGB", (400, 400), (255, 255, 255))
    with pytest.raises(ValueError, match="out of image bounds"):
        render_grid_overlay(img, region=(0, 0, 500, 500))


def test_tier_subsets_produce_different_outputs(sample_scene):
    """Toggling tier checkboxes must change pixels."""
    img_broad = render_grid_overlay(sample_scene, tiers=("broad",), max_dim=600)
    img_broad_finer = render_grid_overlay(sample_scene, tiers=("broad", "finer"), max_dim=600)
    img_all = render_grid_overlay(sample_scene, tiers=ALL_TIERS, max_dim=600)
    # tobytes() comparison: if two tier-sets produced the same image,
    # the user's checkbox does nothing.
    assert img_broad.tobytes() != img_broad_finer.tobytes(), \
        "adding 'finer' tier should change the rendered image"
    assert img_broad_finer.tobytes() != img_all.tobytes(), \
        "adding 'detail' tier should change the rendered image"


def test_broad_tier_paints_dark_pixels_on_white_background():
    """A canvas-white-only image with the broad grid should still have
    visible black lines. Smoke check that the grid is actually drawn."""
    img = Image.new("RGB", (800, 800), (255, 255, 255))
    out = render_grid_overlay(img, tiers=("broad",), max_dim=800)
    # Count near-black pixels in the rendered image.
    arr = out.convert("L")
    dark_pixels = sum(1 for px in arr.getdata() if px < 50)
    assert dark_pixels > 100, "broad tier should draw at least 100 dark pixels"


def test_edge_labels_inside_canvas(sample_scene):
    """No label should land outside the canvas (would be invisible)."""
    out = render_grid_overlay(sample_scene, tiers=("broad", "finer"), max_dim=1600)
    w, h = out.size
    # Check that the very first column and first row contain near-white
    # pixels in the top-left where the (0, 0) coordinate label chip sits.
    # The chip has white background → pixel near (0, 0) should be light.
    px = out.convert("L").load()
    top_left_band = [px[x, 0] for x in range(0, min(w, 60))]
    # The label area should contain at least some light (chip) pixels.
    assert any(p > 180 for p in top_left_band), \
        "top-left edge band should contain the bright chip background of a coord label"


def test_legend_in_top_right_corner(sample_scene):
    """The legend chip sits in the top-right; verify its background
    survives in the corner pixels."""
    out = render_grid_overlay(sample_scene, tiers=("broad",), max_dim=1600)
    w, _ = out.size
    px = out.convert("L").load()
    # Sample a 30×30 band in the very top-right.
    samples = [px[x, y] for x in range(w - 30, w) for y in range(0, 30)]
    bright_count = sum(1 for p in samples if p > 200)
    # The legend has a semi-transparent white background → most pixels
    # in this band should be light.
    assert bright_count > 200, \
        f"top-right corner should contain the legend chip; saw only {bright_count} bright pixels"


def test_cropped_legend_shows_origin(sample_scene):
    """When `region` is set, the legend includes the crop origin so the
    agent knows the coordinate frame is in source pixels."""
    out = render_grid_overlay(sample_scene, tiers=("broad",), region=(50, 50, 350, 350))
    # No straightforward way to assert "crop origin appears in legend
    # text" without OCR. Smoke check: the rendered image still has the
    # legend chip (bright top-right band).
    px = out.convert("L").load()
    w, _ = out.size
    samples = [px[x, y] for x in range(w - 30, w) for y in range(0, 30)]
    assert sum(1 for p in samples if p > 200) > 100


# ── Issue #2: contrast enhancement for faint freehand scans ───────────────


def _faint_scan(size=(600, 400)) -> Image.Image:
    """Synthetic near-white image with a barely-darker stroke — stands in
    for a faint pencil/freehand elevation scan."""
    img = Image.new("L", size, 248)  # almost-white paper
    px = img.load()
    w, h = size
    # A faint horizontal stroke (gentle dip from the paper tone).
    for x in range(40, w - 40):
        for y in range(h // 2 - 1, h // 2 + 2):
            px[x, y] = 225
    return img.convert("RGB")


def test_enhance_none_is_noop():
    from api.grid_render import render_grid_overlay
    src = _faint_scan()
    plain = render_grid_overlay(src, tiers=("broad",), max_dim=10000, enhance="none")
    default = render_grid_overlay(src, tiers=("broad",), max_dim=10000)
    assert list(plain.getdata()) == list(default.getdata())


def test_enhance_preserves_dimensions_and_coords():
    """Enhancement must not move pixels — output size is identical, so
    SOURCE-pixel coordinates remain valid (issue #2 invariant)."""
    from api.grid_render import render_grid_overlay, ENHANCE_MODES
    src = _faint_scan()
    base = render_grid_overlay(src, tiers=("broad",), max_dim=10000, enhance="none")
    for mode in ENHANCE_MODES:
        out = render_grid_overlay(src, tiers=("broad",), max_dim=10000, enhance=mode)
        assert out.size == base.size, f"{mode} changed output size"


def test_enhance_increases_contrast_on_faint_scan():
    """CLAHE/threshold should widen the intensity spread of the faint
    stroke vs the paper. Test the pure transform so grid lines don't
    pollute the histogram."""
    import numpy as np
    from api.grid_render import _enhance_image
    src = _faint_scan()

    def luma_std(img):
        return float(np.asarray(img.convert("L"), dtype=float).std())

    std_none = luma_std(src)
    std_clahe = luma_std(_enhance_image(src, "clahe"))
    std_threshold = luma_std(_enhance_image(src, "threshold"))
    assert std_clahe > std_none, (std_clahe, std_none)
    assert std_threshold > std_none, (std_threshold, std_none)


def test_enhance_threshold_binarizes():
    """threshold mode should collapse toward a near-binary image: the
    overwhelming majority of pixels are pure black or pure white."""
    import numpy as np
    from api.grid_render import _enhance_image
    out = _enhance_image(_faint_scan(), "threshold")
    arr = np.asarray(out.convert("L"))
    extremes = int(((arr == 0) | (arr == 255)).sum())
    assert extremes / arr.size > 0.95, extremes / arr.size


def test_enhance_rejects_unknown_mode():
    from api.grid_render import render_grid_overlay
    src = _faint_scan()
    with pytest.raises(ValueError):
        render_grid_overlay(src, tiers=("broad",), enhance="sharpen")
