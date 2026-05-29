// W0 — house-first labeling workflow state machine.
//
// Six phases:
//   0 inventory       — every scene categorized + oriented/leveled
//   1 height_anchor   — Bezugshöhe + first labeled in any Ansicht/Schnitt
//   2 footprint       — width + depth + wall_thickness.outer from EG-Grundriss
//   3 orientation     — north edge picked on EG-Grundriss
//   4 bezugsmasse     — every Ansicht/Schnitt has Bezugshöhe + H/V references
//   5 detail          — never auto-completes; user marks done
//
// All predicates are pure functions over (facts, scenes) — no React, no
// localStorage. UI integration in W1+.
//
// Phase pointer = first phase whose predicate fails. Never moves backward.

import type { HouseFacts, SceneMetadataEntry, WorkflowState } from './house_facts';
import { PHASE_IDS, defaultWorkflowState, type PhaseId } from './house_facts';

export type { PhaseId };

/** Minimal projection of a scene needed for phase predicates. */
export interface SceneSummary {
  file: string;
  /** scene_tag — null when the scene exists but isn't yet categorized. */
  tag: string | null;
  /** Whether this scene's labels.json explicitly marks the scene as
   *  "detail / partial view" — gated out of Phase 4 completion. */
  detail_only?: boolean;
}

/** German label per phase id — used by toasts + the WorkflowGuide. */
export const PHASE_LABEL_DE: Record<PhaseId, string> = {
  inventory: 'Szenen-Inventar',
  height_anchor: 'Höhenkoten ankern',
  footprint: 'Hausgrundriss vermessen',
  orientation: 'Himmelsrichtung festlegen',
  bezugsmasse: 'Bezugsmaße pro Szene',
  detail: 'Detail-Beschriftung',
};

const PHASE_ORDER: Record<PhaseId, number> = {
  inventory: 0, height_anchor: 1, footprint: 2,
  orientation: 3, bezugsmasse: 4, detail: 5,
};

// ── Phase 0 — Inventory ─────────────────────────────────────────────────

// Tags that need orientation (Ansicht/Schnitt) or level (Grundriss).
function tagRequiresOrientation(tag: string | null | undefined): boolean {
  return tag === 'ansicht' || tag === 'schnitt';
}
function tagRequiresLevel(tag: string | null | undefined): boolean {
  return tag === 'grundriss';
}

export function isInventoryComplete(facts: HouseFacts, scenes: SceneSummary[]): boolean {
  for (const s of scenes) {
    const meta: SceneMetadataEntry | undefined = facts.scene_metadata[s.file];
    const tag = meta?.scene_tag ?? s.tag;
    if (!tag || tag === 'nicht_klassifiziert') return false;
    if (tagRequiresOrientation(tag) && !meta?.orientation) return false;
    if (tagRequiresLevel(tag) && !meta?.level) return false;
  }
  return scenes.length > 0;
}

// ── Phase 1 — Height anchor ─────────────────────────────────────────────

export function isHeightAnchorComplete(facts: HouseFacts): boolean {
  // Spec: Bezug + First. Other datums are recommended but skippable.
  return facts.heights.bezug_mm === 0 && typeof facts.heights.first_mm === 'number';
}

// ── Phase 2 — Footprint ─────────────────────────────────────────────────

export function isFootprintComplete(facts: HouseFacts): boolean {
  return typeof facts.extent.width_mm === 'number'
      && typeof facts.extent.depth_mm === 'number'
      && typeof facts.wall_thickness.outer_mm === 'number';
}

// ── Phase 3 — Orientation ───────────────────────────────────────────────

export function isOrientationComplete(facts: HouseFacts): boolean {
  const o = facts.orientation;
  if (!o) return false;
  // Either an edge is picked OR a manual angle is set.
  return o.north_edge_label_id != null
      || (typeof o.north_angle_deg === 'number' && Number.isFinite(o.north_angle_deg));
}

// ── Phase 4 — Bezugsmaße ────────────────────────────────────────────────

