from __future__ import annotations

import asyncio

import mcp_server


def test_truncate_lists_reports_omitted_counts() -> None:
    data = mcp_server._truncate_lists({"items": [1, 2, 3]}, {"items": 2})

    assert data["items"] == [1, 2]
    assert data["truncated"] is True
    assert data["truncation"]["items"] == {"returned": 2, "total": 3, "omitted": 1}


def test_score_walls_truncates_large_region_lists(monkeypatch) -> None:
    async def fake_get(path: str, params: dict, started: float):
        assert path == "/datasets/house-x/eg.jpg/score-walls"
        return {
            "ok": True,
            "data": {
                "precision": 1.0,
                "recall": 0.5,
                "missing_regions": [{"id": i} for i in range(4)],
                "off_ink_segments": [{"id": i} for i in range(3)],
            },
        }

    monkeypatch.setattr(mcp_server, "_cv_get", fake_get)

    result = asyncio.run(mcp_server.score_walls("house-x", "eg.jpg", max_regions=2))

    data = result["data"]
    assert len(data["missing_regions"]) == 2
    assert len(data["off_ink_segments"]) == 2
    assert data["truncation"]["missing_regions"]["omitted"] == 2
    assert data["truncation"]["off_ink_segments"]["omitted"] == 1


def test_wall_topology_qa_truncates_each_issue_family(monkeypatch) -> None:
    async def fake_get(path: str, params: dict, started: float):
        assert path == "/datasets/house-x/eg.jpg/wall-topology-qa"
        return {
            "ok": True,
            "data": {
                "wall_count": 3,
                "dangling_endpoints": [{"id": i} for i in range(3)],
                "near_miss_corners": [{"id": i} for i in range(3)],
            },
        }

    monkeypatch.setattr(mcp_server, "_cv_get", fake_get)

    result = asyncio.run(mcp_server.wall_topology_qa("house-x", "eg.jpg", max_items=1))

    data = result["data"]
    assert len(data["dangling_endpoints"]) == 1
    assert len(data["near_miss_corners"]) == 1
    assert data["truncated"] is True
