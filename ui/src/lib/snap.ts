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
}

export function findSnap(args: SnapArgs): SnapTarget | null {
  const { cursor, pendingStart, tool, modifiers, imageRadiusPx } = args;

  if (modifiers.alt) return null;

  // Shift = axis-lock from pendingStart. Applies to linear tools.
  if (
    modifiers.shift &&
    pendingStart &&
    (tool === 'wall' || tool === 'dimensioned_distance' || tool === 'component_line')
  ) {
    return axisLock(cursor, pendingStart);
  }

  const cands = collectCandidates(args);
  if (cands.length === 0) return null;

  let best: SnapTarget | null = null;
  let bestDist = imageRadiusPx;
  for (const c of cands) {
    const d = Math.hypot(c.pt[0] - cursor[0], c.pt[1] - cursor[1]);
    if (d <= bestDist) {
      bestDist = d;
      best = c;
    }
  }
  return best;
}

// ── candidate enumeration ───────────────────────────────────────────────────

function collectCandidates(args: SnapArgs): SnapTarget[] {
  const { tool, labels, cursor, excludeLabelId } = args;
  const cands: SnapTarget[] = [];

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
        for (const p of [...l.geometry.top_edge, ...l.geometry.bottom_edge]) {
          cands.push({ pt: p, kind: 'endpoint', hint: 'opening corner' });
        }
      }
    }

    if (l.type === 'component_line') {
      if (tool === 'component_line' || tool === 'select-drag') {
        for (const p of l.geometry.polyline) {
          cands.push({ pt: p, kind: 'endpoint', hint: 'line vertex' });
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
  // (the "alignment guide" pattern).
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
    }
  }

  return cands;
}

// ── helpers ─────────────────────────────────────────────────────────────────

function axisLock(cursor: Point, anchor: Point): SnapTarget {
  const dx = cursor[0] - anchor[0];
  const dy = cursor[1] - anchor[1];
  // Mathematical angle (atan2 with -dy because SVG y grows down).
  const angle = (Math.atan2(-dy, dx) * 180) / Math.PI;
  // Snap to nearest of 0/45/90/.../-135°.
  const targets = [-180, -135, -90, -45, 0, 45, 90, 135, 180];
  let bestA = 0;
  let bestDiff = Infinity;
  for (const a of targets) {
    const diff = Math.abs(((angle - a + 540) % 360) - 180);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestA = a;
    }
  }
  const r = Math.hypot(dx, dy);
  const rad = (bestA * Math.PI) / 180;
  return {
    pt: [anchor[0] + r * Math.cos(rad), anchor[1] - r * Math.sin(rad)],
    kind: 'angle_lock',
    hint: `${bestA}°`,
  };
}

interface ProjResult { point: Point; within: boolean; dist: number; }

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
