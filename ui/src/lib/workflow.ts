// Scene-class labeling workflow state machine.
//
// Stages:
//   0 inventory   — every scene categorized; Grundriss leveled
//   1 floorplans  — all Grundriss scenes first
//   2 sections    — all Schnitt scenes after floorplans
//   3 elevations  — all Ansicht scenes after sections
//   4 review      — optional final pass
//
// All predicates are pure functions over (facts, scenes) — no React, no
// localStorage.
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
 *  "detail / partial view". */
  detail_only?: boolean;
}

/** German label per phase id — used by toasts + the WorkflowGuide. */
export const PHASE_LABEL_DE: Record<PhaseId, string> = {
  inventory: 'Szenen-Inventar',
  floorplans: 'Grundrisse beschriften',
  sections: 'Schnitte beschriften',
  elevations: 'Ansichten beschriften',
  review: 'Review / Export',
};

const PHASE_ORDER: Record<PhaseId, number> = {
  inventory: 0, floorplans: 1, sections: 2, elevations: 3, review: 4,
};

// ── Inventory ───────────────────────────────────────────────────────────

function tagRequiresLevel(tag: string | null | undefined): boolean {
  return tag === 'grundriss';
}

export function isInventoryComplete(facts: HouseFacts, scenes: SceneSummary[]): boolean {
  for (const s of scenes) {
    const meta: SceneMetadataEntry | undefined = facts.scene_metadata[s.file];
    const tag = meta?.scene_tag ?? s.tag;
    if (!tag || tag === 'nicht_klassifiziert') return false;
    if (tagRequiresLevel(tag) && !meta?.level) return false;
  }
  return scenes.length > 0;
}

function tagOf(facts: HouseFacts, s: SceneSummary): string | null {
  return facts.scene_metadata[s.file]?.scene_tag ?? s.tag;
}

function scenesOf(facts: HouseFacts, scenes: SceneSummary[], tag: string): SceneSummary[] {
  return scenes.filter((s) => tagOf(facts, s) === tag && !s.detail_only);
}

function hasSceneClass(facts: HouseFacts, scenes: SceneSummary[], tag: string): boolean {
  return scenesOf(facts, scenes, tag).length > 0;
}

// The UI does not have every scene's label list, so class-stage completion
// uses persisted facts/calibration as a conservative display predicate. The
// server-side workflow is authoritative for exact geometry blockers.
export function isFloorplansComplete(facts: HouseFacts, scenes: SceneSummary[]): boolean {
  return isInventoryComplete(facts, scenes)
      && hasSceneClass(facts, scenes, 'grundriss')
      && typeof facts.wall_thickness.outer_mm === 'number'
      && typeof facts.extent.width_mm === 'number'
      && typeof facts.extent.depth_mm === 'number';
}

export function isSectionsComplete(facts: HouseFacts, scenes: SceneSummary[]): boolean {
  if (!isFloorplansComplete(facts, scenes)) return false;
  const schnitte = scenesOf(facts, scenes, 'schnitt');
  if (schnitte.length === 0) return true;
  return schnitte.every((s) => facts.calibration_per_scene[s.file]);
}

export function isElevationsComplete(facts: HouseFacts, scenes: SceneSummary[]): boolean {
  if (!isSectionsComplete(facts, scenes)) return false;
  const ansichten = scenesOf(facts, scenes, 'ansicht');
  if (ansichten.length === 0) return true;
  return ansichten.every((s) => facts.calibration_per_scene[s.file]);
}

export function isReviewComplete(facts: HouseFacts): boolean {
  // Defensive optional chain — facts.workflow may be partial (e.g. an
  // agent's set_house_facts patched only `workflow.driven_by` and the
  // server's deep-merge dropped the other fields). Readers must
  // tolerate any missing sub-fields.
  const completed = facts.workflow?.phase_completed_at as Record<string, string | null> | undefined;
  const skipped = facts.workflow?.user_skipped as Record<string, boolean | undefined> | undefined;
  return completed?.review != null
      || completed?.detail != null
      || skipped?.review === true
      || skipped?.detail === true;
}

// ── Composition ─────────────────────────────────────────────────────────

export interface PhaseConfig {
  id: PhaseId;
  order: 0 | 1 | 2 | 3 | 4;
  label_de: string;
  isComplete: (facts: HouseFacts, scenes: SceneSummary[]) => boolean;
}

