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
) -> dict:
    """Build one building-global fact entry with provenance + confidence."""
    if confidence not in CONFIDENCE_LEVELS:
        raise ValueError(f"confidence must be one of {CONFIDENCE_LEVELS}, got {confidence!r}")
    entry: dict[str, Any] = {
        "value": value,
        "unit": unit,
        "confidence": confidence,
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
        "formula": formula,
        "inputs": inputs,
    }


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
    facts = dict(bg.get("facts") or {})

    # Thread extent.depth_mm into derivation inputs (read-only; not stored
    # in the tier) so roof ridge rise can be computed when available.
    deriv_facts = dict(facts)
    if extent and isinstance(extent.get("depth_mm"), (int, float)):
        deriv_facts["depth_mm"] = {"value": float(extent["depth_mm"])}

    return {
        "schema": bg.get("schema", SCHEMA),
        "facts": facts,
        "derived": derive_building_geometry(deriv_facts),
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
