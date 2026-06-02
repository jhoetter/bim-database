"""Server-side semantic dependency checks for scene labels.

The UI prevents these states during normal drawing. MCP/direct HTTP must reject
them too, otherwise agents can save labels the UI would only render as warnings.
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
    key = "house-zzdependencytest"
    file = f"{key}-scene.jpg"
    ds_key = api_main.DATASET_DIR / key
    ds_key.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 300), (255, 255, 255)).save(ds_key / file)
    try:
        yield key, file
    finally:
        shutil.rmtree(ds_key, ignore_errors=True)


def _payload(scene_tag: str, labels: list[dict]):
    return {
        "schema_version": "1.0",
        "scene_tag": scene_tag,
        "image_size_px": [400, 300],
        "labels": labels,
    }


def _wall(label_id: str = "wall-1"):
    return {
        "id": label_id,
        "type": "wall",
        "geometry": {"start": [50, 80], "end": [350, 80]},
        "status": "readable",
        "attributes": {},
    }


def _opening(*, label_id: str = "open-1", y: int = 80, parent_id: str | None = "wall-1"):
    relations = [] if parent_id is None else [{"kind": "belongs_to", "other_id": parent_id}]
    return {
        "id": label_id,
        "type": "floorplan_opening",
        "geometry": {"quad": [[100, y - 10], [160, y - 10], [160, y + 10], [100, y + 10]]},
        "status": "readable",
        "attributes": {"opening_kind": "door"},
        "relations": relations,
    }


def _distance():
    return {
        "id": "dim-1",
        "type": "dimensioned_distance",
        "geometry": {"start": [50, 220], "end": [250, 220]},
        "status": "readable",
        "attributes": {
            "value_mm": 2000,
            "target_orientation": "horizontal",
            "is_reference": False,
        },
    }


def _number(*, target_id: str | None = "dim-1"):
    relations = [] if target_id is None else [{"kind": "labels", "other_id": target_id}]
    return {
        "id": "num-1",
        "type": "dimension_number",
        "geometry": {"anchor": [150, 205]},
        "status": "readable",
        "attributes": {"text": "2,00", "parsed_value_mm": 2000},
        "relations": relations,
    }


def test_floorplan_opening_requires_parent_wall_relation(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.put(
        f"/labels/dataset/{key}/{file}",
        json=_payload("grundriss", [_wall(), _opening(parent_id=None)]),
    )

    assert r.status_code == 422
    assert "must belong_to a wall" in r.text


def test_floorplan_opening_parent_must_exist_and_be_wall(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.put(
        f"/labels/dataset/{key}/{file}",
        json=_payload("grundriss", [_wall(), _opening(parent_id="missing-wall")]),
    )

    assert r.status_code == 422
    assert "relation target" in r.text
    assert "missing-wall" in r.text


def test_floorplan_opening_must_be_on_parent_wall(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.put(
        f"/labels/dataset/{key}/{file}",
        json=_payload("grundriss", [_wall(), _opening(y=150)]),
    )

    assert r.status_code == 422
    assert "not placed on parent wall" in r.text


def test_floorplan_opening_on_parent_wall_is_accepted(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.put(
        f"/labels/dataset/{key}/{file}",
        json=_payload("grundriss", [_wall(), _opening()]),
    )

    assert r.status_code == 200, r.text


def test_dimension_number_requires_dimensioned_distance_relation(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.put(
        f"/labels/dataset/{key}/{file}",
        json=_payload("grundriss", [_distance(), _number(target_id=None)]),
    )

    assert r.status_code == 422
    assert "must label an existing dimensioned_distance" in r.text


def test_dimension_number_linked_to_dimensioned_distance_is_accepted(scene):
    key, file = scene
    client = TestClient(api_main.app)

    r = client.put(
        f"/labels/dataset/{key}/{file}",
        json=_payload("grundriss", [_distance(), _number()]),
    )

    assert r.status_code == 200, r.text
