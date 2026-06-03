#!/usr/bin/env python3
"""Audit and repair crop/region/mass guardrail issues for one house.

This is intentionally file-based so it can be run before the API is healthy or
after a bad agent run has left plan JSON in a misleading state.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path
from typing import Any

from PIL import Image


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _image_size(house_dir: Path, file_name: str) -> tuple[int, int] | None:
    try:
        with Image.open(house_dir / file_name) as img:
            return int(img.width), int(img.height)
    except Exception:  # noqa: BLE001
        return None


def _xywh(vals: list[float]) -> list[float]:
    x, y, w, h = vals
    return [x, y, x + max(0.0, w), y + max(0.0, h)]


def _xyxy(vals: list[float]) -> list[float]:
    x0, y0, x1, y1 = vals
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def _in_bounds(box: list[float], size: tuple[int, int] | None) -> bool:
    if box[2] <= box[0] or box[3] <= box[1]:
        return False
    if size is None:
        return True
    return box[0] >= 0 and box[1] >= 0 and box[2] <= size[0] and box[3] <= size[1]


def _label_bbox(label: dict[str, Any]) -> list[float] | None:
    geom = label.get("geometry") or {}
    pts: list[list[float]] = []
    if label.get("type") == "wall":
        pts.extend(p for p in (geom.get("start"), geom.get("end")) if isinstance(p, list) and len(p) >= 2)
    elif label.get("type") == "floorplan_opening":
        pts.extend(p for p in geom.get("quad") or [] if isinstance(p, list) and len(p) >= 2)
    elif label.get("type") == "dimensioned_distance":
        pts.extend(p for p in (geom.get("start"), geom.get("end")) if isinstance(p, list) and len(p) >= 2)
    elif label.get("type") == "dimension_number":
        if isinstance(geom.get("anchor"), list):
            pts.append(geom["anchor"])
        pts.extend(p for p in geom.get("bbox") or [] if isinstance(p, list) and len(p) >= 2)
    if not pts:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def audit_house(
    dataset_dir: Path,
    key: str,
    *,
    repair_semantic_regions: bool = False,
    baseline_manifest: Path | None = None,
) -> dict[str, Any]:
    house_dir = dataset_dir / key
    manifest_path = house_dir / "manifest.json"
    manifest = _read_json(manifest_path) if manifest_path.exists() else {"drawings": []}
    baseline = _read_json(baseline_manifest) if baseline_manifest and baseline_manifest.exists() else None
    baseline_by_file = {d.get("file"): d for d in (baseline or {}).get("drawings", []) if isinstance(d, dict)}
    findings: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []

    for drawing in manifest.get("drawings") or []:
        file_name = drawing.get("file")
        if not file_name:
            continue
        size = _image_size(house_dir, file_name)
        for warning in drawing.get("crop_warnings") or []:
            findings.append({"file": file_name, "kind": "crop_warning", "severity": "warning", "warning": warning})
        prior = baseline_by_file.get(file_name)
        if prior:
            old_bbox = ((prior.get("crop_from") or {}).get("bbox_pdf_units")) or []
            new_bbox = ((drawing.get("crop_from") or {}).get("bbox_pdf_units")) or []
            if len(old_bbox) == 4 and len(new_bbox) == 4:
                old_area = max(0.0, old_bbox[2] - old_bbox[0]) * max(0.0, old_bbox[3] - old_bbox[1])
                new_area = max(0.0, new_bbox[2] - new_bbox[0]) * max(0.0, new_bbox[3] - new_bbox[1])
                if old_area > 0 and new_area / old_area < 0.75:
                    findings.append({
                        "file": file_name,
                        "kind": "crop_regression_against_baseline",
                        "severity": "warning",
                        "area_ratio": round(new_area / old_area, 3),
                        "prior_bbox_pdf_units": old_bbox,
                        "new_bbox_pdf_units": new_bbox,
                    })

        labels_path = house_dir / "labels" / f"{Path(file_name).stem}.json"
        if labels_path.exists():
            labels_doc = _read_json(labels_path)
            for label in labels_doc.get("labels") or []:
                bbox = _label_bbox(label)
                if size and bbox and not _in_bounds(bbox, size):
                    findings.append({"file": file_name, "kind": "label_out_of_bounds", "severity": "blocker", "label_id": label.get("id"), "bbox_xyxy": bbox, "image_size_px": list(size)})
                attrs = label.get("attributes") or {}
                if attrs.get("mass_id") and label.get("status") == "uncertain":
                    findings.append({"file": file_name, "kind": "uncertain_mass_edge", "severity": "warning", "label_id": label.get("id"), "mass_id": attrs.get("mass_id"), "edge_confidence": attrs.get("edge_confidence")})

        plan_path = house_dir / "plans" / f"{Path(file_name).stem}.plan.json"
        if not plan_path.exists():
            continue
        plan_doc = _read_json(plan_path)
        state = plan_doc.get("state") or {}
        changed = False
        for evidence in state.get("evidence") or []:
            if evidence.get("kind") != "semantic_ink_region":
                continue
            result = evidence.get("result") or {}
            region = result.get("region")
            if not (isinstance(region, list) and len(region) >= 4):
                continue
            vals = [float(v) for v in region[:4]]
            fmt = result.get("bbox_format") or "xywh"
            stored = result.get("bbox_xyxy")
            if isinstance(stored, list) and len(stored) >= 4 and _in_bounds([float(v) for v in stored[:4]], size):
                continue
            xywh_box = _xywh(vals)
            xyxy_box = _xyxy(vals)
            if fmt == "xywh" and not _in_bounds(xywh_box, size) and _in_bounds(xyxy_box, size):
                findings.append({"file": file_name, "kind": "semantic_region_likely_xyxy_stored_as_xywh", "severity": "warning", "evidence_id": evidence.get("id"), "region": vals, "bbox_xywh_interpretation": xywh_box, "bbox_xyxy_interpretation": xyxy_box})
                if repair_semantic_regions:
                    result["bbox_format"] = "xyxy"
                    result["bbox_xyxy"] = xyxy_box
                    result.setdefault("repair_history", []).append({
                        "tool": "scripts/guardrail_house_audit.py",
                        "action": "converted_xywh_to_xyxy",
                        "timestamp": int(time.time()),
                    })
                    changed = True
                    repairs.append({"file": file_name, "evidence_id": evidence.get("id"), "bbox_xyxy": xyxy_box})
            elif not _in_bounds(xywh_box if fmt == "xywh" else xyxy_box, size):
                findings.append({"file": file_name, "kind": "semantic_region_out_of_bounds", "severity": "warning", "evidence_id": evidence.get("id"), "region": vals, "bbox_format": fmt})
        if changed:
            backup = plan_path.with_suffix(plan_path.suffix + f".bak-{int(time.time())}")
            shutil.copy2(plan_path, backup)
            _write_json(plan_path, plan_doc)

    return {
        "key": key,
        "findings": findings,
        "repairs": repairs,
        "count": len(findings),
        "repair_count": len(repairs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key")
    parser.add_argument("--dataset-dir", default="data/dataset")
    parser.add_argument("--repair-semantic-regions", action="store_true")
    parser.add_argument("--baseline-manifest")
    args = parser.parse_args()
    report = audit_house(
        Path(args.dataset_dir),
        args.key,
        repair_semantic_regions=args.repair_semantic_regions,
        baseline_manifest=Path(args.baseline_manifest) if args.baseline_manifest else None,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
