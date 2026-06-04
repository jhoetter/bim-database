"""WS-C bugfix: the evidence pointer must round-trip through the label schema.

The 2026-06-04 EG validation run found that upsert_label(evidence=...) was
rejected: the pointer was stored in `attributes`, whose per-type schema is
additionalProperties:false, and `dpi:"native"` isn't a number. Fix: store
evidence at the label TOP LEVEL (open envelope) with a lenient Evidence def.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

jsonschema = pytest.importorskip("jsonschema")

SCHEMA = json.loads((REPO_ROOT / "schema" / "scene_labels.schema.json").read_text())


def _payload(label):
    return {
        "schema_version": "1.0", "scene_key": "house-22",
        "scene_file": "house-22-floorplan-eg.png", "scene_tag": "grundriss",
        "image_size_px": [4735, 3237], "labels": [label],
    }


def _ev(dpi="native", fidelity="read"):
    return {"scene": "house-22-floorplan-eg.png", "region_bbox": "650,70,1150,230",
            "dpi": dpi, "enhance": "threshold", "grid": "none", "fidelity": fidelity}


def test_top_level_evidence_validates_for_value_types():
    for label in (
        {"id": "a" * 8, "type": "height_mark", "geometry": {"anchor": [1, 1]},
         "status": "readable", "evidence": _ev()},
        {"id": "b" * 8, "type": "dimension_number", "geometry": {"anchor": [1, 1]},
         "status": "readable", "evidence": _ev(dpi=1000, fidelity="zoom_read")},
        {"id": "c" * 8, "type": "dimensioned_distance",
         "geometry": {"start": [0, 0], "end": [9, 0]}, "status": "readable",
         "attributes": {"target_orientation": "horizontal", "is_reference": False},
         "evidence": _ev()},
    ):
        jsonschema.validate(_payload(label), SCHEMA)  # must not raise


def test_attributes_placement_is_still_rejected():
    """Regression: the old (broken) placement must fail, proving the per-type
    attributes really are strict — i.e. the top-level move was necessary."""
    bad = {"id": "d" * 8, "type": "height_mark", "geometry": {"anchor": [1, 1]},
           "status": "readable", "attributes": {"evidence": _ev()}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_payload(bad), SCHEMA)
