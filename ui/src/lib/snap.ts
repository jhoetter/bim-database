// Snap engine. Given the cursor position + current tool + the scene's
// existing labels, returns the best snap target (or null). The screen-pixel
// radius is converted to image-pixel coords by the caller so snap feels
// constant across zoom levels.
//
// Hold Alt → no snap (caller handles that early-out).
// Hold Shift + draw → angle-lock relative to pendingStart (0/45/90/135°).

import type { Label, Point } from '../api/types';

export type SnapKind =
  | 'endpoint'           // existing wall/dim_distance/opening corner
  | 'midpoint'           // dim_distance midpoint (for dim_number anchor)
  | 'wall_line'          // perpendicular projection onto a wall (for openings)
  | 'angle_lock'         // Shift-locked relative to pendingStart
  | 'axis_align';        // same y/x as another label of same kind

export interface SnapTarget {
  pt: Point;
  kind: SnapKind;
  hint: string;
  /** Id of the underlying label that contributed this snap target, if any.
   *  Used by the floorplan_opening tool to record `belongs_to` against the
   *  wall the user snapped onto (M10). */
  source_label_id?: string;
  /** Optional alignment guide to render across the canvas. */
  guide?: {
    type: 'horizontal' | 'vertical';
    /** y (horizontal guide) or x (vertical guide) in image coords */
    value: number;
  };
}

export type SnapTool =
  | 'wall'
  | 'dimensioned_distance'
  | 'dimension_number'
  | 'floorplan_opening'
  | 'view_opening'
  | 'component_line'
  | 'height_mark'
  | 'select-drag';

export interface SnapArgs {
  cursor: Point;
  pendingStart: Point | null;
  tool: SnapTool;
  labels: Label[];
  imageRadiusPx: number;
  modifiers: { shift: boolean; alt: boolean };
  /** When dragging an existing label's handle, exclude its own points
   *  from snap candidates (so a wall endpoint doesn't snap to itself). */
  excludeLabelId?: string;
  /** Building's dominant axis angle in degrees, in [-45, 45]. Computed by
   *  referenceAngle() from existing labels; passed in by the caller so it
   *  stays memoized at the AnnotatePage level. Defaults to 0 (image axis).
   *  When the user disables adaptive snap (Q key), the caller passes 0
   *  even if a non-zero axis was detected. */
  referenceAngleDeg?: number;
  /** First vertex of the currently-pending polyline (component_line or
   *  polygon view_opening). When set and the cursor is within snap radius,
   *  findSnap returns it as an endpoint target — clicking there closes the
   *  polygon (same as Enter). */
  pendingPolylineFirst?: Point;
  /** When true, soft axis-lock is skipped entirely — no ortho-snap of any
   *  kind, not even to image axes. Endpoint + wall_line + length-match
   *  snaps still fire. Shift hard-lock also still works (explicit opt-in).
   *  Used to model "user wants free-angle drawing right now." */
  disableSoftAxisSnap?: boolean;
  /** Override the default 10° softAxisLock tolerance. Caller lowers this
   *  to ~3° when there's no confident axis signal yet (e.g. <2 walls on a
   *  potentially-tilted plan), so the system doesn't yank near-ortho
   *  drawings onto image axes before it knows the building's orientation. */
  softAxisToleranceDeg?: number;
}

