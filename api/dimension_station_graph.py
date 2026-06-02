"""Dimension-chain station graph helper.

The graph keeps the existing no-OCR contract: it detects the running line and
tick stations, then relates ticks to saved wall geometry. The model still reads
printed values from an image crop, but it no longer has to invent which tick
pair a label should span.
"""
from __future__ import annotations

import math
from typing import Any

from .dimension_chain import detect_dimension_chain


def _point(value: Any) -> tuple[float, float] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _wall_segments(labels_doc: dict[str, Any]) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    out = []
    for lab in labels_doc.get("labels") or []:
        if not isinstance(lab, dict) or lab.get("type") != "wall":
            continue
        geom = lab.get("geometry") or {}
        a = _point(geom.get("start"))
        b = _point(geom.get("end"))
        if a and b:
            out.append((str(lab.get("id") or ""), a, b))
    return out


def _point_segment_distance(p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
    vx, vy = b[0] - a[0], b[1] - a[1]
    wx, wy = p[0] - a[0], p[1] - a[1]
    denom = vx * vx + vy * vy
    if denom <= 0:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    q = (a[0] + vx * t, a[1] + vy * t)
    return math.hypot(p[0] - q[0], p[1] - q[1])


def dimension_station_graph(
    image,
    labels_doc: dict[str, Any],
    *,
    region: tuple[int, int, int, int] | None = None,
    orientation: str | None = None,
    thresh: int = 205,
    min_line_frac: float = 0.25,
    min_tick_px: int = 12,
    tick_search_px: int = 45,
    pad_px: int = 80,
    wall_anchor_tol_px: float = 28.0,
) -> dict[str, Any]:
    chain = detect_dimension_chain(
        image,
        region=region,
        orientation=orientation,  # type: ignore[arg-type]
        thresh=thresh,
        min_line_frac=min_line_frac,
        min_tick_px=min_tick_px,
        tick_search_px=tick_search_px,
        pad_px=pad_px,
    )
    walls = _wall_segments(labels_doc)
    stations = []
    for idx, tick in enumerate(chain.get("ticks") or []):
        pt = _point(tick)
        nearest = None
        if pt and walls:
            distances = [
                (wall_id, _point_segment_distance(pt, a, b))
                for wall_id, a, b in walls
            ]
            nearest = min(distances, key=lambda item: item[1])
        stations.append({
            "station_id": f"ST-{idx + 1:03d}",
            "point": tick,
            "nearest_wall_id": nearest[0] if nearest else None,
            "nearest_wall_distance_px": round(nearest[1], 1) if nearest else None,
            "anchor_status": (
                "wall_aligned"
                if nearest and nearest[1] <= wall_anchor_tol_px
                else "needs_visual_review"
            ),
        })
    spans = []
    ticks = chain.get("ticks") or []
    for idx in range(max(0, len(ticks) - 1)):
        a = ticks[idx]
        b = ticks[idx + 1]
        if not (_point(a) and _point(b)):
            continue
        pa = _point(a)
        pb = _point(b)
        assert pa is not None and pb is not None
        spans.append({
            "span_id": f"DSP-{idx + 1:03d}",
            "start_station_id": stations[idx]["station_id"],
            "end_station_id": stations[idx + 1]["station_id"],
            "start": a,
            "end": b,
            "length_px": round(math.hypot(pb[0] - pa[0], pb[1] - pa[1]), 1),
            "anchor_status": (
                "wall_aligned"
                if stations[idx]["anchor_status"] == "wall_aligned"
                and stations[idx + 1]["anchor_status"] == "wall_aligned"
                else "needs_visual_review"
            ),
            "suggested_dimensioned_distance": {"type": "dimensioned_distance", "geometry": {"start": a, "end": b}},
        })
    aligned_spans = [s for s in spans if s["anchor_status"] == "wall_aligned"]
    longest_span = max(spans, key=lambda s: s["length_px"], default=None)
    longest_aligned = max(aligned_spans, key=lambda s: s["length_px"], default=None)
    groups: list[dict[str, Any]] = []
    if spans:
        groups.append({
            "group_id": "CHAIN-001",
            "kind": "adjacent_dimension_chain",
            "span_ids": [s["span_id"] for s in spans],
            "station_ids": [s["station_id"] for s in stations],
            "anchor_status": "wall_aligned" if all(s["anchor_status"] == "wall_aligned" for s in spans) else "needs_visual_review",
            "instruction": "Read printed part values for each adjacent span; use only visually confirmed spans.",
        })
    if len(stations) >= 2:
        start = stations[0]["point"]
        end = stations[-1]["point"]
        pa = _point(start)
        pb = _point(end)
        if pa and pb:
            groups.append({
                "group_id": "CHAIN-OVERALL",
                "kind": "overall_dimension_span",
                "start_station_id": stations[0]["station_id"],
                "end_station_id": stations[-1]["station_id"],
                "start": start,
                "end": end,
                "length_px": round(math.hypot(pb[0] - pa[0], pb[1] - pa[1]), 1),
                "anchor_status": (
                    "wall_aligned"
                    if stations[0]["anchor_status"] == "wall_aligned"
                    and stations[-1]["anchor_status"] == "wall_aligned"
                    else "needs_visual_review"
                ),
                "suggested_dimensioned_distance": {"type": "dimensioned_distance", "geometry": {"start": start, "end": end}},
                "instruction": "Use when the printed chain has one overall value spanning all visible ticks.",
            })
    reference_candidates = []
    for rank, span in enumerate([longest_aligned, longest_span], start=1):
        if not span or any(existing.get("span_id") == span.get("span_id") for existing in reference_candidates):
            continue
        reference_candidates.append({
            "rank": rank,
            "span_id": span["span_id"],
            "start": span["start"],
            "end": span["end"],
            "length_px": span["length_px"],
            "anchor_status": span["anchor_status"],
            "reason": "longest wall-aligned span" if span is longest_aligned else "longest detected span",
        })
    return {
        "station_graph_contract": "dimension-station-graph/v1",
        "found": bool(chain.get("found")),
        "orientation": chain.get("orientation"),
        "line": chain.get("line"),
        "crop_region": chain.get("crop_region"),
        "station_count": len(stations),
        "span_count": len(spans),
        "stations": stations,
        "spans": spans,
        "groups": groups,
        "reference_candidates": reference_candidates,
        "unreviewed_station_count": len([s for s in stations if s["anchor_status"] != "wall_aligned"]),
        "chain_prior": {k: v for k, v in chain.items() if k not in {"ticks"}},
        "note": "No OCR. The model reads printed values from crop_region and attaches them to reviewed spans.",
    }
