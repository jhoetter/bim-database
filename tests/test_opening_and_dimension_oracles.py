from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402


def _scene_root(key: str) -> Path:
    root = api_main.DATASET_DIR / key
    shutil.rmtree(root, ignore_errors=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    return root


def test_opening_candidates_detect_wall_gap_and_overlay_renders():
    key = "house-zzopening"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    img = Image.new("RGB", (520, 240), "white")
    draw = ImageDraw.Draw(img)
    draw.line([40, 120, 180, 120], fill="black", width=20)
    draw.line([245, 120, 480, 120], fill="black", width=20)
    img.save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [520, 240]
    labels["labels"] = [{
        "id": "wall-1",
        "type": "wall",
        "status": "readable",
        "geometry": {"start": [40, 120], "end": [480, 120]},
        "attributes": {"thickness_mm": 300},
    }]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        r = client.get(f"/datasets/{key}/{file}/opening-candidates")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        gaps = [c for c in data["candidates"] if c["kind"] == "wall_gap"]
        assert gaps
        assert gaps[0]["parent_wall_id"] == "wall-1"
        assert 40 <= gaps[0]["span_px"] <= 90
        candidate_id = gaps[0]["candidate_id"]

        overlay = client.get(
            f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/overlay"
        )
        assert overlay.status_code == 200, overlay.text
        assert overlay.headers["content-type"].startswith("image/png")

        stale_apply = client.post(
            f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/apply",
            json={
                "opening_kind": "door",
                "expected_candidate_fingerprint": "stale-fingerprint",
            },
        )
        assert stale_apply.status_code == 400
        assert "fingerprint changed" in stale_apply.text

        stale_decision = client.post(
            f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/decision",
            json={
                "outcome": "rejected_false_positive",
                "expected_candidate_fingerprint": "stale-fingerprint",
            },
        )
        assert stale_decision.status_code == 400
        assert "fingerprint changed" in stale_decision.text

        decision = client.post(
            f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/decision",
            json={
                "outcome": "accepted_uncertain",
                "note": "current candidate fingerprint accepted in test",
                "expected_candidate_fingerprint": gaps[0]["candidate_fingerprint"],
            },
        )
        assert decision.status_code == 200, decision.text

        apply = client.post(
            f"/datasets/{key}/{file}/opening-candidates/{candidate_id}/apply",
            json={
                "opening_kind": "door",
                "note": "accepted in test",
                "expected_candidate_fingerprint": gaps[0]["candidate_fingerprint"],
                "allow_plan_order_override": True,
                "override_reason": "oracle route test applies candidate outside the scene-plan workflow",
            },
        )
        assert apply.status_code == 200, apply.text
        label_id = apply.json()["data"]["label_id"]
        saved = client.get(f"/labels/dataset/{key}/{file}").json()
        label = next(l for l in saved["labels"] if l["id"] == label_id)
        assert label["type"] == "floorplan_opening"
        assert label["attributes"]["opening_kind"] == "door"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_dimension_station_graph_returns_spans_and_wall_anchor_context():
    key = "house-zzstation"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    img = Image.new("RGB", (500, 300), "white")
    draw = ImageDraw.Draw(img)
    draw.line([60, 220, 440, 220], fill="black", width=2)
    for x in [60, 180, 300, 440]:
        draw.line([x, 195, x, 245], fill="black", width=3)
    img.save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [500, 300]
    labels["labels"] = [{
        "id": "wall-left",
        "type": "wall",
        "status": "readable",
        "geometry": {"start": [60, 80], "end": [60, 240]},
        "attributes": {},
    }]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        r = client.get(
            f"/datasets/{key}/{file}/dimension-station-graph",
            params={"region": "20,170,470,270", "orientation": "horizontal"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["found"] is True
        assert data["station_count"] >= 4
        assert data["span_count"] >= 3
        assert data["groups"]
        assert data["reference_candidates"]
        assert any(st["nearest_wall_id"] == "wall-left" for st in data["stations"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_upsert_opening_on_wall_derives_quad_and_returns_local_qa():
    key = "house-zzopeningtxn"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    Image.new("RGB", (520, 240), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [520, 240]
    labels["labels"] = [{
        "id": "wall-1",
        "type": "wall",
        "status": "readable",
        "geometry": {"start": [40, 120], "end": [480, 120]},
        "attributes": {"thickness_mm": 300},
    }]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        r = client.post(
            f"/datasets/{key}/{file}/openings/on-wall",
            json={
                "parent_wall_id": "wall-1",
                "span_start": [180, 120],
                "span_end": [245, 120],
                "opening_kind": "door",
                "wall_half_width_px": 10,
                "allow_plan_order_override": True,
                "override_reason": "transaction route test",
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["persisted"] is True
        assert data["local_qa"]["ok"] is True
        saved = client.get(f"/labels/dataset/{key}/{file}").json()
        op = next(l for l in saved["labels"] if l["type"] == "floorplan_opening")
        assert op["relations"] == [{"kind": "belongs_to", "other_id": "wall-1"}]
        assert op["attributes"]["qa_status"] == "passed"
        assert op["attributes"]["parent_wall_id"] == "wall-1"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_dimension_chain_transaction_writes_pairs_and_calibration_provenance():
    key = "house-zzdimtxn"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    Image.new("RGB", (500, 300), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "ansicht"
    labels["scene_orientation"] = "north"
    labels["image_size_px"] = [500, 300]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        r = client.post(
            f"/datasets/{key}/{file}/dimension-chain-transaction",
            json={
                "orientation": "horizontal",
                "dimension_semantic": "site_setback",
                "calibration_role": "site_metric",
                "calibration_confidence": "medium",
                "overall_value_mm": 6000,
                "spans": [
                    {
                        "span_id": "DSP-001",
                        "start": [50, 220],
                        "end": [250, 220],
                        "value_mm": 3000,
                        "dimension_text": "3,00",
                        "is_reference": True,
                        "reference_review": "site line used only as approximate calibration",
                    },
                    {
                        "span_id": "DSP-002",
                        "start": [250, 220],
                        "end": [450, 220],
                        "value_mm": 3000,
                        "dimension_text": "3,00",
                    },
                ],
            },
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["sum_check"]["ok"] is True
        assert len(data["written"]) == 2
        cal = data["calibration_after"]
        assert cal["calibration_approximate"] is True
        assert cal["calibration_roles"] == ["site_metric"]
        assert cal["calibration_source_semantics"] == ["site_setback"]
        saved = client.get(f"/labels/dataset/{key}/{file}").json()
        assert len([l for l in saved["labels"] if l["type"] == "dimensioned_distance"]) == 2
        assert len([l for l in saved["labels"] if l["type"] == "dimension_number"]) == 2

        dim_id = data["written"][1]["dimension_id"]
        review = client.post(
            f"/datasets/{key}/{file}/reference-dim-review",
            json={
                "label_id": dim_id,
                "dimension_semantic": "building",
                "calibration_role": "building_metric",
                "calibration_confidence": "high",
                "review": "reviewed against building facade dimension",
                "evidence_ids": ["ev-1"],
            },
        )
        assert review.status_code == 200, review.text
        reviewed = review.json()["data"]
        assert reviewed["calibration_role"] == "building_metric"
        assert reviewed["calibration_after"]["calibration_roles"] == ["building_metric", "site_metric"]
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_view_geometry_candidates_route_returns_component_and_opening_priors():
    key = "house-zzviewcandidates"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    img = Image.new("RGB", (420, 260), "white")
    draw = ImageDraw.Draw(img)
    draw.line([40, 70, 360, 70], fill="black", width=3)
    draw.rectangle([160, 110, 220, 165], outline="black", width=3)
    img.save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "ansicht"
    labels["scene_orientation"] = "north"
    labels["image_size_px"] = [420, 260]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        r = client.get(f"/datasets/{key}/{file}/view-geometry-candidates")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["candidate_contract"] == "view-geometry-candidates/v1"
        assert any(c["kind"] == "component_line" for c in data["candidates"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_labeled_render_accepts_opening_visibility_modes():
    key = "house-zzrenderopen"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    Image.new("RGB", (240, 180), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [240, 180]
    labels["labels"] = [{
        "id": "op-1",
        "type": "floorplan_opening",
        "status": "readable",
        "geometry": {"quad": [[50, 50], [120, 50], [120, 70], [50, 70]]},
        "attributes": {"opening_kind": "door"},
        "relations": [{"kind": "belongs_to", "other_id": "wall-1"}],
    }]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        for mode in ("full", "outline", "hide"):
            r = client.get(
                f"/datasets/{key}/{file}/grid-with-labels",
                params={"show_openings": mode, "clean": "true"},
            )
            assert r.status_code == 200, r.text
            assert r.headers["content-type"].startswith("image/png")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_labeled_render_accepts_named_view_mode():
    key = "house-zzviewmode"
    file = f"{key}-scene.jpg"
    root = _scene_root(key)
    Image.new("RGB", (240, 180), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [240, 180]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    try:
        client = TestClient(api_main.app)
        r = client.get(
            f"/datasets/{key}/{file}/grid-with-labels",
            params={"view_mode": "topology_qa_view"},
        )
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("image/png")

        bad = client.get(
            f"/datasets/{key}/{file}/grid-with-labels",
            params={"view_mode": "not_a_mode"},
        )
        assert bad.status_code == 400
    finally:
        shutil.rmtree(root, ignore_errors=True)
