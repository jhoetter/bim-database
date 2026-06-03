from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from scripts.guardrail_house_audit import audit_house


def test_guardrail_audit_repairs_xyxy_region_stored_as_xywh(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    key = "house-guardrail-audit"
    house = dataset / key
    (house / "plans").mkdir(parents=True)
    (house / "labels").mkdir()
    file_name = f"{key}-floorplan-dg.png"
    Image.new("RGB", (2751, 1959), "white").save(house / file_name)
    (house / "manifest.json").write_text(json.dumps({
        "key": key,
        "drawings": [{"file": file_name, "kind": "floorplan"}],
    }))
    (house / "labels" / f"{Path(file_name).stem}.json").write_text(json.dumps({"labels": []}))
    plan_path = house / "plans" / f"{Path(file_name).stem}.plan.json"
    plan_path.write_text(json.dumps({
        "state": {
            "evidence": [{
                "id": "EV-045",
                "kind": "semantic_ink_region",
                "result": {
                    "semantic_class": "landscape_vehicle",
                    "region": [2027, 1451, 2095, 1645],
                    "bbox_format": "xywh",
                },
            }],
        },
    }))

    dry = audit_house(dataset, key)
    assert dry["repair_count"] == 0
    assert dry["findings"][0]["kind"] == "semantic_region_likely_xyxy_stored_as_xywh"

    repaired = audit_house(dataset, key, repair_semantic_regions=True)
    assert repaired["repair_count"] == 1
    data = json.loads(plan_path.read_text())
    result = data["state"]["evidence"][0]["result"]
    assert result["bbox_format"] == "xyxy"
    assert result["bbox_xyxy"] == [2027.0, 1451.0, 2095.0, 1645.0]
    assert result["repair_history"][0]["action"] == "converted_xywh_to_xyxy"