export function findSnap(args: SnapArgs): SnapTarget | null {
  const { cursor, pendingStart, tool, modifiers, imageRadiusPx } = args;
  const refAngle = args.referenceAngleDeg ?? 0;

  if (modifiers.alt) return null;

  const isLinearTool =
    tool === 'wall' || tool === 'dimensioned_distance' || tool === 'component_line';

  // Shift = HARD axis-lock from pendingStart relative to the building axis.
  // No tolerance — even a 30° cursor angle gets snapped to the nearest
  // building-aligned 45° multiple.
  if (modifiers.shift && pendingStart && isLinearTool) {
    return hardAxisLock(cursor, pendingStart, refAngle);
  }

  // Endpoint / line / midpoint candidates have priority — an exact point
  // beats an angle snap.
  const cands = collectCandidates(args);
  let best: SnapTarget | null = null;
  let bestDist = imageRadiusPx;
  for (const c of cands) {
    const d = Math.hypot(c.pt[0] - cursor[0], c.pt[1] - cursor[1]);
    if (d <= bestDist) {
      bestDist = d;
      best = c;
    }
  }
  if (best) return best;

  // Soft angle snap relative to the building axis. Skipped entirely when
  // the user explicitly disabled ortho-snap (Q hotkey), since the image-axis
  // fallback was the part that fought the user on tilted plans without a
  // detected axis. Tolerance is configurable so the caller can be gentle
  // (3°) when axis confidence is low.
  if (pendingStart && isLinearTool && !args.disableSoftAxisSnap) {
    const tol = args.softAxisToleranceDeg ?? 10;
    const soft = softAxisLock(cursor, pendingStart, refAngle, tol);
    if (soft) return soft;
  }

  return null;
}

// 8 multiples of 45° centred on the building axis, expressed in image-frame
// degrees. With refAngle=0 these are the classical {-180, -135, …, 180}.
// With refAngle=2.4 the building's "horizontal" is at 2.4° from the image
// horizontal, so we snap to {-177.6, -132.6, …, 182.4}.
function rotatedAxisTargets(refAngleDeg: number): number[] {
  const out: number[] = [];
  for (let k = -4; k < 5; k++) out.push(refAngleDeg + k * 45);
  return out;
}

function nearestAxisTarget(angleDeg: number, refAngleDeg: number): { target: number; diff: number } {
  let bestTarget = 0;
  let bestDiff = Infinity;
  for (const t of rotatedAxisTargets(refAngleDeg)) {
    const diff = Math.abs(((angleDeg - t + 540) % 360) - 180);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestTarget = t;
    }
  }
  return { target: bestTarget, diff: bestDiff };
}

// Soft axis-lock — only fires when the cursor's direction is already within
// `toleranceDeg` of a building-axis target. 10° is intentionally wide:
// most plans are ortho/45°, so a 7-8° human drift should silently land on
// the axis. Alt escapes; Q (caller-side) disables the building-axis bias
// and falls back to image axes.
function softAxisLock(cursor: Point, anchor: Point, refAngleDeg: number, toleranceDeg = 10): SnapTarget | null {
  const dx = cursor[0] - anchor[0];
  const dy = cursor[1] - anchor[1];
  const r = Math.hypot(dx, dy);
  if (r < 8) return null;
  const angle = (Math.atan2(-dy, dx) * 180) / Math.PI;
  const { target, diff } = nearestAxisTarget(angle, refAngleDeg);
  if (diff > toleranceDeg) return null;
  const rad = (target * Math.PI) / 180;
  // Hint shows the building-relative angle (0 = along the building's
  // horizontal axis, 90 = perpendicular). Adds (Bau) suffix when the
  // building axis is non-zero so the user knows the snap is rotated.
  const relAngle = ((target - refAngleDeg) % 360 + 360) % 360;
  const hint = refAngleDeg !== 0
    ? `${relAngle.toFixed(0)}° · Bau ${refAngleDeg.toFixed(1)}°`
    : `${relAngle.toFixed(0)}°`;
  return {
    pt: [anchor[0] + r * Math.cos(rad), anchor[1] - r * Math.sin(rad)],
    kind: 'angle_lock',
    hint,
  };
}

