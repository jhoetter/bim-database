"""M6 (code-quality-tracker): dedicated unit tests for api/topology_repair.

Covers the pure logic that the MCP repair-candidate flow depends on —
fingerprinting, finding→cluster grouping, and candidate application — which
previously had only indirect happy-path coverage via MCP smoke tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from api.topology_repair import (  # noqa: E402
    apply_candidate_to_labels,
    cluster_findings,
    finding_fingerprint,
    repair_candidate_report,
    simulate_candidate,
)


# ── finding_fingerprint ────────────────────────────────────────────────────


def test_fingerprint_is_deterministic_and_wall_order_independent():
    a = finding_fingerprint("f.jpg", "score", "wall_off_ink",
                            {"wall_ids": ["wall-2", "wall-1"]})
    b = finding_fingerprint("f.jpg", "score", "wall_off_ink",
                            {"wall_ids": ["wall-1", "wall-2"]})
    assert a == b  # order of wall_ids must not change the fingerprint
    assert a.startswith("score:wall_off_ink:")


def test_fingerprint_distinguishes_category_and_file():
    base = {"wall_ids": ["wall-1"]}
    fp = finding_fingerprint("f.jpg", "score", "wall_off_ink", base)
    assert fp != finding_fingerprint("f.jpg", "score", "wall_topology", base)
    assert fp != finding_fingerprint("other.jpg", "score", "wall_off_ink", base)


# ── cluster_findings ───────────────────────────────────────────────────────


def _finding(fp, cat, sev, region, wall_ids):
    return {"fingerprint": fp, "category": cat, "severity": sev,
            "region": region, "payload": {"wall_ids": wall_ids}}


def test_findings_sharing_a_wall_merge_into_one_cluster():
    f1 = _finding("fp1", "wall_topology", "blocker", [0, 0, 100, 100], ["wall-1"])
    f2 = _finding("fp2", "wall_off_ink", "warning", [500, 500, 600, 600], ["wall-1"])
    f3 = _finding("fp3", "wall_topology", "warning", [900, 900, 999, 999], ["wall-9"])
    clusters = cluster_findings([f1, f2, f3], {"labels": []})
    # f1 + f2 share wall-1 -> one cluster; f3 is its own.
    assert len(clusters) == 2
    big = max(clusters, key=lambda c: c["findings_count"])
    assert big["findings_count"] == 2
    assert set(big["finding_ids"]) == {"fp1", "fp2"}
    # Every cluster gets stable ids + a type/summary.
    for c in clusters:
        assert c["cluster_id"].startswith("TOPO-CL-")
        assert c["cluster_fingerprint"].startswith("cluster:")
        assert "cluster_type" in c
    # Sorted blocker-first.
    assert clusters[0]["severity"] == "blocker"


def test_clustering_is_stable_across_calls():
    fs = [_finding("a", "wall_topology", "warning", [0, 0, 50, 50], ["w1"]),
          _finding("b", "wall_topology", "warning", [10, 10, 60, 60], ["w1"])]
    c1 = cluster_findings(fs, {"labels": []})
    c2 = cluster_findings(fs, {"labels": []})
    assert [c["cluster_id"] for c in c1] == [c["cluster_id"] for c in c2]


# ── apply_candidate_to_labels ──────────────────────────────────────────────


def _doc():
    return {"labels": [
        {"id": "wall-1", "type": "wall", "geometry": {"start": [0, 0], "end": [100, 0]}},
        {"id": "wall-2", "type": "wall", "geometry": {"start": [100, 0], "end": [200, 0]}},
    ]}


def test_endpoint_edit_moves_geometry_without_mutating_input():
    doc = _doc()
    out = apply_candidate_to_labels(
        doc, {"edits": [{"label_id": "wall-1", "endpoint": "end", "to": [10, 20]}]})
    moved = next(l for l in out["labels"] if l["id"] == "wall-1")
    assert moved["geometry"]["end"] == [10.0, 20.0]
    # Original is untouched (deepcopy contract).
    assert doc["labels"][0]["geometry"]["end"] == [100, 0]


def test_delete_edit_removes_label():
    out = apply_candidate_to_labels(
        _doc(), {"edits": [{"label_id": "wall-2", "delete": True}]})
    assert [l["id"] for l in out["labels"]] == ["wall-1"]


def test_no_edit_classification_is_a_noop():
    doc = _doc()
    out = apply_candidate_to_labels(doc, {"op": "no_edit_classification"})
    assert out == doc


def test_merge_collinear_fragments_replaces_and_drops():
    out = apply_candidate_to_labels(_doc(), {
        "op": "merge_collinear_fragments",
        "edits": [{"replace_wall_ids": ["wall-1", "wall-2"], "wall": [[0, 0], [200, 0]]}],
    })
    assert [l["id"] for l in out["labels"]] == ["wall-1"]
    merged = out["labels"][0]
    assert merged["geometry"] == {"start": [0, 0], "end": [200, 0]}


def test_edit_on_missing_label_raises():
    with pytest.raises(ValueError):
        apply_candidate_to_labels(
            _doc(), {"edits": [{"label_id": "ghost", "endpoint": "end", "to": [1, 2]}]})


# ── simulate_candidate ─────────────────────────────────────────────────────


def test_simulate_returns_before_after_delta():
    sim = simulate_candidate(_doc(), {"op": "no_edit_classification"})
    assert set(sim) == {"before", "after", "delta"}
    # A no-op candidate leaves every topology count unchanged.
    assert all(v == 0 for v in sim["delta"].values())


def test_repair_report_includes_nearby_semantic_context():
    labels_doc = {"scene_file": "scene.png", "labels": []}
    plan_state = {
        "evidence": [{
            "id": "EV-001",
            "kind": "semantic_ink_region",
            "summary": "Site boundary, not a structural wall.",
            "result": {
                "semantic_class": "site_boundary",
                "region": [5, 15, 60, 50],
                "bbox_format": "xywh",
                "confidence": "high",
                "applies_to_wall_score": True,
            },
        }],
        "current_state": {
            "scores": {
                "score_walls": {
                    "missing_regions": [[10, 20, 30, 40, 1200]],
                    "off_ink_segments": [],
                },
            },
        },
    }

    report = repair_candidate_report(labels_doc, topology_result={}, plan_state=plan_state)

    assert report["semantic_context_count"] == 1
    cluster = report["clusters"][0]
    assert cluster["semantic_context"][0]["evidence_id"] == "EV-001"
    assert cluster["semantic_context"][0]["semantic_class"] == "site_boundary"
    assert cluster["candidates"][0]["semantic_context"][0]["applies_to_wall_score"] is True


def test_off_ink_segment_review_region_is_line_bbox_not_xywh():
    from api.topology_repair import current_findings_from_results

    findings = current_findings_from_results(
        file="scene.png",
        labels_doc={"scene_file": "scene.png", "labels": []},
        score_walls_result={
            "missing_regions": [],
            "off_ink_segments": [[2547, 1460, 2058, 1426, 0.0]],
        },
    )

    assert len(findings) == 1
    assert findings[0]["category"] == "off_ink_segment"
    # Reversed endpoint line plus 16px review padding. The old xywh path made
    # this thousands of pixels wide/high.
    assert findings[0]["region"] == [2042.0, 1410.0, 2563.0, 1476.0]


def test_repair_report_clips_regions_to_image_bounds_when_available():
    labels_doc = {
        "scene_file": "scene.png",
        "image_size_px": [120, 100],
        "labels": [],
    }
    plan_state = {
        "current_state": {
            "scores": {
                "score_walls": {
                    "missing_regions": [[90, 80, 80, 60, 4800]],
                    "off_ink_segments": [],
                },
            },
        },
    }

    report = repair_candidate_report(labels_doc, topology_result={}, plan_state=plan_state)

    assert report["region_warning_count"] >= 1
    cluster = report["clusters"][0]
    assert cluster["region"] == [90.0, 80.0, 120.0, 100.0]
    assert cluster["region_clipped"] is True
    assert cluster["candidates"][0]["region"] == [90.0, 80.0, 120.0, 100.0]
