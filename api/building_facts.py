"""Building-global facts tier + deterministic derivation (issue #8).

Höhenkoten (FH/TH/DG/EG/UG/Bezug), the müNN datum, roof pitch, Kniestock
and storey heights are properties of the *building*, not of a single
view — identical on every facade. They're typically legible exactly once
(usually in the Schnitt) and should be available on every Ansicht /
Schnitt of the house.

This module holds:
  - the building-global facts *tier* shape — each value carries provenance
    (which scene + which label it was read from) and a confidence;
  - `derive_building_geometry` — DETERMINISTIC math on top of the anchors
    (müNN ↔ relative, storey heights from level deltas, roof rise from
    pitch + run). Derived facts are flagged `derived: true` and
    `needs_cross_check: true` — they are computed, not read, and a human
    or vision pass should confirm them.

Storage lives under `house_facts.json["building_global"]`:

    "building_global": {
      "schema": 1,
      "facts": {
        "FH_mm":        {"value": 7210, "unit": "mm",
                          "source": {"scene": "...jpg", "label_id": "hm:lab-.."},
                          "confidence": "high"},
        "EG_munn_mm":   {"value": 843800, "unit": "mm", "source": {...},
                          "confidence": "high"},
        "roof_pitch_deg": {"value": 30, "unit": "deg", ...},
        "kniestock_mm":  {"value": 1250, "unit": "mm", ...}
      }
    }

Pure functions throughout — no disk I/O, no LLM. The MCP layer reads the
stored tier, calls `derive_building_geometry`, and presents both.
"""
from __future__ import annotations

import math
from typing import Any

SCHEMA = 1

CONFIDENCE_LEVELS = ("low", "medium", "high")
PROVENANCE_QUALITY_LEVELS = (
    "direct_read",
    "derived",
    "transferred",
    "conflicting",
    "review_required",
)

# Relative-height fact names (measured from EG ±0.00), in bottom→top order.
# Used both to validate fact names and to compute storey deltas.
RELATIVE_HEIGHT_FACTS = ("UG_mm", "EG_mm", "OG_mm", "DG_mm", "TH_mm", "FH_mm")

# Ordered floor levels for storey-height deltas.
LEVEL_ORDER = ("UG_mm", "EG_mm", "OG_mm", "DG_mm")

# The full recognized fact vocabulary the setter accepts.
KNOWN_FACTS = set(RELATIVE_HEIGHT_FACTS) | {
    "EG_munn_mm",        # müNN datum: absolute elevation of EG ±0.00
    "bezug_mm",          # reference datum (usually 0)
    "first_mm",          # first-floor height (legacy heights.first_mm twin)
    "roof_pitch_deg",    # Dachneigung
    "kniestock_mm",      # Kniestock (knee-wall)
    "ridge_munn_mm",     # absolute ridge elevation, if read directly
}


def make_fact(
    value: float,
    *,
    source_scene: str | None = None,
    source_label_id: str | None = None,
    confidence: str = "medium",
    unit: str = "mm",
    notes: str | None = None,
    provenance_quality: str = "direct_read",
    review_required: bool | None = None,
) -> dict:
    """Build one building-global fact entry with provenance + confidence."""
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    if provenance_quality not in PROVENANCE_QUALITY_LEVELS:
        raise ValueError(
            f"provenance_quality must be one of {PROVENANCE_QUALITY_LEVELS}, got {provenance_quality!r}"
        )
    entry: dict[str, Any] = {
        "value": value,
        "unit": unit,
        "confidence": confidence,
        "provenance_quality": provenance_quality,
        "review_required": (
            bool(review_required)
            if review_required is not None
            else confidence != "high" or provenance_quality in {"transferred", "conflicting", "review_required"}
        ),
        "source": {"scene": source_scene, "label_id": source_label_id},
    }
    if notes:
        entry["notes"] = notes
    return entry


def _val(facts: dict, name: str) -> float | None:
    """Read a stored fact's numeric value, or None if absent/malformed."""
    e = facts.get(name)
    if isinstance(e, dict) and isinstance(e.get("value"), (int, float)):
        return float(e["value"])
    return None


def _derived(name: str, value: float, *, unit: str, formula: str, inputs: list[str]) -> dict:
    return {
        "name": name,
        "value": value,
        "unit": unit,
        "derived": True,
        "needs_cross_check": True,
        "provenance_quality": "derived",
        "review_required": True,
        "formula": formula,
        "inputs": inputs,
    }


