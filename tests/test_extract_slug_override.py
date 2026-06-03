"""Regression: slug_override must not double-prefix the house key.

A scene's filename stem is `{key}-{base_slug}`. To RE-EXTRACT an existing
scene the caller passes the full stem (e.g. "house-22-floorplan-eg") as
slug_override — which already starts with "{key}-". The extract route used
to re-prepend the key, producing a PHANTOM scene
("house-22-house-22-floorplan-eg") and leaving the real scene's crop
untouched — so re-cropping silently did nothing (the user kept seeing the
old bounding box). This locks the fix.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def install_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr("api.main.DATASET_DIR", tmp_path / "dataset")
    pdf_path = tmp_path / "house-slug.pdf"

    def _install(size=(612, 792)):
        doc = fitz.open()
        page = doc.new_page(width=size[0], height=size[1])
        page.draw_rect(fitz.Rect(60, 60, 400, 400), color=(0, 0, 0), width=2)
        doc.save(str(pdf_path))
        doc.close()
        return pdf_path

    monkeypatch.setattr("api.routes_pdf._consolidated_path", lambda key: pdf_path)
    return _install


KEY = "house-slug"


def _extract(client, *, slug_override, bbox):
    body = {"items": [{
        "page": 1, "bbox_pdf_units": bbox, "kind": "floorplan", "floor": "eg",
        "slug_override": slug_override, "dpi": 150, "bbox_is_authoritative": True,
    }]}
    return client.post(f"/pdfs/{KEY}/extract", json=body)


def test_override_with_key_prefix_does_not_double_prefix(client, install_pdf):
    """slug_override 'house-slug-floorplan-eg' must produce the file
    'house-slug-floorplan-eg.jpg' — NOT 'house-slug-house-slug-...'."""
    install_pdf()
    resp = _extract(client, slug_override="house-slug-floorplan-eg",
                    bbox=[60, 60, 400, 400])
    assert resp.status_code == 201, resp.text
    files = [e["file"] for e in resp.json()["extracted"]]
    assert files == ["house-slug-floorplan-eg.jpg"], files
    assert not any("house-slug-house-slug" in f for f in files)


def test_reextract_overwrites_same_scene(client, install_pdf):
    """Re-extracting with the same full-stem override updates the SAME
    scene (different bbox recorded), not a phantom duplicate."""
    install_pdf()
    r1 = _extract(client, slug_override="house-slug-floorplan-eg",
                  bbox=[60, 60, 200, 200])
    assert r1.status_code == 201, r1.text
    r2 = _extract(client, slug_override="house-slug-floorplan-eg",
                  bbox=[60, 60, 400, 400])
    assert r2.status_code == 201, r2.text
    # Same filename both times.
    assert r1.json()["extracted"][0]["file"] == r2.json()["extracted"][0]["file"]
    # The recorded bbox reflects the SECOND extract (the re-crop took effect).
    rec = r2.json()["extracted"][0]["crop_from"]["bbox_pdf_units"]
    assert rec == pytest.approx([60, 60, 400, 400], abs=0.5), rec
    # Only one scene exists in the dataset (no phantom).
    ds = client.get(f"/datasets/{KEY}").json()
    eg_scenes = [d["file"] for d in ds["drawings"] if "floorplan-eg" in d["file"]]
    assert eg_scenes == ["house-slug-floorplan-eg.jpg"], eg_scenes


def test_reextract_with_existing_labels_requires_confirmation(client, install_pdf, tmp_path):
    install_pdf()
    r1 = _extract(client, slug_override="house-slug-floorplan-eg",
                  bbox=[20, 20, 430, 430])
    assert r1.status_code == 201, r1.text
    file_name = r1.json()["extracted"][0]["file"]
    labels_dir = tmp_path / "dataset" / KEY / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    (labels_dir / f"{Path(file_name).stem}.json").write_text(json.dumps({
        "scene_file": file_name,
        "scene_tag": "grundriss",
        "scene_level": "eg",
        "labels": [{
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [10, 10], "end": [100, 10]},
            "attributes": {},
        }],
    }))

    blocked = _extract(client, slug_override="house-slug-floorplan-eg",
                       bbox=[50, 50, 250, 250])
    assert blocked.status_code == 409
    detail = blocked.json()["detail"]
    assert detail["required_confirmation"] == "confirm_reextract_existing_scene=true"
    assert detail["crop_warnings"]

    confirmed = client.post(f"/pdfs/{KEY}/extract", json={"items": [{
        "page": 1, "bbox_pdf_units": [50, 50, 250, 250], "kind": "floorplan",
        "floor": "eg", "slug_override": "house-slug-floorplan-eg", "dpi": 150,
        "bbox_is_authoritative": True, "confirm_reextract_existing_scene": True,
    }]})
    assert confirmed.status_code == 201, confirmed.text
    entry = confirmed.json()["extracted"][0]
    assert entry["crop_intent"] == "scene_full_context"
    assert entry["crop_warnings"]


def test_override_without_key_prefix_still_works(client, install_pdf):
    """A bare override (no key prefix) is still prefixed exactly once."""
    install_pdf()
    resp = _extract(client, slug_override="floorplan-eg", bbox=[60, 60, 400, 400])
    assert resp.status_code == 201, resp.text
    assert resp.json()["extracted"][0]["file"] == "house-slug-floorplan-eg.jpg"
