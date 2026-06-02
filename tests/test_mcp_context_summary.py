from __future__ import annotations

import asyncio

import mcp_server


def test_get_scene_context_summary_truncates_labels(monkeypatch) -> None:
    labels = [
        {"id": f"W{i}", "type": "wall", "status": "readable", "geometry": {"start": [0, i], "end": [1, i]}}
        for i in range(5)
    ]

    async def fake_get(path: str, params=None):
        if path == "/datasets/house-x":
            return 200, {"drawings": [{"file": "eg.jpg", "kind": "floorplan", "floor": "eg", "labeled": True}]}
        if path == "/labels/dataset/house-x/eg.jpg":
            return 200, {"scene_tag": "grundriss", "scene_level": "eg", "image_size_px": [100, 100], "labels": labels}
        if path == "/datasets/house-x/eg.jpg/plan-state/status":
            return 404, {"detail": "no plan"}
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_api_get", fake_get)

    result = asyncio.run(mcp_server.get_scene_context_summary("house-x", "eg.jpg", max_labels=2))

    assert result["ok"], result
    data = result["data"]
    assert data["summary_contract"] == "mcp-context-bloat/scene-context-summary-v1"
    assert data["label_counts"] == {"wall": 5}
    assert len(data["labels"]) == 2
    assert data["labels_truncated"] is True
    assert data["plan"]["status"] == "missing"
    assert "geometry" not in data["labels"][0]


def test_get_house_context_summary_is_bounded(monkeypatch) -> None:
    async def fake_get(path: str, params=None):
        if path == "/datasets/house-x":
            return 200, {
                "drawings": [
                    {"file": "eg.jpg", "kind": "floorplan", "floor": "eg", "labeled": True},
                    {"file": "north.jpg", "kind": "elevation", "view": "north", "labeled": True},
                ]
            }
        if path == "/datasets/house-x/house_facts":
            return 404, {}
        if path == "/labels/dataset/house-x/eg.jpg":
            return 200, {
                "scene_tag": "grundriss",
                "scene_level": "eg",
                "labels": [{"id": "W1", "type": "wall", "geometry": {"start": [0, 0], "end": [1, 1]}}],
            }
        if path == "/labels/dataset/house-x/north.jpg":
            return 200, {
                "scene_tag": "ansicht",
                "scene_orientation": "north",
                "labels": [{"id": "O1", "type": "view_opening", "geometry": {"polygon": [[0, 0], [1, 0], [1, 1]]}}],
            }
        if path.endswith("/plan-state/status"):
            return 200, {"status": "needs_repair", "summary": "one blocker", "blockers": [{"id": "DEF-1"}]}
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_api_get", fake_get)

    result = asyncio.run(mcp_server.get_house_context_summary("house-x", include_plan_status=True))

    assert result["ok"], result
    data = result["data"]
    assert data["summary_contract"] == "mcp-context-bloat/house-context-summary-v1"
    assert data["scene_count"] == 2
    assert data["total_labels"] == 2
    assert data["scenes"][0]["file"] == "eg.jpg"
    assert data["scenes"][0]["plan"]["blocker_count"] == 1
    assert "labels" not in data["scenes"][0]


def test_list_scene_labels_reports_truncation(monkeypatch) -> None:
    async def fake_get(path: str, params=None):
        if path == "/labels/dataset/house-x/eg.jpg":
            return 200, {
                "scene_tag": "grundriss",
                "labels": [
                    {"id": "W1", "type": "wall", "geometry": {"start": [0, 0], "end": [1, 1]}},
                    {"id": "W2", "type": "wall", "geometry": {"start": [0, 1], "end": [1, 1]}},
                ],
            }
        raise AssertionError(path)

    monkeypatch.setattr(mcp_server, "_api_get", fake_get)

    result = asyncio.run(mcp_server.list_scene_labels("house-x", "eg.jpg", max_labels=1))

    assert result["ok"], result
    assert result["data"]["labels_total"] == 2
    assert result["data"]["labels_truncated"] is True
    assert len(result["data"]["labels"]) == 1
