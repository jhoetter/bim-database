from __future__ import annotations

import asyncio

import mcp_server


def test_bounded_payload_truncates_known_high_cardinality_keys() -> None:
    data = {
        "score": {"f1": 0.7},
        "missing_regions": [{"id": i, "polygon": [[i, i], [i + 1, i]]} for i in range(5)],
        "line": [[0, 0], [1, 1]],
    }

    bounded, meta = mcp_server._bounded_payload(data, max_items=2)

    assert len(bounded["missing_regions"]) == 2
    assert bounded["line"] == [[0, 0], [1, 1]]
    assert bounded["_bounds"]["truncated"] is True
    assert meta["omitted_counts"]["missing_regions"] == 3


def test_bounded_payload_summary_only_removes_heavy_keys() -> None:
    bounded, meta = mcp_server._bounded_payload(
        {"markdown": "x" * 1000, "debug": {"raw": [1, 2]}, "status": "needs_repair"},
        summary_only=True,
    )

    assert "markdown" not in bounded
    assert "debug" not in bounded
    assert bounded["status"] == "needs_repair"
    assert meta["truncated"] is True


def test_list_scene_labels_respects_max_labels(monkeypatch) -> None:
    async def fake_get(path: str, params=None):
        assert path == "/labels/dataset/house-x/scene.jpg"
        return 200, {
            "scene_tag": "grundriss",
            "labels": [
                {"id": f"L{i}", "type": "wall", "status": "readable", "geometry": {"start": [0, i], "end": [1, i]}}
                for i in range(5)
            ],
        }

    monkeypatch.setattr(mcp_server, "_api_get", fake_get)

    result = asyncio.run(mcp_server.list_scene_labels("house-x", "scene.jpg", max_labels=2))

    assert result["ok"], result
    assert result["data"]["count"] == 5
    assert result["data"]["returned"] == 2
    assert result["data"]["truncated"] is True
