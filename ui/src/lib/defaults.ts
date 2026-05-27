// Default-value learning. Stored per (scope, scene_tag, label_type) tuple
// in localStorage so the annotator only types each attribute once per
// session per tag. Status is NOT remembered — it always defaults to
// 'readable' (honesty principle from spec/annotation-tool.md §4.1).

import type { Label, LabelScope, SceneTag } from '../api/types';

const STORAGE_KEY = 'bim-db:annotate:defaults';

type DefaultsTree = {
  [scope in LabelScope]?: {
    [tag in SceneTag]?: {
      [type: string]: Record<string, unknown>;
    };
  };
};

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

// Per-type attribute allow-list: what we actually want to learn. Excludes
// per-label values (text, value_mm) which differ between strokes; only
// captures style/preset choices.
const LEARNABLE: Record<string, string[]> = {
  wall: ['thickness_mm'],
  floorplan_opening: ['opening_kind', 'swing', 'swing_side', 'width_mm'],
  view_opening: ['opening_kind', 'frame_visible'],
  component_line: ['line_kind'],
  dimensioned_distance: ['target_orientation', 'is_reference'],
  // dim_number, height_mark — text/value vary; nothing to learn
};

export function getDefaults(
  scope: LabelScope,
  tag: SceneTag,
  type: Label['type'],
): Record<string, unknown> {
  const tree = load();
  return tree[scope]?.[tag]?.[type] ?? {};
}

export function rememberDefaults(
  scope: LabelScope,
  tag: SceneTag,
  type: Label['type'],
  attributes: Record<string, unknown>,
): void {
  const allow = LEARNABLE[type];
  if (!allow || allow.length === 0) return;
  const tree = load();
  const node = ((tree[scope] ??= {})[tag] ??= {});
  const slice: Record<string, unknown> = {};
  for (const k of allow) {
    if (k in attributes) slice[k] = attributes[k];
  }
  if (Object.keys(slice).length === 0) return;
  node[type] = { ...(node[type] ?? {}), ...slice };
  save(tree);
}

export function clearDefaults(scope: LabelScope, tag: SceneTag): void {
  const tree = load();
  if (tree[scope]?.[tag]) {
    delete tree[scope]![tag];
    save(tree);
  }
}
