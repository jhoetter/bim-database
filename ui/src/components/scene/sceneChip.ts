// U12 — single source of truth for "what a scene looks like as a chip".
// Five views render scenes (SceneStrip on Extract + Annotate,
// HouseCard, ExportPage scene table, the U9 popover header). They all
// have to agree on terminology, indicators and the labeled checkmark.
// This file gives them one shape and one helper.

import type { DatasetDrawing } from '../../api/types';

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

const FLOOR_LABEL: Record<string, string> = {
  KG: 'KG', UG: 'UG', EG: 'EG', OG: 'OG', '1OG': '1.OG', '2OG': '2.OG',
  DG: 'DG', SP: 'Spitzboden',
};

const VIEW_LABEL: Record<string, string> = {
  N: 'Nord', S: 'Süd', O: 'Ost', W: 'West',
  nord: 'Nord', sued: 'Süd', ost: 'Ost', west: 'West',
};

export function chipKindLabel(s: Pick<SceneChipData, 'kind' | 'floor' | 'view'>): string {
  if (s.kind === 'floorplan' && s.floor) return `${KIND_LABEL.floorplan} ${FLOOR_LABEL[s.floor] ?? s.floor}`;
  if ((s.kind === 'elevation' || s.kind === 'section') && s.view) {
    return `${KIND_LABEL[s.kind]} ${VIEW_LABEL[s.view] ?? s.view}`;
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

export function chipReadinessTitle(s: Pick<SceneChipData, 'readiness'>): string {
  if (!s.readiness) return '';
  const { hasH, hasV } = s.readiness;
  if (hasH && hasV) return ' · Bezug H+V gesetzt → Skalierung+Entzerrung bereit';
  if (hasH) return ' · nur horizontaler Bezug — vertikalen fehlt noch';
  if (hasV) return ' · nur vertikaler Bezug — horizontalen fehlt noch';
  return ' · keine Bezugsmaße — Skalierung+Entzerrung noch nicht möglich';
}

/** Build a SceneChipData from a DatasetDrawing. The readiness field is
 *  optional — supply it from the caller when a per-scene labels summary
 *  has already been fetched. */
export function fromDatasetDrawing(
  d: DatasetDrawing,
  readiness?: { hasH: boolean; hasV: boolean },
): SceneChipData {
  const cf = (d.crop_from as { page?: number } | undefined);
  return {
    file: d.file,
    url: d.url,
    title: d.title ?? d.file,
    kind: ((['floorplan', 'elevation', 'section', 'detail'] as const).includes(
      d.kind as SceneKind,
    ) ? (d.kind as SceneKind) : null),
    floor: d.floor ?? null,
    view: d.view ?? null,
    page: cf?.page ?? null,
    labeled: !!d.labeled,
    labelCount: d.label_count ?? 0,
    readiness,
  };
}

// Helpful aliases the caller can read out the dictionaries; keeps a
// single export site instead of duplicating these constants.
export const SCENE_KINDS: SceneKind[] = ['floorplan', 'elevation', 'section', 'detail'];
export const SCENE_FLOORS = ['KG', 'UG', 'EG', 'OG', '1OG', '2OG', 'DG', 'SP'];
export const SCENE_VIEWS  = ['N', 'S', 'O', 'W'];
export { KIND_LABEL, FLOOR_LABEL, VIEW_LABEL };
