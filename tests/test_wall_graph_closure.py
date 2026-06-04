"""Wall-graph closure tracker WS-1/WS-2.2: close the graph of placed walls.

close_wall_graph snaps near-miss corners to shared points and extends small
gaps, never fabricating or deleting a wall; endpoints with a too-large gap are
reported as missing walls (trace them, don't force-close). Topology tolerances
are DPI-scaled.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main  # noqa: E402,F401  (load package root first; main<->routes cycle)
from api.topology_repair import close_wall_graph_labels  # noqa: E402
from api.wall_topology import wall_topology_qa  # noqa: E402


def _wall(i, s, e):
    return {"id": f"lab-{i:04d}", "type": "wall", "status": "readable",
            "geometry": {"start": list(s), "end": list(e)}}


def _square_with_offsets_and_a_far_stub():
    # a near-square whose 4 corners are ~20-28px apart (near-miss, snappable)
    # plus one isolated wall far away whose endpoints can't snap (missing wall).
    return {"schema_version": "1.0", "scene_key": "k", "scene_file": "f.png",
            "scene_tag": "grundriss", "image_size_px": [2000, 2000], "labels": [
        _wall(1, (100, 100), (480, 100)),   # top
        _wall(2, (500, 120), (500, 480)),   # right
        _wall(3, (480, 500), (100, 500)),   # bottom
        _wall(4, (100, 480), (100, 120)),   # left
        _wall(9, (1500, 1500), (1500, 1800)),  # isolated stub, far from all
    ]}


def test_closer_snaps_near_corners_keeps_real_gaps():
    doc = _square_with_offsets_and_a_far_stub()
    before = len(wall_topology_qa(doc["labels"], source_dpi=600).get("dangling_endpoints") or [])
    assert before > 0
    new_doc, rep = close_wall_graph_labels(doc, file="f.png", source_dpi=600)
    assert rep["closed_count"] > 0
    assert rep["dangling_after"] < rep["dangling_before"]
    # no wall fabricated or deleted
    assert sum(1 for l in new_doc["labels"] if l["type"] == "wall") == 5
    # the isolated stub's endpoints survive as missing-wall endpoints
    miss_walls = {m["wall_id"] for m in rep["missing_wall_endpoints"]}
    assert "lab-0009" in miss_walls
    # closure ops are only the safe kinds
    assert all(a["op"] in {"snap_endpoint_to_endpoint", "extend_to_intersection",
                           "merge_collinear_fragments"} for a in rep["applied"])


def test_closer_is_idempotent():
    doc = _square_with_offsets_and_a_far_stub()
    once, _ = close_wall_graph_labels(doc, file="f.png", source_dpi=600)
    twice, rep2 = close_wall_graph_labels(once, file="f.png", source_dpi=600)
    assert rep2["closed_count"] == 0  # nothing left to safely close


def test_dpi_scaling_changes_tolerance():
    # two wall endpoints 30px apart: dangling at 600dpi (tol 18), connected at
    # 1200dpi (tol scales to 36).
    doc = {"labels": [
        _wall(1, (100, 100), (400, 100)),
        _wall(2, (430, 100), (430, 400)),  # start 30px from wall1 end
    ]}
    d600 = len(wall_topology_qa(doc["labels"], source_dpi=600).get("dangling_endpoints") or [])
    d1200 = len(wall_topology_qa(doc["labels"], source_dpi=1200).get("dangling_endpoints") or [])
    assert d1200 < d600
