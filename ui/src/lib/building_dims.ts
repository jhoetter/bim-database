// House-wide building dimensions cache (X4).
//
// When the user labels a dimensioned_distance with is_reference=true, the
// labeled value_mm is the building's outer horizontal or vertical extent
// (M1: the longest H + longest V dim per scene get is_reference=true). This
// dimension is a PROPERTY OF THE BUILDING — Nordansicht's building width
// is the same as Südansicht's building width is the same as the EG floor
// plan's building width.
//
// On save we cache (scope, house, sceneTag, orientation) → { value_mm,
// from_scene_file }. When the user creates a new is_reference dim on a
// sibling scene of the same sceneTag, we pre-fill its value_mm from the
// cache and surface a transient provenance hint via the AnnotatePage
// crossSceneProvenance map (X5).
//
// Why per-sceneTag, not just per-house: Ansicht's horizontal = building
// width; Schnitt's horizontal = building depth (perpendicular axis). Same
// "longest H" label but different real-world axis. Per-sceneTag keeps the
// two from polluting each other.

import type { LabelScope } from '../api/types';

export type DimOrientation = 'horizontal' | 'vertical';
export interface BuildingDimEntry {
  value_mm: number;
  from_scene_file: string;
}

function key(
  scope: LabelScope, houseKey: string, sceneTag: string, orientation: DimOrientation,
): string {
  return `bim-db:annotate:building-dim:${scope}:${houseKey}:${sceneTag}:${orientation}`;
}

export function getBuildingDim(
  scope: LabelScope, houseKey: string, sceneTag: string, orientation: DimOrientation,
): BuildingDimEntry | null {
  try {
    const raw = window.localStorage.getItem(key(scope, houseKey, sceneTag, orientation));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (typeof parsed?.value_mm === 'number' && typeof parsed?.from_scene_file === 'string') {
      return parsed as BuildingDimEntry;
    }
  } catch { /* fall through */ }
  return null;
}

export function rememberBuildingDim(
  scope: LabelScope, houseKey: string, sceneTag: string, orientation: DimOrientation,
  value_mm: number, from_scene_file: string,
): void {
  if (!Number.isFinite(value_mm) || value_mm <= 0) return;
  try {
    window.localStorage.setItem(
      key(scope, houseKey, sceneTag, orientation),
      JSON.stringify({ value_mm, from_scene_file } as BuildingDimEntry),
    );
  } catch { /* no-op */ }
}

/** Classify a 2-point line as horizontal | vertical | null. Uses the same
 *  H/V buckets the M1 reference picker does (within ±15° of horizontal /
 *  ±15° of vertical). */
export function dimOrientation(start: [number, number], end: [number, number]): DimOrientation | null {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const a = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
  if (a < 15 || a > 165) return 'horizontal';
  if (a > 75 && a < 105) return 'vertical';
  return null;
}
