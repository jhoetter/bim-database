#!/usr/bin/env python3
"""Validate every data/houses/*.json against schema/house.schema.json and
verify that all enum values come from data/ontology.json. Run via
`make validate`."""
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
HOUSES = BASE / "data" / "houses"
SYNTHETIC = BASE / "data" / "synthetic"
ONTOLOGY = json.loads((BASE / "data" / "ontology.json").read_text())
SCHEMA = json.loads((BASE / "schema" / "house.schema.json").read_text())
LABELS_SCHEMA_PATH = BASE / "schema" / "scene_labels.schema.json"
LABELS_SCHEMA = json.loads(LABELS_SCHEMA_PATH.read_text()) if LABELS_SCHEMA_PATH.exists() else None

try:
    import jsonschema
except ImportError:
    print("jsonschema not installed (pip install jsonschema). Falling back to enum-only checks.")
    jsonschema = None

# Field → ontology section
ENUM_FIELDS = {
    "source":          "sources",
    "building_type":   "building_types",
    "construction":    "constructions",
    "roof_type":       "roof_types",
    "style":           "styles",
    "energy_standard": "energy_standards",
}
IMAGE_ENUM_FIELDS = {
    "category": "image_categories",
    "medium":   "image_mediums",
    "view":     "image_views",
    "floor":    "levels",
}


def check_enums(rec: dict, name: str) -> list[str]:
    errs = []
    for field, group in ENUM_FIELDS.items():
        v = rec.get(field)
        if v is None: continue
        if v not in ONTOLOGY[group]:
            errs.append(f"{name}: {field}={v!r} not in ontology.{group}")
    for level in rec.get("levels") or []:
        if level not in ONTOLOGY["levels"]:
            errs.append(f"{name}: levels[] value {level!r} not in ontology.levels")
    for i, img in enumerate(rec.get("images") or []):
        for field, group in IMAGE_ENUM_FIELDS.items():
            v = img.get(field)
            if v is None: continue
            if v not in ONTOLOGY[group]:
                errs.append(f"{name}: images[{i}].{field}={v!r} not in ontology.{group}")
    return errs


def check_id_matches_filename(rec: dict, path: Path) -> list[str]:
    expected = int(path.stem.split("-")[1])
    if rec.get("id") != expected:
        return [f"{path.name}: id={rec.get('id')} but filename suggests {expected}"]
    # Also assert the parent folder matches.
    if path.parent.name != f"house-{expected}":
        return [f"{path.name}: lives under {path.parent.name}/, expected house-{expected}/"]
    return []


def check_image_files_exist(rec: dict, name: str, folder: Path) -> list[str]:
    errs = []
    for i, img in enumerate(rec.get("images") or []):
        p = folder / img["file"]
        if not p.exists():
            errs.append(f"{name}: images[{i}].file {img['file']!r} not found in {folder.name}/")
    return errs


def check_modelable_support(rec: dict, name: str) -> list[str]:
    """Warn (not fail) when a house claims to be assessed-modelable
    (bim_ai_blocking_issues=[]) but the source data is too thin to back it
    up — i.e. data_quality is missing or below T2."""
    if rec.get("bim_ai_blocking_issues") != []:
        return []
    dq = rec.get("data_quality") or {}
    fp = dq.get("floorplan_grade", "none")
    ext = dq.get("exterior_coverage", "none")
    if fp == "none" and ext == "none":
        return [f"{name}: WARN — claims modelable but data_quality is empty (no floorplan, no exterior). Run `make derive-quality`."]
    if fp == "none" or fp == "room_labels":
        return [f"{name}: WARN — claims modelable but floorplan_grade={fp!r}. The 'modelable' verdict is undersupported."]
    return []


def main():
    all_errs: list[str] = []
    warnings: list[str] = []
    files = sorted(HOUSES.glob("house-*/house-*.json"),
                   key=lambda q: int(q.stem.split("-")[1]))
    for path in files:
        rec = json.loads(path.read_text())
        name = path.name
        if jsonschema:
            try:
                jsonschema.validate(rec, SCHEMA)
            except jsonschema.ValidationError as e:
                all_errs.append(f"{name}: schema: {e.message} at {list(e.absolute_path)}")
        all_errs += check_id_matches_filename(rec, path)
        all_errs += check_enums(rec, name)
        all_errs += check_image_files_exist(rec, name, path.parent)
        warnings += check_modelable_support(rec, name)

    # Scene-label files (M1+) — validate every data/{houses,synthetic}/<key>/labels/*.json
    label_files: list[Path] = []
    for root in (SYNTHETIC, HOUSES):
        if root.exists():
            label_files.extend(root.glob("*/labels/*.json"))
    label_files.sort()
    label_count = 0
    for lp in label_files:
        try:
            labels = json.loads(lp.read_text())
        except json.JSONDecodeError as e:
            all_errs.append(f"{lp.relative_to(BASE)}: invalid JSON ({e})")
            continue
        if jsonschema and LABELS_SCHEMA:
            try:
                jsonschema.validate(labels, LABELS_SCHEMA)
                label_count += 1
            except jsonschema.ValidationError as e:
                all_errs.append(f"{lp.relative_to(BASE)}: schema: {e.message} at {list(e.absolute_path)}")
        else:
            label_count += 1

    if all_errs:
        for e in all_errs:
            print("✗", e)
        print(f"\n{len(all_errs)} issue(s) across {len(files)} record(s) + {len(label_files)} label file(s)")
        sys.exit(1)
    for w in warnings:
        print("⚠", w)
    suffix = f" ({len(warnings)} warning(s))" if warnings else ""
    label_suffix = f", {label_count} label file(s)" if label_count else ""
    print(f"✓ {len(files)} records valid{label_suffix}{suffix}")


if __name__ == "__main__":
    main()