// Shift-held hard axis-lock — always returns a target regardless of how
// far the cursor is from it. Snaps to the nearest 45° multiple of the
// building axis.
function hardAxisLock(cursor: Point, anchor: Point, refAngleDeg: number): SnapTarget {
  const dx = cursor[0] - anchor[0];
  const dy = cursor[1] - anchor[1];
  const r = Math.hypot(dx, dy);
  const angle = (Math.atan2(-dy, dx) * 180) / Math.PI;
  const { target } = nearestAxisTarget(angle, refAngleDeg);
  const rad = (target * Math.PI) / 180;
  const relAngle = ((target - refAngleDeg) % 360 + 360) % 360;
  return {
    pt: [anchor[0] + r * Math.cos(rad), anchor[1] - r * Math.sin(rad)],
    kind: 'angle_lock',
    hint: refAngleDeg !== 0 ? `${relAngle.toFixed(0)}° · Bau` : `${relAngle.toFixed(0)}°`,
  };
}

// Reduce a line's angle to the [-45, 45] "axis representative" — every
// 90° rotation maps to the same value (since perpendicular walls share the
// building axis), and folding around 45° means a wall at 89° clusters with
// walls at -1°. Returns null for too-short lines.
function lineAngleAxis(start: Point, end: Point): number | null {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy);
  if (len < 12) return null;
  let a = (Math.atan2(-dy, dx) * 180) / Math.PI;       // -180..180
  a = ((a % 90) + 90) % 90;                            // 0..90
  if (a > 45) a -= 90;                                 // -45..45
  return a;
}

/**
 * Derive the building's dominant axis angle from existing labels.
 *
 * Most architectural plans have a single building coordinate system shared
 * by walls, dimensions, and component lines. When the photo / scan is
 * rotated relative to the image frame (folded-paper photos, slightly-
 * crooked scans), every line in the plan is rotated by the same angle.
 * We recover that angle by clustering line angles mod 90 and taking the
 * median of the dominant cluster.
 *
 * Returns 0 if no signal (no walls/dims yet) or if the labels are too
 * scattered to confidently identify a single axis.
 *
 * Range: [-45, 45] degrees. A small non-zero value (e.g. 2.4°) is normal
 * for a photographed paper plan.
 */
export function referenceAngle(labels: Label[]): number {
  const angles: number[] = [];
  for (const l of labels) {
    if (l.type === 'wall' || l.type === 'dimensioned_distance') {
      const a = lineAngleAxis(l.geometry.start, l.geometry.end);
      if (a != null) angles.push(a);
    } else if (l.type === 'component_line') {
      const pts = l.geometry.polyline;
      for (let i = 0; i + 1 < pts.length; i++) {
        const a = lineAngleAxis(pts[i], pts[i + 1]);
        if (a != null) angles.push(a);
      }
    }
  }
  if (angles.length < 2) return 0;        // need ≥2 lines to trust the signal
  angles.sort((a, b) => a - b);
  // Median is robust against outliers (e.g. a diagonal stair edge in a
  // mostly-ortho plan). For the dominant-cluster guarantee we'd want a
  // mode/peak, but the median works well for typical bim-database plans.
  const med = angles[Math.floor(angles.length / 2)];
  // If the signal is essentially zero, return exact 0 so the hint suppresses
  // the "(Bau X°)" suffix.
  return Math.abs(med) < 0.25 ? 0 : med;
}

// ── candidate enumeration ───────────────────────────────────────────────────

