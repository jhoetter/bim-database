#!/usr/bin/env python3
"""Run a local before/after context-bloat benchmark.

This is not a labeling agent. It measures representative MCP payloads for a
dataset scene and verifies that compact paths preserve quality-critical access:
same render bytes via handle mode, same label counts via summaries, and same
scalar QA metrics under bounded result modes.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api.main as api_main
import mcp_server


def _json_size(value: Any) -> int:
    return len(json.dumps(value, default=_content_to_dict, ensure_ascii=False).encode("utf-8"))


def _content_to_dict(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _parse_env(result: list[Any]) -> dict[str, Any]:
    text = result[-1].text
    return json.loads(text)


def _image_bytes_from_inline(result: list[Any]) -> bytes:
    import base64

    first = result[0]
    return base64.b64decode(first.data.encode("ascii"))


async def _setup_client() -> httpx.AsyncClient:
    if mcp_server._http is not None:
        await mcp_server._http.aclose()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api_main.app),
        base_url="http://test",
        timeout=httpx.Timeout(30.0),
    )
    mcp_server._http = client
    return client


async def run_benchmark(key: str, file: str | None = None) -> dict[str, Any]:
    client = await _setup_client()
    try:
        house = await mcp_server.get_house(key=key)
        if not house.get("ok"):
            raise SystemExit(house)
        drawings = house["data"].get("drawings") or []
        if not drawings:
            raise SystemExit(f"{key} has no drawings")
        target = file or _choose_scene_with_labels(drawings)
        if not target:
            raise SystemExit(f"{key} has no labeled scenes")

        full_house = await mcp_server.get_house(key=key)
        house_summary = await mcp_server.get_house_context_summary(key=key)
        full_labels_status, full_labels = await mcp_server._api_get(f"/labels/dataset/{key}/{target}")
        if full_labels_status >= 400:
            raise SystemExit(full_labels)
        label_summary = await mcp_server.list_scene_labels(key=key, file=target, max_labels=20)
        scene_summary = await mcp_server.get_scene_context_summary(key=key, file=target, max_labels=20)

        inline_view = await mcp_server.get_scene_view(
            key=key, file=target, tiers="broad", max_dim=600, image_delivery="inline",
        )
        handle_view = await mcp_server.get_scene_view(
            key=key, file=target, tiers="broad", max_dim=600, image_delivery="handle",
        )
        inline_env = _parse_env(inline_view)
        handle_env = _parse_env(handle_view)
        inline_bytes = _image_bytes_from_inline(inline_view)
        handle_path = Path(handle_env["data"]["image_handle"]["path"])
        handle_bytes = handle_path.read_bytes()

        full_score = await mcp_server.score_walls(key=key, file=target, max_regions=10_000)
        bounded_score = await mcp_server.score_walls(key=key, file=target, max_regions=1)
        score_quality_equal = _score_scalars(full_score) == _score_scalars(bounded_score)

        full_label_count = len(full_labels.get("labels") or [])
        compact_label_count = label_summary["data"]["labels_total"]
        scene_label_count = scene_summary["data"]["labels_total"]

        before = {
            "full_house_bytes": _json_size(full_house),
            "full_labels_bytes": _json_size(full_labels),
            "inline_scene_view_bytes": _json_size(inline_view),
            "score_walls_unbounded_bytes": _json_size(full_score),
        }
        after = {
            "house_summary_bytes": _json_size(house_summary),
            "label_summary_bytes": _json_size(label_summary),
            "scene_summary_bytes": _json_size(scene_summary),
            "handle_scene_view_bytes": _json_size(handle_view),
            "score_walls_bounded_bytes": _json_size(bounded_score),
        }
        return {
            "benchmark_contract": "mcp-context-bloat/benchmark-v1",
            "key": key,
            "file": target,
            "before": before,
            "after": after,
            "ratios": {
                "house_summary_vs_full": _ratio(before["full_house_bytes"], after["house_summary_bytes"]),
                "label_summary_vs_full": _ratio(before["full_labels_bytes"], after["label_summary_bytes"]),
                "image_handle_vs_inline": _ratio(before["inline_scene_view_bytes"], after["handle_scene_view_bytes"]),
                "score_bounded_vs_unbounded": _ratio(
                    before["score_walls_unbounded_bytes"],
                    after["score_walls_bounded_bytes"],
                ),
            },
            "quality_checks": {
                "image_sha256_equal": hashlib.sha256(inline_bytes).hexdigest() == hashlib.sha256(handle_bytes).hexdigest(),
                "inline_image_bytes": len(inline_bytes),
                "handle_image_bytes": len(handle_bytes),
                "full_label_count": full_label_count,
                "compact_label_count": compact_label_count,
                "scene_summary_label_count": scene_label_count,
                "label_counts_equal": full_label_count == compact_label_count == scene_label_count,
                "score_scalar_quality_equal": score_quality_equal,
                "score_scalars": _score_scalars(full_score),
            },
            "pass": (
                hashlib.sha256(inline_bytes).hexdigest() == hashlib.sha256(handle_bytes).hexdigest()
                and full_label_count == compact_label_count == scene_label_count
                and score_quality_equal
            ),
        }
    finally:
        await client.aclose()
        mcp_server._http = None


def _score_scalars(score: dict[str, Any]) -> dict[str, Any]:
    data = score.get("data") or {}
    return {
        key: data.get(key)
        for key in ("precision", "recall", "f1", "n_walls")
        if key in data
    }


def _ratio(before: int, after: int) -> float | None:
    if after <= 0:
        return None
    return round(before / after, 3)


def _choose_scene_with_labels(drawings: list[dict[str, Any]]) -> str | None:
    labeled = [d for d in drawings if d.get("label_count")]
    if labeled:
        return max(labeled, key=lambda d: d.get("label_count") or 0).get("file")
    return drawings[0].get("file") if drawings else None


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Context Bloat Benchmark",
        "",
        f"- House: `{report['key']}`",
        f"- Scene: `{report['file']}`",
        f"- Pass: `{report['pass']}`",
        "",
        "## Payloads",
        "",
        "| Path | Before bytes | After bytes | Reduction |",
        "|---|---:|---:|---:|",
    ]
    mapping = [
        ("House routing", "full_house_bytes", "house_summary_bytes", "house_summary_vs_full"),
        ("Label routing", "full_labels_bytes", "label_summary_bytes", "label_summary_vs_full"),
        ("Scene view", "inline_scene_view_bytes", "handle_scene_view_bytes", "image_handle_vs_inline"),
        ("Score walls", "score_walls_unbounded_bytes", "score_walls_bounded_bytes", "score_bounded_vs_unbounded"),
    ]
    for label, before_key, after_key, ratio_key in mapping:
        lines.append(
            f"| {label} | {report['before'][before_key]:,} | "
            f"{report['after'][after_key]:,} | {report['ratios'][ratio_key]}x |"
        )
    lines += [
        "",
        "## Quality Checks",
        "",
        "| Check | Value |",
        "|---|---|",
    ]
    for key, value in report["quality_checks"].items():
        lines.append(f"| `{key}` | `{value}` |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="house-22")
    parser.add_argument("--file")
    parser.add_argument("--format", choices=("json", "markdown"), default="markdown")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = asyncio.run(run_benchmark(args.key, args.file))
    text = json.dumps(report, indent=2, sort_keys=True) if args.format == "json" else render_markdown(report)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text)
    else:
        print(text, end="")
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
