// Length-quantize hint. While drawing a wall / dim_distance, scan existing
// line labels and find one whose length matches the current draw within
// `tolerancePct`. If a match exists, the UI can show "= 2.40 m" so the user
// knows they're about to create a near-duplicate length.
//
// Why: real plans repeat dimensions (a row of identical windows, equal-width
// rooms). Drift across labels = lost downstream signal. Surfacing the match
// during the draw lets the user nudge to exact equality.

import type { Label, Point } from '../api/types';

export interface LengthMatch {
  /** Image-px length of the matched existing label. */
  matchedLength: number;
  /** Id of the matched label. */
  matchedLabelId: string;
  /** Type of the matched label, for the human-readable hint. */
  matchedLabelType: string;
  /** True when the current draw is already within `snapTolerancePct` of the
   *  match — UI should then offer to snap on click. */
  withinSnapTolerance: boolean;
}

function lineLength(start: Point, end: Point): number {
  return Math.hypot(end[0] - start[0], end[1] - start[1]);
}

/**
 * Search labels for one whose length is closest to `currentLength`, within
 * `hintTolerancePct`. Returns null if no match. Same-id label is excluded.
 */
export function findLengthMatch(
  currentLength: number,
  labels: Label[],
  selfId: string | null = null,
  hintTolerancePct = 0.05,
  snapTolerancePct = 0.015,
): LengthMatch | null {
  if (currentLength < 1) return null;
  let best: LengthMatch | null = null;
  let bestDelta = currentLength * hintTolerancePct;
  for (const l of labels) {
    if (selfId && l.id === selfId) continue;
    if (l.type !== 'wall' && l.type !== 'dimensioned_distance') continue;
    const g = l.geometry as { start: Point; end: Point };
    const len = lineLength(g.start, g.end);
    const delta = Math.abs(len - currentLength);
    if (delta <= bestDelta) {
      bestDelta = delta;
      best = {
        matchedLength: len,
        matchedLabelId: l.id,
        matchedLabelType: l.type,
        withinSnapTolerance: delta <= currentLength * snapTolerancePct,
      };
    }
  }
  return best;
}

/**
 * Project an endpoint to the exact length of a matched label, keeping the
 * draw direction. Used when the user clicks while in snap-tolerance range —
 * we override the click endpoint to land on the exact match length.
 */
export function applyLengthMatch(start: Point, end: Point, targetLength: number): Point {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy);
  if (len < 1) return end;
  const scale = targetLength / len;
  return [start[0] + dx * scale, start[1] + dy * scale];
}
