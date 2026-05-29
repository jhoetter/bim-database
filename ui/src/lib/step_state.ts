// R3 — per-house step computation. Used by every page that renders the
// StepperBar so the four flags stay consistent across IntakePage,
// ExtractPage, DatasetHousePage, AnnotatePage.

import type { DatasetHouse, IncomingPdf } from '../api/types';
import { loadHouseFacts } from './house_facts';

export interface PerHouseStepState {
  intakeDone: boolean;
  extractDone: boolean;
  annotateDone: boolean;
  exportDone: boolean;
}

export function computePerHouseSteps(
  houseKey: string,
  intake: IncomingPdf | null,
  dataset: DatasetHouse | null,
): PerHouseStepState {
  const intakeDone = !!intake && (intake.state === 'partial' || intake.state === 'extracted' || intake.state === 'annotated');
  const drawings = dataset?.drawings ?? [];
  const extractDone = drawings.length > 0;
  // Annotated: the workflow phase advanced past 'inventory' OR at least
  // one labeled scene exists (label_count > 0). Both signals get a "✓"
  // because either reflects real work.
  const facts = loadHouseFacts('dataset', houseKey);
  const anyLabeled = drawings.some((d) => d.labeled);
  const phasePastInventory =
    !!facts.workflow?.phase_completed_at.inventory ||
    facts.workflow?.phase === 'detail';
  const annotateDone = anyLabeled || phasePastInventory;
  // Export: the R6 endpoint produces /exports/<key>/ on disk. We can't see
  // disk from the client; until R6 lands we just mirror annotateDone &&
  // facts.workflow.phase_completed_at.detail.
  const exportDone = facts.workflow?.phase_completed_at.detail != null;
  return { intakeDone, extractDone, annotateDone, exportDone };
}

const LAST_STEP_KEY = (houseKey: string) => `bim-db:last-step:dataset:${houseKey}`;

export function rememberLastStep(houseKey: string, step: 'intake' | 'extract' | 'annotate' | 'export'): void {
  try { window.localStorage.setItem(LAST_STEP_KEY(houseKey), step); } catch { /* no-op */ }
}

export function getLastStep(houseKey: string): 'intake' | 'extract' | 'annotate' | 'export' | null {
  try {
    const v = window.localStorage.getItem(LAST_STEP_KEY(houseKey));
    if (v === 'intake' || v === 'extract' || v === 'annotate' || v === 'export') return v;
    return null;
  } catch { return null; }
}
