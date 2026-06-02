import type { Label, ScenePlan } from '../api/types';

export function scenePlanStatusText(plan: ScenePlan | null, labels: Label[] = []): string {
  const state = plan?.state ?? null;
  const terminality = state?.current_state?.terminality;
  const status = terminality?.status ?? plan?.status ?? state?.status ?? null;
  const blockers = (state?.defects ?? []).filter((d) =>
    (d.status === 'open' || d.status === 'in_progress') && d.severity === 'blocker',
  );
  if (!plan?.exists && labels.length > 0) return 'missing plan';
  if (!status) return '';
  if (status === 'needs_repair') return `needs repair · ${terminality?.open_blockers ?? blockers.length}`;
  if (status === 'blocked_external') return 'blocked externally';
  if (blockers.length > 0) return `blocked · ${blockers.length}`;
  return status;
}

export function scenePlanNextAction(plan: ScenePlan | null) {
  const state = plan?.state ?? null;
  if (state?.current_state?.terminality?.next_action) {
    return state.current_state.terminality.next_action;
  }
  const rank = { blocker: 0, warning: 1, info: 2 } as Record<string, number>;
  const defect = [...(state?.defects ?? [])]
    .filter((d) => d.status === 'open' || d.status === 'in_progress')
    .sort((a, b) => (rank[a.severity] ?? 9) - (rank[b.severity] ?? 9) || a.id.localeCompare(b.id))[0];
  if (defect) {
    return {
      kind: 'defect',
      action_id: `ACT-${defect.id}`,
      mode: 'scene-defect-repair',
      task_id: null,
      defect_id: defect.id,
      id: defect.id,
      title: defect.title,
      severity: defect.severity,
      category: defect.category,
      region: defect.region,
      instruction: defect.expected_resolution || 'Analyze, repair, verify, and update this defect.',
    };
  }
  const task = (state?.tasks ?? []).find((t) =>
    t.required && ['todo', 'in_progress', 'blocked', 'needs_repair'].includes(t.status),
  );
  if (!task) return null;
  return {
    kind: 'task',
    action_id: `ACT-${task.id}`,
    mode: task.phase === 'analysis' ? 'scene-review' : 'scene-full-pass',
    task_id: task.id,
    defect_id: null,
    id: task.id,
    title: task.title,
    phase: task.phase,
    category: task.category,
    instruction: `Work only on ${task.id}: ${task.title}. Produce analysis evidence, at most one edit, then verification evidence.`,
  };
}

export function scenePlanWarnings(plan: ScenePlan | null, labels: Label[]): string[] {
  const warnings: string[] = [];
  const md = plan?.markdown ?? '';
  if (labels.length > 0 && !plan?.exists) {
    warnings.push('Labels exist, but this scene has no plan.');
    return warnings;
  }
  if (!plan?.exists) return warnings;
  const state = plan.state;
  const openDefects = (state?.defects ?? []).filter((d) => d.status === 'open' || d.status === 'in_progress');
  const blockers = openDefects.filter((d) => d.severity === 'blocker');
  if (blockers.length > 0) {
    warnings.push(`${blockers.length} blocker defect(s) are still open.`);
  }
  const badVerified = (state?.tasks ?? []).filter((task) =>
    task.status === 'verified' && (task.gates ?? []).some((gate) => gate.status === 'failed' || gate.status === 'pending'),
  );
  if (badVerified.length > 0) {
    warnings.push(`${badVerified.length} verified task(s) still have failed or pending gates.`);
  }
  const finalQa = (state?.tasks ?? []).find((task) => task.id === 'FINAL_QA');
  if (finalQa?.status === 'verified' && blockers.length > 0) {
    warnings.push('Final QA is verified while blocker defects are open.');
  }
  if (labels.length > 0 && !planHasAnalysis(md)) {
    warnings.push('Analysis Summary is still blank.');
  }
  const hasWalls = labels.some((l) => l.type === 'wall');
  const hasOpenings = labels.some((l) => l.type === 'floorplan_opening');
  if (hasWalls && !planTaskDone(md, 'A2')) {
    warnings.push('Wall labels exist before A2 outer silhouette analysis is complete.');
  }
  if (hasOpenings && !(planTaskDone(md, 'V2') || planTaskDone(md, 'V3'))) {
    warnings.push('Openings exist before wall topology verification is complete.');
  }
  const lower = md.toLowerCase();
  const lastFailed = Math.max(lower.lastIndexOf('verification'), lower.lastIndexOf('verify'));
  const lastAnalysis = lower.lastIndexOf('analysis');
  if (lastFailed >= 0 && lower.slice(lastFailed, lastFailed + 240).includes('fail') && lastAnalysis < lastFailed) {
    warnings.push('A failed verification appears without later analysis.');
  }
  return warnings;
}

export function planTaskDone(markdown: string, taskId: string): boolean {
  return new RegExp(`^\\s*-\\s*\\[[xX]\\]\\s*(?:\\*\\*)?${taskId}\\b`, 'm').test(markdown);
}

export function planHasAnalysis(markdown: string): boolean {
  const m = markdown.match(/## 1\. Analysis Summary([\s\S]*?)(?:\n## |\s*$)/);
  if (!m) return false;
  return m[1].split(/\r?\n/).some((raw) => {
    const line = raw.trim();
    if (!line || !line.startsWith('- ')) return false;
    const value = line.includes(':') ? line.split(':').slice(1).join(':').trim() : line.slice(2).trim();
    return value.length > 0;
  });
}
