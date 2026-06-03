"""Topology finding clustering and deterministic repair candidates.

The scene-plan state stores an audit trail of defects. This module computes
the current machine view: stable finding fingerprints, spatial clusters, and
candidate wall edits that can be visually accepted/rejected by an agent.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from .geometry_util import as_point as _as_point
from .geometry_util import dist as _dist
from .geometry_util import wall_segment as _wall_segment
from .region_contract import normalize_bbox_region, normalize_review_region

Point = tuple[float, float]
Segment = tuple[Point, Point]


def _round_to(value: float, step: int) -> int:
    return int(round(float(value) / step) * step)


def _norm_point(pt: Any, step: int = 8) -> list[int]:
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        return [_round_to(float(pt[0]), step), _round_to(float(pt[1]), step)]
    return [0, 0]


def _norm_region(region: Any, step: int = 16) -> list[int]:
    if isinstance(region, (list, tuple)):
        vals: list[int] = []
        for v in region[:4]:
            if isinstance(v, (int, float)):
                vals.append(_round_to(float(v), step))
        if len(vals) == 4:
            return vals
    return [0, 0, 0, 0]


def _score_missing_region_bbox(region: Any) -> list[int] | None:
    """Convert score_walls [x, y, width, height, area] regions to review bboxes."""
    if not (isinstance(region, (list, tuple)) and len(region) >= 4):
        return None
    if not all(isinstance(v, (int, float)) for v in region[:4]):
        return None
    x, y, width, height = [float(v) for v in region[:4]]
    return [int(round(x)), int(round(y)), int(round(x + max(0.0, width))), int(round(y + max(0.0, height)))]


def _stable_hash(payload: Any, length: int = 12) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:length]


def _bbox_union(regions: list[Any]) -> list[float] | None:
    boxes = []
    for reg in regions:
        if not (isinstance(reg, (list, tuple)) and len(reg) >= 4):
            continue
        x0, y0, x1, y1 = [float(v) for v in reg[:4]]
        boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _region_to_xyxy(region: Any, bbox_format: str | None = None) -> list[float] | None:
    try:
        normalized = normalize_bbox_region(
            region,
            bbox_format=bbox_format,
            reject_out_of_bounds=False,
        )
        return normalized.bbox_xyxy
    except ValueError:
        return None


def _regions_overlap_or_near(a: Any, b: Any, pad: float = 24.0) -> bool:
    au = _bbox_union([a])
    bu = _bbox_union([b])
    if au is None or bu is None:
        return False
    return not (
        au[2] + pad < bu[0]
        or bu[2] + pad < au[0]
        or au[3] + pad < bu[1]
        or bu[3] + pad < au[1]
    )


def _semantic_context_from_plan_state(plan_state: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(plan_state, dict):
        return []
    out: list[dict[str, Any]] = []
    for evidence in plan_state.get("evidence") or []:
        if not isinstance(evidence, dict) or evidence.get("kind") != "semantic_ink_region":
            continue
        result = evidence.get("result") or {}
        bbox_format = str(result.get("bbox_format") or "xywh")
        bbox = result.get("bbox_xyxy")
        if not (isinstance(bbox, list) and len(bbox) >= 4):
            bbox = _region_to_xyxy(result.get("region"), bbox_format)
        if bbox is None:
            continue
        out.append({
            "evidence_id": evidence.get("id"),
            "semantic_class": result.get("semantic_class") or "unknown",
            "confidence": result.get("confidence") or "medium",
            "region": result.get("region"),
            "bbox_format": bbox_format,
            "bbox_xyxy": bbox,
            "applies_to_wall_score": bool(result.get("applies_to_wall_score")),
            "summary": evidence.get("summary") or "",
        })
    return out


def _semantic_context_for_region(region: Any, semantic_regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bbox = _region_to_xyxy(region, "xyxy")
    if bbox is None:
        return []
    matches = []
    for semantic in semantic_regions:
        if _regions_overlap_or_near(bbox, semantic.get("bbox_xyxy"), pad=32.0):
            matches.append(semantic)
    return matches[:3]


def _point_line_projection(pt: Point, seg: Segment) -> tuple[Point, float, float]:
    (x0, y0), (x1, y1) = seg
    dx, dy = x1 - x0, y1 - y0
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return seg[0], 0.0, _dist(pt, seg[0])
    t = ((pt[0] - x0) * dx + (pt[1] - y0) * dy) / denom
    proj = (x0 + t * dx, y0 + t * dy)
    return proj, t, _dist(pt, proj)


def _line_intersection(a: Segment, b: Segment) -> Point | None:
    (x1, y1), (x2, y2) = a
    (x3, y3), (x4, y4) = b
    den = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(den) < 1e-9:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / den
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / den
    return (px, py)


def _opening_regions(labels_doc: dict[str, Any]) -> list[list[float]]:
    out = []
    for lab in labels_doc.get("labels") or []:
        if not isinstance(lab, dict) or lab.get("type") != "floorplan_opening":
            continue
        quad = ((lab.get("geometry") or {}).get("quad") or [])
        pts = [_as_point(p) for p in quad if _as_point(p) is not None]
        if pts:
            out.append([
                min(p[0] for p in pts) - 20,
                min(p[1] for p in pts) - 20,
                max(p[0] for p in pts) + 20,
                max(p[1] for p in pts) + 20,
            ])
    return out


def _near_opening(region: Any, labels_doc: dict[str, Any]) -> bool:
    return any(_regions_overlap_or_near(region, op, pad=24) for op in _opening_regions(labels_doc))


def finding_fingerprint(file: str, source: str, category: str, payload: dict[str, Any]) -> str:
    ids = sorted(str(x) for x in payload.get("wall_ids") or [] if x)
    if payload.get("wall_id"):
        ids.append(str(payload["wall_id"]))
    nearest = payload.get("nearest_endpoint") or {}
    if isinstance(nearest, dict) and nearest.get("wall_id"):
        ids.append(str(nearest.get("wall_id")))
    body = {
        "file": file,
        "source": source,
        "category": category,
        "wall_ids": sorted(set(ids)),
        "point": _norm_point(payload.get("point")),
        "region": _norm_region(payload.get("review_region") or payload.get("region")),
    }
    return f"{source}:{category}:{_stable_hash(body)}"


def current_findings_from_results(
    *,
    file: str,
    labels_doc: dict[str, Any],
    score_walls_result: dict[str, Any] | None = None,
    topology_result: dict[str, Any] | None = None,
    continuity_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if score_walls_result:
        for idx, region in enumerate(score_walls_result.get("missing_regions") or [], start=1):
            normalized = normalize_review_region(region, region_kind="missing_region_xywh")
            review_region = (normalized or {}).get("bbox_xyxy") or _score_missing_region_bbox(region) or region
            payload = {"region": region, "review_region": review_region}
            if isinstance(region, (list, tuple)) and len(region) >= 5 and isinstance(region[4], (int, float)):
                payload["area_px"] = region[4]
            fp = finding_fingerprint(file, "score_walls", "missing_region", payload)
            findings.append({
                "fingerprint": fp,
                "source": "score_walls",
                "category": "missing_region",
                "severity": "blocker",
                "region": review_region,
                "title": f"Wall score missing region {idx}",
                "payload": payload,
            })
        for idx, seg in enumerate(score_walls_result.get("off_ink_segments") or [], start=1):
            normalized = normalize_review_region(seg, region_kind="off_ink_segment_line", pad_px=16)
            review_region = (normalized or {}).get("bbox_xyxy") or seg
            payload = {"region": seg, "review_region": review_region}
            wall_id = _wall_id_for_score_segment(labels_doc, seg)
            if wall_id:
                payload["wall_id"] = wall_id
            fp = finding_fingerprint(file, "score_walls", "off_ink_segment", payload)
            findings.append({
                "fingerprint": fp,
                "source": "score_walls",
                "category": "off_ink_segment",
                "severity": "blocker",
                "region": review_region,
                "title": f"Wall score off-ink segment {idx}",
                "payload": payload,
            })
    if topology_result:
        for item in topology_result.get("dangling_endpoints") or []:
            payload = dict(item)
            fp = finding_fingerprint(file, "wall_topology_qa", "dangling_endpoint", payload)
            findings.append({
                "fingerprint": fp,
                "source": "wall_topology_qa",
                "category": "dangling_endpoint",
                "severity": "warning",
                "region": item.get("review_region"),
                "title": "Dangling wall endpoint",
                "payload": payload,
            })
        for item in topology_result.get("near_miss_corners") or []:
            payload = dict(item)
            fp = finding_fingerprint(file, "wall_topology_qa", "near_miss_corner", payload)
            findings.append({
                "fingerprint": fp,
                "source": "wall_topology_qa",
                "category": "near_miss_corner",
                "severity": "warning",
                "region": item.get("review_region"),
                "title": "Near-miss wall corner",
                "payload": payload,
            })
        for item in topology_result.get("collinear_fragments") or []:
            payload = dict(item)
            payload["wall_ids"] = item.get("wall_ids") or []
            fp = finding_fingerprint(file, "wall_topology_qa", "collinear_fragment", payload)
            findings.append({
                "fingerprint": fp,
                "source": "wall_topology_qa",
                "category": "collinear_fragment",
                "severity": "warning",
                "region": item.get("review_region"),
                "title": "Possible split wall",
                "payload": payload,
            })
        for item in topology_result.get("short_stubs") or []:
            payload = dict(item)
            fp = finding_fingerprint(file, "wall_topology_qa", "short_stub", payload)
            findings.append({
                "fingerprint": fp,
                "source": "wall_topology_qa",
                "category": "short_stub",
                "severity": "warning",
                "region": item.get("review_region"),
                "title": "Short wall stub",
                "payload": payload,
            })
    if continuity_result:
        for item in continuity_result.get("candidates") or []:
            payload = dict(item)
            fp = finding_fingerprint(file, "wall_continuity_check", "continuity_candidate", payload)
            findings.append({
                "fingerprint": fp,
                "source": "wall_continuity_check",
                "category": "continuity_candidate",
                "severity": "warning",
                "region": item.get("review_region"),
                "title": "Wall continuity candidate",
                "payload": payload,
            })
    for finding in findings:
        finding["cluster_hint"] = _norm_region(finding.get("region"))
        finding["near_opening"] = _near_opening(finding.get("region"), labels_doc)
    return findings


def cluster_findings(findings: list[dict[str, Any]], labels_doc: dict[str, Any]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for finding in findings:
        placed = False
        wall_ids = _finding_wall_ids(finding)
        for cluster in clusters:
            shared_wall = bool(wall_ids & set(cluster.get("wall_ids") or []))
            near = _regions_overlap_or_near(finding.get("region"), cluster.get("region"), pad=48)
            if shared_wall or near:
                cluster["findings"].append(finding)
                cluster["finding_ids"].append(finding["fingerprint"])
                cluster["wall_ids"] = sorted(set(cluster.get("wall_ids") or []) | wall_ids)
                reg = _bbox_union([cluster.get("region"), finding.get("region")])
                if reg:
                    cluster["region"] = reg
                cluster["categories"] = sorted(set(cluster.get("categories") or []) | {finding["category"]})
                placed = True
                break
        if not placed:
            clusters.append({
                "cluster_id": "",
                "severity": finding.get("severity", "warning"),
                "region": finding.get("region"),
                "findings": [finding],
                "finding_ids": [finding["fingerprint"]],
                "wall_ids": sorted(wall_ids),
                "categories": [finding["category"]],
            })
    for idx, cluster in enumerate(clusters, start=1):
        cluster_type = _cluster_type(cluster, labels_doc)
        cluster["cluster_type"] = cluster_type
        cluster["summary"] = _cluster_summary(cluster_type, cluster)
        cluster["confidence"] = _cluster_confidence(cluster_type, cluster)
        cluster["cluster_fingerprint"] = f"cluster:{_stable_hash({'f': sorted(cluster.get('finding_ids') or [])}, 12)}"
        cluster["cluster_id"] = f"TOPO-CL-{idx:03d}-{_stable_hash({'r': _norm_region(cluster.get('region')), 'f': cluster.get('finding_ids')}, 6)}"
        cluster["findings_count"] = len(cluster["findings"])
    clusters.sort(key=lambda c: ({"blocker": 0, "warning": 1, "info": 2}.get(c.get("severity"), 9), c.get("cluster_id", "")))
    return clusters


def _finding_wall_ids(finding: dict[str, Any]) -> set[str]:
    payload = finding.get("payload") or {}
    ids = set()
    for key in ("wall_id",):
        if payload.get(key):
            ids.add(str(payload[key]))
    nearest = payload.get("nearest_endpoint")
    if isinstance(nearest, dict) and nearest.get("wall_id"):
        ids.add(str(nearest["wall_id"]))
    for wid in payload.get("wall_ids") or []:
        ids.add(str(wid))
    return ids


def _wall_id_for_score_segment(labels_doc: dict[str, Any], seg: Any) -> str | None:
    if not (isinstance(seg, (list, tuple)) and len(seg) >= 4):
        return None
    try:
        sx0, sy0, sx1, sy1 = [int(round(float(v))) for v in seg[:4]]
    except (TypeError, ValueError):
        return None
    best_id = None
    best_delta = None
    for lab in labels_doc.get("labels") or []:
        if not isinstance(lab, dict) or lab.get("type") != "wall":
            continue
        wall = _wall_segment(lab)
        if wall is None:
            continue
        (a, b) = wall
        candidates = [
            abs(int(round(a[0])) - sx0) + abs(int(round(a[1])) - sy0)
            + abs(int(round(b[0])) - sx1) + abs(int(round(b[1])) - sy1),
            abs(int(round(a[0])) - sx1) + abs(int(round(a[1])) - sy1)
            + abs(int(round(b[0])) - sx0) + abs(int(round(b[1])) - sy0),
        ]
        delta = min(candidates)
        if best_delta is None or delta < best_delta:
            best_delta = delta
            best_id = str(lab.get("id") or "")
    return best_id if best_delta is not None and best_delta <= 12 else None


def _cluster_type(cluster: dict[str, Any], labels_doc: dict[str, Any]) -> str:
    cats = set(cluster.get("categories") or [])
    if _near_opening(cluster.get("region"), labels_doc):
        return "intentional_opening_gap_candidate"
    if "near_miss_corner" in cats:
        return "near_miss_corner"
    if "collinear_fragment" in cats or "continuity_candidate" in cats:
        return "collinear_split_candidate"
    if "short_stub" in cats:
        return "short_stub_candidate"
    if "dangling_endpoint" in cats:
        return "unconnected_t_junction"
    if "missing_region" in cats or "off_ink_segment" in cats:
        return "score_review"
    return "unknown_topology"


def _cluster_confidence(cluster_type: str, cluster: dict[str, Any]) -> str:
    if cluster_type in {"near_miss_corner", "collinear_split_candidate"}:
        return "high"
    if cluster_type in {"intentional_opening_gap_candidate", "short_stub_candidate"}:
        return "medium"
    return "low"


def _cluster_summary(cluster_type: str, cluster: dict[str, Any]) -> str:
    count = len(cluster.get("findings") or [])
    if cluster_type == "near_miss_corner":
        return f"{count} topology finding(s) indicate endpoints that nearly meet."
    if cluster_type == "intentional_opening_gap_candidate":
        return f"{count} topology finding(s) overlap a known opening; likely intentional gap."
    if cluster_type == "collinear_split_candidate":
        return f"{count} finding(s) indicate collinear wall fragments that may be mergeable."
    if cluster_type == "short_stub_candidate":
        return f"{count} finding(s) indicate a short wall stub needing review."
    if cluster_type == "score_review":
        return f"{count} wall-score finding(s) need visual classification."
    return f"{count} topology finding(s) need review."


def generate_repair_candidates(
    labels_doc: dict[str, Any],
    topology_result: dict[str, Any] | None = None,
    score_walls_result: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if topology_result is None:
        from .wall_topology import wall_topology_qa
        topology_result = wall_topology_qa(labels_doc.get("labels") or [])
    findings = current_findings_from_results(
        file=str(labels_doc.get("scene_file") or ""),
        labels_doc=labels_doc,
        score_walls_result=score_walls_result,
        topology_result=topology_result,
    )
    clusters = cluster_findings(findings, labels_doc)
    labels_by_id = {str(l.get("id")): l for l in labels_doc.get("labels") or [] if isinstance(l, dict) and l.get("id")}
    candidates: list[dict[str, Any]] = []
    for cluster in clusters:
        cluster_candidates: list[dict[str, Any]] = []
        for finding in cluster.get("findings") or []:
            payload = finding.get("payload") or {}
            if finding.get("category") in {"dangling_endpoint", "near_miss_corner"}:
                cand = _snap_candidate(cluster, payload, labels_by_id)
                if cand:
                    cluster_candidates.append(cand)
                ext = _extend_candidate(cluster, payload, labels_by_id)
                if ext:
                    cluster_candidates.append(ext)
                if finding.get("near_opening"):
                    cluster_candidates.append(_no_edit_candidate(cluster, "intentional_opening_gap"))
            elif finding.get("category") == "collinear_fragment":
                cand = _merge_candidate(cluster, payload, labels_by_id)
                if cand:
                    cluster_candidates.append(cand)
            elif finding.get("category") == "short_stub":
                cand = _delete_stub_candidate(cluster, payload, labels_by_id)
                if cand:
                    cluster_candidates.append(cand)
            elif finding.get("category") == "off_ink_segment":
                move = _move_off_ink_to_missing_region_candidate(cluster, payload, labels_by_id)
                if move:
                    cluster_candidates.append(move)
                cand = _reanchor_off_ink_candidate(cluster, payload, labels_by_id)
                if cand:
                    cluster_candidates.append(cand)
                delete = _delete_off_ink_candidate(cluster, payload, labels_by_id)
                if delete:
                    cluster_candidates.append(delete)
        if not cluster_candidates and cluster.get("cluster_type") == "intentional_opening_gap_candidate":
            cluster_candidates.append(_no_edit_candidate(cluster, "intentional_opening_gap"))
        if not cluster_candidates:
            cluster_candidates.append(_no_edit_candidate(cluster, "needs_manual_geometry"))
        for idx, cand in enumerate(cluster_candidates, start=1):
            cand["candidate_id"] = f"CAND-{_stable_hash({'cluster': cluster['cluster_id'], 'op': cand.get('op'), 'edits': cand.get('edits')}, 10)}"
            cand["cluster_id"] = cluster["cluster_id"]
            cand["cluster_fingerprint"] = cluster.get("cluster_fingerprint")
            cand["finding_ids"] = list(cluster.get("finding_ids") or [])
            cand["cluster_type"] = cluster.get("cluster_type")
            cand["region"] = cluster.get("region")
            cand.setdefault("rank", idx)
            cand.setdefault("needs_visual_review", True)
            candidates.append(cand)
    candidates.sort(key=lambda c: ({"low": 0, "medium": 1, "high": 2}.get(str(c.get("risk")), 9), -float(c.get("confidence", 0.0)), c.get("candidate_id", "")))
    return candidates


def _move_off_ink_to_missing_region_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    wall_id = str(payload.get("wall_id") or "")
    wall = labels_by_id.get(wall_id)
    seg = _wall_segment(wall) if wall else None
    if wall is None or seg is None:
        return None
    missing = next(
        (
            f for f in cluster.get("findings") or []
            if f.get("category") == "missing_region" and isinstance(f.get("region"), list) and len(f.get("region")) >= 4
        ),
        None,
    )
    if missing is None:
        return None
    region = [float(v) for v in missing["region"][:4]]
    cx = (region[0] + region[2]) / 2.0
    cy = (region[1] + region[3]) / 2.0
    (s, e) = seg
    dx = e[0] - s[0]
    dy = e[1] - s[1]
    if abs(dx) >= abs(dy):
        shift = cy - ((s[1] + e[1]) / 2.0)
        if abs(shift) > 140:
            return None
        new_start = [s[0], s[1] + shift]
        new_end = [e[0], e[1] + shift]
    else:
        shift = cx - ((s[0] + e[0]) / 2.0)
        if abs(shift) > 140:
            return None
        new_start = [s[0] + shift, s[1]]
        new_end = [e[0] + shift, e[1]]
    return {
        "op": "move_off_ink_wall_to_score_region",
        "confidence": 0.62,
        "risk": "medium",
        "expected_gain": "moves a misplaced wall toward nearby uncovered wall ink reported in the same score cluster",
        "why": [
            f"off-ink wall {wall_id} is clustered with a missing wall-ink region",
            f"estimated perpendicular shift {shift:.1f}px",
            "must be verified in ink_compare overlay before applying",
        ],
        "edits": [
            {"label_id": wall_id, "endpoint": "start", "to": new_start},
            {"label_id": wall_id, "endpoint": "end", "to": new_end},
        ],
        "candidate_wall": [new_start, new_end],
        "predicted_delta": {"off_ink_segments": -1, "missing_regions": -1},
    }


def _reanchor_off_ink_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    wall_id = str(payload.get("wall_id") or "")
    wall = labels_by_id.get(wall_id)
    seg = _wall_segment(wall) if wall else None
    if wall is None or seg is None:
        return None
    overlap = None
    region = payload.get("region")
    if isinstance(region, (list, tuple)) and len(region) >= 5:
        overlap = region[4]
    return {
        "op": "reanchor_off_ink_wall",
        "confidence": 0.82,
        "risk": "low",
        "apply_mode": "tool_required",
        "expected_gain": "replaces a misplaced wall with the measured ink-anchored centerline via upsert_wall_anchored",
        "why": [
            f"score_walls flagged wall {wall_id} as off-ink",
            f"overlap {overlap}" if overlap is not None else "low local ink overlap",
            "candidate uses measured refine_wall path instead of visual guessing",
        ],
        "edits": [{
            "label_id": wall_id,
            "suggested_tool": "upsert_wall_anchored",
            "candidate": {
                "id": wall_id,
                "start": [seg[0][0], seg[0][1]],
                "end": [seg[1][0], seg[1][1]],
                "attributes": wall.get("attributes") or {},
            },
            "anchor": {"search_px": 60, "min_confidence": 0.72, "min_overlap": 0.6, "snap_corners": True},
        }],
        "predicted_delta": {"off_ink_segments": -1},
    }


def _delete_off_ink_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    wall_id = str(payload.get("wall_id") or "")
    if wall_id not in labels_by_id:
        return None
    return {
        "op": "delete_off_ink_wall_if_false_positive",
        "confidence": 0.35,
        "risk": "high",
        "expected_gain": "removes a wall label only if visual review confirms it is a speculative room/furniture/site rectangle",
        "why": [f"wall {wall_id} is off-ink; delete only after crop review rejects it as a real wall"],
        "edits": [{"label_id": wall_id, "delete": True}],
        "predicted_delta": {"off_ink_segments": -1},
    }


def _snap_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    nearest = payload.get("nearest_endpoint") or {}
    p0 = _as_point(payload.get("point"))
    p1 = _as_point(nearest.get("point")) if isinstance(nearest, dict) else None
    if p0 is None or p1 is None:
        return None
    dist = float(nearest.get("distance_px") or _dist(p0, p1))
    if dist > 72:
        return None
    wall_a = str(payload.get("wall_id") or "")
    wall_b = str(nearest.get("wall_id") or "")
    if wall_a not in labels_by_id or wall_b not in labels_by_id:
        return None
    target = [(p0[0] + p1[0]) / 2.0, (p0[1] + p1[1]) / 2.0]
    return {
        "op": "snap_endpoint_to_endpoint",
        "confidence": max(0.1, min(0.95, 1.0 - dist / 100.0)),
        "risk": "low" if dist <= 36 else "medium",
        "expected_gain": "removes two dangling endpoints and one near-miss if the crop confirms a real corner",
        "why": [f"endpoint distance {dist:.1f}px", "candidate preserves both wall labels"],
        "edits": [
            {"label_id": wall_a, "endpoint": str(payload.get("endpoint") or "end"), "to": target},
            {"label_id": wall_b, "endpoint": str(nearest.get("which") or "start"), "to": target},
        ],
        "predicted_delta": {"dangling_endpoints": -2, "near_miss_corners": -1, "components": -1},
    }


def _extend_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    p = _as_point(payload.get("point"))
    wall_id = str(payload.get("wall_id") or "")
    if p is None or wall_id not in labels_by_id:
        return None
    best = None
    for other_id, other in labels_by_id.items():
        if other_id == wall_id or other.get("type") != "wall":
            continue
        seg = _wall_segment(other)
        if seg is None:
            continue
        proj, t, dist = _point_line_projection(p, seg)
        if -0.05 <= t <= 1.05 and dist <= 42 and (best is None or dist < best["dist"]):
            best = {"wall_id": other_id, "point": proj, "dist": dist}
    if best is None:
        return None
    return {
        "op": "extend_to_intersection",
        "confidence": max(0.1, min(0.85, 1.0 - best["dist"] / 80.0)),
        "risk": "medium",
        "expected_gain": "connects a dangling endpoint to a nearby wall segment if no opening is visible",
        "why": [f"endpoint is {best['dist']:.1f}px from wall {best['wall_id']}"],
        "edits": [{"label_id": wall_id, "endpoint": str(payload.get("endpoint") or "end"), "to": [best["point"][0], best["point"][1]]}],
        "target_wall_id": best["wall_id"],
        "predicted_delta": {"dangling_endpoints": -1, "components": -1},
    }


def _merge_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    ids = [str(x) for x in payload.get("wall_ids") or []]
    repair = payload.get("suggested_repair") or {}
    wall = repair.get("candidate_wall")
    if len(ids) < 2 or not wall:
        return None
    return {
        "op": "merge_collinear_fragments",
        "confidence": 0.72,
        "risk": "medium",
        "expected_gain": "replaces collinear fragments with one continuous wall unless the gap is an opening",
        "why": [f"wall_topology_qa reported gap {payload.get('gap_px')}px", f"line distance {payload.get('line_distance_px')}px"],
        "edits": [{"replace_wall_ids": ids[:2], "wall": wall}],
        "predicted_delta": {"dangling_endpoints": -2, "collinear_fragments": -1, "components": -1},
    }


def _delete_stub_candidate(cluster: dict[str, Any], payload: dict[str, Any], labels_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    wall_id = str(payload.get("wall_id") or "")
    if wall_id not in labels_by_id:
        return None
    return {
        "op": "delete_or_demote_short_stub",
        "confidence": 0.45,
        "risk": "high",
        "expected_gain": "removes a short isolated wall only if visual review confirms it is not structural",
        "why": [f"stub length {payload.get('length_px')}px"],
        "edits": [{"label_id": wall_id, "delete": True}],
        "predicted_delta": {"short_stubs": -1},
    }


def _no_edit_candidate(cluster: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "op": "no_edit_classification",
        "classification": reason,
        "confidence": 0.55 if reason == "intentional_opening_gap" else 0.25,
        "risk": "low",
        "expected_gain": "classifies the current cluster without mutating geometry",
        "why": [reason],
        "edits": [],
        "predicted_delta": {},
    }


def apply_candidate_to_labels(labels_doc: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(labels_doc)
    labels = out.setdefault("labels", [])
    by_id = {str(l.get("id")): l for l in labels if isinstance(l, dict) and l.get("id")}
    if candidate.get("op") in {"no_edit_classification", "reanchor_off_ink_wall"}:
        return out
    if candidate.get("op") == "merge_collinear_fragments":
        edits = candidate.get("edits") or []
        if not edits:
            return out
        repl = edits[0]
        ids = [str(x) for x in repl.get("replace_wall_ids") or []]
        wall = repl.get("wall")
        if len(ids) < 2 or not wall:
            raise ValueError("merge candidate requires replace_wall_ids and wall")
        first = by_id.get(ids[0])
        if not first:
            raise ValueError(f"wall {ids[0]!r} not found")
        first["geometry"] = {"start": [wall[0][0], wall[0][1]], "end": [wall[1][0], wall[1][1]]}
        out["labels"] = [lab for lab in labels if str(lab.get("id")) not in set(ids[1:])]
        return out
    for edit in candidate.get("edits") or []:
        label_id = str(edit.get("label_id") or "")
        lab = by_id.get(label_id)
        if not lab:
            raise ValueError(f"label {label_id!r} not found")
        if edit.get("delete"):
            out["labels"] = [x for x in out.get("labels") or [] if str(x.get("id")) != label_id]
            continue
        endpoint = str(edit.get("endpoint") or "")
        to = edit.get("to")
        if endpoint not in {"start", "end"} or not (isinstance(to, list) and len(to) == 2):
            raise ValueError("endpoint edit requires endpoint=start|end and to=[x,y]")
        lab.setdefault("geometry", {})[endpoint] = [float(to[0]), float(to[1])]
    return out


def simulate_candidate(labels_doc: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    from .wall_topology import wall_topology_qa

    before = wall_topology_qa(labels_doc.get("labels") or [])
    after_doc = apply_candidate_to_labels(labels_doc, candidate)
    after = wall_topology_qa(after_doc.get("labels") or [])
    return {
        "before": _topology_counts(before),
        "after": _topology_counts(after),
        "delta": {
            key: _topology_counts(after).get(key, 0) - _topology_counts(before).get(key, 0)
            for key in ("dangling_endpoints", "near_miss_corners", "collinear_fragments", "short_stubs", "components")
        },
    }


def _topology_counts(result: dict[str, Any]) -> dict[str, int]:
    return {
        "wall_count": int(result.get("wall_count") or 0),
        "dangling_endpoints": len(result.get("dangling_endpoints") or []),
        "near_miss_corners": len(result.get("near_miss_corners") or []),
        "collinear_fragments": len(result.get("collinear_fragments") or []),
        "short_stubs": len(result.get("short_stubs") or []),
        "components": len(result.get("components") or []),
    }


def _decision_for_cluster(cluster: dict[str, Any], decisions: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(decisions, dict):
        return None
    cluster_fp = str(cluster.get("cluster_fingerprint") or "")
    if cluster_fp and isinstance(decisions.get(cluster_fp), dict):
        return decisions[cluster_fp]
    finding_ids = set(str(x) for x in cluster.get("finding_ids") or [])
    for decision in decisions.values():
        if not isinstance(decision, dict):
            continue
        decision_finding_ids = set(str(x) for x in decision.get("finding_ids") or [])
        if finding_ids and finding_ids <= decision_finding_ids:
            return decision
    return None


def repair_candidate_report(
    labels_doc: dict[str, Any],
    topology_result: dict[str, Any] | None = None,
    *,
    limit: int = 20,
    plan_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if topology_result is None:
        from .wall_topology import wall_topology_qa
        topology_result = wall_topology_qa(labels_doc.get("labels") or [])
    score_walls_result = None
    if isinstance(plan_state, dict):
        score_walls_result = (((plan_state.get("current_state") or {}).get("scores") or {}).get("score_walls"))
    raw_size = labels_doc.get("image_size_px")
    image_size = (
        (int(raw_size[0]), int(raw_size[1]))
        if isinstance(raw_size, list) and len(raw_size) >= 2 and all(isinstance(v, (int, float)) for v in raw_size[:2])
        else None
    )
    region_warnings: list[dict[str, Any]] = []
    findings = current_findings_from_results(
        file=str(labels_doc.get("scene_file") or ""),
        labels_doc=labels_doc,
        score_walls_result=score_walls_result,
        topology_result=topology_result,
    )
    clusters = cluster_findings(findings, labels_doc)
    candidates = generate_repair_candidates(labels_doc, topology_result=topology_result, score_walls_result=score_walls_result)
    semantic_regions = _semantic_context_from_plan_state(plan_state)
    candidates_by_cluster: dict[str, list[dict[str, Any]]] = {}
    for cand in candidates:
        cand = dict(cand)
        if image_size is not None:
            normalized = normalize_review_region(cand.get("region"), region_kind="xyxy", image_size=image_size)
            if normalized:
                cand["region"] = normalized["bbox_xyxy"]
                if normalized.get("clipped"):
                    cand["region_clipped"] = True
                    region_warnings.append({"kind": "candidate_region_clipped", "candidate_id": cand.get("candidate_id")})
            else:
                cand["region_invalid"] = True
                region_warnings.append({"kind": "candidate_region_invalid", "candidate_id": cand.get("candidate_id")})
        try:
            cand["simulation"] = simulate_candidate(labels_doc, cand)
        except Exception as exc:  # noqa: BLE001
            cand["simulation_error"] = str(exc)
        candidates_by_cluster.setdefault(str(cand.get("cluster_id")), []).append(cand)
    compact_clusters = []
    decisions = (((plan_state or {}).get("current_state") or {}).get("repair_candidate_decisions") or {})
    for cluster in clusters[:limit]:
        public = {k: v for k, v in cluster.items() if k != "findings"}
        if image_size is not None:
            normalized = normalize_review_region(public.get("region"), region_kind="xyxy", image_size=image_size)
            if normalized:
                public["region"] = normalized["bbox_xyxy"]
                if normalized.get("clipped"):
                    public["region_clipped"] = True
                    region_warnings.append({"kind": "cluster_region_clipped", "cluster_id": public.get("cluster_id")})
            else:
                public["region_invalid"] = True
                region_warnings.append({"kind": "cluster_region_invalid", "cluster_id": public.get("cluster_id")})
        semantic_context = _semantic_context_for_region(public.get("region"), semantic_regions)
        if semantic_context:
            public["semantic_context"] = semantic_context
        decision = _decision_for_cluster(public, decisions)
        if decision:
            public["decision"] = {
                "outcome": decision.get("outcome"),
                "candidate_id": decision.get("candidate_id"),
                "updated_at": decision.get("updated_at"),
                "evidence_ids": decision.get("evidence_ids") or [],
            }
        cluster_candidates = []
        for cand in candidates_by_cluster.get(str(cluster.get("cluster_id")), [])[:3]:
            if semantic_context:
                cand = dict(cand)
                cand["semantic_context"] = semantic_context
            cluster_candidates.append(cand)
        public["candidates"] = cluster_candidates
        compact_clusters.append(public)
    reviewed_clusters = len([c for c in compact_clusters if c.get("decision")])
    high_conf_unclassified = len([
        c for c in compact_clusters
        if c.get("confidence") == "high" and c.get("candidates") and not c.get("decision")
    ])
    return {
        "status": "needs_review" if compact_clusters else "clean",
        "finding_count": len(findings),
        "cluster_count": len(clusters),
        "reviewed_cluster_count": reviewed_clusters,
        "high_confidence_unclassified_count": high_conf_unclassified,
        "candidate_count": len(candidates),
        "semantic_context_count": len(semantic_regions),
        "region_warning_count": len(region_warnings),
        "region_warnings": region_warnings[:20],
        "clusters": compact_clusters,
    }


def quality_report(plan_state: dict[str, Any], candidate_report: dict[str, Any]) -> dict[str, Any]:
    """Return a compact report proving whether topology warnings were reviewed."""
    current = plan_state.get("current_state") or {}
    findings = current.get("findings") or {}
    clusters = current.get("finding_clusters") or {}
    defects = plan_state.get("defects") or []
    evidence = plan_state.get("evidence") or []
    decisions = current.get("repair_candidate_decisions") or {}
    decision_items = [d for d in decisions.values() if isinstance(d, dict)]
    accepted = [d for d in decision_items if d.get("outcome") == "accepted_applied"]
    rejected_false = [d for d in decision_items if d.get("outcome") == "rejected_false_positive"]
    rejected_opening = [d for d in decision_items if d.get("outcome") == "rejected_intentional_opening"]
    visual_by_candidate: dict[str, int] = {}
    for ev in evidence:
        result = ev.get("result") or {}
        if not isinstance(result, dict):
            continue
        candidate_id = result.get("candidate_id")
        if ev.get("tool") == "get_scene_view_with_repair_candidate" and candidate_id:
            visual_by_candidate[str(candidate_id)] = visual_by_candidate.get(str(candidate_id), 0) + 1
    open_blockers = [
        d for d in defects
        if d.get("status") in {"open", "in_progress"} and d.get("severity") == "blocker"
    ]
    superseded = [d for d in defects if d.get("status") == "superseded"]
    return {
        "current_finding_count": int(findings.get("count") or 0),
        "current_cluster_count": int(clusters.get("count") or 0),
        "duplicate_or_superseded_historical_defects": len(superseded),
        "candidate_repairs_accepted": len(accepted),
        "candidate_rejections_false_positive": len(rejected_false),
        "candidate_rejections_intentional_opening": len(rejected_opening),
        "candidate_decision_count": len(decision_items),
        "visual_crop_inspections_per_accepted_repair": {
            str(d.get("candidate_id")): (
                visual_by_candidate.get(str(d.get("candidate_id")), 0)
                or len(d.get("evidence_ids") or [])
            )
            for d in accepted
        },
        "final_current_blocker_count": len(open_blockers),
        "final_unclassified_high_confidence_warning_count": int(candidate_report.get("high_confidence_unclassified_count") or 0),
        "candidate_report": {
            "status": candidate_report.get("status"),
            "cluster_count": candidate_report.get("cluster_count"),
            "reviewed_cluster_count": candidate_report.get("reviewed_cluster_count"),
            "candidate_count": candidate_report.get("candidate_count"),
        },
    }


def topology_regression_snapshot(plan_state: dict[str, Any], candidate_report: dict[str, Any]) -> dict[str, Any]:
    current = plan_state.get("current_state") or {}
    topology = current.get("topology") or {}
    clusters = candidate_report.get("clusters") or []
    candidate_ops = sorted({
        str(candidate.get("op"))
        for cluster in clusters
        for candidate in (cluster.get("candidates") or [])
        if candidate.get("op")
    })
    decisions = current.get("repair_candidate_decisions") or {}
    outcomes = sorted({
        str(decision.get("outcome"))
        for decision in decisions.values()
        if isinstance(decision, dict) and decision.get("outcome")
    })
    return {
        "topology": {
            "wall_count": int(topology.get("wall_count") or 0),
            "endpoint_count": int(topology.get("endpoint_count") or 0),
            "dangling_endpoints": int(topology.get("dangling_endpoints") or 0),
            "near_miss_corners": int(topology.get("near_miss_corners") or 0),
            "collinear_fragments": int(topology.get("collinear_fragments") or 0),
            "short_stubs": int(topology.get("short_stubs") or 0),
            "components": int(topology.get("components") or 0),
        },
        "current_cluster_count": int(candidate_report.get("cluster_count") or 0),
        "candidate_count": int(candidate_report.get("candidate_count") or 0),
        "candidate_types": candidate_ops,
        "reviewed_cluster_count": int(candidate_report.get("reviewed_cluster_count") or 0),
        "decision_outcomes": outcomes,
        "unclassified_high_confidence_warning_count": int(candidate_report.get("high_confidence_unclassified_count") or 0),
    }
