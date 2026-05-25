#!/usr/bin/env python3
"""Auto-derive `data_quality` for each house from its existing `images`
array + source field. Run once after adding a new house, or rerun whenever
images get re-categorized. Conservative — only sets the axes the heuristic
can reasonably infer; upgrades past 'dimensioned' / 'summary' require a
human read of the source PDFs and should be set manually.

Run via `make derive-quality`."""
import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent.parent
HOUSES = BASE / "data" / "houses"


def derive(rec: dict) -> dict:
    images = rec.get("images") or []
    cats = Counter(i["category"] for i in images)

    # ── floorplan grade ────────────────────────────────────────────────
    fp = cats.get("floorplan", 0)
    fp_with_floor = sum(1 for i in images if i["category"] == "floorplan" and i.get("floor"))
    if fp == 0:
        floorplan_grade = "none"
    elif rec.get("source") == "documentation" and fp_with_floor >= 1:
        # Documentation records ARE architectural-set plans (vermaßt + Wandstärken)
        floorplan_grade = "fully_specified"
    elif rec.get("source") == "catalog":
        # Catalog plans carry outer dimensions but no wall thicknesses
        floorplan_grade = "dimensioned"
    else:
        floorplan_grade = "room_labels"

    # ── exterior coverage ──────────────────────────────────────────────
    # Count photo/render exteriors + elevation drawings as facade evidence
    ext = cats.get("exterior", 0) + cats.get("elevation", 0)
    if ext == 0:
        exterior_coverage = "none"
    elif ext == 1:
        exterior_coverage = "single_view"
    elif ext <= 3:
        exterior_coverage = "multi_view"
    else:
        exterior_coverage = "all_facades"

    # ── elevation set ──────────────────────────────────────────────────
    elev = cats.get("elevation", 0)
    if elev == 0:
        elevation_set = "none"
    elif elev <= 2:
        elevation_set = "schematic"
    else:
        # Heuristic: 3+ separate elevation images usually means dimensioned
        elevation_set = "dimensioned"
    # Documentation records with elevations are usually dimensioned
    if rec.get("source") == "documentation" and elev > 0:
        elevation_set = "dimensioned"

    # ── presence flags ─────────────────────────────────────────────────
    section_drawing = "dimensioned" if (rec.get("source") == "documentation" and cats.get("section", 0)) \
                      else "schematic" if cats.get("section", 0) else "none"
    roof_plan = "present" if cats.get("roof_plan", 0) else "absent"
    site_plan = "present" if cats.get("site_plan", 0) else "absent"

    # ── construction specs ─────────────────────────────────────────────
    if rec.get("construction"):
        construction_specs = "summary"
    else:
        construction_specs = "none"
    # Documentation records often have a Baubeschreibung in source_pdfs
    if rec.get("source") == "documentation":
        sorg = (rec.get("source_origin") or "").lower()
        if "baubeschreibung" in sorg:
            construction_specs = "full_baubeschreibung"

    return {
        "floorplan_grade": floorplan_grade,
        "exterior_coverage": exterior_coverage,
        "elevation_set": elevation_set,
        "section_drawing": section_drawing,
        "roof_plan": roof_plan,
        "site_plan": site_plan,
        "construction_specs": construction_specs,
    }


def merge_into(existing: dict | None, derived: dict) -> dict:
    """Preserve any human-set axis that's HIGHER than what we'd derive.
    Heuristic: if a field is set to a value that the heuristic wouldn't
    reach (fully_specified / construction_grade / wall_buildup /
    full_baubeschreibung), keep the human value."""
    if not existing:
        return derived
    HUMAN_ONLY = {"fully_specified", "construction_grade", "wall_buildup", "full_baubeschreibung"}
    out = dict(derived)
    for k, v in existing.items():
        if v in HUMAN_ONLY:
            out[k] = v
    return out


def main():
    n_changed = 0
    for p in sorted(HOUSES.glob("house-*/house-*.json"),
                    key=lambda q: int(q.stem.split("-")[1])):
        rec = json.loads(p.read_text())
        new = merge_into(rec.get("data_quality"), derive(rec))
        if rec.get("data_quality") != new:
            rec["data_quality"] = new
            p.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n")
            n_changed += 1
    print(f"updated data_quality on {n_changed} record(s)")


if __name__ == "__main__":
    main()
