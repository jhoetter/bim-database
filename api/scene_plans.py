"""Per-scene Markdown plans for agentic labeling.

Plans are intentionally plain Markdown artifacts stored next to the dataset
scene. They are the agent's working document: analysis, edit checklist, and
verification log. This module handles path safety, optimistic concurrency, and
small structured updates without making Markdown the source of label truth.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path
from typing import Any

from .persistence import atomic_write_text


PLAN_TEMPLATE_VERSION = "scene-plan-v1"
PLAN_STATUSES = {"draft", "active", "blocked", "complete"}
TASK_STATUSES = {
    "pending": " ",
    "todo": " ",
    "open": " ",
    "in_progress": "~",
    "active": "~",
    "done": "x",
    "complete": "x",
    "blocked": "!",
}


class PlanConflictError(RuntimeError):
    pass


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _bad_part(value: str) -> bool:
    return "/" in value or "\\" in value or ".." in value or not value


def plan_path(dataset_root: Path, key: str, file: str) -> Path:
    if _bad_part(key) or _bad_part(file):
        raise ValueError("bad key or file")
    return dataset_root / key / "plans" / f"{Path(file).stem}.md"


def plan_rel_path(dataset_root: Path, key: str, file: str) -> str:
    return str(plan_path(dataset_root, key, file).relative_to(dataset_root.parent))


def version_for(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()[:16]


def read_plan(dataset_root: Path, key: str, file: str) -> dict[str, Any]:
    p = plan_path(dataset_root, key, file)
    if not p.exists():
        return {
            "exists": False,
            "key": key,
            "file": file,
            "path": plan_rel_path(dataset_root, key, file),
            "markdown": "",
            "version": None,
            "status": None,
            "template_version": PLAN_TEMPLATE_VERSION,
            "last_updated": None,
        }
    markdown = p.read_text()
    return {
        "exists": True,
        "key": key,
        "file": file,
        "path": str(p.relative_to(dataset_root.parent)),
        "markdown": markdown,
        "version": version_for(markdown),
        "status": parse_status(markdown),
        "template_version": parse_template_version(markdown) or PLAN_TEMPLATE_VERSION,
        "last_updated": dt.datetime.fromtimestamp(
            p.stat().st_mtime, tz=dt.timezone.utc
        ).replace(microsecond=0).isoformat(),
    }


def parse_status(markdown: str) -> str | None:
    m = re.search(r"^Status:\s*([a-zA-Z_-]+)\s*$", markdown, flags=re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip()


def parse_template_version(markdown: str) -> str | None:
    m = re.search(r"^Template:\s*(\S+)\s*$", markdown, flags=re.MULTILINE)
    return m.group(1).strip() if m else None


def build_template(
    *,
    key: str,
    file: str,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = None,
) -> str:
    now = _now_iso()
    level = level_or_orientation or "unknown"
    author = created_by or "agent"
    return f"""# Scene plan: {key} / {file}

Status: draft
Template: {PLAN_TEMPLATE_VERSION}
Scene tag: {scene_tag}
Level/orientation: {level}
Created by: {author}
Created at: {now}
Last updated: {now}

## 1. Analysis Summary

- Source view(s) inspected:
- Drawing role:
- Scale / calibration evidence:
- Readable dimension chains:
- Height/datum evidence:
- North/orientation evidence:

## 2. Silhouette And Masses

- Connected masses:
- Outer silhouette hypothesis:
- Clockwise exterior corner sequence:
- Separate structures:
- Excluded non-walls:

## 3. Ambiguities And Risks

- Door/window symbols that may interrupt wall ink:
- Dashed/projection/site/furniture lines that must not become walls:
- Low-confidence regions:
- Cross-scene facts needed:

## 4. Task List

- [ ] A1 analysis: outer wall silhouette and mass decomposition
- [ ] E1 edit: place outer walls
- [ ] V1 verify: outer wall topology + score walls
- [ ] A2 analysis: interior walls
- [ ] E2 edit: place interior walls
- [ ] V2 verify: interior topology + score walls
- [ ] A3 analysis: openings after parent walls
- [ ] E3 edit: place openings with parent relations
- [ ] V3 verify: opening-on-wall placement
- [ ] A4 analysis: enumerate measurements after walls/openings
- [ ] E4 edit: label measurements/reference dims
- [ ] V4 verify: measurement score/visual QA
- [ ] Final QA

## 5. Decision Log

| Time | Mode | Evidence | Decision | Result |
|---|---|---|---|---|

## 6. Final Verification

