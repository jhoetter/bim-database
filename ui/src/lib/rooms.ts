// V3.3 — Grundriss room detection (minimal).
//
// A room is a closed cycle of walls sharing joints. The graph from
// lib/connectivity is the source of truth — we run a cycle detection over
// the wall-joint subgraph and emit each minimum-area face as a room
// candidate. Full room-kind classification (Wohnzimmer / Küche / …)
// requires per-house cache + user input; this module just surfaces the
// polygon + centroid so the canvas can place a generic "Raum" pictogram.
//
// For v1 we do NOT attempt to identify holes, multi-tenant rooms, or
// rooms with non-wall edges. A room here is "smallest cycle of walls
// returning to the same joint". That's enough to mark each detected
// closed loop with a pictogram badge.

import type { Label, Point, WallLabel } from '../api/types';
import { buildConnectivity, type ConnectivityGraph, type JointNode } from './connectivity';

export interface DetectedRoom {
  /** Stable id derived from the sorted joint ids in the cycle. */
  id: string;
  /** Joint vertices in polygon order. */
  polygon: Point[];
  /** Centroid for rendering the room badge. */
  centroid: Point;
  /** Pixel area of the polygon — used to prefer smaller faces (rooms)
   *  over the outer perimeter loop (façade). */
  areaPx: number;
}

interface JointGraph {
  joints: JointNode[];
  /** jointId → set of jointIds reachable via one wall. */
  adj: Map<string, Set<string>>;
  /** (jointA, jointB) → wall label id, so cycles know which wall edges to follow. */
  edgeOf: Map<string, string>;
}

function buildJointGraph(graph: ConnectivityGraph, walls: WallLabel[]): JointGraph {
  const adj = new Map<string, Set<string>>();
  const edgeOf = new Map<string, string>();
  for (const j of graph.joints) adj.set(j.id, new Set());
  for (const w of walls) {
    const jStart = graph.jointOf.get(`${w.id}:start`);
    const jEnd = graph.jointOf.get(`${w.id}:end`);
    if (!jStart || !jEnd || jStart.id === jEnd.id) continue;
    adj.get(jStart.id)!.add(jEnd.id);
    adj.get(jEnd.id)!.add(jStart.id);
    edgeOf.set(`${jStart.id}|${jEnd.id}`, w.id);
    edgeOf.set(`${jEnd.id}|${jStart.id}`, w.id);
  }
  return { joints: graph.joints, adj, edgeOf };
}

function polygonArea(pts: Point[]): number {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % pts.length];
    s += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(s) / 2;
}

function polygonCentroid(pts: Point[]): Point {
  let sx = 0, sy = 0;
  for (const p of pts) { sx += p[0]; sy += p[1]; }
  return [sx / pts.length, sy / pts.length];
}

/** Find rooms — minimal closed wall-cycles. Strategy: for each joint,
 *  BFS outward up to depth 6, collect all cycles that return to the
 *  start, dedupe by canonical id, and filter by area lower bound.
 *  Cap cycles per scene at 24 to avoid pathological inputs. */
export function detectRooms(labels: Label[]): DetectedRoom[] {
  const walls = labels.filter((l) => l.type === 'wall') as WallLabel[];
  if (walls.length < 3) return [];
  const graph = buildConnectivity(labels, 6);
  const jg = buildJointGraph(graph, walls);
  const jointById = new Map<string, JointNode>(jg.joints.map((j) => [j.id, j]));

  const seen = new Map<string, DetectedRoom>();
  const MAX_CYCLE_LEN = 8;
  const MIN_ROOM_AREA = 500;

  // For each joint, run a depth-limited DFS to find cycles back to it.
  for (const start of jg.joints) {
    const stack: { node: string; path: string[]; visited: Set<string> }[] = [
      { node: start.id, path: [start.id], visited: new Set([start.id]) },
    ];
    while (stack.length > 0) {
      const { node, path, visited } = stack.pop()!;
      if (path.length > MAX_CYCLE_LEN) continue;
      const neighbors = jg.adj.get(node);
      if (!neighbors) continue;
      for (const next of neighbors) {
        if (next === start.id && path.length >= 3) {
          const polyIds = [...path];
          const canonical = canonicalCycleKey(polyIds);
          if (seen.has(canonical)) continue;
          const pts = polyIds.map((id) => jointById.get(id)!.pt);
          const a = polygonArea(pts);
          if (a < MIN_ROOM_AREA) continue;
          seen.set(canonical, {
            id: canonical,
            polygon: pts,
            centroid: polygonCentroid(pts),
            areaPx: a,
          });
          continue;
        }
        if (visited.has(next)) continue;
        const nextVisited = new Set(visited);
        nextVisited.add(next);
        stack.push({ node: next, path: [...path, next], visited: nextVisited });
      }
    }
    if (seen.size > 24) break;
  }

  // De-dupe: when one detected room is a superset of another's vertices,
  // keep the smaller-area one (the inner room is the "real" face).
  const rooms = [...seen.values()].sort((a, b) => a.areaPx - b.areaPx);
  const accepted: DetectedRoom[] = [];
  for (const r of rooms) {
    // Reject if this polygon strictly contains any already-accepted one.
    const strictlyContainsAccepted = accepted.some((acc) =>
      acc.polygon.every((p) => pointInPolygon(p, r.polygon)) && acc.areaPx < r.areaPx
    );
    if (strictlyContainsAccepted) continue;
    accepted.push(r);
  }
  return accepted;
}

function canonicalCycleKey(ids: string[]): string {
  // Rotate so the lexicographically smallest id is first, then pick the
  // direction that yields the smaller string — gives one canonical key
  // per cycle regardless of starting point or traversal direction.
  let minIdx = 0;
  for (let i = 1; i < ids.length; i++) {
    if (ids[i] < ids[minIdx]) minIdx = i;
  }
  const rotated = [...ids.slice(minIdx), ...ids.slice(0, minIdx)];
  const reversed = [rotated[0], ...rotated.slice(1).reverse()];
  const forward = rotated.join(',');
  const back = reversed.join(',');
  return forward < back ? forward : back;
}

function pointInPolygon(pt: Point, poly: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    const intersect = ((yi > pt[1]) !== (yj > pt[1])) &&
      (pt[0] < ((xj - xi) * (pt[1] - yi)) / (yj - yi + 1e-9) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}
