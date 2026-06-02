"""Route-level tests for the wall-geometry HTTP endpoints (bim-agent tracker
B2 — exposing the tools over HTTP/MCP). The underlying logic is unit-tested in
test_wall_geometry.py; these check param parsing, the {ok,data} envelope, and
persistence behavior."""
import os
import shutil

import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

import api.main as api_main
from api.main import app

client = TestClient(app)

_SCENE_REL = "data/dataset/house-22/house-22-floorplan-eg.png"
_has_house22 = os.path.exists(_SCENE_REL)


def test_connect_corners_route_closed_square():
    # overshooting edges → corners are line intersections → exact closed square
    edges = [
        [[-5, 0], [105, 0]],
        [[100, -5], [100, 105]],
        [[105, 100], [-5, 100]],
        [[0, 105], [0, -5]],
    ]
    r = client.post("/geometry/connect-corners", json={"edges": edges, "closed": True})
    assert r.status_code == 200
    d = r.json()["data"]
    assert d["count"] == 4 and d["closed"] is True
    corners = {(round(w[0][0]), round(w[0][1])) for w in d["walls"]}
    assert {(0, 0), (100, 0), (100, 100), (0, 100)} <= corners


def test_connect_corners_route_rejects_bad_body():
    r = client.post("/geometry/connect-corners", json={"closed": True})
    assert r.status_code == 400


@pytest.mark.skipif(not _has_house22, reason="house-22 scene not present")
def test_building_silhouette_route_envelope():
    r = client.get("/datasets/house-22/house-22-floorplan-eg.png/building-silhouette",
                   params={"min_wall_px": 16})
    assert r.status_code == 200
    d = r.json()["data"]
    assert "masses" in d and isinstance(d["masses"], list)
    assert isinstance(d["count"], int)
    assert "params" in d


@pytest.mark.skipif(not _has_house22, reason="house-22 scene not present")
def test_propose_wall_edit_route_advisory_no_write():
    # apply=false → computes before/after but never persists (house-22 stays clean)
    body = {"candidate": {"op": "add", "wall": [[1270, 1130], [2050, 1130]]},
            "apply": False}
    r = client.post("/datasets/house-22/house-22-floorplan-eg.png/propose-wall-edit",
                    json=body)
    assert r.status_code == 200
    d = r.json()["data"]
    assert set(["applied", "gain", "before", "after", "walls_after", "persisted"]) <= set(d)
    assert d["persisted"] is False


def test_propose_wall_edit_route_rejects_missing_candidate():
    if not _has_house22:
        pytest.skip("house-22 scene not present")
    r = client.post("/datasets/house-22/house-22-floorplan-eg.png/propose-wall-edit",
                    json={"apply": False})
    assert r.status_code == 400


def test_upsert_wall_anchored_refines_and_persists_readable_wall():
    key = "house-anchor-route"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.new("RGB", (360, 240), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.line([40, 100, 320, 100], fill=(0, 0, 0), width=18)
        img.save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        r = client.post(
            f"/datasets/{key}/{file}/wall-labels/anchored",
            json={
                "candidate": {"start": [50, 140], "end": [310, 140], "thickness_mm": 300},
                "anchor": {"search_px": 70, "min_confidence": 0.6, "min_overlap": 0.6},
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["persisted"] is True
        assert data["anchoring_status"] == "ink_anchored"
        assert abs(data["anchored"]["start"][1] - 100) <= 8
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        wall = doc["labels"][0]
        assert wall["status"] == "readable"
        assert wall["attributes"]["quality_status"] == "ink_anchored"
        assert wall["attributes"]["anchoring"]["ink_overlap"] >= 0.6
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upsert_wall_anchored_does_not_persist_failed_readable_wall():
    key = "house-anchor-reject"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (240, 180), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        r = client.post(
            f"/datasets/{key}/{file}/wall-labels/anchored",
            json={
                "candidate": {"start": [20, 80], "end": [220, 80]},
                "anchor": {"search_px": 40, "min_confidence": 0.8, "min_overlap": 0.6},
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["persisted"] is False
        assert data["anchoring_status"] == "failed"
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        assert doc["labels"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_raw_wall_write_strict_anchoring_rejects_off_ink_wall():
    key = "house-anchor-strict"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (240, 180), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        labels["labels"] = [{
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [20, 80], "end": [220, 80]},
            "attributes": {"anchoring_required": True},
        }]
        r = client.put(f"/labels/dataset/{key}/{file}", json=labels)
        assert r.status_code == 422
        assert "anchoring_required=true" in r.text
    finally:
        shutil.rmtree(root, ignore_errors=True)
