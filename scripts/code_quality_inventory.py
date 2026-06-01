#!/usr/bin/env python3
"""Inventory broad code-quality surfaces for the tracker."""
from __future__ import annotations

import argparse
import ast
import collections
import json
from pathlib import Path
from typing import Any


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}
MUTATING_METHODS = {"post", "put", "patch"}


def analyze_fastapi_routes(path: Path) -> dict[str, Any]:
    routes: list[dict[str, Any]] = []
    tree = ast.parse(path.read_text(errors="ignore"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            route = _route_from_decorator(decorator)
            if route is None:
                continue
            method, route_path, tags = route
            routes.append(
                {
                    "source": str(path),
                    "name": node.name,
                    "line": node.lineno,
                    "method": method.upper(),
                    "path": route_path,
                    "tags": tags,
                    "risk": classify_route(method, route_path),
                    "category": classify_route_category(route_path, tags),
                }
            )
    return _summarize_routes(sorted(routes, key=lambda item: item["line"]))


def analyze_mcp_tools(path: Path) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    tree = ast.parse(path.read_text(errors="ignore"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_mcp_tool_decorator(decorator) for decorator in node.decorator_list):
            continue
        doc = ast.get_docstring(node) or ""
        tools.append(
            {
                "source": str(path),
                "name": node.name,
                "line": node.lineno,
                "arg_count": len(node.args.args),
                "doc_chars": len(doc),
                "category": classify_tool_category(node.name),
                "payload": classify_tool_payload(node.name, doc),
            }
        )
    return _summarize_tools(sorted(tools, key=lambda item: item["line"]))


def classify_route(method: str, path: str) -> str:
    method = method.lower()
    lowered = path.lower()
    if method == "delete" or any(token in lowered for token in ("/reset", "/delete", "/restore", "/recycle")):
        return "destructive"
    if method in MUTATING_METHODS:
        return "mutating"
    return "read_only"


def classify_route_category(path: str, tags: list[str]) -> str:
    lowered = path.lower()
    if "plan-state" in lowered or lowered.endswith("/plan") or "/plan/" in lowered:
        return "scene_plans"
    if "label" in lowered:
        return "labels"
    if "pdf" in lowered or "submit" in lowered:
        return "pdfs"
    if any(token in lowered for token in ("grid", "render", "view", "page-view", "scene-view")):
        return "rendering"
    if any(token in lowered for token in ("wall", "dimension", "score", "geometry", "corner", "homography")):
        return "geometry_cv"
    if "export" in lowered:
        return "export"
    if tags:
        return tags[0]
    return "uncategorized"


def classify_tool_category(name: str) -> str:
    lowered = name.lower()
    if "plan" in lowered:
        return "scene_plans"
    if any(token in lowered for token in ("label", "tag", "orientation", "level")):
        return "labels"
    if any(token in lowered for token in ("pdf", "extract", "split", "house", "scene", "view")):
        return "datasets"
    if any(token in lowered for token in ("wall", "dimension", "corner", "homography", "geometry", "measure")):
        return "geometry_cv"
    if any(token in lowered for token in ("fact", "workflow", "readiness", "export", "anomal")):
        return "workflow_export"
    return "misc"


def classify_tool_payload(name: str, doc: str) -> str:
    lowered = f"{name}\n{doc}".lower()
    if "imagecontent" in lowered or "image" in lowered or "view" in lowered:
        return "image_or_large"
    if "markdown" in lowered or "summary" in lowered:
        return "large_text"
    return "json"


def render_markdown(report: dict[str, Any]) -> str:
    lines = ["# Code Quality Inventory", ""]
    routes = report.get("fastapi_routes") or {}
    if routes:
        lines += [
            "## FastAPI Routes",
            "",
            f"- Routes: {routes.get('count', 0)}",
            f"- By risk: {_format_counter(routes.get('by_risk') or {})}",
            f"- By category: {_format_counter(routes.get('by_category') or {})}",
            "",
            "| Method | Path | Function | Risk | Category |",
            "|---|---|---|---|---|",
        ]
        for item in routes.get("items", []):
            lines.append(
                f"| {item['method']} | `{item['path']}` | `{item['name']}` | "
                f"{item['risk']} | {item['category']} |"
            )
        lines.append("")

    tools = report.get("mcp_tools") or {}
    if tools:
        lines += [
            "## MCP Tools",
            "",
            f"- Tools: {tools.get('count', 0)}",
            f"- By category: {_format_counter(tools.get('by_category') or {})}",
            f"- By payload: {_format_counter(tools.get('by_payload') or {})}",
            "",
            "| Tool | Args | Doc chars | Category | Payload |",
            "|---|---:|---:|---|---|",
        ]
        for item in tools.get("items", []):
            lines.append(
                f"| `{item['name']}` | {item['arg_count']} | {item['doc_chars']} | "
                f"{item['category']} | {item['payload']} |"
            )
        lines.append("")
    return "\n".join(lines)


def build_report(api_path: Path, mcp_path: Path) -> dict[str, Any]:
    return {
        "fastapi_routes": analyze_fastapi_routes(api_path),
        "mcp_tools": analyze_mcp_tools(mcp_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="api/main.py")
    parser.add_argument("--mcp", default="mcp_server.py")
    parser.add_argument("--format", choices=("markdown", "json"), default="markdown")
    args = parser.parse_args()
    report = build_report(Path(args.api), Path(args.mcp))
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report))


def _route_from_decorator(decorator: ast.expr) -> tuple[str, str, list[str]] | None:
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "app"
        and func.attr in HTTP_METHODS
    ):
        return None
    if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
        return None
    route_path = decorator.args[0].value
    if not isinstance(route_path, str):
        return None
    return func.attr, route_path, _tags_from_keywords(decorator.keywords)


def _tags_from_keywords(keywords: list[ast.keyword]) -> list[str]:
    for keyword in keywords:
        if keyword.arg != "tags" or not isinstance(keyword.value, ast.List):
            continue
        tags: list[str] = []
        for item in keyword.value.elts:
            if isinstance(item, ast.Constant) and isinstance(item.value, str):
                tags.append(item.value)
        return tags
    return []


def _is_mcp_tool_decorator(decorator: ast.expr) -> bool:
    return (
        isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "tool"
        and isinstance(decorator.func.value, ast.Name)
        and decorator.func.value.id == "mcp"
    )


def _summarize_routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(routes),
        "by_risk": dict(collections.Counter(item["risk"] for item in routes)),
        "by_category": dict(collections.Counter(item["category"] for item in routes)),
        "items": routes,
    }


def _summarize_tools(tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(tools),
        "by_category": dict(collections.Counter(item["category"] for item in tools)),
        "by_payload": dict(collections.Counter(item["payload"] for item in tools)),
        "items": tools,
    }


def _format_counter(values: dict[str, int]) -> str:
    return ", ".join(f"{key}={value}" for key, value in sorted(values.items())) or "none"


if __name__ == "__main__":
    main()
