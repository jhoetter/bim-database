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
    const tag = meta?.kind ?? s.tag;
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
    const tag = meta?.kind ?? s.tag;
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

/** Mark a phase user-skipped (so the guide stops nagging). Caller writes
 *  the returned facts back via saveHouseFacts. */
export function setPhaseSkipped(
  facts: HouseFacts, phase: PhaseId, skipped: boolean,
): HouseFacts {
  const wf: WorkflowState = facts.workflow
    ? { ...facts.workflow, user_skipped: { ...facts.workflow.user_skipped, [phase]: skipped } }
    : { ...defaultWorkflowState(), user_skipped: { [phase]: skipped } };
  return { ...facts, workflow: wf };
}