/** Per scene, the calibration must exist. Detail-only scenes opt out. */
export function isBezugsmasseComplete(facts: HouseFacts, scenes: SceneSummary[]): boolean {
  for (const s of scenes) {
    const meta = facts.scene_metadata[s.file];
    const tag = meta?.scene_tag ?? s.tag;
    if (!tag || (tag !== 'ansicht' && tag !== 'schnitt')) continue;
    if (s.detail_only) continue;
    if (!facts.calibration_per_scene[s.file]) return false;
  }
  return true;
}

// ── Phase 5 — Detail (never auto-completes) ─────────────────────────────

export function isDetailComplete(facts: HouseFacts): boolean {
  return facts.workflow?.phase_completed_at.detail != null
      || facts.workflow?.user_skipped.detail === true;
}

// ── Composition ─────────────────────────────────────────────────────────

export interface PhaseConfig {
  id: PhaseId;
  order: 0 | 1 | 2 | 3 | 4 | 5;
  label_de: string;
  isComplete: (facts: HouseFacts, scenes: SceneSummary[]) => boolean;
}

export const PHASE_CONFIGS: PhaseConfig[] = [
  { id: 'inventory',     order: 0, label_de: PHASE_LABEL_DE.inventory,     isComplete: isInventoryComplete },
  { id: 'height_anchor', order: 1, label_de: PHASE_LABEL_DE.height_anchor, isComplete: (f) => isHeightAnchorComplete(f) },
  { id: 'footprint',     order: 2, label_de: PHASE_LABEL_DE.footprint,     isComplete: (f) => isFootprintComplete(f) },
  { id: 'orientation',   order: 3, label_de: PHASE_LABEL_DE.orientation,   isComplete: (f) => isOrientationComplete(f) },
  { id: 'bezugsmasse',   order: 4, label_de: PHASE_LABEL_DE.bezugsmasse,   isComplete: isBezugsmasseComplete },
  { id: 'detail',        order: 5, label_de: PHASE_LABEL_DE.detail,        isComplete: (f) => isDetailComplete(f) },
];

/** First phase whose predicate fails. 'detail' is the terminal state when
 *  every other phase is complete. Skipped phases count as complete. */
export function currentPhase(facts: HouseFacts, scenes: SceneSummary[]): PhaseId {
  for (const p of PHASE_CONFIGS) {
    if (facts.workflow?.user_skipped[p.id]) continue;
    if (!p.isComplete(facts, scenes)) return p.id;
  }
  return 'detail';
}

/** Per-phase completion as a snapshot for UI rendering. */
export function phaseStatusSnapshot(
  facts: HouseFacts, scenes: SceneSummary[],
): Record<PhaseId, { complete: boolean; skipped: boolean; completedAt: string | null }> {
  const wf = facts.workflow ?? defaultWorkflowState();
  const out = {} as Record<PhaseId, { complete: boolean; skipped: boolean; completedAt: string | null }>;
  for (const p of PHASE_CONFIGS) {
    const skipped = wf.user_skipped[p.id] === true;
    out[p.id] = {
      complete: skipped || p.isComplete(facts, scenes),
      skipped,
      completedAt: wf.phase_completed_at[p.id] ?? null,
    };
  }
  return out;
}

/** Compare phase-pointer before vs. after. Returns the id of the phase
 *  that *just completed* if the pointer advanced past it, else null.
 *  Stamps `phase_completed_at` for the newly-completed phase. */
