"""Measurement-as-oracle cross-checks for labeling correctness.

The insight (from the labeling drive): a dimension chain is the ground truth
that validates geometry placement *metrically*, not just "is there ink under
my label". On a Grundriss the chain segments are:
  - COLLINEAR — every part of one chain sits on a single horizontal (or
    vertical) line; and
  - ADDITIVE — the parts sum to the overall dimension printed above them; and
  - ALIGNED to features — each chain tick is the projection of a wall face /
    opening edge, so wall x-positions must line up with the chain's tick
    x-positions (and vice-versa).

score-walls only checks ink coverage; it will happily accept a wall sitting on
the *wrong* line if that line also has ink. These cross-checks catch exactly
that class of error: a wall at the wrong x lands on ink but does NOT line up
with a dimension tick.

All functions are PURE — no I/O, no rendering — so they unit-test
deterministically and are cheap to call as cross-checks during a run. The
vision-LLM decides what to do with a failed check. Coordinates are source-px.
"""
from __future__ import annotations


def chain_collinear(positions: list[float], *, tol_px: float = 6.0) -> dict:
    """Do the cross-axis positions of one dimension chain's ticks lie on a
    single line? For a HORIZONTAL chain pass the ticks' y-values (they should
    all be ~equal); for a VERTICAL chain pass their x-values.

    Returns {collinear, spread_px, mean, n}. `spread_px` = max - min; collinear
    iff spread <= tol_px. Fewer than 2 ticks is trivially collinear.
    """
    pts = [float(p) for p in positions]
    if len(pts) < 2:
        return {"collinear": True, "spread_px": 0.0,
                "mean": (pts[0] if pts else None), "n": len(pts)}
    spread = max(pts) - min(pts)
    return {"collinear": spread <= tol_px,
            "spread_px": round(spread, 2),
            "mean": round(sum(pts) / len(pts), 2),
            "n": len(pts)}


def chain_sum_consistent(
    part_values_mm: list[float],
    total_mm: float,
    *,
    tol_frac: float = 0.02,
    tol_abs_mm: float = 30.0,
) -> dict:
    """Do a dimension chain's part values add up to the printed overall?

    A misread part (e.g. 8.63⁴ read as 863) shows up as a chain that doesn't
    sum. Returns {consistent, sum_mm, total_mm, delta_mm, rel_err}. Consistent
    iff |sum - total| <= max(tol_abs_mm, tol_frac*total) — the absolute floor
    keeps small chains from being over-strict.
    """
    if total_mm <= 0:
        return {"consistent": False, "sum_mm": None, "total_mm": total_mm,
                "delta_mm": None, "rel_err": None,
                "reason": "non-positive total"}
    s = float(sum(part_values_mm))
    delta = abs(s - total_mm)
    tol = max(tol_abs_mm, tol_frac * total_mm)
    return {"consistent": delta <= tol,
            "sum_mm": round(s, 1),
            "total_mm": float(total_mm),
            "delta_mm": round(delta, 1),
            "rel_err": round(delta / total_mm, 4),
            "tol_mm": round(tol, 1)}


def ticks_align_with_features(
    tick_positions: list[float],
    feature_positions: list[float],
    *,
    tol_px: float = 8.0,
) -> dict:
    """Cross-check geometry placement against the dimension chain.

    `tick_positions` are the chain's tick coordinates on the running axis (the
    x of each tick for a horizontal chain). `feature_positions` are the wall-
    face / opening-edge coordinates on the SAME axis (e.g. each vertical
    wall's x). Every tick should have a feature within tol_px — a tick with no
    nearby feature means a wall the chain says exists is missing or misplaced.

    Returns {matched, n_ticks, n_matched, unmatched_ticks:[{pos,nearest,dist}],
             match_frac}. The vision-LLM treats unmatched_ticks as a to-do /
    relocate list.
    """
    feats = [float(f) for f in feature_positions]
    unmatched = []
    n_matched = 0
    for t in tick_positions:
        t = float(t)
        if not feats:
            unmatched.append({"pos": round(t, 1), "nearest": None, "dist": None})
            continue
        nearest = min(feats, key=lambda f: abs(f - t))
        dist = abs(nearest - t)
        if dist <= tol_px:
            n_matched += 1
        else:
            unmatched.append({"pos": round(t, 1),
                              "nearest": round(nearest, 1),
                              "dist": round(dist, 1)})
    n = len(tick_positions)
    return {"matched": not unmatched,
            "n_ticks": n,
            "n_matched": n_matched,
            "unmatched_ticks": unmatched,
            "match_frac": round(n_matched / n, 3) if n else 1.0}


def _orientation(start, end, *, tol_deg_frac: float = 0.0) -> str | None:
    """horizontal / vertical / None from a segment's dominant axis. A segment
    is horizontal if |dx| > |dy|, vertical if |dy| > |dx|."""
    dx = abs(float(end[0]) - float(start[0]))
    dy = abs(float(end[1]) - float(start[1]))
    if dx == 0 and dy == 0:
        return None
    return "horizontal" if dx >= dy else "vertical"


