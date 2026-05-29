"""Stage 2 — quality gate."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageFilter

from ingestion.config import QualityThresholds
from ingestion.gate import score_page
from ingestion.normalize import normalize_file


def test_clean_jpeg_passes(synth_jpeg: Path):
    img = normalize_file(synth_jpeg)[0].image
    _, decision = score_page(img, thresholds=QualityThresholds(), is_native_pdf=False)
    assert decision.decision in {"pass", "warn"}  # synthetic image is borderline-natural


def test_underexposed_rejects():
    # Solid near-black canvas — exposure_mean below floor.
    img = Image.new("RGB", (2400, 3200), (10, 10, 10))
    _, decision = score_page(img, thresholds=QualityThresholds(), is_native_pdf=False)
    assert decision.decision == "reject"
    assert any("underexposed" in r for r in decision.reasons)


def test_blurry_warns_or_rejects(blurry_jpeg: Path):
    img = normalize_file(blurry_jpeg)[0].image
    _, decision = score_page(img, thresholds=QualityThresholds(), is_native_pdf=False)
    assert decision.decision in {"warn", "reject"}
    assert any("focus" in r or "soft" in r for r in decision.reasons)


def test_low_res_rejects():
    img = Image.new("RGB", (400, 400), (220, 220, 220))
    _, decision = score_page(img, thresholds=QualityThresholds(), is_native_pdf=False)
    assert decision.decision == "reject"
    assert any("resolution" in r for r in decision.reasons)


def test_native_pdf_skips_skew_check(synth_jpeg):
    """is_native_pdf=True must short-circuit the skew metric (we render
    PDFs flat) and the document-present heuristic (the entire frame is
    the page)."""
    img = normalize_file(synth_jpeg)[0].image
    metrics, _ = score_page(img, thresholds=QualityThresholds(), is_native_pdf=True)
    assert metrics.skew_deg is None
    assert metrics.document_present is True
