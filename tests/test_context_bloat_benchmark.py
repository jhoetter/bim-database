from __future__ import annotations

import asyncio
from pathlib import Path

import scripts.context_bloat_benchmark as bench


def test_render_markdown_reports_ratios() -> None:
    report = {
        "key": "house-x",
        "file": "eg.jpg",
        "pass": True,
        "before": {
            "full_house_bytes": 100,
            "full_labels_bytes": 100,
            "inline_scene_view_bytes": 100,
            "score_walls_unbounded_bytes": 100,
        },
        "after": {
            "house_summary_bytes": 10,
            "label_summary_bytes": 20,
            "handle_scene_view_bytes": 5,
            "score_walls_bounded_bytes": 50,
        },
        "ratios": {
            "house_summary_vs_full": 10.0,
            "label_summary_vs_full": 5.0,
            "image_handle_vs_inline": 20.0,
            "score_bounded_vs_unbounded": 2.0,
        },
        "quality_checks": {"image_sha256_equal": True},
    }

    md = bench.render_markdown(report)

    assert "Context Bloat Benchmark" in md
    assert "20.0x" in md
    assert "`image_sha256_equal`" in md


def test_run_benchmark_checks_handle_bytes_labels_and_score(monkeypatch, tmp_path: Path) -> None:
    handle = tmp_path / "img.png"
    handle.write_bytes(b"same-image")

    class Client:
        async def aclose(self) -> None:
            return None

    async def fake_setup():
        return Client()

    async def get_house(key: str):
        return {"ok": True, "data": {"drawings": [{"file": "eg.jpg", "label_count": 2}]}}

    async def house_summary(key: str):
        return {"ok": True, "data": {"scene_count": 1, "total_labels": 2}}

    async def api_get(path: str):
        return 200, {"labels": [{"id": "w1"}, {"id": "w2"}]}

    async def label_summary(key: str, file: str, max_labels: int = 20):
        return {"ok": True, "data": {"labels_total": 2, "labels": [{"id": "w1"}, {"id": "w2"}]}}

    async def scene_summary(key: str, file: str, max_labels: int = 20):
        return {"ok": True, "data": {"labels_total": 2}}

    async def scene_view(key: str, file: str, tiers: str, max_dim: int, image_delivery: str):
        if image_delivery == "inline":
            import base64
            from mcp.types import ImageContent, TextContent
            return [
                ImageContent(type="image", data=base64.b64encode(b"same-image").decode("ascii"), mimeType="image/png"),
                TextContent(type="text", text='{"ok":true,"data":{"image_delivery":"inline"}}'),
            ]
        from mcp.types import TextContent
        return [
            TextContent(
                type="text",
                text='{"ok":true,"data":{"image_delivery":"handle","image_handle":{"path":"%s"}}}' % handle,
            )
        ]

    async def score_walls(key: str, file: str, max_regions: int):
        return {"ok": True, "data": {"precision": 1.0, "recall": 0.9, "f1": 0.947, "n_walls": 2}}

    monkeypatch.setattr(bench, "_setup_client", fake_setup)
    monkeypatch.setattr(bench.mcp_server, "get_house", get_house)
    monkeypatch.setattr(bench.mcp_server, "get_house_context_summary", house_summary)
    monkeypatch.setattr(bench.mcp_server, "_api_get", api_get)
    monkeypatch.setattr(bench.mcp_server, "list_scene_labels", label_summary)
    monkeypatch.setattr(bench.mcp_server, "get_scene_context_summary", scene_summary)
    monkeypatch.setattr(bench.mcp_server, "get_scene_view", scene_view)
    monkeypatch.setattr(bench.mcp_server, "score_walls", score_walls)

    report = asyncio.run(bench.run_benchmark("house-x"))

    assert report["pass"] is True
    assert report["quality_checks"]["image_sha256_equal"] is True
    assert report["quality_checks"]["label_counts_equal"] is True
    assert report["quality_checks"]["score_scalar_quality_equal"] is True
    assert report["ratios"]["image_handle_vs_inline"] > 1