export function advanceWorkflow(
  prevFacts: HouseFacts,
  nextFacts: HouseFacts,
  scenes: SceneSummary[],
  sourceScene: string,
  nowIso: string,
): { newFacts: HouseFacts; advancedTo: PhaseId | null; nowOnPhase: PhaseId } {
  const before = currentPhase(prevFacts, scenes);
  const after = currentPhase(nextFacts, scenes);
  const beforeOrder = PHASE_ORDER[before];
  const afterOrder = PHASE_ORDER[after];
  if (afterOrder <= beforeOrder) {
    return { newFacts: nextFacts, advancedTo: null, nowOnPhase: after };
  }
  // The pointer advanced past `before` — stamp its completion. There may
  // be multiple phases in between if a single save completed several at
  // once (rare but possible — e.g. a Bezugsmaß save that also completes
  // height_anchor's predicate retroactively). Stamp them all.
  const wf: WorkflowState = nextFacts.workflow
    ? { ...nextFacts.workflow }
    : defaultWorkflowState();
  wf.phase_completed_at = { ...wf.phase_completed_at };
  wf.source_scene = { ...wf.source_scene };
  for (const id of PHASE_IDS) {
    const ord = PHASE_ORDER[id];
    if (ord >= beforeOrder && ord < afterOrder) {
      if (!wf.phase_completed_at[id]) {
        wf.phase_completed_at[id] = nowIso;
        if (!wf.source_scene[id]) wf.source_scene[id] = sourceScene;
      }
    }
  }
  wf.phase = after;
  return {
    newFacts: { ...nextFacts, workflow: wf },
    advancedTo: before,  // the most-recently-completed phase
    nowOnPhase: after,
  };
}

// ── Scene recommendation ────────────────────────────────────────────────

/** First source listed for any fact key in `sources`; used to recover the
 *  scene file a fact was promoted from. */
function firstSourceScene(sources: Record<string, string[]>, key: string): string | null {
  const refs = sources[key];
  if (!refs || refs.length === 0) return null;
  // Format: '<file>#<kind>:<labelId>'.
  const file = refs[0].split('#')[0];
  return file || null;
}

/** Phase-1 recommended scene: prefer the scene that *already* placed the
 *  Bezugshöhe (so the user continues there); else the first Ansicht
 *  alphabetically; else the first Schnitt; else null. */
export function recommendHeightScene(
  facts: HouseFacts, scenes: SceneSummary[],
): string | null {
  const fromBezug = firstSourceScene(facts.heights.sources, 'bezug_mm');
  if (fromBezug) return fromBezug;
  const fromFirst = firstSourceScene(facts.heights.sources, 'first_mm');
  if (fromFirst) return fromFirst;
  const tagOf = (s: SceneSummary) => facts.scene_metadata[s.file]?.scene_tag ?? s.tag;
  const ansichten = scenes.filter((s) => tagOf(s) === 'ansicht').sort((a, b) => a.file.localeCompare(b.file));
  if (ansichten.length > 0) return ansichten[0].file;
  const schnitte = scenes.filter((s) => tagOf(s) === 'schnitt').sort((a, b) => a.file.localeCompare(b.file));
  if (schnitte.length > 0) return schnitte[0].file;
  return null;
}

/** Phase-2/3 recommended scene: the EG Grundriss; else the lowest level
 *  Grundriss available; else null. Phase 2 and Phase 3 share the same
 *  scene (the user dimensions the Grundriss, then picks the north edge
 *  on the same view). */
export function recommendFootprintScene(
  facts: HouseFacts, scenes: SceneSummary[],
): string | null {
  const tagOf = (s: SceneSummary) => facts.scene_metadata[s.file]?.scene_tag ?? s.tag;
  const grundrisse = scenes.filter((s) => tagOf(s) === 'grundriss');
  const eg = grundrisse.find((s) => facts.scene_metadata[s.file]?.level === 'eg');
  if (eg) return eg.file;
  const levelOrder = ['eg', 'og', 'dg', 'spitzboden', 'ug', 'kg'] as const;
  for (const lvl of levelOrder) {
    const found = grundrisse.find((s) => facts.scene_metadata[s.file]?.level === lvl);
    if (found) return found.file;
  }
  return grundrisse[0]?.file ?? null;
}

/** Phase 4 recommended scene: the next Ansicht/Schnitt that still lacks
 *  per-scene calibration. Deterministic alphabetical walk. */
