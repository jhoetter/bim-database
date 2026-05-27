// Heuristic kind inference for newly-drawn labels (M3.3).
//
// Principle: smart default beats blind default. Instead of always setting
// new openings to 'window' or new lines to 'other', use the surrounding
// context to pick a more useful pre-class. The user can still override
// instantly via the post-draw classifier chip or kind hotkeys.
//
// Heuristics are conservative — when the signal is weak, we fall back to
// the same blind default we had before (so we never make things worse).

import type { FloorplanOpeningLabel, Label, Point, ViewOpeningLabel } from '../api/types';

/** Most common opening_kind among siblings within `radiusPx` of `anchor`,
 *  or null if no clear majority. */
export function inferOpeningKind(
  anchor: Point,
  others: Label[],
  family: 'floorplan_opening' | 'view_opening',
  radiusPx: number,
): string | null {
  const counts = new Map<string, number>();
  for (const l of others) {
    if (l.type !== family) continue;
    let centroid: Point | null = null;
    if (family === 'floorplan_opening') {
      const q = (l as FloorplanOpeningLabel).geometry.quad;
      centroid = [(q[0][0] + q[2][0]) / 2, (q[0][1] + q[2][1]) / 2];
    } else {
      const g = (l as ViewOpeningLabel).geometry as Record<string, unknown>;
      if (g.shape === 'circle') centroid = g.center as Point;
      else if (g.shape === 'polygon') {
        const p = g.polygon as Point[];
        let sx = 0, sy = 0;
        for (const pt of p) { sx += pt[0]; sy += pt[1]; }
        centroid = [sx / p.length, sy / p.length];
      } else {
        const t = (g.top_edge as Point[]) ?? [];
        const b = (g.bottom_edge as Point[]) ?? [];
        if (t.length > 0 && b.length > 0) {
          centroid = [(t[0][0] + b[b.length - 1][0]) / 2, (t[0][1] + b[b.length - 1][1]) / 2];
        }
      }
    }
    if (!centroid) continue;
    const d = Math.hypot(centroid[0] - anchor[0], centroid[1] - anchor[1]);
    if (d > radiusPx) continue;
    const k = (l.attributes as { opening_kind?: string }).opening_kind ?? 'window';
    counts.set(k, (counts.get(k) ?? 0) + 1);
  }
  if (counts.size === 0) return null;
  // Pick the most-common kind, but only if it dominates (≥3 instances OR
  // it's the unique kind in the cluster).
  let bestKind: string | null = null;
  let bestCount = 0;
  for (const [k, n] of counts) {
    if (n > bestCount) { bestCount = n; bestKind = k; }
  }
  if (bestCount >= 3) return bestKind;
  if (counts.size === 1 && bestCount >= 2) return bestKind;
  return null;
}

/** Heuristic for component_line: pick gebaeudekante (vertical building edge),
 *  dachschraege (diagonal roof edge), or null. Diagonal lines clustered in
 *  the upper half of the image → dachschraege; vertical lines anywhere →
 *  gebaeudekante. Returns null when the angle is too ambiguous. */
export function inferLineKind(polyline: Point[], imageHeight: number): string | null {
  if (polyline.length < 2) return null;
  // Use the longest segment angle (the dominant geometric direction of this
  // line, even if it has bends).
  let bestLen = 0;
  let bestAngle = 0;
  let bestMidY = 0;
  for (let i = 0; i + 1 < polyline.length; i++) {
    const dx = polyline[i + 1][0] - polyline[i][0];
    const dy = polyline[i + 1][1] - polyline[i][1];
    const len = Math.hypot(dx, dy);
    if (len > bestLen) {
      bestLen = len;
      bestAngle = (Math.atan2(-dy, dx) * 180) / Math.PI;
      bestMidY = (polyline[i][1] + polyline[i + 1][1]) / 2;
    }
  }
  if (bestLen < 10) return null;
  // Reduce angle to absolute deviation from horizontal in [0, 90].
  let abs = Math.abs(bestAngle);
  if (abs > 90) abs = 180 - abs;

  // Near-vertical (|90°| ± 15°) → gebaeudekante (an outer wall edge).
  if (abs >= 75) return 'gebaeudekante';
  // Diagonal (25..65°) AND in the upper half of the image → dachschraege.
  if (abs >= 25 && abs <= 65 && bestMidY < imageHeight / 2) return 'dachschraege';
  return null;
}
