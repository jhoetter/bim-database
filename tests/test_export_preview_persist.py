"""Issue #27: export preview persists single-ref isotropic calibration.

The single-ref isotropic homography (#26) made `assume_isotropic=true`
calibrate an axis-aligned scene from ONE reference dim, but the preview
path computed it transiently and never persisted `calibration_per_scene`,
so the W4 / export-readiness gate couldn't see it. Now a valid
rectification persists the scene's calibration (with the
`single_ref_assumed_isotropic` flag); a degenerate/insufficient result
persists nothing.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import fitz  # noqa: F401  (ensures PyMuPDF import parity with the app)
import pytest
from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402


@pytest.fixture
def single_ref_house():
    """A throwaway house with one elevation scene whose only reference dim
    is a single horizontal M1 ref (no vertical) — the single-ref case."""
    key = "house-zziso27"
    ds = api_main.DATASET_DIR / key
    (ds / "labels").mkdir(parents=True, exist_ok=True)
    file = f"{key}-elevation-west.jpg"
    # A real (non-blank) scene image so rectify_image can run.
    img = Image.new("RGB", (2000, 1000), (255, 255, 255))
    for x in range(100, 1900):
        img.putpixel((x, 500), (0, 0, 0))
    img.save(ds / file, format="JPEG", quality=90)
    labels = {
        "schema_version": "1.1",
        "scene_tag": "ansicht",
        "scene_orientation": "west",
        "image_size_px": [2000, 1000],
        "labels": [
            {
                "id": "lab-href",
                "type": "dimensioned_distance",
                "attributes": {
                    "is_reference": True, "value_mm": 9860,
                    "target_orientation": "horizontal",
                },
                "geometry": {"start": [100, 500], "end": [1000, 500]},  # horizontal
            },
        ],
    }
    (ds / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    (ds / "manifest.json").write_text(json.dumps({
        "key": key, "drawings": [{"file": file, "kind": "elevation", "labeled": True}],
    }))
    try:
        yield key, file
    finally:
        shutil.rmtree(ds, ignore_errors=True)


def _calibration(key, file):
    p = api_main.DATASET_DIR / key / "house_facts.json"
    if not p.exists():
        return None
    return (json.loads(p.read_text()).get("calibration_per_scene") or {}).get(file)


def test_preview_persists_single_ref_calibration_with_optin(single_ref_house):
    key, file = single_ref_house
    client = TestClient(api_main.app)
    r = client.post(f"/exports/{key}/{file}/preview", params={"assume_isotropic": "true"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body
    assert body["homography"]["single_ref_assumed_isotropic"] is True
    # Persisted with the honesty flag.
    pc = body["persisted_calibration"]
    assert pc and pc["single_ref_assumed_isotropic"] is True
    assert pc["computed_from"] == "M1-H-Bezug"
    # And it actually landed in house_facts.calibration_per_scene.
    persisted = _calibration(key, file)
    assert persisted and persisted["single_ref_assumed_isotropic"] is True


def test_preview_without_optin_persists_nothing(single_ref_house):
    """Single ref + assume_isotropic=false is insufficient — the
    degenerate guard must persist nothing (W4 stays honestly pending)."""
    key, file = single_ref_house
    client = TestClient(api_main.app)
    r = client.post(f"/exports/{key}/{file}/preview", params={"assume_isotropic": "false"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] != "ok"  # insufficient_references
    assert body["persisted_calibration"] is None
    assert _calibration(key, file) is None
