// U12 — shared terminology + label helpers for everywhere a scene's
// kind/floor/view shows up. SceneStrip (Extract + Annotate),
// HouseCard, ExportPage scene table, the SceneDetailsCard popover.
// One dictionary, one label function, no per-call-site re-implementations.

export type SceneKind = 'floorplan' | 'elevation' | 'section' | 'detail';

export interface SceneChipData {
  file: string;
  url: string;
  title: string;
  kind: SceneKind | null;
  floor: string | null;
  view: string | null;
  /** Source PDF page when the scene was cut from an intake PDF. */
  page: number | null;
  labeled: boolean;
  labelCount: number;
  /** H/V reference presence — drives the readiness dot. Optional because
   *  most call sites don't have it loaded; the renderer hides the dot
   *  when this is absent. */
  readiness?: { hasH: boolean; hasV: boolean };
}

const KIND_LABEL: Record<SceneKind, string> = {
  floorplan: 'Grundriss',
  elevation: 'Ansicht',
  section:   'Schnitt',
  detail:    'Detail',
};

// Floor codes drift in casing between R2 (lowercase) and the post-draw
// chip (uppercase). Normalise via toUpperCase before lookup.
const FLOOR_LABEL: Record<string, string> = {
  KG: 'KG', UG: 'UG', EG: 'EG', OG: 'OG', '1OG': '1.OG', '2OG': '2.OG',
  DG: 'DG', SP: 'Spitzboden',
};

function floorLabel(raw: string | null | undefined): string | null {
  if (!raw) return null;
  return FLOOR_LABEL[raw.toUpperCase()] ?? raw;
}

function viewLabel(raw: string | null | undefined): string | null {
  if (!raw) return null;
  return VIEW_LABEL[raw] ?? VIEW_LABEL[raw.toLowerCase()] ?? raw;
}

// View names must round-trip three vocabularies:
//   - server R2 stores English ('north', 'south', 'east', 'west')
//   - the post-draw chip writes German ('nord', 'sued' / 'süd', 'ost', 'west')
//   - keyboard shortcuts use single letters (N/S/O/W)
// All three forms map back to the same German label.
const VIEW_LABEL: Record<string, string> = {
  N: 'Nord', S: 'Süd', O: 'Ost', W: 'West',
  nord: 'Nord', sued: 'Süd', 'süd': 'Süd', ost: 'Ost', west: 'West',
  north: 'Nord', south: 'Süd', east: 'Ost',
};

export function chipKindLabel(s: Pick<SceneChipData, 'kind' | 'floor' | 'view'>): string {
  if (s.kind === 'floorplan' && s.floor) return `${KIND_LABEL.floorplan} ${floorLabel(s.floor) ?? s.floor}`;
  if ((s.kind === 'elevation' || s.kind === 'section') && s.view) {
    return `${KIND_LABEL[s.kind]} ${viewLabel(s.view) ?? s.view}`;
  }
  if (s.kind) return KIND_LABEL[s.kind];
  return 'Unklassifiziert';
}

export function chipReadinessColor(s: Pick<SceneChipData, 'readiness'>): string | null {
  if (!s.readiness) return null;
  const { hasH, hasV } = s.readiness;
  if (hasH && hasV) return '#10b981'; // emerald — ready
  if (hasH || hasV) return '#f59e0b'; // amber — half
  return '#d4d4d8';                   // zinc — none
}

// Helpful aliases the caller can read out the dictionaries; keeps a
// single export site instead of duplicating these constants.
export const SCENE_KINDS: SceneKind[] = ['floorplan', 'elevation', 'section', 'detail'];
export const SCENE_FLOORS = ['KG', 'UG', 'EG', 'OG', '1OG', '2OG', 'DG', 'SP'];
export const SCENE_VIEWS  = ['N', 'S', 'O', 'W'];
export { KIND_LABEL, FLOOR_LABEL, VIEW_LABEL };
