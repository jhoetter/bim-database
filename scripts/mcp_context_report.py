#!/usr/bin/env python3
"""Measure MCP context pressure from local agent artifacts.

The script is intentionally dependency-free so it can run against archived
Claude JSONL files and MCP logs without starting the app.
"""
from __future__ import annotations

import argparse
import ast
import collections
import json
import re
from pathlib import Path
from typing import Any


BIG_LINE_BYTES = 50_000


def analyze_tool_catalog(paths: list[Path]) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        try:
            tree = ast.parse(path.read_text(errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _has_mcp_tool_decorator(node):
                continue
            doc = ast.get_docstring(node) or ""
            args = [arg.arg for arg in node.args.args]
            tools.append(
                {
                    "source": str(path),
                    "name": node.name,
                    "doc_chars": len(doc),
                    "doc_words": len(doc.split()),
                    "arg_count": len(args),
                }
            )
    total_chars = sum(t["doc_chars"] for t in tools)
    rough_chars = total_chars + sum(len(t["name"]) + t["arg_count"] * 12 for t in tools)
    return {
        "tool_count": len(tools),
        "doc_chars": total_chars,
        "doc_words": sum(t["doc_words"] for t in tools),
        "rough_catalog_chars": rough_chars,
        "rough_catalog_tokens_char4": rough_chars // 4,
        "largest_docstrings": sorted(tools, key=lambda t: t["doc_chars"], reverse=True)[:20],
    }


def analyze_transcripts(paths: list[Path], big_line_bytes: int = BIG_LINE_BYTES) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "files": [],
        "total_bytes": 0,
        "lines": 0,
        "json_lines": 0,
        "tool_result_count": 0,
        "mapped_tool_result_count": 0,
        "image_like_lines": 0,
        "big_lines": 0,
        "big_line_bytes": big_line_bytes,
        "largest_lines": [],
        "usage_totals": collections.Counter(),
        "tool_results": collections.defaultdict(lambda: {"calls": 0, "bytes": 0, "max_bytes": 0}),
        "tool_mentions": collections.Counter(),
    }
    tool_use_id_to_name: dict[str, str] = {}
    assistant_uuid_to_names: dict[str, list[str]] = collections.defaultdict(list)

    for path in _expand_files(paths):
        if not path.is_file():
            continue
        stats["files"].append(str(path))
        stats["total_bytes"] += path.stat().st_size
        with path.open(errors="ignore") as handle:
            for line_no, line in enumerate(handle, 1):
                stats["lines"] += 1
                line_len = len(line.encode("utf-8", errors="ignore"))
                _record_largest(stats["largest_lines"], line_len, path, line_no)
                if line_len >= big_line_bytes:
                    stats["big_lines"] += 1
                if _line_looks_like_image(line):
                    stats["image_like_lines"] += 1
                for name in re.findall(r'"name"\s*:\s*"([^"]+)"', line):
                    stats["tool_mentions"][name] += 1
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stats["json_lines"] += 1
                _collect_usage(obj, stats["usage_totals"])
                _index_tool_uses(obj, tool_use_id_to_name, assistant_uuid_to_names)
                if "toolUseResult" in obj:
                    stats["tool_result_count"] += 1
                    name = _resolve_tool_result_name(obj, tool_use_id_to_name, assistant_uuid_to_names)
                    if name != "unknown":
                        stats["mapped_tool_result_count"] += 1
                    bucket = stats["tool_results"][name]
                    bucket["calls"] += 1
                    bucket["bytes"] += line_len
                    bucket["max_bytes"] = max(bucket["max_bytes"], line_len)

    stats["usage_totals"] = dict(stats["usage_totals"])
    stats["tool_mentions"] = dict(stats["tool_mentions"].most_common(100))
    stats["tool_results"] = {
        name: {
            **bucket,
            "avg_bytes": int(bucket["bytes"] / bucket["calls"]) if bucket["calls"] else 0,
        }
        for name, bucket in sorted(
            stats["tool_results"].items(),
            key=lambda item: item[1]["bytes"],
            reverse=True,
        )
    }
    return stats


def analyze_mcp_log(paths: list[Path]) -> dict[str, Any]:
    request_counts: collections.Counter[tuple[str, str]] = collections.Counter()
    scene_counts: collections.Counter[str] = collections.Counter()
    totals = collections.Counter()
    for path in _expand_files(paths):
        if not path.is_file():
            continue
        with path.open(errors="ignore") as handle:
            for line in handle:
                if "startup:" in line:
                    totals["startups"] += 1
                if "ListToolsRequest" in line:
                    totals["list_tools"] += 1
                if "CallToolRequest" in line:
                    totals["call_tools"] += 1
                match = re.search(r"HTTP Request: (GET|POST|PUT|DELETE|PATCH) http://[^/]+([^ ?\"]+)", line)
                if match:
                    totals["http_requests"] += 1
                    request_counts[(match.group(1), match.group(2))] += 1
                for scene in re.findall(r"house-\d+-[^/? \"]+", line):
                    scene_counts[scene] += 1
    return {
        "totals": dict(totals),
        "top_requests": [
            {"method": method, "path": path, "count": count}
            for (method, path), count in request_counts.most_common(50)
        ],
        "top_scenes": [
            {"scene": scene, "count": count}
            for scene, count in scene_counts.most_common(30)
        ],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# MCP Context Report", ""]
    catalog = report.get("tool_catalog") or {}
    if catalog:
        lines += [
            "## Tool Catalog",
            "",
            f"- Tools: {catalog.get('tool_count', 0)}",
            f"- Doc chars: {catalog.get('doc_chars', 0):,}",
            f"- Rough catalog chars: {catalog.get('rough_catalog_chars', 0):,}",
            f"- Rough char/4 token estimate: {catalog.get('rough_catalog_tokens_char4', 0):,}",
            "",
            "| Tool | Source | Doc chars | Args |",
            "|---|---|---:|---:|",
        ]
        for item in catalog.get("largest_docstrings", [])[:10]:
            lines.append(
                f"| `{item['name']}` | `{Path(item['source']).name}` | "
                f"{item['doc_chars']:,} | {item['arg_count']} |"
            )
        lines.append("")

    transcript = report.get("transcripts") or {}
    if transcript:
        usage = transcript.get("usage_totals") or {}
        lines += [
            "## Transcripts",
            "",
            f"- Files: {len(transcript.get('files', []))}",
            f"- Total bytes: {transcript.get('total_bytes', 0):,}",
            f"- Tool results: {transcript.get('tool_result_count', 0):,}",
            f"- Image-like lines: {transcript.get('image_like_lines', 0):,}",
            f"- Lines >= {transcript.get('big_line_bytes', BIG_LINE_BYTES):,} bytes: {transcript.get('big_lines', 0):,}",
            f"- Input tokens: {usage.get('input_tokens', 0):,}",
            f"- Cache creation input tokens: {usage.get('cache_creation_input_tokens', 0):,}",
            f"- Cache read input tokens: {usage.get('cache_read_input_tokens', 0):,}",
            f"- Output tokens: {usage.get('output_tokens', 0):,}",
            "",
            "| Tool result | Calls | Total MB | Max KB | Avg KB |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, item in list((transcript.get("tool_results") or {}).items())[:15]:
            lines.append(
                f"| `{name}` | {item['calls']:,} | {item['bytes'] / 1_000_000:.2f} | "
                f"{item['max_bytes'] / 1_000:.1f} | {item['avg_bytes'] / 1_000:.1f} |"
            )
        lines.append("")

    log = report.get("mcp_log") or {}
    if log:
        totals = log.get("totals") or {}
        lines += [
            "## MCP Log",
            "",
            f"- Startups: {totals.get('startups', 0):,}",
            f"- ListTools requests: {totals.get('list_tools', 0):,}",
            f"- CallTool requests: {totals.get('call_tools', 0):,}",
            f"- HTTP requests: {totals.get('http_requests', 0):,}",
            "",
            "| Count | Method | Path |",
            "|---:|---|---|",
        ]
        for item in log.get("top_requests", [])[:15]:
            lines.append(f"| {item['count']:,} | {item['method']} | `{item['path']}` |")
        lines.append("")
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    report: dict[str, Any] = {}
    if args.catalog:
        report["tool_catalog"] = analyze_tool_catalog([Path(p) for p in args.catalog])
    if args.transcript:
        report["transcripts"] = analyze_transcripts([Path(p) for p in args.transcript], args.big_line_bytes)
    if args.mcp_log:
        report["mcp_log"] = analyze_mcp_log([Path(p) for p in args.mcp_log])
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", action="append", default=[], help="Python MCP server file to inspect")
    parser.add_argument("--transcript", action="append", default=[], help="Claude JSONL file or directory")
    parser.add_argument("--mcp-log", action="append", default=[], help="MCP server log file or directory")
    parser.add_argument("--big-line-bytes", type=int, default=BIG_LINE_BYTES)
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    report = build_report(args)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))


def _has_mcp_tool_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
        ):
            return True
    return False


