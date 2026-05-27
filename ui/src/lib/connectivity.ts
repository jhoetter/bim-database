// Connectivity graph over scene labels.
//
// Walls, dim_distances, openings, component_lines all carry endpoints in
// image-pixel coords. When two endpoints fall within snap radius of each
// other we treat them as one logical "joint" — the point where the building's
// geometry actually connects. The graph is the source of truth for every
// downstream operation that depends on connectivity:
//
//   • Joint-aware drag (M1.2): one vertex move → every label sharing the
//     joint moves in lockstep.
//   • Wall split (M1.3): splits create new joints between two segments.
//   • Select-connected (M1.4): walls reachable from one wall via shared
//     joints form a component; click-to-select that whole component.
//   • Refine queue (M5.1): outlier detection (length, angle) compares each
//     label against its connectivity neighbors.
//
// Algorithm: O(n²) clustering — for each endpoint, find an existing joint
// within `tolerancePx`; otherwise spawn a new one. n is typically <100 per
// scene, so this is fine. If performance ever matters, replace with a grid
// hash bucketed at the tolerance scale.

import type { Label, Point } from '../api/types';

/** A single endpoint reference. EndpointId encodes which slot on the label.
 *
 * For walls + dim_distances: 'start' | 'end'.
 * For openings: the four corner indices 'quad.0' … 'quad.3' (floorplan) or
 *   'top.N' / 'bottom.N' (rectangle view_opening) — view_opening with
 *   non-rectangle shape contributes no endpoints (circles + polygons live
 *   inside walls, not at joints).
 * For component_line: 'polyline.N' where N is the vertex index.
 * For height_mark: not connective (single anchor, no shared joints).
 */
export interface EndpointRef {
  labelId: string;
  endpointId: string;
  pt: Point;
}

export interface JointNode {
  /** Stable id within this graph instance. */
  id: string;
  /** Average position of all endpoints clustered into this joint. */
  pt: Point;
  /** Every endpoint that landed in this cluster. */
  members: EndpointRef[];
}

export interface ConnectivityGraph {
  joints: JointNode[];
  /** Lookup endpoint → joint, keyed by `${labelId}:${endpointId}`. */
  jointOf: Map<string, JointNode>;
  /** All connected components: each is the set of label ids reachable
   *  from any seed label via shared joints. */
  components: Array<Set<string>>;
  /** Quick lookup: labelId → component index. */
  componentOf: Map<string, number>;
}

/** All point coordinates that count as "snappable" for the given label.
 *  Exposed so other modules (anchored-polyline auto-commit, N2) can ask
 *  "is this cursor at an existing label's endpoint?" without re-implementing. */
export function endpointPointsOfLabel(label: Label): Point[] {
  return endpointsForLabel(label).map((e) => e.pt);
}

function endpointsForLabel(label: Label): EndpointRef[] {
  const out: EndpointRef[] = [];
  const id = label.id;
  if (label.type === 'wall' || label.type === 'dimensioned_distance') {
    const g = label.geometry as { start: Point; end: Point };
    out.push({ labelId: id, endpointId: 'start', pt: g.start });
    out.push({ labelId: id, endpointId: 'end', pt: g.end });
  } else if (label.type === 'floorplan_opening') {
    const quad = (label.geometry as { quad: Point[] }).quad;
    quad.forEach((pt, i) => out.push({ labelId: id, endpointId: `quad.${i}`, pt }));
  } else if (label.type === 'view_opening') {
    const g = label.geometry as Record<string, unknown>;
    // Circle/polygon openings are not joint-bearing — they live INSIDE a
    // wall, not at a wall corner. Only the rectangle (top_edge/bottom_edge)
    // form contributes endpoints; even those are usually wall-interior.
    if (!g.shape) {
      const top = (g.top_edge as Point[]) ?? [];
      const bot = (g.bottom_edge as Point[]) ?? [];
      top.forEach((pt, i) => out.push({ labelId: id, endpointId: `top.${i}`, pt }));
      bot.forEach((pt, i) => out.push({ labelId: id, endpointId: `bottom.${i}`, pt }));
    }
  } else if (label.type === 'component_line') {
    const poly = (label.geometry as { polyline: Point[] }).polyline;
    poly.forEach((pt, i) => out.push({ labelId: id, endpointId: `polyline.${i}`, pt }));
  }
  // height_mark + dimension_number have anchors but aren't joints.
  return out;
}

function dist(a: Point, b: Point): number {
  return Math.hypot(a[0] - b[0], a[1] - b[1]);
}

export function buildConnectivity(labels: Label[], tolerancePx: number): ConnectivityGraph {
  const joints: JointNode[] = [];
  const jointOf = new Map<string, JointNode>();

  for (const label of labels) {
    for (const ep of endpointsForLabel(label)) {
      // Find an existing joint that this endpoint falls within.
      let target: JointNode | null = null;
      let bestDist = tolerancePx;
      for (const j of joints) {
        const d = dist(j.pt, ep.pt);
        if (d <= bestDist) {
          bestDist = d;
          target = j;
        }
      }
      if (target == null) {
        target = { id: `j${joints.length}`, pt: ep.pt, members: [] };
        joints.push(target);
      } else {
        // Re-centre the joint at the mean of its members so the next
        // candidate snaps to a stable centroid, not whichever endpoint
        // happened to land there first.
        const n = target.members.length + 1;
        target.pt = [
          (target.pt[0] * (n - 1) + ep.pt[0]) / n,
          (target.pt[1] * (n - 1) + ep.pt[1]) / n,
        ];
      }
      target.members.push(ep);
      jointOf.set(`${ep.labelId}:${ep.endpointId}`, target);
    }
  }

  // Connected components via union-find over labels that share a joint.
  const parent = new Map<string, string>();
  const find = (x: string): string => {
    let p = parent.get(x);
    if (p === undefined || p === x) return p ?? x;
    p = find(p);
    parent.set(x, p);
    return p;
  };
  const union = (a: string, b: string) => {
    const ra = find(a);
    const rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };
  for (const label of labels) parent.set(label.id, label.id);
  for (const j of joints) {
    if (j.members.length < 2) continue;
    const first = j.members[0].labelId;
    for (let i = 1; i < j.members.length; i++) union(first, j.members[i].labelId);
  }
  const compIdx = new Map<string, number>();
  const components: Array<Set<string>> = [];
  for (const label of labels) {
    const root = find(label.id);
    let idx = compIdx.get(root);
    if (idx === undefined) {
      idx = components.length;
      components.push(new Set());
      compIdx.set(root, idx);
    }
    components[idx].add(label.id);
  }
  const componentOf = new Map<string, number>();
  for (const label of labels) componentOf.set(label.id, compIdx.get(find(label.id))!);

  return { joints, jointOf, components, componentOf };
}

/** Convenience: every endpoint that shares a joint with the given (label, endpoint).
 *  Returns the empty array when the endpoint is unique (not part of a real joint). */
export function jointMembersAt(
  graph: ConnectivityGraph,
  labelId: string,
  endpointId: string,
): EndpointRef[] {
  const j = graph.jointOf.get(`${labelId}:${endpointId}`);
  if (!j || j.members.length < 2) return [];
  return j.members;
}
