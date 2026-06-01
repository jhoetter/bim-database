"""Compact handoff summaries for MCP-driven labeling runs."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

DATASET_DIR = Path(__file__).parent / "data" / "dataset"
SCHEMA_VERSION = "mcp-handoff-v1"


def write_scene_handoff(
    key: str,
    file: str,
    payload: dict[str, Any],
    *,
    dataset_dir: Path = DATASET_DIR,
) -> dict[str, Any]:
    now = int(time.time() * 1000)
    handoff = {
        "schema_version": SCHEMA_VERSION,
        "key": key,
        "file": file,
        "updated_at_ms": now,
        **payload,
    }
    root = _handoff_dir(key, dataset_dir)
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{_safe_slug(file)}.json"
    path.write_text(json.dumps(handoff, indent=2, sort_keys=True), encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size, "handoff": handoff}


def read_scene_handoff(
    key: str,
    file: str,
    *,
    dataset_dir: Path = DATASET_DIR,
) -> dict[str, Any] | None:
    path = _handoff_dir(key, dataset_dir) / f"{_safe_slug(file)}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_house_handoffs(
    key: str,
    *,
    dataset_dir: Path = DATASET_DIR,
) -> list[dict[str, Any]]:
    root = _handoff_dir(key, dataset_dir)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        try:
            handoff = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows.append({"path": str(path), "error": "invalid_json"})
            continue
        rows.append({
            "path": str(path),
            "bytes": path.stat().st_size,
            "key": handoff.get("key"),
            "file": handoff.get("file"),
            "phase": handoff.get("phase"),
            "status": handoff.get("status"),
            "summary": handoff.get("summary"),
            "open_defect_count": len(handoff.get("open_defects") or []),
            "uncertain_label_count": len(handoff.get("uncertain_labels") or []),
            "updated_at_ms": handoff.get("updated_at_ms"),
        })
    return rows


def _handoff_dir(key: str, dataset_dir: Path) -> Path:
    return dataset_dir / key / "handoffs"


def _safe_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._") or "scene"
