// N3.2 — T-intersection auto-split.
//
// When a new wall's endpoint commits within snap radius of another wall's
// EDGE (not at one of its endpoints), the existing wall gets split at the
// projected point. After the split, the new wall's endpoint and the two
// pieces of the old wall share the same joint — joint-aware drag (M1.2)
// then "just works" at the T-intersection.
//
// Same idea applies in principle to dim_distance + component_line, but
// keeping the scope tight: walls only for now. Adding line types is the
// next step if useful.

import type { Label, Point, WallLabel } from '../api/types';
import { pointToSegment } from './snap';

export interface AutoSplitResult {
  /** The new label, with its endpoints possibly nudged onto the wall edges
   *  they snapped to. */
  newLabel: Label;
  /** Map of original-wall-id → [left half, right half]. The caller deletes
   *  the original wall and appends both halves. */
  splits: Map<string, [WallLabel, WallLabel]>;
}

function uuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

const NOW = () => new Date().toISOString();

function near(a: Point, b: Point, r: number): boolean {
  return Math.hypot(a[0] - b[0], a[1] - b[1]) < r;
}

/** Look at the new wall's two endpoints. For each, find the closest OTHER
 *  wall whose edge (interior — not within snap radius of its endpoints) the
 *  endpoint lands on. Return the splits + the (possibly-adjusted) new wall. */
export function autoTSplit(
  newLabel: Label,
  allLabels: Label[],
  snapPx: number,
): AutoSplitResult {
  if (newLabel.type !== 'wall') {
    return { newLabel, splits: new Map() };
  }
  const splits = new Map<string, [WallLabel, WallLabel]>();
  const ends: { start: Point; end: Point } = {
    start: newLabel.geometry.start,
    end: newLabel.geometry.end,
  };

  for (const epKey of ['start', 'end'] as const) {
    const ep = ends[epKey];
    let best: { other: WallLabel; projPt: Point; dist: number } | null = null;
    for (const other of allLabels) {
      if (other.id === newLabel.id) continue;
      if (other.type !== 'wall') continue;
      // Skip if our endpoint is at one of `other`'s endpoints — that's a
      // regular joint, not a T. Joint-aware drag handles those.
      if (near(ep, other.geometry.start, snapPx)) continue;
      if (near(ep, other.geometry.end, snapPx)) continue;
      // Skip walls we've already scheduled for splitting on the previous
      // endpoint — they'll be deleted in the same setLabels pass.
      if (splits.has(other.id)) continue;
      const proj = pointToSegment(ep, other.geometry.start, other.geometry.end);
      if (!proj.within) continue;
      if (proj.dist > snapPx) continue;
      if (!best || proj.dist < best.dist) {
        best = { other: other as WallLabel, projPt: proj.point, dist: proj.dist };
      }
    }
    if (best) {
      // Snap our endpoint to the projection so the joint is exact.
      ends[epKey] = best.projPt;
      // Build the two halves of the original wall sharing the projection
      // point. Inherit attributes (thickness etc.) + status + relations.
      const left: WallLabel = {
        ...best.other,
        id: uuid(),
        geometry: { start: best.other.geometry.start, end: best.projPt },
        updated_at: NOW(),
      };
      const right: WallLabel = {
        ...best.other,
        id: uuid(),
        geometry: { start: best.projPt, end: best.other.geometry.end },
        updated_at: NOW(),
      };
      splits.set(best.other.id, [left, right]);
    }
  }

  const adjustedNew: Label = {
    ...newLabel,
    geometry: { start: ends.start, end: ends.end },
    updated_at: NOW(),
  } as Label;

  return { newLabel: adjustedNew, splits };
}
