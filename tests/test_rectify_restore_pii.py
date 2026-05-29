"""Stages 3 / 4 / PII — sanity checks that work without OpenCV."""
from __future__ import annotations

from PIL import Image

from ingestion.pii import flag_title_block, page_has_dimension_text, redact_region
from ingestion.rectify import rectify
from ingestion.restore import NoopEnhancer, get_enhancer


def test_rectify_passthrough_native_pdf():
    img = Image.new("RGB", (1200, 1600), (252, 252, 250))
    result = rectify(img, method="perspective_contour", is_native_pdf=True)
    assert result.method == "passthrough"
    assert result.succeeded is True
    assert result.image.size == (1200, 1600)


def test_rectify_explicit_passthrough():
    img = Image.new("RGB", (1200, 1600), (252, 252, 250))
    result = rectify(img, method="passthrough", is_native_pdf=False)
    assert result.method == "passthrough"
    assert result.image.size == (1200, 1600)


def test_noop_enhancer_is_safe_on_text():
    img = Image.new("RGB", (800, 1200), (252, 252, 250))
    enh = NoopEnhancer()
    out = enh.enhance(img, has_dimension_text=True)
    assert out.backend == "noop"
    assert out.applied_to_text is True  # noop is always safe on text
    assert out.image.size == (800, 1200)


def test_get_enhancer_unknown_backend():
    import pytest
    with pytest.raises(ValueError, match="unknown enhancer backend"):
        get_enhancer("does-not-exist")


def test_redact_region_paints_white():
    img = Image.new("RGB", (200, 200), (10, 10, 10))
    out = redact_region(img, (50, 50, 150, 150))
    px = out.load()
    assert px[100, 100] == (255, 255, 255)
    assert px[10, 10] == (10, 10, 10)


def test_page_has_dimension_text_truthy_on_drawing(synth_jpeg):
    from ingestion.normalize import normalize_file
    img = normalize_file(synth_jpeg)[0].image
    assert page_has_dimension_text(img) is True


def test_flag_title_block_returns_bbox_or_none(synth_jpeg):
    from ingestion.normalize import normalize_file
    img = normalize_file(synth_jpeg)[0].image
    flag = flag_title_block(img)
    # Either flagged (bottom-right of our synthetic image has text) or
    # not — we don't pin the heuristic to a brittle value, just confirm
    # the contract.
    if flag.title_block_suspected:
        assert flag.title_block_bbox_px is not None
        x0, y0, x1, y1 = flag.title_block_bbox_px
        assert x1 > x0 and y1 > y0
    else:
        assert flag.title_block_bbox_px is None
