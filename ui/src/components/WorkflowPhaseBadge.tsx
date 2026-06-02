// Workflow stage badge for house-list cards. Reads house_facts
// directly from localStorage (synchronous, cheap) so cards can show
// progress without a server round-trip.
//
// Renders nothing when no workflow state exists (the user has never
// opened this house). Otherwise: a 5-segment strip + "Stufe N / 5"
// label + optional "Fertig" pill if review is complete.

import { loadHouseFacts } from '../lib/house_facts';
import { currentPhase, PHASE_LABEL_DE, phaseStatusSnapshot, type PhaseId, type SceneSummary } from '../lib/workflow';
import type { LabelScope } from '../api/types';

export interface WorkflowPhaseBadgeProps {
  scope: LabelScope;
  houseKey: string;
  /** Files in this house — used by predicates that need to know how many
   *  scenes exist per class. */
  sceneFiles?: string[];
  /** Visual variant. 'compact' = single line (fits inside a house-card
   *  bottom row); 'full' = strip + label. */
  variant?: 'compact' | 'full';
}

const PHASE_LIST: PhaseId[] = [
  'inventory', 'floorplans', 'sections', 'elevations', 'review',
];

export function WorkflowPhaseBadge({
  scope, houseKey, sceneFiles, variant = 'compact',
}: WorkflowPhaseBadgeProps) {
  const facts = loadHouseFacts(scope, houseKey);
  const wf = facts.workflow;
  // If the user has never opened the editor on this house, both predicates
  // would fire (inventory wants scenes that aren't here, etc.). Rather
  // than show a misleading inventory stage, surface nothing.
  if (!wf || PHASE_LIST.every((p) => wf.phase_completed_at[p] == null)) return null;

  const scenes: SceneSummary[] = (sceneFiles ?? []).map((file) => ({
    file,
    tag: facts.scene_metadata[file]?.scene_tag ?? null,
  }));
  const phase = currentPhase(facts, scenes);
  const snap = phaseStatusSnapshot(facts, scenes);
  const idx = PHASE_LIST.indexOf(phase);
  const completed = facts.workflow?.phase_completed_at as Record<string, string | null> | undefined;
  const allDone = completed?.review != null || completed?.detail != null;

  if (variant === 'compact') {
    return (
      <div
        className="flex items-center gap-1"
        title={`Stufe ${idx + 1} / 5 — ${PHASE_LABEL_DE[phase]}`}
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
        {allDone ? 'Fertig markiert' : `Stufe ${idx + 1} / 5 — ${PHASE_LABEL_DE[phase]}`}
      </div>
    </div>
  );
}
