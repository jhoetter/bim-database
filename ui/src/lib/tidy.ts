// Post-commit cleanup of just-drawn line geometry.
//
// Live snap during draw catches MOST drift (a 7° wobble lands on 0° via
// softAxisLock, an endpoint-near-endpoint click snaps to the existing
// endpoint). But two cases slip through:
//   1. The user held Alt during draw (snap disabled) and then released,
//      leaving a near-ortho line that should snap.
//   2. The committed endpoint is JUST outside snap radius of another
//      label's endpoint — close enough that the user clearly meant it
//      to be the same point, but the live engine didn't fire.
//
// This module retroactively snaps both. Runs immediately before setLabels()
// on a fresh draw. The returned `tidied` flag is used to surface a toast so
// Cmd-Z is discoverable.

import type { Label, Point } from '../api/types';

const ORTHO_TOLERANCE_DEG = 2;        // ≤ softAxisLock's 10° so we only
                                       // catch cases the live snap missed

function rotatedAxisTargets(refAngleDeg: number): number[] {
  const out: number[] = [];
  for (let k = -4; k < 5; k++) out.push(refAngleDeg + k * 45);
  return out;
}

function fitOrtho(start: Point, end: Point, refAngleDeg: number): { end: Point; changed: boolean } {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (length < 1) return { end, changed: false };
  const angleDeg = (Math.atan2(-dy, dx) * 180) / Math.PI;
  let bestTarget = angleDeg;
  let bestDiff = Infinity;
  for (const t of rotatedAxisTargets(refAngleDeg)) {
    const diff = Math.abs(((angleDeg - t + 540) % 360) - 180);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestTarget = t;
    }
  }
  if (bestDiff < 0.001) return { end, changed: false };          // already exact
  if (bestDiff > ORTHO_TOLERANCE_DEG) return { end, changed: false }; // too far
  const rad = (bestTarget * Math.PI) / 180;
  return {
    end: [start[0] + length * Math.cos(rad), start[1] - length * Math.sin(rad)],
    changed: true,
  };
}

function endpointCandidates(label: Label): Point[] {
  const g: any = label.geometry;
  if ('start' in g && 'end' in g) return [g.start, g.end];
  if ('polyline' in g && Array.isArray(g.polyline)) return g.polyline as Point[];
  if ('quad' in g && Array.isArray(g.quad)) return g.quad as Point[];
  if ('top_edge' in g && 'bottom_edge' in g) {
    return [...(g.top_edge as Point[]), ...(g.bottom_edge as Point[])];
  }
  if ('anchor' in g) return [g.anchor as Point];
  return [];
}

function fuseEndpoint(
  pt: Point,
  others: Label[],
  selfId: string,
  imageRadiusPx: number,
): Point {
  let best: Point | null = null;
  let bestDist = imageRadiusPx;
  for (const other of others) {
    if (other.id === selfId) continue;
    for (const c of endpointCandidates(other)) {
      const d = Math.hypot(c[0] - pt[0], c[1] - pt[1]);
      if (d < bestDist) {
        bestDist = d;
        best = c;
      }
    }
  }
  return best ?? pt;
}

/**
 * Tidy a freshly-committed line label. Returns the (possibly-modified) label
 * plus a flag describing what changed, so the caller can show a toast.
 *
 * Only fires on label types whose geometry is {start, end}: wall,
 * dimensioned_distance, component_line (handled segment-by-segment by caller).
 */
export function tidyLineLabel(
  label: Label,
  others: Label[],
  imageRadiusPx: number,
  referenceAngleDeg: number = 0,
): { label: Label; orthoChanged: boolean; endpointFused: boolean } {
  const g: any = label.geometry;
  if (!('start' in g) || !('end' in g)) {
    return { label, orthoChanged: false, endpointFused: false };
  }
  let start = g.start as Point;
  let end = g.end as Point;
  let orthoChanged = false;
  let endpointFused = false;

  // 1. Ortho-tidy: align near-ortho lines to the building axis (or image
  // axis if no axis detected). Catches the cases the live snap missed.
  const fitted = fitOrtho(start, end, referenceAngleDeg);
  if (fitted.changed) {
    end = fitted.end;
    orthoChanged = true;
  }

  // 2. Endpoint-fuse: snap each endpoint to a nearby existing endpoint.
  const fStart = fuseEndpoint(start, others, label.id, imageRadiusPx);
  const fEnd = fuseEndpoint(end, others, label.id, imageRadiusPx);
  if (fStart[0] !== start[0] || fStart[1] !== start[1]) {
    start = fStart;
    endpointFused = true;
  }
  if (fEnd[0] !== end[0] || fEnd[1] !== end[1]) {
    end = fEnd;
    endpointFused = true;
  }

  if (!orthoChanged && !endpointFused) {
    return { label, orthoChanged: false, endpointFused: false };
  }
  return {
    label: { ...label, geometry: { ...g, start, end } } as Label,
    orthoChanged,
    endpointFused,
  };
}
