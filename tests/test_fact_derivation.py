"""Unit tests for api/fact_derivation.py — Python port of the SPA's
promoteToFacts pipeline. Per agentic-labeling-followups-tracker §G1-1
through §G1-8.

These tests pin the contract that the MCP path and the SPA path produce
identical facts. If the SPA's `computeSceneCalibration` /
`promoteToFacts` change, the equivalent Python here must change in
lockstep.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.fact_derivation import (  # noqa: E402
    _migrate_v1_0_facts,
    compute_scene_calibration,
    derive_scene_metadata_entry,
    dim_orientation,
    promote_scene_to_facts,
    prune_scene_from_facts,
    recompute_facts_after_label_write,
)


# ── dim_orientation ───────────────────────────────────────────────────────


def test_dim_orientation_horizontal():
    assert dim_orientation([0, 0], [100, 0]) == "horizontal"
    assert dim_orientation([0, 0], [100, 10]) == "horizontal"   # within ±15°
    assert dim_orientation([100, 0], [0, 0]) == "horizontal"    # reversed
    assert dim_orientation([0, 0], [-100, 5]) == "horizontal"   # negative dx


def test_dim_orientation_vertical():
    assert dim_orientation([0, 0], [0, 100]) == "vertical"
    assert dim_orientation([0, 0], [10, 100]) == "vertical"     # within ±15°
    assert dim_orientation([0, 100], [0, 0]) == "vertical"


def test_dim_orientation_diagonal():
    assert dim_orientation([0, 0], [100, 100]) is None          # 45°
    assert dim_orientation([0, 0], [100, 50]) is None           # ~26°


def test_dim_orientation_degenerate():
    assert dim_orientation([0, 0], [0, 0]) is None
    assert dim_orientation([0], [0, 0]) is None
    assert dim_orientation([0, 0], [0]) is None


# ── compute_scene_calibration ─────────────────────────────────────────────


def _ref_dim(start, end, value_mm, label_id="lab-x"):
    return {
        "id": label_id, "type": "dimensioned_distance", "status": "readable",
        "geometry": {"start": start, "end": end},
        "attributes": {"value_mm": value_mm, "is_reference": True},
    }


def test_calibration_both_axes():
    labels = [
        _ref_dim([0, 0], [1000, 0], 10000, "h"),     # 0.1 px/mm
        _ref_dim([0, 0], [0, 500], 5000, "v"),       # 0.1 px/mm
    ]
    c = compute_scene_calibration(labels)
    assert c is not None
    assert c["computed_from"] == "M1-both"
    assert abs(c["px_per_mm"] - 0.1) < 1e-9


def test_calibration_h_only():
    c = compute_scene_calibration([_ref_dim([0, 0], [1000, 0], 10000)])
    # Issue #26: single-axis calibration carries the isotropic honesty flag.
    assert c == {
        "px_per_mm": 0.1,
        "computed_from": "M1-H-Bezug",
        "single_ref_assumed_isotropic": True,
    }


def test_calibration_v_only():
    c = compute_scene_calibration([_ref_dim([0, 0], [0, 1000], 10000)])
    assert c == {
        "px_per_mm": 0.1,
        "computed_from": "M1-V-Bezug",
        "single_ref_assumed_isotropic": True,
    }


def test_calibration_no_reference_dims():
    # Same label but is_reference=False → ignored
    lab = _ref_dim([0, 0], [1000, 0], 10000)
    lab["attributes"]["is_reference"] = False
    assert compute_scene_calibration([lab]) is None


def test_calibration_skips_zero_or_negative_value():
    assert compute_scene_calibration([_ref_dim([0, 0], [1000, 0], 0)]) is None
    assert compute_scene_calibration([_ref_dim([0, 0], [1000, 0], -100)]) is None


def test_calibration_skips_degenerate_geometry():
    assert compute_scene_calibration([_ref_dim([0, 0], [0, 0], 1000)]) is None


def test_calibration_skips_diagonal_dim():
    # Diagonal dim — dim_orientation returns None → skipped
    assert compute_scene_calibration([_ref_dim([0, 0], [100, 100], 1000)]) is None


# ── derive_scene_metadata_entry ──────────────────────────────────────────


def test_scene_metadata_entry_basic():
    labels_json = {
        "scene_tag": "grundriss",
        "scene_orientation": None,
        "scene_level": "eg",
        "image_size_px": [2400, 1600],
        "labels": [],
    }
    meta = derive_scene_metadata_entry(labels_json)
    assert meta["scene_tag"] == "grundriss"
    assert "kind" not in meta  # G6: legacy field is gone
    assert meta["level"] == "eg"
    assert meta["orientation"] is None
    assert meta["image_size_px"] == [2400, 1600]


def test_scene_metadata_entry_defaults_to_unclassified():
    meta = derive_scene_metadata_entry({"labels": []})
    assert meta["scene_tag"] == "nicht_klassifiziert"


# ── promote_scene_to_facts ───────────────────────────────────────────────


def test_promote_extent_from_ansicht_horizontal_writes_width():
    facts: dict = {}
    labels_json = {
        "scene_tag": "ansicht",
        "labels": [_ref_dim([0, 0], [1000, 0], 12500)],
    }
    promote_scene_to_facts(facts, scene_file="house-22-elevation-east.jpg",
                           labels_json=labels_json)
    assert facts["extent"]["width_mm"] == 12500
    assert "depth_mm" not in facts["extent"]


def test_promote_extent_from_schnitt_horizontal_writes_depth():
    facts: dict = {}
    labels_json = {
        "scene_tag": "schnitt",
        "labels": [_ref_dim([0, 0], [1000, 0], 9800)],
    }
    promote_scene_to_facts(facts, scene_file="house-22-section.jpg",
                           labels_json=labels_json)
    assert facts["extent"]["depth_mm"] == 9800
    assert "width_mm" not in facts["extent"]


def test_promote_extent_vertical_on_ansicht_writes_height():
    facts: dict = {}
    labels_json = {
        "scene_tag": "ansicht",
        "labels": [_ref_dim([0, 0], [0, 1000], 8500)],
    }
    promote_scene_to_facts(facts, scene_file="x.jpg", labels_json=labels_json)
    assert facts["extent"]["height_mm"] == 8500


def test_promote_extent_vertical_on_grundriss_writes_depth():
    """H2 (followups-2 tracker): on a Grundriss, vertical dim is the
    building DEPTH (Gebäudetiefe), not height. The W2 predicate reads
    depth_mm; vertical-on-Grundriss must populate it."""
    facts: dict = {}
    labels_json = {
        "scene_tag": "grundriss",
        "scene_level": "eg",
        "labels": [
            _ref_dim([0, 0], [1000, 0], 12000, "h"),  # horizontal → width
            _ref_dim([0, 0], [0, 800], 8000, "v"),    # vertical → depth (NOT height)
        ],
    }
    promote_scene_to_facts(facts, scene_file="eg.jpg", labels_json=labels_json)
    assert facts["extent"]["width_mm"] == 12000
    assert facts["extent"]["depth_mm"] == 8000
    assert "height_mm" not in facts["extent"]


def test_promote_extent_vertical_on_schnitt_writes_height():
    facts: dict = {}
    labels_json = {
        "scene_tag": "schnitt",
        "labels": [_ref_dim([0, 0], [0, 1000], 8500)],
    }
    promote_scene_to_facts(facts, scene_file="x.jpg", labels_json=labels_json)
    assert facts["extent"]["height_mm"] == 8500


def test_promote_extent_keeps_largest():
    facts: dict = {}
    labels_json = {
        "scene_tag": "ansicht",
        "labels": [
            _ref_dim([0, 0], [1000, 0], 12500, "a"),
            _ref_dim([0, 0], [800, 0], 10000, "b"),
        ],
    }
    promote_scene_to_facts(facts, scene_file="x.jpg", labels_json=labels_json)
    assert facts["extent"]["width_mm"] == 12500


def test_promote_heights_bezug_zero_with_datum_still_sets_bezug_mm():
    """H1 (followups-2 tracker): a height_mark with value_mm: 0 sets
    bezug_mm = 0 regardless of datum. Previously this only worked when
    datum was None or 'other'; agents following the playbook example
    (datum: 'ok_ffb') hit a phantom W1-incomplete state."""
    facts: dict = {}
    labels_json = {
        "scene_tag": "ansicht",
        "scene_level": "eg",
        "labels": [
            {"id": "h0", "type": "height_mark", "status": "readable",
             "geometry": {"anchor": [100, 800]},
             "attributes": {"value_mm": 0, "datum": "ok_ffb"}},
        ],
    }
    promote_scene_to_facts(facts, scene_file="ansicht.jpg",
                           labels_json=labels_json)
    # bezug_mm set despite datum: ok_ffb
    assert facts["heights"]["bezug_mm"] == 0
    # ok_ffb_eg_mm also set (same value, datum-specific key)
    assert facts["heights"]["ok_ffb_eg_mm"] == 0
    # Both source-chains reference the same label
    assert any("h0" in s for s in facts["heights"]["sources"].get("bezug_mm", []))
    assert any("h0" in s for s in facts["heights"]["sources"].get("ok_ffb_eg_mm", []))


def test_promote_heights_bezug_and_first():
    facts: dict = {}
    labels_json = {
        "scene_tag": "ansicht",
        "labels": [
            {"id": "h0", "type": "height_mark", "status": "readable",
             "geometry": {"anchor": [100, 800]},
             "attributes": {"value_mm": 0}},
            {"id": "h1", "type": "height_mark", "status": "readable",
             "geometry": {"anchor": [100, 100]},
             "attributes": {"value_mm": 8500, "datum": "first"}},
        ],
    }
    promote_scene_to_facts(facts, scene_file="ansicht.jpg",
                           labels_json=labels_json)
    assert facts["heights"]["bezug_mm"] == 0
    assert facts["heights"]["first_mm"] == 8500


def test_promote_openings_bucketed_by_50mm():
    facts: dict = {}
    labels_json = {
        "scene_tag": "grundriss",
        "labels": [
            {"id": "o1", "type": "floorplan_opening", "status": "readable",
             "geometry": {"quad": [[0,0],[100,0],[100,100],[0,100]]},
             "attributes": {"opening_kind": "window", "width_mm": 1238}},
            {"id": "o2", "type": "floorplan_opening", "status": "readable",
             "geometry": {"quad": [[0,0],[100,0],[100,100],[0,100]]},
             "attributes": {"opening_kind": "window", "width_mm": 1262}},
            {"id": "o3", "type": "floorplan_opening", "status": "readable",
             "geometry": {"quad": [[0,0],[100,0],[100,100],[0,100]]},
             "attributes": {"opening_kind": "door", "width_mm": 900}},
        ],
    }
    promote_scene_to_facts(facts, scene_file="eg.jpg", labels_json=labels_json)
    cat = sorted(facts["openings_catalog"], key=lambda o: (o["kind"], o["width_mm"]))
    # 1238 and 1262 both round to 1250 (50mm buckets) → same bucket, count=2.
    assert any(o["kind"] == "window" and o["width_mm"] == 1250 and o["instances"] == 2
               for o in cat)
    assert any(o["kind"] == "door" and o["width_mm"] == 900 and o["instances"] == 1
               for o in cat)


def test_promote_scene_metadata_idempotent():
    """Calling promote twice with the same input produces the same facts."""
    facts1: dict = {}
    facts2: dict = {}
    labels_json = {
        "scene_tag": "ansicht", "scene_orientation": "south",
        "image_size_px": [2200, 1100],
        "labels": [_ref_dim([0, 0], [1000, 0], 11200)],
    }
    promote_scene_to_facts(facts1, scene_file="x.jpg", labels_json=labels_json)
    promote_scene_to_facts(facts2, scene_file="x.jpg", labels_json=labels_json)
    promote_scene_to_facts(facts2, scene_file="x.jpg", labels_json=labels_json)
    assert facts1["scene_metadata"] == facts2["scene_metadata"]
    assert facts1["calibration_per_scene"] == facts2["calibration_per_scene"]
    assert facts1["extent"]["width_mm"] == facts2["extent"]["width_mm"]


# ── recompute_facts_after_label_write (I/O entrypoint) ───────────────────


@pytest.fixture
def fake_dataset(tmp_path: Path) -> Path:
    """Synthesise a minimal dataset/<key>/ tree."""
    root = tmp_path / "dataset"
    house = root / "house-test"
    (house / "labels").mkdir(parents=True)
    (house / "manifest.json").write_text(json.dumps({
        "key": "house-test",
        "drawings": [
            {"file": "eg.jpg", "kind": "floorplan", "labeled": True},
            {"file": "sued.jpg", "kind": "elevation", "labeled": True},
        ],
    }))
    (house / "labels" / "eg.json").write_text(json.dumps({
        "schema_version": "1.0",
        "scene_tag": "grundriss",
        "scene_level": "eg",
        "image_size_px": [2400, 1600],
        "labels": [
            _ref_dim([0, 0], [1200, 0], 12000, "ref-h"),
            _ref_dim([0, 0], [0, 800], 8000, "ref-v"),
        ],
    }))
    (house / "labels" / "sued.json").write_text(json.dumps({
        "schema_version": "1.0",
        "scene_tag": "ansicht",
        "scene_orientation": "south",
        "image_size_px": [2200, 1100],
        "labels": [
            _ref_dim([0, 0], [1100, 0], 11000, "ref-h-sued"),
        ],
    }))
    return root


def test_recompute_populates_calibration_per_scene(fake_dataset):
    facts = recompute_facts_after_label_write("house-test", dataset_root=fake_dataset)
    assert "eg.jpg" in facts["calibration_per_scene"]
    assert "sued.jpg" in facts["calibration_per_scene"]


def test_recompute_prunes_stale_scene_metadata(fake_dataset):
    # Pre-seed facts with a stale entry for a deleted scene.
    (fake_dataset / "house-test" / "house_facts.json").write_text(json.dumps({
        "schema_version": "1.0",
        "scene_metadata": {
            "eg.jpg": {"kind": "old", "image_size_px": [1, 1]},
            "DELETED-SCENE.jpg": {"kind": "old", "image_size_px": [1, 1]},
        },
        "calibration_per_scene": {"DELETED-SCENE.jpg": {"px_per_mm": 0.1, "computed_from": "M1-both"}},
    }))
    facts = recompute_facts_after_label_write("house-test", dataset_root=fake_dataset)
    assert "DELETED-SCENE.jpg" not in facts["scene_metadata"]
    assert "DELETED-SCENE.jpg" not in facts["calibration_per_scene"]


def test_recompute_preserves_human_set_heights(fake_dataset):
    """The SPA may set facts.heights manually via the form. recompute
    must NOT wipe those when no height_mark labels exist."""
    (fake_dataset / "house-test" / "house_facts.json").write_text(json.dumps({
        "schema_version": "1.0",
        "heights": {"bezug_mm": 0, "first_mm": 8500, "sources": {}},
    }))
    facts = recompute_facts_after_label_write("house-test", dataset_root=fake_dataset)
    assert facts["heights"]["bezug_mm"] == 0
    assert facts["heights"]["first_mm"] == 8500


def test_recompute_strict_drops_unsourced_heights(fake_dataset):
    (fake_dataset / "house-test" / "house_facts.json").write_text(json.dumps({
        "schema_version": "1.0",
        "heights": {"bezug_mm": 0, "first_mm": 8500, "sources": {}},
    }))
    facts = recompute_facts_after_label_write(
        "house-test", dataset_root=fake_dataset, strict=True,
    )
    assert "bezug_mm" not in facts["heights"]
    assert "first_mm" not in facts["heights"]
    assert any("HOUSE_FACTS_STRICT" in w for w in facts["_derivation_warnings"])


# ── G6: v1.0 → v1.1 migration ──────────────────────────────────────────


def test_migrate_renames_kind_to_scene_tag():
    facts = {
        "schema_version": "1.0",
        "scene_metadata": {
            "a.jpg": {"kind": "grundriss", "level": "eg"},
            "b.jpg": {"kind": "ansicht", "orientation": "south"},
        },
    }
    out = _migrate_v1_0_facts(facts)
    assert out["schema_version"] == "1.1"
    assert out["scene_metadata"]["a.jpg"]["scene_tag"] == "grundriss"
    assert "kind" not in out["scene_metadata"]["a.jpg"]
    assert out["scene_metadata"]["b.jpg"]["scene_tag"] == "ansicht"
    assert "kind" not in out["scene_metadata"]["b.jpg"]


def test_migrate_v1_1_is_identity():
    facts = {
        "schema_version": "1.1",
        "scene_metadata": {"a.jpg": {"scene_tag": "grundriss"}},
    }
    out = _migrate_v1_0_facts(facts)
    assert out is facts  # no-op
    assert out["scene_metadata"]["a.jpg"]["scene_tag"] == "grundriss"


def test_migrate_drops_orphan_kind_when_both_present():
    """If a v1.0 entry already has scene_tag (somehow), drop the kind
    rather than overwriting."""
    facts = {
        "schema_version": "1.0",
        "scene_metadata": {
            "a.jpg": {"kind": "old_value", "scene_tag": "grundriss"},
        },
    }
    out = _migrate_v1_0_facts(facts)
    assert out["scene_metadata"]["a.jpg"]["scene_tag"] == "grundriss"
    assert "kind" not in out["scene_metadata"]["a.jpg"]


def test_recompute_runs_migration_inline(fake_dataset):
    """recompute_facts_after_label_write should migrate v1.0 → v1.1
    on read, so subsequent reads always see the new shape."""
    # Pre-seed v1.0 facts with a stale .kind entry.
    (fake_dataset / "house-test" / "house_facts.json").write_text(json.dumps({
        "schema_version": "1.0",
        "scene_metadata": {
            "eg.jpg": {"kind": "ansicht", "level": None},  # will be overwritten by labels
        },
    }))
    facts = recompute_facts_after_label_write("house-test", dataset_root=fake_dataset)
    assert facts["schema_version"] == "1.1"
    # The eg.jpg entry's scene_tag comes from the labels JSON ("grundriss"),
    # not from the migrated old kind.
    assert facts["scene_metadata"]["eg.jpg"]["scene_tag"] == "grundriss"
    assert "kind" not in facts["scene_metadata"]["eg.jpg"]


def test_prune_scene_from_facts(fake_dataset):
    # Real call-site usage: caller has ALREADY removed the manifest
    # entry + labels file before invoking prune. Mirror that here.
    recompute_facts_after_label_write("house-test", dataset_root=fake_dataset)
    # Remove sued.jpg from the manifest + labels dir (as
    # delete_extracted_scene in api/main.py does), THEN prune.
    mp = fake_dataset / "house-test" / "manifest.json"
    m = json.loads(mp.read_text())
    m["drawings"] = [d for d in m["drawings"] if d["file"] != "sued.jpg"]
    mp.write_text(json.dumps(m))
    (fake_dataset / "house-test" / "labels" / "sued.json").unlink()
    prune_scene_from_facts("house-test", "sued.jpg", dataset_root=fake_dataset)
    facts = json.loads((fake_dataset / "house-test" / "house_facts.json").read_text())
    assert "sued.jpg" not in facts["scene_metadata"]
    assert "sued.jpg" not in facts["calibration_per_scene"]
    # eg.jpg still there
    assert "eg.jpg" in facts["scene_metadata"]