export const PHASE_CONFIGS: PhaseConfig[] = [
  { id: 'inventory',  order: 0, label_de: PHASE_LABEL_DE.inventory,  isComplete: isInventoryComplete },
  { id: 'floorplans', order: 1, label_de: PHASE_LABEL_DE.floorplans, isComplete: isFloorplansComplete },
  { id: 'sections',   order: 2, label_de: PHASE_LABEL_DE.sections,   isComplete: isSectionsComplete },
  { id: 'elevations', order: 3, label_de: PHASE_LABEL_DE.elevations, isComplete: isElevationsComplete },
  { id: 'review',     order: 4, label_de: PHASE_LABEL_DE.review,     isComplete: (f) => isReviewComplete(f) },
];

/** First phase whose predicate fails. 'review' is the terminal state when
 *  every other phase is complete. Skipped phases count as complete. */
export function currentPhase(facts: HouseFacts, scenes: SceneSummary[]): PhaseId {
  // Defensive: facts.workflow.user_skipped may be undefined if an agent
  // patched workflow with only driven_by + similar fields.
  const skipped = facts.workflow?.user_skipped ?? {};
  for (const p of PHASE_CONFIGS) {
    if (skipped[p.id]) continue;
    if (!p.isComplete(facts, scenes)) return p.id;
  }
  return 'review';
}

/** Per-phase completion as a snapshot for UI rendering. */
export function phaseStatusSnapshot(
  facts: HouseFacts, scenes: SceneSummary[],
): Record<PhaseId, { complete: boolean; skipped: boolean; completedAt: string | null }> {
  const wf = _normalizeWorkflow(facts.workflow);
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

/** Defensive normalization — fills in any missing sub-fields with the
 *  defaults. Used by every reader that touches workflow.phase_completed_at
 *  or workflow.user_skipped so a partial workflow (agent's driven_by-only
 *  patch) can't crash the UI. */
function _normalizeWorkflow(wf: HouseFacts['workflow']): WorkflowState {
  const def = defaultWorkflowState();
  if (!wf) return def;
  const phase = PHASE_IDS.includes(wf.phase as PhaseId) ? wf.phase as PhaseId : def.phase;
  return {
    ...def,
    ...wf,
    phase,
    schema_version: '1.2',
    phase_completed_at: { ...def.phase_completed_at, ...(wf.phase_completed_at ?? {}) },
    source_scene: { ...def.source_scene, ...(wf.source_scene ?? {}) },
    user_skipped: { ...(wf.user_skipped ?? {}) },
  };
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
  // once. Stamp them all.
  // Use the defensive normalizer so a partial workflow (e.g. agent's
  // driven_by-only patch) gets the missing sub-fields back.
  const wf: WorkflowState = _normalizeWorkflow(nextFacts.workflow);
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

function sortByLevelThenName(facts: HouseFacts, scenes: SceneSummary[]): SceneSummary[] {
  const levelOrder = ['eg', 'ug', 'kg', 'og', 'dg', 'spitzboden'] as const;
  return [...scenes].sort((a, b) => {
    const la = facts.scene_metadata[a.file]?.level;
    const lb = facts.scene_metadata[b.file]?.level;
    const ia = levelOrder.indexOf(la as typeof levelOrder[number]);
    const ib = levelOrder.indexOf(lb as typeof levelOrder[number]);
    const oa = ia < 0 ? 99 : ia;
    const ob = ib < 0 ? 99 : ib;
    return oa - ob || a.file.localeCompare(b.file);
  });
}

export function recommendFloorplanScene(facts: HouseFacts, scenes: SceneSummary[]): string | null {
  return sortByLevelThenName(facts, scenesOf(facts, scenes, 'grundriss'))[0]?.file ?? null;
}

export function recommendSectionScene(facts: HouseFacts, scenes: SceneSummary[]): string | null {
  return scenesOf(facts, scenes, 'schnitt')
    .filter((s) => !facts.calibration_per_scene[s.file])
    .sort((a, b) => a.file.localeCompare(b.file))[0]?.file ?? null;
}

export function recommendElevationScene(facts: HouseFacts, scenes: SceneSummary[]): string | null {
  return scenesOf(facts, scenes, 'ansicht')
    .filter((s) => !facts.calibration_per_scene[s.file])
    .sort((a, b) => a.file.localeCompare(b.file))[0]?.file ?? null;
}

export function recommendSceneFor(
  phase: PhaseId, facts: HouseFacts, scenes: SceneSummary[],
): string | null {
  switch (phase) {
    case 'inventory': return null;  // user picks from the gap list
    case 'floorplans': return recommendFloorplanScene(facts, scenes);
    case 'sections': return recommendSectionScene(facts, scenes);
    case 'elevations': return recommendElevationScene(facts, scenes);
    case 'review': return null;
  }
}

// ── Geometric extent derivation ─────────────────────────────────────────

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
