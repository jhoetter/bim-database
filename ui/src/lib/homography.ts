// Computes a 2D affine "rectification" from the scene's labeled reference
// strokes. Returns null when the labels don't provide enough constraint
// (the UI shows "noch nicht genug Bezüge" in that case).
//
// First-cut: affine-only. A horizontal reference stroke + a vertical
// reference stroke pin scale, rotation, and a uniform shear. Full
// perspective rectification (8-DOF homography) needs 4 corner-region
// strokes — accepted as a future upgrade in spec/annotation-tool.md §7.2.
//
// All inputs/outputs are in raw pixel coordinates; mm values are the
// real-world lengths the user labeled.

import type { DimensionedDistanceLabel, Label, Point } from '../api/types';

export interface Affine {
  // x_world = a*x_px + c*y_px + tx
  // y_world = b*x_px + d*y_px + ty
  a: number; b: number; c: number; d: number; tx: number; ty: number;
}

export interface RectificationResult {
  affine: Affine;
  /** 3x3 row-major for storage; bottom row [0,0,1] for an affine. */
  matrix: number[][];
  /** Stroke ids used to compute the transform. */
  computed_from: string[];
  /** Bounding-box size of the rectified image, in scaled output pixels. */
  rectified_size_px: [number, number];
  /** Top-left corner of the warped image in scaled-output pixel coords. */
  rectified_offset_px: [number, number];
  /** Pixels-per-mm scale applied to fit `target_max_dim` (display only). */
  display_scale: number;
  /** Sum of per-stroke residuals after fitting, in scaled-output pixels. */
  rms_residual_px: number;
  /** 'ok' | 'insufficient_references' | 'degenerate' */
  status: 'ok' | 'insufficient_references' | 'degenerate';
  reason?: string;
}

export interface RectifyOptions {
  /** Max dimension of the rectified output (so output fits a UI pane). */
  target_max_dim?: number;
  imageSize: [number, number];
}

const DEFAULT_TARGET_MAX = 1200;

function isDimDistance(l: Label): l is DimensionedDistanceLabel {
  return l.type === 'dimensioned_distance';
}

function strokeMmDir(s: DimensionedDistanceLabel): { px: Point; mm: number } | null {
  const v: Point = [s.geometry.end[0] - s.geometry.start[0], s.geometry.end[1] - s.geometry.start[1]];
  const mm = s.attributes.value_mm;
  if (mm == null) return null;
  return { px: v, mm };
}

function pickLongest(
  refs: DimensionedDistanceLabel[],
  isAxis: (s: DimensionedDistanceLabel) => boolean,
): DimensionedDistanceLabel | null {
  let best: DimensionedDistanceLabel | null = null;
  let bestLen = -1;
  for (const s of refs) {
    if (!isAxis(s)) continue;
    const v = strokeMmDir(s);
    if (!v) continue;
    const len = Math.hypot(v.px[0], v.px[1]);
    if (len > bestLen) {
      best = s;
      bestLen = len;
    }
  }
  return best;
}

function isHorizontal(s: DimensionedDistanceLabel): boolean {
  const o = s.attributes.target_orientation;
  return o === 'horizontal' || (typeof o === 'string' && /^angle_deg:0(\.0+)?$/.test(o));
}

function isVertical(s: DimensionedDistanceLabel): boolean {
  const o = s.attributes.target_orientation;
  return o === 'vertical' || (typeof o === 'string' && /^angle_deg:90(\.0+)?$/.test(o));
}

/**
 * Compute the affine that maps image pixels → rectified output pixels using
 * the labeled reference strokes. Currently: needs ≥1 horizontal + ≥1
 * vertical reference (both `is_reference: true` + non-null `value_mm`).
 * Picks the longest of each axis; returns 'insufficient_references' otherwise.
 */