def score_measurements_from_labels(
    walls: list[dict],
    dims: list[dict],
    *,
    tol_px: float = 8.0,
    axis_tol_px: float = 14.0,
) -> dict:
    """Score the saved geometry against the saved dimension chains — the
    label-level entry the /score-measurements route wraps. PURE.

    `walls`: [{start:[x,y], end:[x,y]}, ...] (the wall labels' geometry).
    `dims`:  [{start, end, value_mm?}, ...] (dimensioned_distance geometry +
             optional read value).

    For each dimension segment, its two endpoints are TICKS that should be the
    projection of a wall face on the running axis. A horizontal dim's ticks
    are x-positions that should align with VERTICAL walls' x; a vertical dim's
    ticks with HORIZONTAL walls' y. A tick with no wall face within tol_px is
    a placement defect (wall missing or misplaced) — exactly what score-walls
    (ink-only) cannot catch.

    Dims are grouped into CHAINS by orientation + shared cross-axis line
    (within axis_tol_px); per chain we report collinearity and, when every
    part carries a value, the part-sum (for the agent to compare to the
    printed overall by eye).

    Returns {ok, n_dims, n_walls, total_ticks, matched_ticks, match_frac,
             unmatched_ticks:[{axis,pos,nearest,dist}], chains:[...]}.
    """
    # wall face positions per axis
    vert_wall_x = []   # vertical walls -> their x (constant)
    horiz_wall_y = []  # horizontal walls -> their y (constant)
    for w in walls:
        s, e = w.get("start"), w.get("end")
        if not s or not e:
            continue
        o = _orientation(s, e)
        if o == "vertical":
            vert_wall_x.append((float(s[0]) + float(e[0])) / 2.0)
        elif o == "horizontal":
            horiz_wall_y.append((float(s[1]) + float(e[1])) / 2.0)

    unmatched = []
    total_ticks = 0
    matched = 0
    # group dims into chains: (orientation, rounded cross-axis bucket)
    chains: dict[tuple, dict] = {}
    for d in dims:
        s, e = d.get("start"), d.get("end")
        if not s or not e:
            continue
        o = _orientation(s, e)
        if o is None:
            continue
        if o == "horizontal":
            ticks = [float(s[0]), float(e[0])]      # x positions
            cross = (float(s[1]) + float(e[1])) / 2.0
            feats = vert_wall_x
            axis = "x"
        else:
            ticks = [float(s[1]), float(e[1])]      # y positions
            cross = (float(s[0]) + float(e[0])) / 2.0
            feats = horiz_wall_y
            axis = "y"
        bucket = (o, round(cross / max(1.0, axis_tol_px)))
        ch = chains.setdefault(bucket, {
            "orientation": o, "cross": cross, "tick_pos": [],
            "values_mm": [], "cross_positions": []})
        ch["cross_positions"].append(cross)
        for t in ticks:
            ch["tick_pos"].append(t)
            total_ticks += 1
            if feats:
                nearest = min(feats, key=lambda f: abs(f - t))
                dist = abs(nearest - t)
                if dist <= tol_px:
                    matched += 1
                else:
                    unmatched.append({"axis": axis, "pos": round(t, 1),
                                      "nearest": round(nearest, 1),
                                      "dist": round(dist, 1)})
            else:
                unmatched.append({"axis": axis, "pos": round(t, 1),
                                  "nearest": None, "dist": None})
        v = d.get("value_mm")
        if v:
            ch["values_mm"].append(float(v))

    chain_out = []
    for ch in chains.values():
        coll = chain_collinear(ch["cross_positions"], tol_px=tol_px)
        chain_out.append({
            "orientation": ch["orientation"],
            "cross_axis": round(ch["cross"], 1),
            "n_parts": len(ch["tick_pos"]) // 2,
            "collinear": coll["collinear"],
            "spread_px": coll["spread_px"],
            "sum_mm": round(sum(ch["values_mm"]), 1) if ch["values_mm"] else None,
        })

    return {
        "ok": not unmatched and all(c["collinear"] for c in chain_out),
        "n_dims": len(dims),
        "n_walls": len(walls),
        "total_ticks": total_ticks,
        "matched_ticks": matched,
        "match_frac": round(matched / total_ticks, 3) if total_ticks else 1.0,
        "unmatched_ticks": unmatched,
        "chains": chain_out,
    }


def score_chain(
    tick_positions: list[float],
    cross_axis_positions: list[float],
    part_values_mm: list[float],
    total_mm: float,
    feature_positions: list[float],
    *,
    tol_px: float = 8.0,
    tol_frac: float = 0.02,
) -> dict:
    """Combined metric oracle for one dimension chain: collinear ticks +
    additive parts + ticks aligned to features. Returns the three sub-results
    plus an overall `ok` (all three pass). Pure.
    """
    collinear = chain_collinear(cross_axis_positions, tol_px=tol_px)
    summed = chain_sum_consistent(part_values_mm, total_mm, tol_frac=tol_frac)
    aligned = ticks_align_with_features(tick_positions, feature_positions,
                                        tol_px=tol_px)
    return {
        "ok": bool(collinear["collinear"] and summed["consistent"]
                   and aligned["matched"]),
        "collinear": collinear,
        "sum": summed,
        "alignment": aligned,
    }