function collectCandidates(args: SnapArgs): SnapTarget[] {
  const { tool, labels, cursor, excludeLabelId, pendingPolylineFirst } = args;
  const cands: SnapTarget[] = [];

  // First-vertex close target for in-progress polylines (P7). Lets the
  // user see + click the close-polygon snap exactly like Enter would commit.
  if (pendingPolylineFirst && (tool === 'component_line' || tool === 'view_opening')) {
    cands.push({
      pt: pendingPolylineFirst,
      kind: 'endpoint',
      hint: 'schließen',
    });
  }

  // Endpoints of walls + dim_distances + opening corners are useful for the
  // "drag this near another existing geometry's point" pattern.
  for (const l of labels) {
    if (l.id === excludeLabelId) continue;

    if (l.type === 'wall' || l.type === 'dimensioned_distance') {
      const wantsEndpoints =
        tool === 'wall' ||
        tool === 'dimensioned_distance' ||
        tool === 'component_line' ||
        tool === 'select-drag';
      if (wantsEndpoints) {
        cands.push({ pt: l.geometry.start, kind: 'endpoint', hint: `${l.type} start` });
        cands.push({ pt: l.geometry.end, kind: 'endpoint', hint: `${l.type} end` });
      }
    }

    if (l.type === 'floorplan_opening') {
      if (tool === 'wall' || tool === 'select-drag' || tool === 'dimensioned_distance') {
        for (const p of l.geometry.quad) {
          cands.push({ pt: p, kind: 'endpoint', hint: 'opening corner' });
        }
      }
    }

    if (l.type === 'view_opening') {
      if (tool === 'select-drag' || tool === 'dimensioned_distance' || tool === 'view_opening') {
        // ViewOpening geometry is a tagged union (rectangle / circle / polygon).
        // For circle we offer 4 cardinal points; for polygon, every vertex;
        // for the rectangle legacy form, top_edge + bottom_edge endpoints.
        const g = l.geometry as Record<string, unknown>;
        if (g.shape === 'circle') {
          const c = g.center as Point;
          const r = g.radius_px as number;
          for (const p of [[c[0] + r, c[1]], [c[0] - r, c[1]], [c[0], c[1] + r], [c[0], c[1] - r]] as Point[]) {
            cands.push({ pt: p, kind: 'endpoint', hint: 'opening edge' });
          }
        } else if (g.shape === 'polygon') {
          for (const p of g.polygon as Point[]) {
            cands.push({ pt: p, kind: 'endpoint', hint: 'opening vertex' });
          }
        } else {
          for (const p of [...(g.top_edge as Point[]), ...(g.bottom_edge as Point[])]) {
            cands.push({ pt: p, kind: 'endpoint', hint: 'opening corner' });
          }
        }
      }
    }

    if (l.type === 'component_line') {
      const useVertices =
        tool === 'component_line' ||
        tool === 'select-drag' ||
        tool === 'wall' ||
        tool === 'dimensioned_distance';
      if (useVertices) {
        for (const p of l.geometry.polyline) {
          cands.push({ pt: p, kind: 'endpoint', hint: 'line vertex' });
        }
      }
      // P8 — snap to ANY point along an existing component_line segment when
      // drawing another component_line, wall, or dim. Lets a new roof line
      // start mid-edge of a wall polygon, etc.
      const useEdges =
        tool === 'component_line' || tool === 'wall' || tool === 'dimensioned_distance';
      if (useEdges) {
        const poly = l.geometry.polyline;
        for (let i = 0; i + 1 < poly.length; i++) {
          const proj = perpProjection(cursor, poly[i], poly[i + 1]);
          if (proj.within) {
            cands.push({
              pt: proj.point,
              kind: 'wall_line',
              hint: 'an Linie',
              source_label_id: l.id,
            });
          }
        }
      }
    }
  }

  // dim_number: snap anchor to midpoint of any dim_distance (suggests link).
  if (tool === 'dimension_number') {
    for (const l of labels) {
      if (l.type === 'dimensioned_distance') {
        const mid: Point = [
          (l.geometry.start[0] + l.geometry.end[0]) / 2,
          (l.geometry.start[1] + l.geometry.end[1]) / 2,
        ];
        cands.push({ pt: mid, kind: 'midpoint', hint: 'distance midpoint' });
      }
    }
  }

  // floorplan_opening: snap to a wall (perpendicular projection onto wall axis).
  // This is the headline "windows in walls" snap (UX called out as core).
  // source_label_id carries the wall id so the editor can write a belongs_to
  // relation on commit (M10).
  if (tool === 'floorplan_opening') {
    for (const l of labels) {
      if (l.type !== 'wall') continue;
      const proj = perpProjection(cursor, l.geometry.start, l.geometry.end);
      if (proj.within) {
        cands.push({
          pt: proj.point,
          kind: 'wall_line',
          hint: 'an Wand',
          source_label_id: l.id,
        });
      }
    }
  }

  // view_opening, height_mark: snap to same-y as an existing same-type label
  // (the "alignment guide" pattern). height_mark also snaps to same-x —
  // typical labeling pattern is a column of Höhenkoten on one vertical line.
  if (tool === 'view_opening' || tool === 'height_mark') {
    const sameType = labels.filter((l) => l.type === tool);
    for (const other of sameType) {
      if (other.id === excludeLabelId) continue;
      const otherY =
        other.type === 'height_mark'
          ? other.geometry.anchor[1]
          : (other as { geometry: { top_edge: Point[] } }).geometry.top_edge[0]?.[1];
      if (typeof otherY === 'number' && Math.abs(otherY - cursor[1]) < args.imageRadiusPx) {
        cands.push({
          pt: [cursor[0], otherY],
          kind: 'axis_align',
          hint: 'gleiche Höhe',
          guide: { type: 'horizontal', value: otherY },
        });
      }
      if (tool === 'height_mark' && other.type === 'height_mark') {
        const otherX = other.geometry.anchor[0];
        if (Math.abs(otherX - cursor[0]) < args.imageRadiusPx * 1.5) {
          cands.push({
            pt: [otherX, cursor[1]],
            kind: 'axis_align',
            hint: 'gleiche Bezugsachse',
            guide: { type: 'vertical', value: otherX },
          });
        }
      }
    }
  }

  // Wall / dim_distance / component_line: alignment guides to existing
  // wall endpoints. When the cursor is within a tolerance of an existing
  // wall's endpoint X or Y, snap to that X/Y so the new endpoint ends up
  // exactly aligned (90° corner if the existing wall is orthogonal).
  if (
    tool === 'wall' || tool === 'dimensioned_distance' || tool === 'component_line'
  ) {
    for (const l of labels) {
      if (l.id === excludeLabelId) continue;
      if (l.type !== 'wall') continue;
      for (const p of [l.geometry.start, l.geometry.end]) {
        if (Math.abs(p[0] - cursor[0]) < args.imageRadiusPx * 1.2) {
          cands.push({
            pt: [p[0], cursor[1]],
            kind: 'axis_align',
            hint: 'X-Achse einer Wand',
            guide: { type: 'vertical', value: p[0] },
          });
        }
        if (Math.abs(p[1] - cursor[1]) < args.imageRadiusPx * 1.2) {
          cands.push({
            pt: [cursor[0], p[1]],
            kind: 'axis_align',
            hint: 'Y-Achse einer Wand',
            guide: { type: 'horizontal', value: p[1] },
          });
        }
      }
    }
  }

  return cands;
}

