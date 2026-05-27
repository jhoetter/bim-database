// Per-label color resolution. Centralized so the canvas overlay, the label
// list chip, and any other "show class at a glance" surface stay in sync.
//
// Why subtype-aware: a wall is structurally the same class but an opening
// is wildly different depending on opening_kind (window vs door vs dormer
// vs garage). Reading a busy floorplan annotation is much easier when those
// kinds visually pop apart.

import type { Label } from '../api/types';

// Per-type "fallback" colors — used when no subtype is set or the label
// has no kind attribute.
const TYPE_FALLBACK: Record<Label['type'], string> = {
  wall: '#1f2937',                  // slate-800 — structural, neutral dark
  floorplan_opening: '#0284c7',     // sky-600 — most openings are windows
  view_opening: '#0284c7',
  component_line: '#6b7280',        // gray-500 — generic line
  height_mark: '#15803d',           // green-700 — height datum
  dimensioned_distance: '#9333ea',  // purple-600 — measurement
  dimension_number: '#9333ea',
};

// Opening kind → color. Windows blue, doors teal, dormers orange, garage
// doors brown, passages gray, skylights cyan, other = generic blue.
const OPENING_KIND_COLOR: Record<string, string> = {
  window: '#0284c7',        // sky-600
  door: '#0d9488',          // teal-600
  passage: '#71717a',       // zinc-500
  garage_door: '#92400e',   // amber-800
  skylight: '#0891b2',      // cyan-600
  dormer: '#ea580c',        // orange-600
  other: '#0284c7',
};

// component_line line_kind → color. gebaeudekante (vertical wall edges) =
// slate (structural), dachschraege (roof) = orange, other = gray.
const LINE_KIND_COLOR: Record<string, string> = {
  gebaeudekante: '#1f2937',     // slate-800 — same as wall, since it IS a wall edge
  dachschraege: '#ea580c',      // orange-600 — roof
  other: '#6b7280',             // gray-500
};

export function labelColor(label: Label): string {
  if (label.type === 'floorplan_opening' || label.type === 'view_opening') {
    const kind = (label.attributes as { opening_kind?: string }).opening_kind;
    if (kind && OPENING_KIND_COLOR[kind]) return OPENING_KIND_COLOR[kind];
  }
  if (label.type === 'component_line') {
    const kind = (label.attributes as { line_kind?: string }).line_kind;
    if (kind && LINE_KIND_COLOR[kind]) return LINE_KIND_COLOR[kind];
  }
  if (label.type === 'dimensioned_distance') {
    // is_reference dims pop with a brighter color so the user sees which
    // ones drive the homography.
    const isRef = (label.attributes as { is_reference?: boolean }).is_reference;
    if (isRef) return '#db2777';   // pink-600
  }
  return TYPE_FALLBACK[label.type];
}

// Legend entries for the label-list panel. (type, subtype) → swatch + label.
export interface LegendEntry {
  swatch: string;
  label: string;
  kindKey: string;          // 'wall' | 'opening:window' | 'line:dachschraege' | …
}

export const LEGEND: LegendEntry[] = [
  { swatch: TYPE_FALLBACK.wall,                  label: 'Wand',                 kindKey: 'wall' },
  { swatch: OPENING_KIND_COLOR.window,           label: 'Fenster',              kindKey: 'opening:window' },
  { swatch: OPENING_KIND_COLOR.door,             label: 'Tür',                  kindKey: 'opening:door' },
  { swatch: OPENING_KIND_COLOR.skylight,         label: 'Dachfenster',          kindKey: 'opening:skylight' },
  { swatch: OPENING_KIND_COLOR.dormer,           label: 'Gaube',                kindKey: 'opening:dormer' },
  { swatch: OPENING_KIND_COLOR.garage_door,      label: 'Tor',                  kindKey: 'opening:garage_door' },
  { swatch: OPENING_KIND_COLOR.passage,          label: 'Durchgang',            kindKey: 'opening:passage' },
  { swatch: LINE_KIND_COLOR.dachschraege,        label: 'Dachkante',            kindKey: 'line:dachschraege' },
  { swatch: TYPE_FALLBACK.height_mark,           label: 'Höhenkote',            kindKey: 'height_mark' },
  { swatch: TYPE_FALLBACK.dimensioned_distance,  label: 'Bemaßung',             kindKey: 'dim' },
  { swatch: '#db2777',                           label: 'Bemaßung (Bezug)',     kindKey: 'dim:reference' },
];
