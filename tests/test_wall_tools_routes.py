"""Route-level tests for the wall-geometry HTTP endpoints (bim-agent tracker
B2 — exposing the tools over HTTP/MCP). The underlying logic is unit-tested in
test_wall_geometry.py; these check param parsing, the {ok,data} envelope, and
persistence behavior."""
import os

import pytest
from fastapi.testclient import TestClient

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
