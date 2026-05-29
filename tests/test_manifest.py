"""manifest read/write + v1.0 → v2.0 upgrade."""
from __future__ import annotations

import json
from pathlib import Path

from ingestion.manifest import (
    assert_v2_shape,
    load_manifest,
    make_page_record,
    make_pipeline_record,
    upgrade_to_v2,
    write_manifest,
)
from ingestion.config import load_profile


def test_upgrade_v1_to_v2_adds_defaults():
    v1 = {
        "schema_version": "1.0",
        "house_key": "house-7",
        "consolidated_pdf": "house-7.pdf",
        "source_filenames": ["a.pdf"],
        "uploaded_at": "2026-05-01T10:00:00Z",
        "page_count": 4,
        "state": "partial",
        "user_notes": "",
        "extracted_scenes": [],
    }
    v2 = upgrade_to_v2(v1)
    assert v2["schema_version"] == "2.0"
    assert v2["source_type"] == "batch"
    assert v2["submitter"] is None
    assert v2["consent"] is None
    assert v2["pages"] == []
    assert v2["pipeline"]["thresholds_profile"] == "v1-legacy"


def test_upgrade_v2_is_identity():
    v2 = {"schema_version": "2.0", "house_key": "house-1", "source_type": "form"}
    out = upgrade_to_v2(v2)
    assert out is v2


def test_load_manifest_roundtrip(tmp_path: Path):
    bundle = tmp_path / "house-9"
    bundle.mkdir()
    cfg = load_profile()
    manifest = {
        "schema_version": "2.0",
        "house_key": "house-9",
        "consolidated_pdf": "house-9.pdf",
        "source_filenames": ["x.heic"],
        "uploaded_at": "2026-05-29T10:00:00Z",
        "page_count": 1,
        "state": "partial",
        "user_notes": "",
        "extracted_scenes": [],
        "source_type": "batch",
        "submitter": None,
        "consent": None,
        "pages": [make_page_record(
            page=1,
            source_file="x.heic",
            source_page=None,
            width_px=2000,
            height_px=3000,
            blur_laplacian_var=200.0,
            exposure_mean=140.0,
            glare_fraction=0.01,
            skew_deg=0.5,
            document_present=True,
            decision="pass",
            decision_reasons=[],
            rectified=True,
            rectify_method="perspective_contour",
            title_block_suspected=False,
            title_block_bbox_px=None,
            redacted=False,
        )],
        "pipeline": make_pipeline_record(cfg),
    }
    write_manifest(bundle, manifest)
    loaded = load_manifest(bundle)
    assert loaded is not None
    assert_v2_shape(loaded)
    assert loaded["house_key"] == "house-9"
    assert loaded["pages"][0]["decision"] == "pass"


def test_load_manifest_returns_none_for_missing(tmp_path: Path):
    assert load_manifest(tmp_path / "nonexistent") is None
