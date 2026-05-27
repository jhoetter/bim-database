// Default-value learning. Keyed by (scope, houseKey, scene_tag, label_type)
// — per-house so that the user's preferences carry across scenes of the
// same house (e.g. wall_thickness=365 set once in floorplan_eg also
// pre-fills walls drawn in floorplan_og of the same house).
//
// Fallback chain when looking up a default:
//   1. (scope, houseKey, scene_tag, type)  — this scene's tag
//   2. (scope, houseKey, '*',       type)  — any tag of THIS house
//   3. (scope, '*',      scene_tag, type)  — same tag of ANY house
//   4. {}                                   — nothing learned
//
// rememberDefaults writes to (scope, houseKey, scene_tag, type) only. The
// fallback layers are read-only — they exist so we don't make the user
// re-set thickness on a brand-new scene when they've already established
// the convention elsewhere.

import type { Label, LabelScope, SceneTag } from '../api/types';

const STORAGE_KEY = 'bim-db:annotate:defaults';
const WILDCARD = '*';

type AttrSet = Record<string, unknown>;
type ByType = { [type: string]: AttrSet };
type ByTag = { [tag: string]: ByType };
type ByHouse = { [houseKey: string]: ByTag };
type DefaultsTree = { [scope in LabelScope]?: ByHouse };

function load(): DefaultsTree {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

function save(tree: DefaultsTree): void {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tree)); } catch { /* no-op */ }
}

const LEARNABLE: Record<string, string[]> = {
  wall: ['thickness_mm'],
  floorplan_opening: ['opening_kind', 'swing', 'swing_side', 'width_mm'],
  view_opening: ['opening_kind', 'frame_visible'],
  component_line: ['line_kind'],
  dimensioned_distance: ['target_orientation', 'is_reference'],
};

export function getDefaults(
  scope: LabelScope,
  houseKey: string,
  tag: SceneTag,
  type: Label['type'],
): Record<string, unknown> {
  const tree = load();
  const layers: Array<AttrSet | undefined> = [
    tree[scope]?.[houseKey]?.[tag]?.[type],          // 1
    tree[scope]?.[houseKey]?.[WILDCARD]?.[type],     // 2 — any tag of this house
    tree[scope]?.[WILDCARD]?.[tag]?.[type],          // 3 — same tag of any house
  ];
  // Merge bottom-up so the most-specific layer wins.
  let out: AttrSet = {};
  for (let i = layers.length - 1; i >= 0; i--) {
    if (layers[i]) out = { ...out, ...layers[i] };
  }
  return out;
}

export function rememberDefaults(
  scope: LabelScope,
  houseKey: string,
  tag: SceneTag,
  type: Label['type'],
  attributes: Record<string, unknown>,
): void {
  const allow = LEARNABLE[type];
  if (!allow || allow.length === 0) return;
  const tree = load();
  const byHouse = (tree[scope] ??= {});
  const byTag = (byHouse[houseKey] ??= {});
  const byType = (byTag[tag] ??= {});
  const slice: AttrSet = {};
  for (const k of allow) {
    if (k in attributes) slice[k] = attributes[k];
  }
  if (Object.keys(slice).length === 0) return;
  const mergedSpecific: AttrSet = { ...(byType[type] ?? {}), ...slice };
  byType[type] = mergedSpecific;
  // Also write a copy to the wildcard-tag layer of THIS house — that's
  // what makes layer (2) above useful. The wildcard keeps the most-recent
  // per-house value regardless of which tag taught it.
  const byTagWild: ByType = (byHouse[WILDCARD] ??= {});
  const mergedWild: AttrSet = { ...(byTagWild[type] ?? {}), ...slice };
  byTagWild[type] = mergedWild;
  save(tree);
}

export function clearDefaults(
  scope: LabelScope,
  houseKey: string,
  tag: SceneTag,
): void {
  const tree = load();
  const byHouse = tree[scope];
  if (!byHouse) return;
  if (byHouse[houseKey]) {
    delete byHouse[houseKey][tag];
    if (Object.keys(byHouse[houseKey]).length === 0) delete byHouse[houseKey];
  }
  save(tree);
}
