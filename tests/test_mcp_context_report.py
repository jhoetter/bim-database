from __future__ import annotations

import json
from pathlib import Path

from scripts.mcp_context_report import analyze_mcp_log, analyze_tool_catalog, analyze_transcripts


def test_analyze_tool_catalog_counts_mcp_docstrings(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text(
        """
class M:
    def tool(self):
        def dec(fn):
            return fn
        return dec
mcp = M()

@mcp.tool()
async def small(key: str) -> dict:
    \"\"\"Small tool.\"\"\"
    return {}

async def ignored() -> None:
    pass
""",
    )

    report = analyze_tool_catalog([server])

    assert report["tool_count"] == 1
    assert report["doc_chars"] == len("Small tool.")
    assert report["largest_docstrings"][0]["name"] == "small"


def test_analyze_transcripts_maps_tool_results_and_images(tmp_path: Path) -> None:
    transcript = tmp_path / "run.jsonl"
    assistant = {
        "type": "assistant",
        "uuid": "assistant-1",
        "message": {
            "usage": {"input_tokens": 10, "output_tokens": 3},
            "content": [
                {
                    "type": "tool_use",
                    "id": "tool-1",
                    "name": "mcp__bim-database__get_scene_view",
                    "input": {},
                }
            ],
        },
    }
    result = {
        "type": "user",
        "sourceToolAssistantUUID": "assistant-1",
        "toolUseResult": {},
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": [{"type": "image", "source": {"type": "base64", "data": "abc"}}],
                }
            ]
        },
    }
    transcript.write_text(json.dumps(assistant) + "\n" + json.dumps(result) + "\n")

    report = analyze_transcripts([transcript], big_line_bytes=20)

    assert report["tool_result_count"] == 1
    assert report["mapped_tool_result_count"] == 1
    assert report["image_like_lines"] == 1
    assert report["big_lines"] == 2
    assert report["usage_totals"]["input_tokens"] == 10
    bucket = report["tool_results"]["mcp__bim-database__get_scene_view"]
    assert bucket["calls"] == 1
    assert bucket["bytes"] > 0


def test_analyze_mcp_log_counts_requests(tmp_path: Path) -> None:
    log = tmp_path / "mcp.log"
    log.write_text(
        "\n".join(
            [
                "2026 [INFO] startup: API_BASE=http://127.0.0.1:12500",
                "2026 [INFO] Processing request of type ListToolsRequest",
                "2026 [INFO] Processing request of type CallToolRequest",
                '2026 [INFO] HTTP Request: GET http://127.0.0.1:12500/datasets/house-22 "HTTP/1.1 200 OK"',
                '2026 [INFO] HTTP Request: GET http://127.0.0.1:12500/labels/dataset/house-22/house-22-floorplan-eg.jpg "HTTP/1.1 200 OK"',
            ]
        )
    )

    report = analyze_mcp_log([log])

    assert report["totals"]["startups"] == 1
    assert report["totals"]["list_tools"] == 1
    assert report["totals"]["call_tools"] == 1
    assert report["totals"]["http_requests"] == 2
    assert report["top_requests"][0]["path"] == "/datasets/house-22"
