// Detect closed-wall regions in the current label set.
//
// Why: the user cares about AREAS (rooms, footprints) — but annotates LINES
// (walls). After laying down walls, we should automatically surface the
// closed regions they enclose so the user can read the floorplan at a
// glance + later derive areas/volumes from them.
//
// Strategy:
//   1. Cluster wall endpoints within `tolerancePx` into shared vertices.
//   2. Build an undirected graph: each wall is an edge between two vertices.
//   3. Find simple cycles. We use a per-edge BFS variant: for each edge
//      (u,v), find the shortest cycle that contains it. Pick distinct
//      cycles, dedupe by sorted-vertex signature.
//
// This isn't a full planar-face algorithm — for very busy graphs we'd want
// minimum cycle basis. But for typical floorplan annotation (≤ ~50 walls,
// few branches per node) the shortest-cycle-per-edge heuristic gives one
// region per room without exponential blow-up.

import type { Label, Point, WallLabel } from '../api/types';

export interface ClosedRegion {
  /** Polygon vertices in image coords, traversed in cycle order. */
  polygon: Point[];
  /** Ids of the wall labels that form this region (for hover-highlighting). */
  wallIds: string[];
  /** Approximate area in image px² — sign-agnostic. */
  areaPx: number;
}

function dist(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

interface Vertex { pt: Point; }
interface Edge { u: number; v: number; wallId: string; }

function clusterEndpoints(walls: WallLabel[], tolerancePx: number): { vertices: Vertex[]; edges: Edge[] } {
  const vertices: Vertex[] = [];
  const edges: Edge[] = [];
  const pointToVertex = (pt: Point): number => {
    for (let i = 0; i < vertices.length; i++) {
      if (dist(vertices[i].pt, pt) <= tolerancePx) return i;
    }
    vertices.push({ pt });
    return vertices.length - 1;
  };
  for (const w of walls) {
    const u = pointToVertex(w.geometry.start);
    const v = pointToVertex(w.geometry.end);
    if (u === v) continue;          // degenerate (zero-length wall)
    edges.push({ u, v, wallId: w.id });
  }
  return { vertices, edges };
}

function shoelace(polygon: Point[]): number {
  let s = 0;
  for (let i = 0; i < polygon.length; i++) {
    const [x1, y1] = polygon[i];
    const [x2, y2] = polygon[(i + 1) % polygon.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}

/**
 * BFS shortest cycle through a specific edge (u, v). Returns the cycle as
 * an ordered list of vertex indices (closed: first == last is implied), or
 * null if no cycle through this edge exists.
 */
function shortestCycleThrough(
  startU: number,
  startV: number,
  excludeEdgeKey: string,
  adj: Map<number, Array<{ to: number; edgeKey: string }>>,
): number[] | null {
  // BFS from v back to u, forbidding the direct (u,v) edge.
  const parent = new Map<number, number>();
  const visited = new Set<number>([startV]);
  const queue: number[] = [startV];
  parent.set(startV, -1);
  while (queue.length > 0) {
    const cur = queue.shift()!;
    if (cur === startU && cur !== startV) {
      // reconstruct path
      const path: number[] = [];
      let node: number | undefined = cur;
      while (node !== undefined && node !== -1) {
        path.push(node);
        node = parent.get(node);
        if (node === -1) break;
      }
      path.reverse();
      return [startU, ...path];
    }
    for (const { to, edgeKey } of adj.get(cur) ?? []) {
      if (edgeKey === excludeEdgeKey) continue;
      if (visited.has(to)) continue;
      visited.add(to);
      parent.set(to, cur);
      if (to === startU) {
        const path: number[] = [];
        let node: number | undefined = to;
        while (node !== undefined && node !== -1) {
          path.push(node);
          node = parent.get(node);
          if (node === -1) break;
        }
        path.reverse();
        return path;
      }
      queue.push(to);
    }
  }
  return null;
}

function cycleSignature(cycle: number[]): string {
  const sorted = [...cycle].sort((a, b) => a - b);
  return sorted.join(',');
}

export function detectClosedRegions(
  labels: Label[],
  tolerancePx: number,
  maxCycles = 24,
): ClosedRegion[] {
  const walls = labels.filter((l): l is WallLabel => l.type === 'wall');
  if (walls.length < 3) return [];
  const { vertices, edges } = clusterEndpoints(walls, tolerancePx);

  const adj = new Map<number, Array<{ to: number; edgeKey: string }>>();
  const edgeKey = (u: number, v: number, wallId: string) =>
    `${Math.min(u, v)}-${Math.max(u, v)}-${wallId}`;
  for (const e of edges) {
    const k = edgeKey(e.u, e.v, e.wallId);
    if (!adj.has(e.u)) adj.set(e.u, []);
    if (!adj.has(e.v)) adj.set(e.v, []);
    adj.get(e.u)!.push({ to: e.v, edgeKey: k });
    adj.get(e.v)!.push({ to: e.u, edgeKey: k });
  }

  const seen = new Set<string>();
  const regions: ClosedRegion[] = [];
  for (const e of edges) {
    if (regions.length >= maxCycles) break;
    const k = edgeKey(e.u, e.v, e.wallId);
    const cycle = shortestCycleThrough(e.u, e.v, k, adj);
    if (!cycle || cycle.length < 3) continue;
    const sig = cycleSignature(cycle);
    if (seen.has(sig)) continue;
    seen.add(sig);
    const polygon = cycle.map((i) => vertices[i].pt);
    // Find which walls participate
    const wallIds: string[] = [];
    for (let i = 0; i < cycle.length; i++) {
      const a = cycle[i];
      const b = cycle[(i + 1) % cycle.length];
      const ed = edges.find(
        (x) => (x.u === a && x.v === b) || (x.u === b && x.v === a),
      );
      if (ed) wallIds.push(ed.wallId);
    }
    regions.push({ polygon, wallIds, areaPx: shoelace(polygon) });
  }
  // Sort by area descending so larger envelopes render first; per-room
  // smaller regions paint on top.
  regions.sort((a, b) => b.areaPx - a.areaPx);
  return regions;
}
