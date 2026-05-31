"""Scene-category label palette enforcement.

The UI tool palette is not enough: MCP and direct HTTP writes must reject
labels that are semantically invalid for the scene category.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402


@pytest.fixture
def scene():
    key = "house-zzpalettetest"
    file = f"{key}-scene.jpg"
    ds_key = api_main.DATASET_DIR / key
    ds_key.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (255, 255, 255)).save(ds_key / file)
    try:
        yield key, file
    finally:
        shutil.rmtree(ds_key, ignore_errors=True)


def _payload(scene_tag: str, label: dict):
    return {
        "schema_version": "1.0",
        "scene_tag": scene_tag,
        "image_size_px": [400, 300],
        "labels": [label],
    }


def _wall():
    return {
        "id": "wall-1",
        "type": "wall",
        "geometry": {"start": [50, 80], "end": [350, 80]},
        "status": "readable",
        "attributes": {},
    }


def _height_mark():
    return {
        "id": "hm-1",
        "type": "height_mark",
        "geometry": {"anchor": [200, 150]},
        "status": "readable",
        "attributes": {"value_mm": 0, "datum": "ok_ffb"},
    }


def test_floorplan_accepts_wall(scene):
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(f"/labels/dataset/{key}/{file}", json=_payload("grundriss", _wall()))
    assert r.status_code == 200, r.text


def test_floorplan_rejects_height_mark(scene):
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(
        f"/labels/dataset/{key}/{file}", json=_payload("grundriss", _height_mark())
    )
    assert r.status_code == 422
    assert "height_mark" in r.text
    assert "grundriss" in r.text


def test_section_accepts_height_mark(scene):
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(f"/labels/dataset/{key}/{file}", json=_payload("schnitt", _height_mark()))
    assert r.status_code == 200, r.text


def test_elevation_rejects_wall(scene):
    key, file = scene
    client = TestClient(api_main.app)
    r = client.put(f"/labels/dataset/{key}/{file}", json=_payload("ansicht", _wall()))
    assert r.status_code == 422
    assert "wall" in r.text
    assert "ansicht" in r.text
