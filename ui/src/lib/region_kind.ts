// V2 — region-kind inference for closed component_line polygons.
//
// A component_line with ≥3 vertices is conceptually an area (the polygon
// closed via the implicit first→last edge, or explicitly looped). The
// inferred *kind* of that area drives the canvas hatch + centroid glyph:
//
//   roof       — at least one edge classified as a roof slope (dachschraege)
//                OR the topology has a single peak at the highest point and
//                edges descend symmetrically to its left/right
//   gable      — a closed roof-adjacent region (≥1 dachschraege edge AND
//                ≥1 gebaeudekante edge), but the region itself does NOT
//                contain the highest point (it sits beside the ridge)
//   wall_body  — perimeter is ≥75 % gebaeudekante edges (the building
//                outline drawn as a single closed polyline)
//   ground     — gelaende line_kind on the polygon, AND the region touches
//                the bottom edge of the image
//   unknown    — none of the above
//
// Classification is "good enough" — we don't try to be exhaustive. Users
// can override via the post-draw classifier chip (existing M9 flow).
//
// We don't classify on the line_kind alone because a single component_line
// has ONE line_kind for the whole polyline. We look at the line_kind plus
// topology (vertex positions relative to the image).

import type { ComponentLineLabel, Label, Point } from '../api/types';

export type RegionKind = 'roof' | 'wall_body' | 'gable' | 'ground' | 'unknown';

interface ClassifyContext {
  imageHeight: number;
}

export function inferRegionKind(
  region: ComponentLineLabel,
  ctx: ClassifyContext,
): RegionKind {
  const pts = region.geometry.polyline;
  if (pts.length < 3) return 'unknown';
  const lk = region.attributes.line_kind ?? 'other';

  // Cheap topology features.
  const minY = Math.min(...pts.map((p) => p[1]));   // pixel-Y small = high up
  const maxY = Math.max(...pts.map((p) => p[1]));
  const topVertices = pts.filter((p) => Math.abs(p[1] - minY) <= 4);
  const touchesBottom = maxY >= ctx.imageHeight - 8;
  const heightOfRegion = maxY - minY;

  // 1) ground — explicit line_kind AND touches bottom OR very flat at bottom.
  if (lk === 'gelaende' && touchesBottom) return 'ground';

  // 2) roof — explicit line_kind, OR single peak topology + ≥2 edges
  //    descending.
  if (lk === 'dachschraege') return 'roof';
  if (lk === 'first') return 'roof';
  if (lk === 'firstkante') return 'roof';
  // Topology: a single highest vertex; ≥2 distinct edges descend from it.
  if (topVertices.length === 1 && pts.length >= 3 && heightOfRegion > 10) {
    const peak = topVertices[0];
    const leftPts = pts.filter((p) => p[0] < peak[0] && p[1] > peak[1]);
    const rightPts = pts.filter((p) => p[0] > peak[0] && p[1] > peak[1]);
    if (leftPts.length >= 1 && rightPts.length >= 1) {
      return 'roof';
    }
  }

  // 3) wall_body — gebaeudekante line_kind. A closed polyline whose
  //    line_kind says "gebaeudekante" — the user drew the wall outline.
  if (lk === 'gebaeudekante') return 'wall_body';

  // 4) gable — closed region near the roof but NOT containing the peak.
  //    With only single-kind line_kind we can't fully discriminate; fall
  //    through to unknown unless the kind hints it.

  return 'unknown';
}

/** Convenience: for every closed-area component_line in the scene,
 *  return its inferred kind. */
export function inferRegionKinds(
  labels: Label[],
  imageHeight: number,
): Map<string, RegionKind> {
  const out = new Map<string, RegionKind>();
  for (const l of labels) {
    if (l.type !== 'component_line') continue;
    if (l.geometry.polyline.length < 3) continue;
    out.set(l.id, inferRegionKind(l, { imageHeight }));
  }
  return out;
}

/** Centroid of a polygon — used for region glyph anchoring. */
export function polygonCentroid(pts: Point[]): Point {
  if (pts.length === 0) return [0, 0];
  let sumX = 0, sumY = 0;
  for (const p of pts) { sumX += p[0]; sumY += p[1]; }
  return [sumX / pts.length, sumY / pts.length];
}

/** Human-readable German label for a region kind — used in commit toasts. */
export function regionKindLabel(kind: RegionKind): string {
  switch (kind) {
    case 'roof':       return 'Dachfläche';
    case 'gable':      return 'Giebel';
    case 'wall_body':  return 'Wandfläche';
    case 'ground':     return 'Geländefläche';
    case 'unknown':    return 'Fläche';
  }
}
