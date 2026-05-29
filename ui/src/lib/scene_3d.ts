// R5 — assemble a 3D scene from house_facts + per-scene labels.
//
// Coordinate convention (per spec §5.2):
//   - Y up
//   - X along the building's ê axis (east from the orientation graph)
//   - Z along the n̂ axis (north from the orientation graph)
//   - units: millimetres
//   - origin (0, 0, 0) = the Bezugshöhe (±0,00) in image space mapped to world
//
// Every assembled mesh comes with a confidence tag (solid / approximate /
// guessed / missing). Hover-tooltips on the canvas surface the source
// label IDs so the user can jump back to the editor.

import type {
  Point, ViewOpeningLabel, SceneLabels, SceneOrientation,
} from '../api/types';
import type { HouseFacts } from './house_facts';
import { resolveOrientationBasis } from './workflow';

export type Confidence = 'solid' | 'approximate' | 'guessed' | 'missing';

export interface BuiltScene3D {
  building: {
    width_mm: number;          // along ê axis (X in 3D)
    depth_mm: number;          // along n̂ axis (Z in 3D)
    /** Polygon in 3D footprint coords, y=0. Always 4 vertices for v1. */
    footprint: [number, number, number][];
    /** Confidence per assembled component. */
    confidence: {
      footprint: Confidence;
      walls: Confidence;
      slabs: Confidence;
      roof: Confidence;
    };
    /** Source label ids that drove each part (for hover tooltips). */
    sources: Record<string, string[]>;
  };
  /** Ground plane y (gelaende_mm in world coords). Always negative when
   *  Gelände sits below ±0,00 (typical). */
  ground_y: number;
  /** Top-of-wall y. Defaults to traufe_mm; falls back to first_mm. */
  wall_top_y: number;
  /** Roof peak y (first_mm) if a gable could be inferred. */
  ridge_y: number | null;
  /** Outer wall thickness in mm. */
  wall_thickness_mm: number;
  /** Floor slabs (semi-transparent horizontal rectangles). */
  floor_slabs: Array<{ name: string; y_mm: number }>;
  /** Per-face openings — one entry per face, with the 3D positions of
   *  every opening rectangle. */
  openings: Array<{
    face: 'north' | 'south' | 'east' | 'west';
    items: Array<{
      kind: string;
      width_mm: number;
      height_mm: number;
      cx_along_face_mm: number;
      cy_world_mm: number;
      confidence: Confidence;
      sources: string[];
    }>;
  }>;
  /** Height marks: y in world coords, label name. */
  height_marks: Array<{ y_mm: number; label: string }>;
  /** Compass: rotation of the north arrow (radians), 0 = +Z. */
  north_arrow_angle: number;
  /** A short list of facts the renderer couldn't satisfy — surfaced in
   *  the "what's missing" panel. */
  missing: string[];
}

/** Per-house scene bundle: HouseFacts + every scene's parsed labels. */
export interface SceneBundle {
  facts: HouseFacts;
  scenes: Record<string, SceneLabels>;
}

function val(n: number | undefined): number | null {
  return typeof n === 'number' && Number.isFinite(n) ? n : null;
}

/** Compute the world-y of a label in a given Ansicht given the scene's
 *  Bezugshöhe y_px and px_per_mm. The y axis flips because pixel-y grows
 *  downward but world-y grows upward (spec §5.2). */
function pixelYToWorldY(yPx: number, bezugYPx: number, pxPerMm: number): number {
  return (bezugYPx - yPx) / pxPerMm;
}

/** Compute the world-x ALONG THE FACE for an Ansicht label, given the
 *  scene's reference column and px_per_mm. The reference column = the
 *  leftmost wall edge of the building in that view. For v1 we assume
 *  the leftmost edge of the image is the leftmost wall, which is true
 *  for tightly-cropped scenes from R2. */
function pixelXToFaceMm(xPx: number, pxPerMm: number): number {
  return xPx / pxPerMm;
}

/** Find the first scene of `tag` matching `orientation` in the bundle. */
function findSceneByOrientation(
  bundle: SceneBundle,
  tag: 'ansicht' | 'schnitt',
  o: SceneOrientation,
): SceneLabels | null {
  for (const file of Object.keys(bundle.scenes)) {
    const meta = bundle.facts.scene_metadata[file];
    if (meta?.scene_tag !== tag) continue;
    if (meta?.orientation !== o) continue;
    return bundle.scenes[file];
  }
  return null;
}

