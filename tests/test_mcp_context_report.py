from __future__ import annotations

import json
from pathlib import Path

from scripts.mcp_context_report import (
    analyze_dataset,
    analyze_mcp_log,
    analyze_plan_quality,
    analyze_tool_catalog,
    analyze_transcripts,
    render_markdown,
    _load_profiles,
)


def test_analyze_tool_catalog_counts_mcp_docstrings(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text(
        '''
class M:
    def tool(self):
        def dec(fn):
            return fn
        return dec
mcp = M()

@mcp.tool()
async def small(key: str) -> dict:
    """Small tool."""
    return {}

async def ignored() -> None:
    pass
''',
    )

    report = analyze_tool_catalog([server])

    assert report["tool_count"] == 1
    assert report["doc_chars"] == len("Small tool.")
    assert report["largest_docstrings"][0]["name"] == "small"


def test_analyze_tool_catalog_reports_profile_sizes(tmp_path: Path) -> None:
    server = tmp_path / "server.py"
    server.write_text(
        '''
class M:
    def tool(self):
        def dec(fn):
            return fn
        return dec
mcp = M()

@mcp.tool()
async def a() -> dict:
    """AAAA"""
    return {}

@mcp.tool()
async def b() -> dict:
    """BBBBBBBB"""
    return {}
''',
    )

    report = analyze_tool_catalog([server], profiles={"small": {"a"}})

    assert report["profiles"]["small"]["tool_count"] == 1
    assert report["profiles"]["small"]["doc_chars"] == 4


def test_load_profiles_reads_annotated_tool_profile_assignment(tmp_path: Path) -> None:
    source = tmp_path / "mcp_server.py"
    source.write_text('_TOOL_PROFILES: dict[str, set[str]] = {"floorplan": {"a", "b"}}\n')

    assert _load_profiles(str(source)) == {"floorplan": {"a", "b"}}


def test_analyze_transcripts_maps_tool_results_images_and_quality(tmp_path: Path) -> None:
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
        "toolUseResult": {
            "precision": 0.9,
            "recall": 0.8,
            "missing_regions": [{"bbox": [1, 2, 3, 4]}],
        },
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
    assert report["inline_image_result_count"] == 1
    assert report["image_like_lines"] == 1
    assert report["big_lines"] == 2
    assert report["usage_totals"]["input_tokens"] == 10
    bucket = report["tool_results"]["mcp__bim-database__get_scene_view"]
    assert bucket["calls"] == 1
    assert bucket["inline_image_results"] == 1
    assert bucket["bytes"] > 0
    assert report["quality_signals"]["precision"]["latest"] == 0.9
    assert report["quality_signals"]["missing_regions"]["latest"] == "1 item(s)"


def test_analyze_mcp_log_counts_requests_and_state_reads(tmp_path: Path) -> None:
    log = tmp_path / "mcp.log"
    log.write_text(
        "\n".join(
            [
                "2026 [INFO] startup: API_BASE=http://127.0.0.1:12500",
                "2026 [INFO] Processing request of type ListToolsRequest",
                "2026 [INFO] Processing request of type CallToolRequest",
                '2026 [INFO] HTTP Request: GET http://127.0.0.1:12500/datasets/house-22 "HTTP/1.1 200 OK"',
                '2026 [INFO] HTTP Request: GET http://127.0.0.1:12500/labels/dataset/house-22/house-22-floorplan-eg.jpg "HTTP/1.1 200 OK"',
                '2026 [INFO] HTTP Request: POST http://127.0.0.1:12500/datasets/house-22/house-22-floorplan-eg.jpg/plan-state/evidence "HTTP/1.1 200 OK"',
            ]
        )
    )

    report = analyze_mcp_log([log])

    assert report["totals"]["startups"] == 1
    assert report["totals"]["list_tools"] == 1
    assert report["totals"]["call_tools"] == 1
    assert report["totals"]["http_requests"] == 3
    assert report["totals"]["state_reads"] == 2
    assert report["top_requests"][0]["path"] == "/datasets/house-22"
    assert report["top_state_reads"][0]["path"] == "/datasets/house-22"


def test_analyze_dataset_samples_payload_sizes(tmp_path: Path) -> None:
    root = tmp_path / "house-1"
    (root / "labels").mkdir(parents=True)
    (root / "plans").mkdir()
    (root / "manifest.json").write_text('{"key":"house-1","drawings":[]}')
    (root / "labels" / "a.json").write_text('{"labels":[]}')
    (root / "plans" / "a.json").write_text('{"state":{"evidence":[]}}')
    (root / "plans" / "a.md").write_text("# Plan\n")

    report = analyze_dataset([root])

    assert report["totals"]["manifest_files"] == 1
    assert report["totals"]["labels_files"] == 1
    assert report["totals"]["plan_state_files"] == 1
    assert report["totals"]["plan_markdown_files"] == 1
    assert report["largest_files"][0]["bytes"] > 0


def test_analyze_plan_quality_summarizes_plan_states(tmp_path: Path) -> None:
    root = tmp_path / "house-1"
    (root / "plans").mkdir(parents=True)
    (root / "plans" / "eg.plan.json").write_text(json.dumps({
        "key": "house-1",
        "file": "eg.png",
        "scene_tag": "grundriss",
        "status": "verified",
        "defects": [
            {"id": "DEF-1", "status": "open", "severity": "warning"},
            {"id": "DEF-2", "status": "fixed", "severity": "blocker"},
        ],
        "current_state": {
            "terminality": {"terminal": True, "status": "verified", "percent_complete": 100},
            "repair_candidate_decisions": {"r1": {"outcome": "accepted_applied"}},
            "opening_candidate_decisions": {"o1": {"outcome": "accepted_applied"}},
            "findings": {"count": 1},
            "finding_clusters": {"count": 1},
        },
    }))

    report = analyze_plan_quality([root])

    assert report["totals"]["plan_states"] == 1
    assert report["totals"]["terminal_scenes"] == 1
    assert report["totals"]["open_warnings"] == 1
    assert report["totals"]["opening_candidate_decisions"] == 1
    assert report["scenes"][0]["file"] == "eg.png"


def test_render_markdown_includes_quality_and_dataset_sections(tmp_path: Path) -> None:
    report = {
        "transcripts": {
            "files": ["run.jsonl"],
            "total_bytes": 100,
            "tool_result_count": 1,
            "inline_image_result_count": 1,
            "image_like_lines": 1,
            "big_line_bytes": 50,
            "big_lines": 1,
            "usage_totals": {"input_tokens": 10},
            "tool_results": {
                "get_scene_view": {
                    "calls": 1,
                    "bytes": 100,
                    "inline_image_results": 1,
                    "inline_image_bytes": 90,
                    "max_bytes": 100,
                    "avg_bytes": 100,
                }
            },
            "quality_signals": {"f1": {"count": 1, "latest": 0.95, "max": 0.95}},
        },
        "dataset": {
            "totals": {"labels_files": 1, "labels_bytes": 20},
            "largest_files": [{"path": "labels/a.json", "kind": "labels", "bytes": 20}],
        },
        "plan_quality": {
            "totals": {"plan_states": 1, "terminal_scenes": 1, "open_warnings": 2},
            "quality_warnings": ["2 open warnings"],
            "scenes": [{"file": "eg.png", "status": "verified", "percent_complete": 100, "open_blockers": 0, "open_warnings": 2, "repair_candidate_decisions": 1, "opening_candidate_decisions": 1}],
        },
    }

    md = render_markdown(report)

    assert "Inline image results" in md
    assert "Quality Signals" in md
    assert "`f1`" in md
    assert "Dataset Payload Samples" in md
    assert "Plan Quality" in md
    assert "Opening decisions" in md