export function computeRectification(
  labels: Label[],
  opts: RectifyOptions,
): RectificationResult {
  const refs = labels
    .filter(isDimDistance)
    .filter((s) => s.attributes.is_reference && s.attributes.value_mm != null);

  const H = pickLongest(refs, isHorizontal);
  const V = pickLongest(refs, isVertical);

  if (!H || !V) {
    return {
      affine: { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 },
      matrix: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
      computed_from: [],
      rectified_size_px: opts.imageSize,
      rectified_offset_px: [0, 0],
      display_scale: 1,
      rms_residual_px: 0,
      status: 'insufficient_references',
      reason: !H && !V
        ? 'Mindestens 1 horizontale + 1 vertikale Referenz-Strecke benötigt.'
        : !H
          ? 'Es fehlt eine horizontale Referenz-Strecke (is_reference + value_mm + target_orientation=horizontal).'
          : 'Es fehlt eine vertikale Referenz-Strecke.',
    };
  }

  const vh = strokeMmDir(H)!;
  const vv = strokeMmDir(V)!;
  const [px, py] = vh.px;
  const [qx, qy] = vv.px;
  const Lh = vh.mm;
  const Lv = vv.mm;

  // Solve M * [px qx; py qy] = [Lh 0; 0 Lv].
  const det = px * qy - qx * py;
  if (Math.abs(det) < 1e-6) {
    return {
      affine: { a: 1, b: 0, c: 0, d: 1, tx: 0, ty: 0 },
      matrix: [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
      computed_from: [H.id, V.id],
      rectified_size_px: opts.imageSize,
      rectified_offset_px: [0, 0],
      display_scale: 1,
      rms_residual_px: 0,
      status: 'degenerate',
      reason: 'Horizontale und vertikale Referenz sind nicht linear unabhängig — Strecken stehen fast parallel zueinander.',
    };
  }

  // World-coords matrix M (px → mm). y axis: SVG y grows downward, world y
  // grows downward too (consistent with the on-page drawing orientation),
  // so we keep the raw sign.
  const m11 = (Lh * qy) / det;
  const m12 = (-Lh * qx) / det;
  const m21 = (-Lv * py) / det;
  const m22 = (Lv * px) / det;

  // Compute the bbox in mm of all four image corners after applying (M, 0).
  // Then translate so the top-left mm corner sits at (0, 0), and scale to
  // fit the requested output max dim.
  const [imgW, imgH] = opts.imageSize;
  const corners: Point[] = [[0, 0], [imgW, 0], [imgW, imgH], [0, imgH]];
  const mmCorners = corners.map(([x, y]) => [m11 * x + m12 * y, m21 * x + m22 * y] as Point);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const [x, y] of mmCorners) {
    if (x < minX) minX = x;
    if (y < minY) minY = y;
    if (x > maxX) maxX = x;
    if (y > maxY) maxY = y;
  }
  const mmW = maxX - minX;
  const mmH = maxY - minY;
  const target = opts.target_max_dim ?? DEFAULT_TARGET_MAX;
  const scale = target / Math.max(mmW, mmH);

  // Final affine: M' = scale * (M, with translation that maps (minX, minY)
  // to (0, 0)).
  const a = m11 * scale;
  const c = m12 * scale;
  const b = m21 * scale;
  const d = m22 * scale;
  const tx = -minX * scale;
  const ty = -minY * scale;

  // Residual estimate: project both refs through the affine, compare against
  // expected world lengths.
  const projLen = (p: Point) => {
    const w: Point = [a * p[0] + c * p[1] + tx, b * p[0] + d * p[1] + ty];
    return w;
  };
  const wH0 = projLen(H.geometry.start);
  const wH1 = projLen(H.geometry.end);
  const wV0 = projLen(V.geometry.start);
  const wV1 = projLen(V.geometry.end);
  const horizScaledLen = Math.hypot(wH1[0] - wH0[0], wH1[1] - wH0[1]);
  const vertScaledLen = Math.hypot(wV1[0] - wV0[0], wV1[1] - wV0[1]);
  const expHoriz = Lh * scale;
  const expVert = Lv * scale;
  const rms = Math.sqrt(
    ((horizScaledLen - expHoriz) ** 2 + (vertScaledLen - expVert) ** 2) / 2,
  );

  return {
    affine: { a, b, c, d, tx, ty },
    matrix: [[a, c, tx], [b, d, ty], [0, 0, 1]],
    computed_from: [H.id, V.id],
    rectified_size_px: [Math.round(mmW * scale), Math.round(mmH * scale)],
    rectified_offset_px: [0, 0],
    display_scale: scale,
    rms_residual_px: rms,
    status: 'ok',
  };
}

/** Apply an affine to a point. */
export function applyAffine(A: Affine, p: Point): Point {
  return [A.a * p[0] + A.c * p[1] + A.tx, A.b * p[0] + A.d * p[1] + A.ty];
}

/** Apply an affine to a whole label by walking the relevant geometry fields. */
export function rectifyLabel(A: Affine, l: Label): Label {
  switch (l.type) {
    case 'wall':
    case 'dimensioned_distance':
      return {
        ...l,
        geometry: { start: applyAffine(A, l.geometry.start), end: applyAffine(A, l.geometry.end) },
      } as Label;
    case 'dimension_number':
      return {
        ...l,
        geometry: {
          anchor: l.geometry.anchor ? applyAffine(A, l.geometry.anchor) : undefined,
          bbox: l.geometry.bbox
            ? (l.geometry.bbox.map((p) => applyAffine(A, p)) as [Point, Point, Point, Point])
            : undefined,
        },
      } as Label;
    case 'floorplan_opening':
      return {
        ...l,
        geometry: {
          quad: l.geometry.quad.map((p) => applyAffine(A, p)) as [Point, Point, Point, Point],
        },
      } as Label;
    case 'view_opening':
      return {
        ...l,
        geometry: {
          top_edge: l.geometry.top_edge.map((p) => applyAffine(A, p)) as Point[],
          bottom_edge: l.geometry.bottom_edge.map((p) => applyAffine(A, p)) as Point[],
        },
      } as Label;
    case 'component_line':
      return {
        ...l,
        geometry: { polyline: l.geometry.polyline.map((p) => applyAffine(A, p)) as Point[] },
      } as Label;
    case 'height_mark':
      return { ...l, geometry: { anchor: applyAffine(A, l.geometry.anchor) } } as Label;
  }
}
