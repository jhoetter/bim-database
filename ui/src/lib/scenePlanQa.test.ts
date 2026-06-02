import { describe, expect, it } from 'vitest';
import type { Label, ScenePlan } from '../api/types';
import { planHasAnalysis, planTaskDone, scenePlanNextAction, scenePlanStatusText, scenePlanWarnings } from './scenePlanQa';

const wall = {
  id: 'w1',
  type: 'wall',
  status: 'readable',
  geometry: { start: [0, 0], end: [100, 0] },
  attributes: {},
} as Label;

const opening = {
  id: 'o1',
  type: 'floorplan_opening',
  status: 'readable',
  geometry: { quad: [[10, -5], [20, -5], [20, 5], [10, 5]] },
  attributes: { opening_kind: 'window' },
  relations: [{ kind: 'belongs_to', other_id: 'w1' }],
} as Label;

function plan(markdown: string): ScenePlan {
  return {
    exists: true,
    key: 'house-test',
    file: 'scene.jpg',
    path: 'data/dataset/house-test/plans/scene.md',
    markdown,
    version: 'v1',
    status: 'active',
    template_version: 'scene-plan-v1',
    last_updated: null,
  };
}

describe('scene plan QA', () => {
  it('warns when labels exist without a plan', () => {
    expect(scenePlanWarnings({ exists: false } as ScenePlan, [wall])).toContain(
      'Labels exist, but this scene has no plan.',
    );
  });

  it('detects analysis and completed tasks', () => {
    const md = '## 1. Analysis Summary\n\n- Drawing role: EG plan\n\n## 4. Task List\n\n- [x] A2 analysis: outer wall silhouette\n';
    expect(planHasAnalysis(md)).toBe(true);
    expect(planTaskDone(md, 'A2')).toBe(true);
  });

  it('warns for walls/openings ahead of plan verification', () => {
    const md = '## 1. Analysis Summary\n\n- Drawing role: EG plan\n\n## 4. Task List\n\n- [ ] A2 analysis: outer wall silhouette\n- [ ] V2 verify: topology + score walls\n';
    const warnings = scenePlanWarnings(plan(md), [wall, opening]);
    expect(warnings).toContain('Wall labels exist before A2 outer silhouette analysis is complete.');
    expect(warnings).toContain('Openings exist before wall topology verification is complete.');
  });

  it('warns when failed verification has no later analysis', () => {
    const md = '## 1. Analysis Summary\n\n- Drawing role: EG plan\n\n## 5. Decision Log\n\n| Time | Mode | Evidence | Decision | Result |\n|---|---|---|---|---|\n| t | verification | wall qa | retry | failed |\n';
    expect(scenePlanWarnings(plan(md), [wall])).toContain(
      'A failed verification appears without later analysis.',
    );
  });

  it('warns when structured plan state has open blockers or bad verified gates', () => {
    const p = plan('## 1. Analysis Summary\n\n- Drawing role: EG plan\n');
    p.state = {
      schema_version: 'scene-plan-state-v1',
      key: 'house-test',
      file: 'scene.jpg',
      scene_tag: 'grundriss',
      status: 'blocked',
      defects: [
        {
          id: 'DEF-001',
          title: 'No openings',
          status: 'open',
          severity: 'blocker',
          category: 'opening_relation',
        },
      ],
      tasks: [
        {
          id: 'FINAL_QA',
          title: 'Final QA',
          phase: 'verification',
          category: 'qa',
          status: 'verified',
          required: true,
          gates: [{ id: 'NO_BLOCKER_DEFECTS', status: 'failed' }],
        },
      ],
    };
    const warnings = scenePlanWarnings(p, [wall]);
    expect(warnings).toContain('1 blocker defect(s) are still open.');
    expect(warnings).toContain('1 verified task(s) still have failed or pending gates.');
    expect(warnings).toContain('Final QA is verified while blocker defects are open.');
  });

  it('formats terminality badges and exposes singular next action', () => {
    const p = plan('## 1. Analysis Summary\n\n- Drawing role: EG plan\n');
    p.state = {
      schema_version: 'scene-plan-state-v1',
      key: 'house-test',
      file: 'scene.jpg',
      scene_tag: 'grundriss',
      status: 'needs_repair',
      current_state: {
        terminality: {
          status: 'needs_repair',
          open_blockers: 2,
          next_action: {
            kind: 'defect',
            action_id: 'ACT-DEF-001',
            mode: 'scene-defect-repair',
            task_id: 'VERIFY_INTERIOR_TOPOLOGY',
            defect_id: 'DEF-001',
            id: 'DEF-001',
            title: 'Wall score missing region 1',
            instruction: 'Repair the wall region.',
          },
        },
      },
      defects: [],
      tasks: [],
    };
    expect(scenePlanStatusText(p, [wall])).toBe('needs repair · 2');
    expect(scenePlanNextAction(p)?.action_id).toBe('ACT-DEF-001');
  });

  it('falls back to defect-derived next action for legacy plan state', () => {
    const p = plan('## 1. Analysis Summary\n\n- Drawing role: EG plan\n');
    p.state = {
      schema_version: 'scene-plan-state-v1',
      key: 'house-test',
      file: 'scene.jpg',
      scene_tag: 'grundriss',
      status: 'active',
      defects: [
        {
          id: 'DEF-002',
          title: 'Topology gap',
          status: 'open',
          severity: 'warning',
          category: 'wall_topology',
          expected_resolution: 'Review endpoint.',
        },
      ],
      tasks: [],
    };
    const action = scenePlanNextAction(p);
    expect(action?.action_id).toBe('ACT-DEF-002');
    expect(action?.mode).toBe('scene-defect-repair');
  });
});