export function recommendBezugsmasseScene(
  facts: HouseFacts, scenes: SceneSummary[],
): string | null {
  const tagOf = (s: SceneSummary) => facts.scene_metadata[s.file]?.scene_tag ?? s.tag;
  const candidates = scenes
    .filter((s) => {
      const t = tagOf(s);
      return (t === 'ansicht' || t === 'schnitt') && !s.detail_only;
    })
    .sort((a, b) => a.file.localeCompare(b.file));
  for (const s of candidates) {
    if (!facts.calibration_per_scene[s.file]) return s.file;
  }
  return null;
}

export function recommendSceneFor(
  phase: PhaseId, facts: HouseFacts, scenes: SceneSummary[],
): string | null {
  switch (phase) {
    case 'inventory': return null;  // user picks from the gap list
    case 'height_anchor': return recommendHeightScene(facts, scenes);
    case 'footprint':     return recommendFootprintScene(facts, scenes);
    case 'orientation':   return recommendFootprintScene(facts, scenes);
    case 'bezugsmasse':   return recommendBezugsmasseScene(facts, scenes);
    case 'detail':        return null;
  }
}

// ── Phase 4 (W5) geometric extent derivation ────────────────────────────

import type { Label, Point, WallLabel } from '../api/types';

/** Pixel length of a wall's geometry. */
function pixelLength(wall: WallLabel): number {
  const dx = wall.geometry.end[0] - wall.geometry.start[0];
  const dy = wall.geometry.end[1] - wall.geometry.start[1];
  return Math.hypot(dx, dy);
}

/** Unit direction vector of a wall (from start to end). */
function wallDirection(wall: WallLabel): [number, number] {
  const dx = wall.geometry.end[0] - wall.geometry.start[0];
  const dy = wall.geometry.end[1] - wall.geometry.start[1];
  const m = Math.hypot(dx, dy);
  return m < 1e-6 ? [1, 0] : [dx / m, dy / m];
}

/** Smallest angle between two unit vectors (degrees, in [0, 180]). */
function angleBetweenDeg(a: [number, number], b: [number, number]): number {
  const dot = Math.max(-1, Math.min(1, a[0] * b[0] + a[1] * b[1]));
  return (Math.acos(dot) * 180) / Math.PI;
}

/** Given the orientation graph + the labels of the Grundriss it points
 *  to, return:
 *  - northEdge: the picked wall (or null)
 *  - eastEdge:  another outer wall ≈ perpendicular to northEdge (or null)
 *  - pxPerMm:   the Grundriss calibration
 *
 *  Used by faceLengthAlong() and the canvas compass overlay. */
export function resolveOrientationBasis(
  facts: HouseFacts,
  grundrissLabels: Label[],
): {
  northEdge: WallLabel | null;
  eastEdge: WallLabel | null;
  pxPerMm: number | null;
} {
  const o = facts.orientation;
  if (!o?.north_edge_label_id) return { northEdge: null, eastEdge: null, pxPerMm: null };
  const northEdge = grundrissLabels.find(
    (l) => l.id === o.north_edge_label_id && l.type === 'wall',
  ) as WallLabel | undefined;
  if (!northEdge) return { northEdge: null, eastEdge: null, pxPerMm: null };
  const nDir = wallDirection(northEdge);
  // Find the longest wall that's ≈90° to northEdge — that's our east edge.
  let eastEdge: WallLabel | null = null;
  let bestLen = 0;
  for (const l of grundrissLabels) {
    if (l.type !== 'wall') continue;
    if (l.id === northEdge.id) continue;
    const ang = angleBetweenDeg(nDir, wallDirection(l));
    if (ang < 75 || ang > 105) continue;  // not perpendicular
    const len = pixelLength(l);
    if (len > bestLen) { bestLen = len; eastEdge = l; }
  }
  const calib = facts.calibration_per_scene[o.source_grundriss_file];
  return { northEdge, eastEdge, pxPerMm: calib?.px_per_mm ?? null };
}

// Re-export Point so the compass widget can import from one place.
export type { Point };
