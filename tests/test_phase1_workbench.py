from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main  # noqa: E402


def _scene_root(key: str) -> Path:
    root = api_main.DATASET_DIR / key
    shutil.rmtree(root, ignore_errors=True)
    (root / "labels").mkdir(parents=True, exist_ok=True)
    return root


def test_workbench_state_is_compact_next_action_entrypoint() -> None:
    key = "house-zzworkbench"
    file = f"{key}-eg.png"
    root = _scene_root(key)
    Image.new("RGB", (320, 220), "white").save(root / file)
    labels = api_main._label_skeleton("dataset", key, file)
    labels["scene_tag"] = "grundriss"
    labels["scene_level"] = "eg"
    labels["image_size_px"] = [320, 220]
    labels["labels"] = [{
        "id": "wall-1",
        "type": "wall",
        "status": "readable",
        "geometry": {"start": [40, 120], "end": [280, 120]},
        "attributes": {"thickness_mm": 300},
    }]
    (root / "labels" / f"{Path(file).stem}.json").write_text(json.dumps(labels))
    (root / "manifest.json").write_text(json.dumps({
        "key": key,
        "drawings": [{
            "file": file,
            "kind": "floorplan",
            "floor": "eg",
            "crop_warnings": [{
                "kind": "crop_regression",
                "severity": "warning",
                "message": "new crop is tighter than prior crop",
            }],
        }],
    }))
    try:
        client = TestClient(api_main.app)
        created = client.post(
            f"/datasets/{key}/{file}/plan-state/template",
            json={"scene_tag": "grundriss", "level_or_orientation": "eg"},
        )
        assert created.status_code == 200, created.text

        r = client.get(f"/datasets/{key}/{file}/plan-state/workbench")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["workbench_contract"] == "scene-workbench-state/v1"
        assert data["key"] == key
        assert data["file"] == file
        assert data["plan"]["exists"] is True
        assert data["next_action"]
        assert data["recommended_view_mode"] in {
            "analysis_view",
            "silhouette_view",
            "coordinate_pick_view",
            "topology_qa_view",
            "measurement_read_view",
            "opening_candidate_view",
            "edit_verify_view",
        }
        assert data["labels_summary"]["total"] == 1
        assert data["labels_summary"]["by_type"] == {"wall": 1}
        assert data["labels_summary"]["mass_groups"] == []
        assert data["semantic_exclusions_summary"]["available"] is True
        assert "transaction_history" in data
        assert "allowed_tools" in data
        assert "required_evidence" in data
        assert data["crop_warnings"][0]["kind"] == "crop_regression"
        assert "candidate_queue_summary" in data
        assert "markdown" not in data
        assert "tasks" not in data
        assert "defects" not in data
    finally:
        shutil.rmtree(root, ignore_errors=True)