function findGrundrissEG(bundle: SceneBundle): SceneLabels | null {
  for (const file of Object.keys(bundle.scenes)) {
    const meta = bundle.facts.scene_metadata[file];
    if (meta?.scene_tag === 'grundriss' && meta?.level === 'eg') return bundle.scenes[file];
  }
  // Fallback: first grundriss of any level.
  for (const file of Object.keys(bundle.scenes)) {
    const meta = bundle.facts.scene_metadata[file];
    if (meta?.scene_tag === 'grundriss') return bundle.scenes[file];
  }
  return null;
}

/** Build a 4-vertex rectangular footprint centred at (0, 0, 0) on the
 *  ground plane. Used when no actual Grundriss walls are available. */
function defaultFootprint(width_mm: number, depth_mm: number): [number, number, number][] {
  const w = width_mm / 2;
  const d = depth_mm / 2;
  return [
    [-w, 0,  d],   // SW corner (front-left)
    [ w, 0,  d],   // SE
    [ w, 0, -d],   // NE
    [-w, 0, -d],   // NW
  ];
}

export function buildScene3D(bundle: SceneBundle): BuiltScene3D {
  const f = bundle.facts;
  const missing: string[] = [];

  // Heights (world-y). gelaende below Bezugshöhe = negative.
  const first_mm = val(f.heights.first_mm);
  const traufe_mm = val(f.heights.traufe_mm);
  const gelaende_mm = val(f.heights.gelaende_mm);
  if (gelaende_mm == null) missing.push('Gelände-Höhe (Phase 1)');
  if (first_mm == null) missing.push('First-Höhe (Phase 1)');

  const ground_y = gelaende_mm ?? -500;
  const wall_top_y = traufe_mm ?? (first_mm != null ? first_mm * 0.75 : 5000);
  const ridge_y = first_mm;

  // Footprint width/depth.
  const width_mm = val(f.extent.width_mm) ?? 12000;
  const depth_mm = val(f.extent.depth_mm) ?? 8000;
  if (val(f.extent.width_mm) == null) missing.push('Hausbreite (Phase 2)');
  if (val(f.extent.depth_mm) == null) missing.push('Haustiefe (Phase 2)');

  // Try to derive a real footprint from the EG-Grundriss walls. Fall back
  // to the rectangle when none is available.
  let footprint: [number, number, number][] = defaultFootprint(width_mm, depth_mm);
  let footprintConfidence: Confidence = val(f.extent.width_mm) != null ? 'solid' : 'guessed';
  const eg = findGrundrissEG(bundle);
  const orientationBasis = eg
    ? resolveOrientationBasis(f, eg.labels)
    : { northEdge: null, eastEdge: null, pxPerMm: null };
  // Real footprint extraction is out of scope for v1; we keep the
  // rectangle but tag confidence='approximate' when an EG-Grundriss exists.
  if (eg) {
    footprintConfidence = 'approximate';
  }
  const footprintSources: string[] = eg?.labels.filter((l) => l.type === 'wall').map((l) => l.id) ?? [];

  // Wall thickness.
  const wall_thickness_mm = val(f.wall_thickness.outer_mm) ?? 365;
  if (val(f.wall_thickness.outer_mm) == null) missing.push('Außenwand-Dicke (Phase 2)');

  // Floor slabs from OK FFB heights.
  const floor_slabs: Array<{ name: string; y_mm: number }> = [];
  const slabSources = ['ok_ffb_eg_mm', 'ok_ffb_og_mm', 'ok_ffb_dg_mm'] as const;
  const slabNames = ['EG', 'OG', 'DG'];
  for (let i = 0; i < slabSources.length; i++) {
    const v = val(f.heights[slabSources[i]]);
    if (v != null) floor_slabs.push({ name: slabNames[i], y_mm: v });
  }
  const slabsConfidence: Confidence = floor_slabs.length > 0 ? 'solid' : 'missing';

  // Roof: we only build it when first_mm is known. The 3D scene composes
  // it as a tent over the footprint.
  const roofConfidence: Confidence =
    first_mm != null && traufe_mm != null ? 'approximate' :
    first_mm != null ? 'approximate' :
    'missing';

  // Openings per Ansicht. For each cardinal direction we look up the
  // Ansicht (if labeled), compute px_per_mm from the scene calibration
  // (R5 §8.1), and project every view_opening's center to 3D.
  const openings: BuiltScene3D['openings'] = [];
  for (const o of ['north', 'south', 'east', 'west'] as SceneOrientation[]) {
    const scene = findSceneByOrientation(bundle, 'ansicht', o);
    if (!scene) {
      openings.push({ face: o, items: [] });
      continue;
    }
    const calib = f.calibration_per_scene[scene.scene_file];
    const meta = f.scene_metadata[scene.scene_file];
    const bezugYPx = meta?.bezug_y_px ?? null;
    if (calib == null || bezugYPx == null) {
      openings.push({ face: o, items: [] });
      continue;
    }
    const pxPerMm = calib.px_per_mm;
    const items: BuiltScene3D['openings'][number]['items'] = [];
    for (const l of scene.labels) {
      if (l.type !== 'view_opening') continue;
      const vo = l as ViewOpeningLabel;
      // Compute centre + extents from geometry shape.
      let cx = 0, cy = 0, w = 0, h = 0;
      const g = vo.geometry as Record<string, unknown>;
      if (g.shape === 'circle') {
        const center = g.center as Point;
        const r = g.radius_px as number;
        cx = center[0]; cy = center[1]; w = r * 2; h = r * 2;
      } else if (g.shape === 'polygon') {
        const poly = g.polygon as Point[];
        const xs = poly.map((p) => p[0]); const ys = poly.map((p) => p[1]);
        cx = (Math.min(...xs) + Math.max(...xs)) / 2;
        cy = (Math.min(...ys) + Math.max(...ys)) / 2;
        w = Math.max(...xs) - Math.min(...xs);
        h = Math.max(...ys) - Math.min(...ys);
      } else {
        const top = (g.top_edge as Point[]) ?? [];
        const bot = (g.bottom_edge as Point[]) ?? [];
        const all = [...top, ...bot];
        if (all.length === 0) continue;
        const xs = all.map((p) => p[0]); const ys = all.map((p) => p[1]);
        cx = (Math.min(...xs) + Math.max(...xs)) / 2;
        cy = (Math.min(...ys) + Math.max(...ys)) / 2;
        w = Math.max(...xs) - Math.min(...xs);
        h = Math.max(...ys) - Math.min(...ys);
      }
      items.push({
        kind: (vo.attributes.opening_kind as string) ?? 'window',
        width_mm: w / pxPerMm,
        height_mm: h / pxPerMm,
        cx_along_face_mm: pixelXToFaceMm(cx, pxPerMm),
        cy_world_mm: pixelYToWorldY(cy, bezugYPx, pxPerMm),
        confidence: 'solid',
        sources: [l.id],
      });
    }
    openings.push({ face: o, items });
  }

  // Height marks (cross-scene Bezug + named datums).
  const height_marks: Array<{ y_mm: number; label: string }> = [];
  if (f.heights.bezug_mm === 0) height_marks.push({ y_mm: 0, label: '±0,00' });
  for (const [k, name] of [
    ['first_mm', 'First'],
    ['traufe_mm', 'Traufe'],
    ['gelaende_mm', 'Gelände'],
    ['ok_ffb_eg_mm', 'OK FFB EG'],
    ['ok_ffb_og_mm', 'OK FFB OG'],
    ['ok_ffb_dg_mm', 'OK FFB DG'],
  ] as const) {
    const v = val(f.heights[k as keyof typeof f.heights] as number | undefined);
    if (v != null) height_marks.push({ y_mm: v, label: name });
  }

  // Compass: rotate +Z to face the picked north edge. If we couldn't
  // resolve the basis the arrow points straight up the +Z axis.
  let north_arrow_angle = 0;
  if (orientationBasis.northEdge) {
    const n = orientationBasis.northEdge;
    const dx = n.geometry.end[0] - n.geometry.start[0];
    const dy = n.geometry.end[1] - n.geometry.start[1];
    // The wall direction is along the face; the outward normal points
    // away from the building. We don't have the centroid here, so use
    // the wall's perpendicular (left-hand normal) — same approximation
    // as W4's SceneCompass widget.
    north_arrow_angle = Math.atan2(-dx, dy);
  }

  return {
    building: {
      width_mm,
      depth_mm,
      footprint,
      confidence: {
        footprint: footprintConfidence,
        walls: footprintConfidence,
        slabs: slabsConfidence,
        roof: roofConfidence,
      },
      sources: {
        footprint: footprintSources,
        walls: footprintSources,
        slabs: [],
        roof: [],
      },
    },
    ground_y,
    wall_top_y,
    ridge_y,
    wall_thickness_mm,
    floor_slabs,
    openings,
    height_marks,
    north_arrow_angle,
    missing,
  };
}

