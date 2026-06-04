"""Wall-graph closure tracker WS-3 (closeable vs missing classification) and
WS-5 (footprint junk + override not a blocker)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main  # noqa: E402,F401
from api.scene_plan_state import _NON_QUALITY_DEFECT_CATEGORIES  # noqa: E402
from api.topology_repair import (  # noqa: E402
    _region_far_outside,
    _wall_footprint_bbox,
    current_findings_from_results,
)
from api.wall_topology import wall_topology_qa  # noqa: E402


def _wall(i, s, e):
    return {"id": f"lab-{i:04d}", "type": "wall", "status": "readable",
            "geometry": {"start": list(s), "end": list(e)}}


# ── WS-3 ─────────────────────────────────────────────────────────────────
def test_dangling_gap_class():
    doc = {"labels": [
        _wall(1, (100, 100), (400, 100)),
        _wall(2, (430, 100), (430, 400)),   # 30px gap -> closeable
        _wall(3, (1200, 100), (1200, 400)),  # ~770px from anything -> missing
    ]}
    dang = wall_topology_qa(doc["labels"], source_dpi=600)["dangling_endpoints"]
    classes = {d["gap_class"] for d in dang}
    assert "closeable_corner" in classes
    assert "missing_wall_endpoint" in classes
    # the 30px one is closeable
    near = [d for d in dang if d.get("gap_px") and d["gap_px"] <= 72]
    assert near and all(d["gap_class"] == "closeable_corner" for d in near)


# ── WS-5.1 ───────────────────────────────────────────────────────────────
def _square():
    return {"labels": [
        _wall(1, (1000, 1000), (1400, 1000)),
        _wall(2, (1400, 1000), (1400, 1400)),
        _wall(3, (1400, 1400), (1000, 1400)),
        _wall(4, (1000, 1400), (1000, 1000)),
    ]}


def test_footprint_filters_far_junk_not_boundary():
    doc = _square()
    fp = _wall_footprint_bbox(doc)
    assert fp == (1000.0, 1000.0, 1400.0, 1400.0)
    # a region at a sheet corner far away (compass/title block) is far outside
    assert _region_far_outside([4500, 100, 4700, 300], fp) is True
    # a region just outside the footprint boundary is kept (could be a wall)
    assert _region_far_outside([1410, 1200, 1450, 1260], fp) is False
    # fewer than 4 walls => no footprint, no filtering
    assert _wall_footprint_bbox({"labels": doc["labels"][:3]}) is None


def test_far_missing_region_dropped_from_findings():
    doc = _square()
    score = {"missing_regions": [
        [4500, 100, 60, 60, 3600],     # far junk (xywh) -> dropped
        [1405, 1200, 40, 60, 2400],    # boundary -> kept
    ]}
    findings = current_findings_from_results(
        file="f.png", labels_doc=doc, score_walls_result=score)
    missing = [f for f in findings if f["category"] == "missing_region"]
    assert len(missing) == 1  # only the boundary one survives


# ── WS-5.2 ───────────────────────────────────────────────────────────────
def test_plan_order_override_is_non_quality():
    assert "plan_order_override" in _NON_QUALITY_DEFECT_CATEGORIES
