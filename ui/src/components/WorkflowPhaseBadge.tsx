// W9 — workflow phase badge for house-list cards. Reads house_facts
// directly from localStorage (synchronous, cheap) so cards can show
// progress without a server round-trip.
//
// Renders nothing when no workflow state exists (the user has never
// opened this house). Otherwise: a 6-segment strip + "Schritt N / 6"
// label + optional "Fertig" pill if the user marked Phase 5 done.

import { loadHouseFacts } from '../lib/house_facts';
import { currentPhase, PHASE_LABEL_DE, phaseStatusSnapshot, type PhaseId, type SceneSummary } from '../lib/workflow';
import type { LabelScope } from '../api/types';

export interface WorkflowPhaseBadgeProps {
  scope: LabelScope;
  houseKey: string;
  /** Files in this house — used by predicates that need to know how many
   *  scenes exist (inventory, bezugsmasse). */
  sceneFiles?: string[];
  /** Visual variant. 'compact' = single line (fits inside a house-card
   *  bottom row); 'full' = strip + label. */
  variant?: 'compact' | 'full';
}

const PHASE_LIST: PhaseId[] = [
  'inventory', 'height_anchor', 'footprint',
  'orientation', 'bezugsmasse', 'detail',
];

export function WorkflowPhaseBadge({
  scope, houseKey, sceneFiles, variant = 'compact',
}: WorkflowPhaseBadgeProps) {
  const facts = loadHouseFacts(scope, houseKey);
  const wf = facts.workflow;
  // If the user has never opened the editor on this house, both predicates
  // would fire (inventory wants scenes that aren't here, etc.). Rather
  // than show a misleading "Phase 0", surface nothing.
  if (!wf || PHASE_LIST.every((p) => wf.phase_completed_at[p] == null)) return null;

  const scenes: SceneSummary[] = (sceneFiles ?? []).map((file) => ({
    file,
    tag: facts.scene_metadata[file]?.kind ?? null,
  }));
  const phase = currentPhase(facts, scenes);
  const snap = phaseStatusSnapshot(facts, scenes);
  const idx = PHASE_LIST.indexOf(phase);
  const allDone = facts.workflow?.phase_completed_at.detail != null;

  if (variant === 'compact') {
    return (
      <div
        className="flex items-center gap-1"
        title={`Schritt ${idx + 1} / 6 — ${PHASE_LABEL_DE[phase]}`}
      >
        {PHASE_LIST.map((p) => {
          const st = snap[p];
          const cls = st.complete
            ? 'bg-emerald-500'
            : p === phase
              ? 'bg-amber-400'
              : 'bg-zinc-200';
          return <span key={p} className={`w-1.5 h-1.5 rounded-full ${cls}`} />;
        })}
        {allDone ? (
          <span className="text-[0.6rem] text-emerald-700 font-semibold ml-0.5">Fertig</span>
        ) : (
          <span className="text-[0.6rem] text-zinc-500 font-mono ml-0.5">P{idx}</span>
        )}
      </div>
    );
  }
  return (
    <div className="space-y-0.5">
      <div className="flex gap-0.5">
        {PHASE_LIST.map((p) => {
          const st = snap[p];
          const cls = st.complete
            ? 'bg-emerald-500'
            : p === phase
              ? 'bg-amber-400'
              : 'bg-zinc-200';
          return <span key={p} className={`flex-1 h-1.5 rounded-sm ${cls}`} />;
        })}
      </div>
      <div className="text-[0.62rem] text-muted">
        {allDone ? 'Fertig markiert' : `Schritt ${idx + 1} / 6 — ${PHASE_LABEL_DE[phase]}`}
      </div>
    </div>
  );
}
