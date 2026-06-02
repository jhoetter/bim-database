"""Deterministic floorplan opening candidates.

This module is intentionally a positional prior, not an opening recognizer.
It looks for low-ink gaps along existing parent-wall labels and packages them
as reviewable candidates so an agent can inspect/apply/reject one focused
opening at a time instead of guessing from the full drawing.
"""
from __future__ import annotations

import math
import hashlib
import json
from typing import Any

import numpy as np


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _wall_segment(label: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    geom = label.get("geometry") or {}
    a = _point(geom.get("start"))
    b = _point(geom.get("end"))
    if not a or not b:
        return None
    if math.hypot(b[0] - a[0], b[1] - a[1]) < 1:
        return None
    return a, b


def _opening_axis(label: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    quad = (label.get("geometry") or {}).get("quad") or []
    pts = [_point(p) for p in quad]
    if len(pts) != 4 or any(p is None for p in pts):
        return None
    p0, p1, p2, p3 = [p for p in pts if p is not None]
    return ((p0[0] + p3[0]) / 2, (p0[1] + p3[1]) / 2), ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)


def _bbox(points: list[tuple[float, float]], pad: float = 24.0) -> list[int]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [
        int(math.floor(min(xs) - pad)),
        int(math.floor(min(ys) - pad)),
        int(math.ceil(max(xs) + pad)),
        int(math.ceil(max(ys) + pad)),
    ]


def _fingerprint(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:14]


def _quad_for_span(
    a: tuple[float, float],
    ux: float,
    uy: float,
    px: float,
    py: float,
    start_t: float,
    end_t: float,
    half_width: float,
) -> list[list[int]]:
    p0 = (a[0] + ux * start_t, a[1] + uy * start_t)
    p1 = (a[0] + ux * end_t, a[1] + uy * end_t)
    return [
        [int(round(p0[0] + px * half_width)), int(round(p0[1] + py * half_width))],
        [int(round(p1[0] + px * half_width)), int(round(p1[1] + py * half_width))],
        [int(round(p1[0] - px * half_width)), int(round(p1[1] - py * half_width))],
        [int(round(p0[0] - px * half_width)), int(round(p0[1] - py * half_width))],
    ]


def _dark_fraction(gray: np.ndarray, x: float, y: float, px: float, py: float, half_width: float, thresh: int) -> float:
    h, w = gray.shape
    samples = max(5, int(round(half_width * 2)))
    dark = 0
    total = 0
    for i in range(samples):
        off = -half_width + (2 * half_width * i / max(1, samples - 1))
        sx = int(round(x + px * off))
        sy = int(round(y + py * off))
        if 0 <= sx < w and 0 <= sy < h:
            total += 1
            if int(gray[sy, sx]) < thresh:
                dark += 1
    return dark / max(1, total)


def _runs(values: list[bool], *, min_len: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = None
    for idx, value in enumerate(values):
        if value and start is None:
            start = idx
        elif not value and start is not None:
            if idx - start >= min_len:
                out.append((start, idx - 1))
            start = None
    if start is not None and len(values) - start >= min_len:
        out.append((start, len(values) - 1))
    return out


def opening_candidate_report(
    image,
    labels_doc: dict[str, Any],
    *,
    strip_half_width_px: float = 18.0,
    step_px: float = 4.0,
    min_gap_px: float = 28.0,
    max_gap_px: float = 260.0,
    endpoint_margin_px: float = 18.0,
    thresh: int = 180,
    limit: int = 40,
) -> dict[str, Any]:
    """Return reviewable opening candidates from saved wall/opening labels."""
    gray = np.asarray(image.convert("L"))
    labels = [lab for lab in labels_doc.get("labels") or [] if isinstance(lab, dict)]
    walls = [lab for lab in labels if lab.get("type") == "wall" and _wall_segment(lab)]
    openings = [lab for lab in labels if lab.get("type") == "floorplan_opening"]
    candidates: list[dict[str, Any]] = []

    for opening in openings:
        axis = _opening_axis(opening)
        if not axis:
            continue
        pts = [p for p in [_point(p) for p in (opening.get("geometry") or {}).get("quad") or []] if p is not None]
        parent_ids = [
            rel.get("other_id")
            for rel in opening.get("relations") or []
            if isinstance(rel, dict) and rel.get("kind") == "belongs_to"
        ]
        candidates.append({
            "candidate_id": f"OPEN-{len(candidates) + 1:03d}",
            "candidate_fingerprint": _fingerprint({
                "kind": "existing_opening",
                "label_id": opening.get("id"),
                "parent": parent_ids[0] if parent_ids else None,
                "axis": axis,
            }),
            "kind": "existing_opening",
            "confidence": "reviewed",
            "parent_wall_id": parent_ids[0] if parent_ids else None,
            "existing_label_id": opening.get("id"),
            "opening_kind": (opening.get("attributes") or {}).get("opening_kind") or "window",
            "centerline": [[round(axis[0][0], 1), round(axis[0][1], 1)], [round(axis[1][0], 1), round(axis[1][1], 1)]],
            "quad": (opening.get("geometry") or {}).get("quad"),
            "region": _bbox(pts) if pts else None,
            "instruction": "Existing opening label; verify parent wall, span, and swing/sash orientation.",
        })

    for wall in walls:
        seg = _wall_segment(wall)
        if not seg:
            continue
        a, b = seg
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < (min_gap_px + endpoint_margin_px * 2):
            continue
        ux, uy = dx / length, dy / length
        px, py = -uy, ux
        sample_count = max(2, int(length / max(1.0, step_px)) + 1)
        ts = [i * length / (sample_count - 1) for i in range(sample_count)]
        fractions = [
            _dark_fraction(
                gray,
                a[0] + ux * t,
                a[1] + uy * t,
                px,
                py,
                strip_half_width_px,
                thresh,
            )
            for t in ts
        ]
        median_dark = float(np.median(fractions)) if fractions else 0.0
        gap_threshold = max(0.04, median_dark * 0.35)
        low_ink = [
            endpoint_margin_px <= t <= length - endpoint_margin_px and frac <= gap_threshold
            for t, frac in zip(ts, fractions)
        ]
        min_run_samples = max(2, int(min_gap_px / max(1.0, step_px)))
        for start_idx, end_idx in _runs(low_ink, min_len=min_run_samples):
            start_t = ts[start_idx]
            end_t = ts[end_idx]
            gap_len = end_t - start_t
            if not (min_gap_px <= gap_len <= max_gap_px):
                continue
            before = fractions[max(0, start_idx - 3):start_idx]
            after = fractions[end_idx + 1:end_idx + 4]
            edge_dark = float(np.mean(before + after)) if (before or after) else median_dark
            gap_dark = float(np.mean(fractions[start_idx:end_idx + 1]))
            if edge_dark < max(0.08, median_dark * 0.5):
                continue
            quad = _quad_for_span(a, ux, uy, px, py, start_t, end_t, strip_half_width_px)
            confidence_score = max(0.0, min(1.0, (edge_dark - gap_dark) / max(0.01, edge_dark)))
            candidates.append({
                "candidate_id": f"OPEN-{len(candidates) + 1:03d}",
                "candidate_fingerprint": _fingerprint({
                    "kind": "wall_gap",
                    "parent": wall.get("id"),
                    "centerline": [
                        [round(a[0] + ux * start_t, 1), round(a[1] + uy * start_t, 1)],
                        [round(a[0] + ux * end_t, 1), round(a[1] + uy * end_t, 1)],
                    ],
                }),
                "kind": "wall_gap",
                "confidence": "high" if confidence_score >= 0.65 else "medium",
                "confidence_score": round(confidence_score, 3),
                "parent_wall_id": wall.get("id"),
                "opening_kind": "unknown",
                "span_px": round(gap_len, 1),
                "centerline": [
                    [round(a[0] + ux * start_t, 1), round(a[1] + uy * start_t, 1)],
                    [round(a[0] + ux * end_t, 1), round(a[1] + uy * end_t, 1)],
                ],
                "quad": quad,
                "region": _bbox([(float(x), float(y)) for x, y in quad]),
                "evidence": {
                    "median_wall_dark_fraction": round(median_dark, 3),
                    "gap_dark_fraction": round(gap_dark, 3),
                    "edge_dark_fraction": round(edge_dark, 3),
                    "gap_threshold": round(gap_threshold, 3),
                },
                "suggested_label": {
                    "type": "floorplan_opening",
                    "geometry": {"quad": quad},
                    "attributes": {"opening_kind": "window"},
                    "relations": [{"kind": "belongs_to", "other_id": wall.get("id")}],
                },
                "instruction": "Inspect the crop; if this is a true wall opening, set opening_kind and upsert the suggested floorplan_opening.",
            })
            if len(candidates) >= limit:
                break
        if len(candidates) >= limit:
            break

    return {
        "candidate_contract": "opening-candidates/v1",
        "count": len(candidates),
        "candidates": candidates[:limit],
        "params": {
            "strip_half_width_px": strip_half_width_px,
            "step_px": step_px,
            "min_gap_px": min_gap_px,
            "max_gap_px": max_gap_px,
            "endpoint_margin_px": endpoint_margin_px,
            "thresh": thresh,
            "limit": limit,
        },
        "note": "CV positional prior only; inspect candidate overlay before writing labels.",
    }
