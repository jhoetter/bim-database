"""V2.4 — the height_mark datum enum covers the real datum set.

During the overnight drive, placing a height_mark with datum 'ok_ffb' /
'geschoss' appeared to be rejected, forcing a fallback to 'other'. This
locks the accepted set (and adds the previously-missing 'bezug' = ±0.00
reference datum) so the agent can label each Höhenkote with its true
datum. (labeling-correctness-verification-tracker V2.4)
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402

# The full datum set the labeling methodology relies on.
DATA = ["first", "traufe", "gelaende", "geschoss", "ok_ffb",
        "sockel", "kniestock", "bezug", "other", None]


@pytest.fixture
def scene():
    """A throwaway dataset scene under the REAL DATASET_DIR (put_labels does
    relative_to(BASE), so the path must live inside the repo). Cleaned up
    after the test."""
    key = "house-zzdatumtest"
    file = f"{key}-section.jpg"
    ds_key = api_main.DATASET_DIR / key
    ds_key.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.new("RGB", (400, 300), (255, 255, 255)).save(ds_key / file)
    try:
        yield key, file
    finally:
        shutil.rmtree(ds_key, ignore_errors=True)


def _payload(datum):
    return {
        "schema_version": "1.0",
        "scene_tag": "schnitt",
        "image_size_px": [400, 300],
        "labels": [{
            "id": "hm-1",
            "type": "height_mark",
            "geometry": {"anchor": [200, 150]},
            "status": "readable",
            "attributes": {"value_mm": 0, "datum": datum},
        }],
    }


@pytest.mark.parametrize("datum", DATA)
def test_v2_4_datum_value_accepted(scene, datum):
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(f"/labels/dataset/{key}/{file}", json=_payload(datum))
    assert r.status_code == 200, f"datum {datum!r} rejected: {r.text}"


def test_v2_4_bezug_specifically_accepted(scene):
    """Regression: 'bezug' (±0.00 reference) was missing from the enum."""
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(f"/labels/dataset/{key}/{file}", json=_payload("bezug"))
    assert r.status_code == 200, r.text


def test_v2_4_bogus_datum_rejected(scene):
    """A datum NOT in the enum is a 422 — the guard still bites."""
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(f"/labels/dataset/{key}/{file}", json=_payload("not_a_datum"))
    assert r.status_code == 422, r.text