// ── helpers ─────────────────────────────────────────────────────────────────
// (axisLock removed — superseded by hardAxisLock above, which takes a
//  building-axis reference angle.)

export interface ProjResult { point: Point; within: boolean; dist: number; }

/** Project p onto segment a→b. Clamped to the segment endpoints; `within`
 *  reports whether the un-clamped projection fell inside the segment. */
export function pointToSegment(p: Point, a: Point, b: Point): ProjResult {
  return perpProjection(p, a, b);
}

function perpProjection(p: Point, a: Point, b: Point): ProjResult {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len2 = dx * dx + dy * dy;
  if (len2 === 0) {
    return { point: a, within: false, dist: Math.hypot(p[0] - a[0], p[1] - a[1]) };
  }
  const t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / len2;
  const tc = Math.max(0, Math.min(1, t));
  const foot: Point = [a[0] + tc * dx, a[1] + tc * dy];
  return {
    point: foot,
    within: t >= 0 && t <= 1,
    dist: Math.hypot(p[0] - foot[0], p[1] - foot[1]),
  };
}

// Visual color per snap kind — used by the canvas indicator.
export const SNAP_COLOR: Record<SnapKind, string> = {
  endpoint: '#16a34a',
  midpoint: '#16a34a',
  wall_line: '#16a34a',
  angle_lock: '#0ea5e9',
  axis_align: '#94a3b8',
};
