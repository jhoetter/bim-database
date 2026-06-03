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
                "run_id": "anchor-run-1",
                "agent_id": "orchestrator",
                "subagent_id": "anchored-wall-worker",
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
        assert wall["run_id"] == "anchor-run-1"
        assert wall["agent_id"] == "orchestrator"
        assert wall["subagent_id"] == "anchored-wall-worker"
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


def test_upsert_rect_mass_persists_grouped_walls_idempotently():
    key = "house-rect-mass"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (320, 240), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        body = {
            "mass_id": "garage-1",
            "kind": "detached_garage",
            "bbox": [40, 50, 260, 190],
            "edge_policy": "use_given",
            "thickness_mm": 300,
        }
        first = client.post(f"/datasets/{key}/{file}/wall-masses/rect", json=body)
        assert first.status_code == 200, first.text
        data = first.json()["data"]
        assert data["mass_contract"] == "wall-mass-transaction/v1"
        assert data["mass_id"] == "garage-1"
        assert len(data["wall_label_ids"]) == 4
        assert not data["rejected_edges"]
        verification = data["transaction_verification"]
        assert verification["verification_contract"] == "wall-mass-transaction-verification/v1"
        assert verification["view_mode"] == "topology_qa_view"
        assert len(verification["source_edges"]) == 4
        assert len(verification["after_edges"]) == 4
        assert len(verification["accepted_edges"]) == 4
        assert verification["rejected_edges"] == []

        second = client.post(f"/datasets/{key}/{file}/wall-masses/rect", json=body)
        assert second.status_code == 200, second.text
        second_data = second.json()["data"]
        assert second_data["wall_label_ids"] == data["wall_label_ids"]
        assert len(second_data["transaction_verification"]["before_edges"]) == 4
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        walls = [lab for lab in doc["labels"] if lab["type"] == "wall"]
        assert len(walls) == 4
        assert {w["attributes"]["mass_id"] for w in walls} == {"garage-1"}
        assert {w["attributes"]["endpoint_reason_start"] for w in walls} == {"mass_corner"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upsert_stepped_mass_persists_l_shape():
    key = "house-stepped-mass"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (360, 300), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        body = {
            "mass_id": "main-l",
            "kind": "main_house",
            "ordered_vertices": [[40, 40], [260, 40], [260, 120], [160, 120], [160, 220], [40, 220]],
            "edge_policy": "use_given",
            "thickness_mm": 300,
        }
        r = client.post(f"/datasets/{key}/{file}/wall-masses/stepped", json=body)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert len(data["wall_label_ids"]) == 6
        assert data["topology_summary"]["connected_components"] >= 1
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        walls = [lab for lab in doc["labels"] if lab["type"] == "wall"]
        assert len(walls) == 6
        assert sorted(w["attributes"]["mass_edge_index"] for w in walls) == list(range(6))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_manual_wall_write_warns_on_missing_endpoint_reasons_and_disconnected_component():
    key = "house-detail-wall-warning"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.new("RGB", (420, 260), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.line([40, 80, 180, 80], fill=(0, 0, 0), width=18)
        draw.line([260, 180, 390, 180], fill=(0, 0, 0), width=18)
        img.save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        labels["labels"] = [{
            "id": "wall-existing",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [40, 80], "end": [180, 80]},
            "attributes": {"thickness_mm": 300},
        }]
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        r = client.post(
            f"/datasets/{key}/{file}/wall-labels/anchored",
            json={
                "candidate": {"start": [260, 178], "end": [390, 178], "thickness_mm": 300},
                "anchor": {"search_px": 24, "min_confidence": 0.5, "min_overlap": 0.5},
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["persisted"] is True
        assert "warnings" in data
        assert any("endpoint_reasons" in warning for warning in data["warnings"])
        assert data["disconnected_component_warning"]["after_components"] > data["disconnected_component_warning"]["before_components"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_detail_wall_mode_requires_evidence_and_endpoint_reasons():
    key = "house-detail-wall-mode"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.new("RGB", (320, 180), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.line([40, 90, 280, 90], fill=(0, 0, 0), width=18)
        img.save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        missing_evidence = client.post(
            f"/datasets/{key}/{file}/wall-labels/anchored",
            json={
                "detail_mode": "detail_refinement",
                "candidate": {
                    "start": [45, 92],
                    "end": [275, 92],
                    "endpoint_reasons": {"start": "t_junction", "end": "real_endpoint"},
                },
            },
        )
        assert missing_evidence.status_code == 400
        assert "detail_mode requires evidence_id" in missing_evidence.text

        missing_reasons = client.post(
            f"/datasets/{key}/{file}/wall-labels/anchored",
            json={
                "detail_mode": "detail_refinement",
                "evidence_id": "EV-001",
                "candidate": {"start": [45, 92], "end": [275, 92]},
            },
        )
        assert missing_reasons.status_code == 400
        assert "endpoint_reasons.start/end" in missing_reasons.text

        ok = client.post(
            f"/datasets/{key}/{file}/wall-labels/anchored",
            json={
                "detail_mode": "detail_refinement",
                "evidence_id": "EV-001",
                "candidate": {
                    "start": [45, 92],
                    "end": [275, 92],
                    "endpoint_reasons": {"start": "t_junction", "end": "real_endpoint"},
                },
                "anchor": {"search_px": 24, "min_confidence": 0.5, "min_overlap": 0.5},
            },
        )
        assert ok.status_code == 200, ok.text
        data = ok.json()["data"]
        assert data["persisted"] is True
        assert data["detail_mode"] == "detail_refinement"
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        assert doc["labels"][0]["attributes"]["detail_mode"] == "detail_refinement"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_classify_ink_region_excludes_site_boundary_from_structural_score():
    key = "house-semantic-score"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        img = Image.new("RGB", (420, 220), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.line([40, 80, 240, 80], fill=(0, 0, 0), width=14)
        draw.line([270, 160, 390, 160], fill=(0, 0, 0), width=14)
        img.save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        labels["labels"] = [{
            "id": "wall-1",
            "type": "wall",
            "status": "readable",
            "geometry": {"start": [40, 80], "end": [240, 80]},
            "attributes": {"thickness_mm": 300},
        }]
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        raw = client.get(
            f"/datasets/{key}/{file}/score-walls",
            params={"min_wall_px": 8, "tol_px": 9},
        )
        assert raw.status_code == 200, raw.text
        assert raw.json()["data"]["missing_regions"]

        classified = client.post(
            f"/datasets/{key}/{file}/classify-ink-region",
            json={
                "region": [250, 145, 160, 35],
                "bbox_format": "xywh",
                "semantic_class": "site_boundary",
                "confidence": "high",
                "summary": "Baugrenze/site line, not structural wall",
            },
        )
        assert classified.status_code == 200, classified.text
        assert classified.json()["data"]["applies_to_wall_score"] is True

        structural = client.get(
            f"/datasets/{key}/{file}/score-walls-structural",
            params={"min_wall_px": 8, "tol_px": 9},
        )
        assert structural.status_code == 200, structural.text
        data = structural.json()["data"]
        assert data["score_contract"] == "score-walls-structural/v1"
        assert data["semantic_exclusion_count"] == 1
        assert data["missing_region_count"] == 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_classify_ink_region_infers_corner_bbox_when_xywh_would_exceed_bounds():
    key = "house-semantic-xyxy"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (220, 160), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        classified = client.post(
            f"/datasets/{key}/{file}/classify-ink-region",
            json={
                "region": [170, 100, 210, 140],
                "semantic_class": "hatching_projection",
                "confidence": "high",
                "summary": "grid-corner bbox, not xywh",
            },
        )
        assert classified.status_code == 200, classified.text
        data = classified.json()["data"]
        assert data["bbox_format"] == "xyxy"
        assert data["bbox_xyxy"] == [170.0, 100.0, 210.0, 140.0]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_classify_ink_region_rejects_ambiguous_or_explicit_out_of_bounds_bbox():
    key = "house-semantic-ambiguous"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (220, 160), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        ambiguous = client.post(
            f"/datasets/{key}/{file}/classify-ink-region",
            json={"region": [10, 10, 20, 20], "semantic_class": "ignored_noise"},
        )
        assert ambiguous.status_code == 400
        assert "ambiguous" in ambiguous.text

        explicit_bad = client.post(
            f"/datasets/{key}/{file}/classify-ink-region",
            json={"region": [170, 100, 210, 140], "bbox_format": "xywh", "semantic_class": "ignored_noise"},
        )
        assert explicit_bad.status_code == 400
        assert "outside image bounds" in explicit_bad.text
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_confirmed_rect_mass_with_low_confidence_does_not_persist_uncertain_walls():
    key = "house-rect-mass-low-conf"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (320, 240), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        body = {
            "mass_id": "garage-ambiguous",
            "kind": "detached_garage",
            "bbox": [40, 50, 260, 190],
            "edge_policy": "refine_to_ink",
            "min_confidence": 0.75,
            "thickness_mm": 180,
        }
        r = client.post(f"/datasets/{key}/{file}/wall-masses/rect", json=body)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["persisted"] is False
        assert data["rejected_edges"]
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        assert doc["labels"] == []
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_uncertain_rect_mass_persists_uncertain_edges_explicitly():
    key = "house-rect-mass-uncertain"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (320, 240), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        body = {
            "mass_id": "garage-hypothesis",
            "kind": "detached_garage",
            "mass_mode": "partial_mass_hypothesis",
            "bbox": [40, 50, 260, 190],
            "edge_policy": "refine_to_ink",
            "min_confidence": 0.75,
            "thickness_mm": 180,
        }
        r = client.post(f"/datasets/{key}/{file}/wall-masses/rect", json=body)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["persisted"] is True
        assert data["mass_mode"] == "partial_mass_hypothesis"
        doc = client.get(f"/labels/dataset/{key}/{file}").json()
        walls = [lab for lab in doc["labels"] if lab["type"] == "wall"]
        assert len(walls) == 4
        assert {lab["status"] for lab in walls} == {"uncertain"}
        assert {lab["attributes"]["mass_mode"] for lab in walls} == {"partial_mass_hypothesis"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_confirmed_mass_overlapping_semantic_projection_requires_override():
    key = "house-rect-mass-semantic-overlap"
    file = f"{key}-scene.png"
    root = api_main.DATASET_DIR / key
    root.mkdir(parents=True, exist_ok=True)
    try:
        Image.new("RGB", (320, 240), (255, 255, 255)).save(root / file)
        labels = api_main._label_skeleton("dataset", key, file)
        labels["scene_tag"] = "grundriss"
        labels["scene_level"] = "eg"
        assert client.put(f"/labels/dataset/{key}/{file}", json=labels).status_code == 200

        classified = client.post(
            f"/datasets/{key}/{file}/classify-ink-region",
            json={
                "region": [60, 60, 240, 180],
                "bbox_format": "xyxy",
                "semantic_class": "hatching_projection",
                "confidence": "high",
            },
        )
        assert classified.status_code == 200, classified.text

        body = {
            "mass_id": "garage-projection",
            "kind": "detached_garage",
            "bbox": [40, 50, 260, 190],
            "edge_policy": "use_given",
            "thickness_mm": 180,
            "allow_plan_order_override": True,
            "override_reason": "test semantic overlap guard",
        }
        blocked = client.post(f"/datasets/{key}/{file}/wall-masses/rect", json=body)
        assert blocked.status_code == 400
        assert "semantic context" in blocked.text

        allowed = client.post(
            f"/datasets/{key}/{file}/wall-masses/rect",
            json={**body, "mass_mode": "partial_mass_hypothesis"},
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["data"]["warnings"]
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