def _expand_files(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*") if p.is_file()))
        else:
            out.append(path)
    return out


def _collect_usage(value: Any, totals: collections.Counter[str]) -> None:
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            for key, val in usage.items():
                if isinstance(val, int):
                    totals[key] += val
        for child in value.values():
            _collect_usage(child, totals)
    elif isinstance(value, list):
        for child in value:
            _collect_usage(child, totals)


def _index_tool_uses(
    obj: dict[str, Any],
    tool_use_id_to_name: dict[str, str],
    assistant_uuid_to_names: dict[str, list[str]],
) -> None:
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return
    for item in content:
        if isinstance(item, dict) and item.get("type") == "tool_use":
            tool_id = item.get("id")
            name = item.get("name")
            if isinstance(tool_id, str) and isinstance(name, str):
                tool_use_id_to_name[tool_id] = name
                uuid = obj.get("uuid")
                if isinstance(uuid, str):
                    assistant_uuid_to_names[uuid].append(name)


def _resolve_tool_result_name(
    obj: dict[str, Any],
    tool_use_id_to_name: dict[str, str],
    assistant_uuid_to_names: dict[str, list[str]],
) -> str:
    message = obj.get("message") if isinstance(obj.get("message"), dict) else {}
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                name = tool_use_id_to_name.get(str(item.get("tool_use_id")))
                if name:
                    return name
    source_uuid = obj.get("sourceToolAssistantUUID")
    if isinstance(source_uuid, str) and assistant_uuid_to_names.get(source_uuid):
        return assistant_uuid_to_names[source_uuid][-1]
    return "unknown"


def _line_looks_like_image(line: str) -> bool:
    return (
        '"type":"image"' in line
        or '"type": "image"' in line
        or '"base64"' in line
        or '"data:image/' in line
    )


def _record_largest(largest: list[dict[str, Any]], size: int, path: Path, line_no: int) -> None:
    largest.append({"bytes": size, "file": str(path), "line": line_no})
    largest.sort(key=lambda item: item["bytes"], reverse=True)
    del largest[20:]


if __name__ == "__main__":
    main()
