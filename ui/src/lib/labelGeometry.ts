// Geometry mutation helpers for labels. Pure functions — given a label and
// a delta or new vertex position, return the new geometry block. Used by the
// drag-handle and body-translate machinery in AnnotatePage.

import type {
  DimensionNumberLabel,
  FloorplanOpeningLabel,
  Label,
  Point,
} from '../api/types';

export function translateLabelGeometry(label: Label, dx: number, dy: number): Label['geometry'] {
  const t = (p: Point): Point => [p[0] + dx, p[1] + dy];
  switch (label.type) {
    case 'wall':
    case 'dimensioned_distance':
      return { start: t(label.geometry.start), end: t(label.geometry.end) };
    case 'dimension_number':
      return {
        anchor: label.geometry.anchor ? t(label.geometry.anchor) : undefined,
        bbox: label.geometry.bbox?.map(t) as DimensionNumberLabel['geometry']['bbox'],
      };
    case 'floorplan_opening':
      return { quad: label.geometry.quad.map(t) as FloorplanOpeningLabel['geometry']['quad'] };
    case 'view_opening':
      return {
        top_edge: label.geometry.top_edge.map(t) as Point[],
        bottom_edge: label.geometry.bottom_edge.map(t) as Point[],
      };
    case 'component_line':
      return { polyline: label.geometry.polyline.map(t) as Point[] };
    case 'height_mark':
      return { anchor: t(label.geometry.anchor) };
  }
}

// Returns the list of "drag handles" for a label — each is a 2D point + an
// id describing which part of the geometry it controls. The id lets the
// canvas know which sub-field to update when the handle moves.

export interface HandleSpec {
  id: string;             // e.g. 'start', 'end', 'quad.0', 'polyline.3'
  pt: Point;
  cursor?: string;        // CSS cursor when hovering
}

export function handlesFor(label: Label): HandleSpec[] {
  switch (label.type) {
    case 'wall':
    case 'dimensioned_distance':
      return [
        { id: 'start', pt: label.geometry.start, cursor: 'crosshair' },
        { id: 'end', pt: label.geometry.end, cursor: 'crosshair' },
      ];
    case 'dimension_number':
      return label.geometry.anchor
        ? [{ id: 'anchor', pt: label.geometry.anchor, cursor: 'move' }]
        : [];
    case 'floorplan_opening':
      return label.geometry.quad.map((pt, i) => ({
        id: `quad.${i}`,
        pt,
        cursor: cornerCursor(i),
      }));
    case 'view_opening':
      return [
        ...label.geometry.top_edge.map((pt, i) => ({ id: `top.${i}`, pt, cursor: 'crosshair' })),
        ...label.geometry.bottom_edge.map((pt, i) => ({ id: `bottom.${i}`, pt, cursor: 'crosshair' })),
      ];
    case 'component_line':
      return label.geometry.polyline.map((pt, i) => ({
        id: `polyline.${i}`,
        pt,
        cursor: 'move',
      }));
    case 'height_mark':
      return [{ id: 'anchor', pt: label.geometry.anchor, cursor: 'move' }];
  }
}

function cornerCursor(i: number): string {
  // quad[0] = top-left, [1] = top-right, [2] = bottom-right, [3] = bottom-left
  return ['nwse-resize', 'nesw-resize', 'nwse-resize', 'nesw-resize'][i] ?? 'move';
}

// Apply a handle move to a label, returning the new geometry block.
export function moveHandle(label: Label, handleId: string, newPt: Point): Label['geometry'] {
  switch (label.type) {
    case 'wall':
    case 'dimensioned_distance':
      if (handleId === 'start') return { ...label.geometry, start: newPt };
      if (handleId === 'end') return { ...label.geometry, end: newPt };
      break;
    case 'dimension_number':
      if (handleId === 'anchor') return { ...label.geometry, anchor: newPt };
      break;
    case 'floorplan_opening':
      if (handleId.startsWith('quad.')) {
        const i = parseInt(handleId.slice(5), 10);
        const quad = label.geometry.quad.slice() as FloorplanOpeningLabel['geometry']['quad'];
        quad[i] = newPt;
        return { quad };
      }
      break;
    case 'view_opening':
      if (handleId.startsWith('top.')) {
        const i = parseInt(handleId.slice(4), 10);
        const top_edge = label.geometry.top_edge.slice();
        top_edge[i] = newPt;
        return { ...label.geometry, top_edge };
      }
      if (handleId.startsWith('bottom.')) {
        const i = parseInt(handleId.slice(7), 10);
        const bottom_edge = label.geometry.bottom_edge.slice();
        bottom_edge[i] = newPt;
        return { ...label.geometry, bottom_edge };
      }
      break;
    case 'component_line':
      if (handleId.startsWith('polyline.')) {
        const i = parseInt(handleId.slice(9), 10);
        const polyline = label.geometry.polyline.slice();
        polyline[i] = newPt;
        return { polyline };
      }
      break;
    case 'height_mark':
      if (handleId === 'anchor') return { anchor: newPt };
      break;
  }
  return label.geometry;
}

// Returns the centroid of a label (used for selection rubber-band hit test
// and for link visuals).
export function labelCentroid(l: Label): Point {
  switch (l.type) {
    case 'wall':
    case 'dimensioned_distance':
      return [(l.geometry.start[0] + l.geometry.end[0]) / 2, (l.geometry.start[1] + l.geometry.end[1]) / 2];
    case 'dimension_number':
      return l.geometry.anchor ?? [0, 0];
    case 'floorplan_opening': {
      const [a, , c] = l.geometry.quad;
      return [(a[0] + c[0]) / 2, (a[1] + c[1]) / 2];
    }
    case 'view_opening': {
      const t = l.geometry.top_edge;
      const b = l.geometry.bottom_edge;
      return [(t[0][0] + b[b.length - 1][0]) / 2, (t[0][1] + b[b.length - 1][1]) / 2];
    }
    case 'component_line':
      return l.geometry.polyline[Math.floor(l.geometry.polyline.length / 2)] ?? [0, 0];
    case 'height_mark':
      return l.geometry.anchor;
  }
}