def _fact_with_defaults(name: str, entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    out.setdefault("name", name)
    confidence = str(out.get("confidence") or "medium")
    provenance_quality = str(out.get("provenance_quality") or "direct_read")
    if provenance_quality not in PROVENANCE_QUALITY_LEVELS:
        provenance_quality = "review_required"
    out["provenance_quality"] = provenance_quality
    out["review_required"] = bool(
        out.get("review_required")
        or confidence != "high"
        or provenance_quality in {"transferred", "conflicting", "review_required"}
        or out.get("conflicts")
    )
    return out


def _claim_value(entry: dict[str, Any]) -> float | None:
    value = entry.get("value")
    return float(value) if isinstance(value, (int, float)) else None


# A relative ±0.00 datum (bezug) names terrain/floor offsets — metres, not the
# hundreds of metres of an absolute müNN sea-level datum. Anything past this is
# almost certainly an absolute value put in the wrong (relative) field.
_RELATIVE_DATUM_MAX_MM = 100_000.0


def detect_fact_conflicts(facts: dict[str, dict[str, Any]], *, tolerance_mm: float = 100.0) -> list[dict[str, Any]]:
    """Return machine-readable review warnings for contradictory fact claims.

    The fact tier intentionally stores the best current value per fact name,
    but agents can still introduce conflicting datum/elevation claims through
    repeated reads or by mixing absolute datum fields. Conflicts are warnings:
    they do not prove which value is wrong, but they must be visible to the
    reviewer and to transferred-calibration consumers.
    """
    conflicts: list[dict[str, Any]] = []

    for name, entry in facts.items():
        previous_values = entry.get("previous_values") if isinstance(entry.get("previous_values"), list) else []
        current_value = _claim_value(entry)
        differing_previous = [
            prev for prev in previous_values
            if isinstance(prev, dict)
            and isinstance(prev.get("value"), (int, float))
            and current_value is not None
            and abs(float(prev["value"]) - current_value) > tolerance_mm
        ]
        if differing_previous:
            conflicts.append({
                "id": f"{name}:previous-value-conflict",
                "kind": "same_fact_conflicting_readings",
                "fact": name,
                "unit": entry.get("unit") or "mm",
                "current": {
                    "value": current_value,
                    "source": entry.get("source"),
                    "confidence": entry.get("confidence"),
                },
                "previous_values": differing_previous,
                "review_required": True,
            })

    # WS-E1: do NOT compare EG_munn_mm (absolute müNN, e.g. 843800) against
    # bezug_mm (relative ±0.00, typically 0) — they are DIFFERENT reference
    # frames and always differ by the sea-level offset, so the old cross-frame
    # check raised a permanent FALSE "1 Konflikt" on every honest house. The
    # only frame error worth flagging here is an absolute value mistakenly
    # recorded in the relative bezug field (a unit/frame slip): a true ±0.00
    # datum is small (terrain/floor offsets are metres, not hundreds of metres).
    bezug = facts.get("bezug_mm")
    if isinstance(bezug, dict):
        bval = _claim_value(bezug)
        if bval is not None and abs(bval) > _RELATIVE_DATUM_MAX_MM:
            conflicts.append({
                "id": "bezug-frame-mistake",
                "kind": "relative_datum_frame_mistake",
                "fact": "bezug_mm",
                "value": bval,
                "unit": bezug.get("unit") or "mm",
                "source": bezug.get("source"),
                "review_required": True,
                "note": (
                    "bezug_mm is the RELATIVE ±0.00 datum and should be small "
                    f"(|value| <= {_RELATIVE_DATUM_MAX_MM:.0f}mm). This looks like an "
                    "absolute müNN value recorded in the relative field — record "
                    "the absolute datum in EG_munn_mm / ridge_munn_mm instead."
                ),
            })

    return conflicts


def derive_building_geometry(facts: dict) -> list[dict]:
    """Compute derived building facts from the stored anchors. Math, not
    OCR. Only computes what the present inputs allow; each result is
    flagged `derived` + `needs_cross_check`.

    `facts` is the `building_global["facts"]` mapping (name -> entry).

    Derivations:
      - müNN absolutes: for each relative height with EG_munn_mm known,
        <X>_munn_mm = EG_munn_mm + <X>_mm   (EG is ±0.00 by definition).
      - storey heights: consecutive deltas of present floor levels,
        storey_<a>_<b>_mm = <b>_mm - <a>_mm.
      - roof rise: from roof_pitch_deg over a horizontal run —
        roof_rise_per_m_mm = 1000 * tan(pitch); and, when extent depth is
        threaded in as `depth_mm`, ridge rise over half-span.
    """
    out: list[dict] = []

    # 1. müNN ↔ relative.
    eg_munn = _val(facts, "EG_munn_mm")
    if eg_munn is not None:
        for name in RELATIVE_HEIGHT_FACTS:
            rel = _val(facts, name)
            if rel is None:
                continue
            token = name[:-3]  # strip "_mm"
            out.append(_derived(
                f"{token}_munn_mm", eg_munn + rel, unit="mm",
                formula=f"EG_munn_mm + {name}",
                inputs=["EG_munn_mm", name],
            ))

    # 2. Storey heights — consecutive deltas of present floor levels.
    present = [(n, _val(facts, n)) for n in LEVEL_ORDER if _val(facts, n) is not None]
    # EG is the ±0.00 datum; if EG_munn is known but EG_mm wasn't stored,
    # treat EG_mm as 0 so UG→EG / EG→OG deltas still compute.
    if _val(facts, "EG_mm") is None and (eg_munn is not None or present):
        present.append(("EG_mm", 0.0))
        present.sort(key=lambda p: LEVEL_ORDER.index(p[0]))
    for (a_name, a_val), (b_name, b_val) in zip(present, present[1:]):
        out.append(_derived(
            f"storey_{a_name[:-3]}_{b_name[:-3]}_mm".lower(),
            b_val - a_val, unit="mm",
            formula=f"{b_name} - {a_name}",
            inputs=[b_name, a_name],
        ))

    # 3. Roof geometry from pitch.
    pitch = _val(facts, "roof_pitch_deg")
    if pitch is not None and 0 < pitch < 90:
        rise_per_m = 1000.0 * math.tan(math.radians(pitch))
        out.append(_derived(
            "roof_rise_per_m_mm", round(rise_per_m, 1), unit="mm",
            formula="1000 * tan(roof_pitch_deg)",
            inputs=["roof_pitch_deg"],
        ))
        depth = _val(facts, "depth_mm")  # optionally threaded in by caller
        if depth is not None and depth > 0:
            out.append(_derived(
                "roof_ridge_rise_mm", round((depth / 2.0) * math.tan(math.radians(pitch)), 1),
                unit="mm",
                formula="(depth_mm / 2) * tan(roof_pitch_deg)",
                inputs=["roof_pitch_deg", "depth_mm"],
            ))

    return out


def build_global_view(
    building_global: dict | None,
    scene_files: list[str],
    *,
    extent: dict | None = None,
) -> dict:
    """Assemble the agent-facing view of the building-global tier.

    Returns:
      facts:           the stored values, each with provenance + confidence
      derived:         deterministically computed facts (see above)
      propagation:     {applies_to_scenes: [...]} — these values are
                       building-wide and available on every scene, read
                       once from the best source.
    """
    bg = building_global or {}
    raw_facts = bg.get("facts") if isinstance(bg.get("facts"), dict) else {}
    facts = {
        name: _fact_with_defaults(name, entry)
        for name, entry in raw_facts.items()
        if isinstance(entry, dict)
    }
    all_conflicts = detect_fact_conflicts(facts)
    # WS-E2: a reviewer (or the agent, with evidence) can adjudicate a conflict
    # via resolve_fact_conflict, recorded under building_global.resolved_conflicts
    # keyed by conflict id. Resolved conflicts move out of review_required and no
    # longer down-tier their fact — but stay visible/auditable in the ledger.
    resolved_map = bg.get("resolved_conflicts") if isinstance(bg.get("resolved_conflicts"), dict) else {}
    conflicts = [c for c in all_conflicts if str(c.get("id")) not in resolved_map]
    resolved_conflicts = [
        {**c, "resolution": resolved_map[str(c.get("id"))]}
        for c in all_conflicts if str(c.get("id")) in resolved_map
    ]
    conflict_ids_by_fact: dict[str, list[str]] = {}
    for conflict in conflicts:
        if conflict.get("fact"):
            conflict_ids_by_fact.setdefault(str(conflict["fact"]), []).append(str(conflict["id"]))
        for claim in conflict.get("facts") or []:
            if isinstance(claim, dict) and claim.get("fact"):
                conflict_ids_by_fact.setdefault(str(claim["fact"]), []).append(str(conflict["id"]))
    for name, ids in conflict_ids_by_fact.items():
        if name not in facts:
            continue
        facts[name]["conflicts"] = sorted(set(ids))
        facts[name]["provenance_quality"] = "conflicting"
        facts[name]["review_required"] = True

    # Thread extent.depth_mm into derivation inputs (read-only; not stored
    # in the tier) so roof ridge rise can be computed when available.
    deriv_facts = dict(facts)
    if extent and isinstance(extent.get("depth_mm"), (int, float)):
        deriv_facts["depth_mm"] = {"value": float(extent["depth_mm"])}

    return {
        "schema": bg.get("schema", SCHEMA),
        "facts": facts,
        "derived": derive_building_geometry(deriv_facts),
        "fact_ledger": {
            "conflicts": conflicts,
            "resolved_conflicts": resolved_conflicts,
            "review_required": bool(conflicts or any(f.get("review_required") for f in facts.values())),
            "provenance_counts": {
                quality: sum(1 for f in facts.values() if f.get("provenance_quality") == quality)
                for quality in PROVENANCE_QUALITY_LEVELS
            },
            "consumer_note": (
                "Agents must inspect this ledger before section/elevation labeling "
                "and before recording transferred calibration."
            ),
        },
        "propagation": {
            "applies_to_scenes": list(scene_files),
            "note": (
                "Building-global facts are house-wide: read once from the "
                "best source (usually the Schnitt) and available on every "
                "Ansicht/Schnitt. Each value records the scene + label it "
                "came from and a confidence."
            ),
        },
    }
