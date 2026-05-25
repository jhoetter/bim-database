#!/usr/bin/env python3
"""Validate every data/houses/*.json against schema/house.schema.json and
verify that all enum values come from data/ontology.json. Run via
`make validate`."""
import json
import sys
from pathlib import Path

BASE = Path(__file__).parent.parent
HOUSES = BASE / "data" / "houses"
ONTOLOGY = json.loads((BASE / "data" / "ontology.json").read_text())
SCHEMA = json.loads((BASE / "schema" / "house.schema.json").read_text())

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


def main():
    all_errs: list[str] = []
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

    if all_errs:
        for e in all_errs:
            print("✗", e)
        print(f"\n{len(all_errs)} issue(s) across {len(files)} record(s)")
        sys.exit(1)
    print(f"✓ {len(files)} records valid")


if __name__ == "__main__":
    main()
