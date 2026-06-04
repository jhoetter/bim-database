"""Evidence-fidelity model (legibility-first-quality tracker WS-C).

Every value a labeling agent records (a dimension number, a dimensioned
distance, a height mark) should be read off a HIGH-FIDELITY crop — a native
`read` or a higher-than-native `zoom_read`, lossless and grid-free — not the
cheap, downscaled, grid-occluded `survey` view. The read/zoom MCP verbs emit an
`evidence_pointer`; the agent attaches it to the label's `attributes.evidence`.

This module is the single source of truth for:
  - what an evidence pointer looks like (normalize),
  - whether it is high fidelity (read/zoom, lossless),
  - which label types REQUIRE high-fidelity evidence to count toward gold.

Persisting the pointer (not the pixels) is what makes context summarization
harmless: the durable artifact is the fact + a reproducible re-render recipe.
"""
from __future__ import annotations

from typing import Any

# Reading tiers, ranked. survey is the cheap orientation view; read is native
# lossless; zoom_read re-renders from the PDF vector above native.
FIDELITY_RANK = {"survey": 1, "read": 2, "zoom_read": 3}

# A value READ off the drawing — these must come from a read-tier crop to be
# trusted as gold. Walls carry their own ink-anchoring provenance and are
# graded by the anchoring/score path, not here.
VALUE_BEARING_LABEL_TYPES = {
    "dimension_number",
    "dimensioned_distance",
    "height_mark",
}


def normalize_evidence(ev: Any) -> dict | None:
    """Coerce a raw evidence pointer (as emitted by read_scene_region /
    zoom_read_scene_region) into a stored dict, or None if unusable."""
    if not isinstance(ev, dict):
        return None
    fidelity = str(ev.get("fidelity") or "").strip().lower()
    if fidelity not in FIDELITY_RANK:
        # Tolerate a bare pointer that names a tier elsewhere; default unknown
        # pointers to the lowest tier so they never silently pass the gate.
        fidelity = "survey"
    out: dict[str, Any] = {
        "scene": ev.get("scene"),
        "region_bbox": ev.get("region_bbox") or ev.get("region"),
        "dpi": ev.get("dpi"),
        "enhance": ev.get("enhance"),
        "grid": ev.get("grid"),
        "fidelity": fidelity,
    }
    return out


def is_high_fidelity(ev: Any) -> bool:
    """True iff the value was read off a read/zoom_read crop (not survey)."""
    norm = normalize_evidence(ev)
    if norm is None:
        return False
    return FIDELITY_RANK.get(norm["fidelity"], 0) >= FIDELITY_RANK["read"]


def label_evidence(label: dict) -> dict | None:
    # Top-level is canonical (attributes is additionalProperties:false); the
    # attributes fallback keeps any older records readable.
    ev = label.get("evidence")
    if ev is not None:
        return ev
    attrs = label.get("attributes") if isinstance(label.get("attributes"), dict) else {}
    return attrs.get("evidence")


def needs_high_fidelity(label: dict) -> bool:
    """Whether this label is a recorded VALUE that must be backed by a
    read-tier crop. Only readable (non-uncertain) values are gated — an
    honestly `uncertain`/`source_unreadable` label is already downgraded."""
    if str(label.get("type") or "") not in VALUE_BEARING_LABEL_TYPES:
        return False
    return str(label.get("status") or "readable") == "readable"


def is_low_fidelity_value(label: dict) -> bool:
    """A readable value-bearing label whose evidence is missing or survey-tier
    — it must not count toward gold."""
    return needs_high_fidelity(label) and not is_high_fidelity(label_evidence(label))