- Wall score:
- Measurement score:
- Topology QA:
- Label counts:
- Uncertain labels:
- Remaining blockers:
"""


def _touch_last_updated(markdown: str) -> str:
    now = _now_iso()
    if re.search(r"^Last updated:", markdown, flags=re.MULTILINE):
        return re.sub(r"^Last updated:.*$", f"Last updated: {now}", markdown, flags=re.MULTILINE)
    return markdown.rstrip() + f"\nLast updated: {now}\n"


def write_plan(
    dataset_root: Path,
    key: str,
    file: str,
    markdown: str,
    *,
    expected_version: str | None = None,
    create_only: bool = False,
) -> dict[str, Any]:
    p = plan_path(dataset_root, key, file)
    if create_only and p.exists():
        raise PlanConflictError("plan already exists")
    if p.exists() and expected_version is not None:
        current = p.read_text()
        if version_for(current) != expected_version:
            raise PlanConflictError("plan version conflict")
    markdown = _touch_last_updated(markdown)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, markdown)
    return read_plan(dataset_root, key, file)


def create_plan_from_template(
    dataset_root: Path,
    key: str,
    file: str,
    *,
    scene_tag: str = "nicht_klassifiziert",
    level_or_orientation: str | None = None,
    created_by: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    markdown = build_template(
        key=key,
        file=file,
        scene_tag=scene_tag,
        level_or_orientation=level_or_orientation,
        created_by=created_by,
    )
    return write_plan(dataset_root, key, file, markdown, create_only=not overwrite)


def append_log(
    dataset_root: Path,
    key: str,
    file: str,
    *,
    mode: str,
    evidence: str,
    decision: str,
    result: str,
    expected_version: str | None = None,
) -> dict[str, Any]:
    current = read_plan(dataset_root, key, file)
    markdown = current["markdown"] if current["exists"] else build_template(key=key, file=file)
    if expected_version is not None and current["version"] != expected_version:
        raise PlanConflictError("plan version conflict")
    row = f"| {_now_iso()} | {mode} | {evidence} | {decision} | {result} |"
    if "| Time | Mode | Evidence | Decision | Result |" in markdown:
        markdown = re.sub(
            r"(\| Time \| Mode \| Evidence \| Decision \| Result \|\n\|---\|---\|---\|---\|---\|)",
            rf"\1\n{row}",
            markdown,
            count=1,
        )
    else:
        markdown = markdown.rstrip() + "\n\n## Decision Log\n\n| Time | Mode | Evidence | Decision | Result |\n|---|---|---|---|---|\n" + row + "\n"
    return write_plan(dataset_root, key, file, markdown)


def set_task_status(
    dataset_root: Path,
    key: str,
    file: str,
    *,
    task_id: str,
    status: str,
    note: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    current = read_plan(dataset_root, key, file)
    if not current["exists"]:
        raise FileNotFoundError("plan does not exist")
    if expected_version is not None and current["version"] != expected_version:
        raise PlanConflictError("plan version conflict")
    marker = TASK_STATUSES.get(status)
    if marker is None:
        raise ValueError(f"unknown task status {status!r}")

    lines = current["markdown"].splitlines()
    task_re = re.compile(
        rf"^(\s*-\s*\[)(?: |x|X|~|!)(\]\s*(?:\*\*)?{re.escape(task_id)}\b.*)$"
    )
    found = False
    out: list[str] = []
    for line in lines:
        m = task_re.match(line)
        if m and not found:
            out.append(f"{m.group(1)}{marker}{m.group(2)}")
            if note:
                out.append(f"  - note: {note}")
            found = True
        else:
            out.append(line)
    if not found:
        raise KeyError(f"task {task_id!r} not found")
    return write_plan(dataset_root, key, file, "\n".join(out) + "\n")


def plan_has_analysis_summary(markdown: str) -> bool:
    m = re.search(
        r"## 1\. Analysis Summary(?P<body>.*?)(?:\n## |\Z)",
        markdown,
        flags=re.DOTALL,
    )
    if not m:
        return False
    body = m.group("body")
    # A blank template has only keys ending in ':'; any non-empty value or
    # additional prose means the agent actually wrote analysis.
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("- Source view(s) inspected:") or line.endswith(":"):
            continue
        return True
    return any(
        ":" in line and line.split(":", 1)[1].strip()
        for line in body.splitlines()
        if line.strip().startswith("- ")
    )


def task_done(markdown: str, task_id: str) -> bool:
    return bool(
        re.search(rf"^\s*-\s*\[[xX]\]\s*(?:\*\*)?{re.escape(task_id)}\b", markdown, re.MULTILINE)
    )
