// Refine queue: scan the scene's labels for things that are likely to need
// fixing, surface them as a single sortable list, and offer one-click fixes
// where possible. The principle is "rough then refine" — draw quickly with
// imperfect input, then let the system tell you what's off.
//
// Issue categories (each row in the queue):
//   • CLASSIFY    — opening with kind='other' or component_line with
//                   line_kind='other'. Either we couldn't auto-infer or
//                   the user dismissed the post-draw chip.
//   • NO_VALUE    — dimensioned_distance with value_mm null. Without a
//                   value the dim is useless for homography.
//   • NO_DATUM    — height_mark with datum null AND value_mm != 0.
//                   The Bezugshöhe (value=0) doesn't need a datum.
//   • OFF_AXIS    — wall whose angle is > 5° off the building axis when
//                   ≥80% of other walls are on-axis. Likely a sloppy
//                   drawing.
//   • NOT_READABLE — status is 'blurry' or 'rejected'; surfaced so the
//                    user can re-triage.

import type { Label, LabelScope, SceneLevel, SceneOrientation, SceneTag } from '../api/types';
import { loadHouseFacts } from './house_facts';
import { referenceAngle } from './snap';

export type RefineKind =
  | 'classify'
  | 'no_value'
  | 'no_datum'
  | 'off_axis'
  | 'not_readable'
  | 'height_conflict'
  | 'extent_mismatch';

export interface RefineIssue {
  labelId: string;
  kind: RefineKind;
  /** Short human-readable description in German. */
  description: string;
  /** When set, a short suffix shown next to the [Fix] button explaining
   *  what one click will actually do (e.g. "→ 0°", "→ readable"). */
  fixHint?: string;
  /** If we can fix this with one click, suggest a payload here. */
  autoFix?:
    | { type: 'snap_to_axis'; targetAngleDeg: number }
    | { type: 'set_status'; status: 'readable' };
}

function lineAngleDeg(start: [number, number], end: [number, number]): number {
  return (Math.atan2(-(end[1] - start[1]), end[0] - start[0]) * 180) / Math.PI;
}

export function collectRefineIssues(
  labels: Label[],
  /** Optional house context — when provided, enables cross-scene checks
   *  like HEIGHT_CONFLICT (datum value disagrees with house facts) and
   *  EXTENT_MISMATCH (is_reference dim disagrees with derived extent). */
  context?: {
    scope: LabelScope;
    houseKey: string;
    sceneLevel: SceneLevel | null;
    sceneTag?: SceneTag;
    sceneOrientation?: SceneOrientation | null;
  },
): RefineIssue[] {
  const out: RefineIssue[] = [];
  const refAngle = referenceAngle(labels);

  // OFF_AXIS detection — only fires when the building has a strong axis
  // signal (≥3 walls). Otherwise we can't reliably call a wall "off-axis".
  const walls = labels.filter((l) => l.type === 'wall') as Array<Extract<Label, { type: 'wall' }>>;
  let strongAxisSignal = false;
  if (walls.length >= 3) {
    let onAxis = 0;
    for (const w of walls) {
      const a = lineAngleDeg(w.geometry.start, w.geometry.end);
      const targets = [refAngle, refAngle + 90, refAngle - 90, refAngle + 45, refAngle - 45, refAngle + 180, refAngle - 180];
      let bestDiff = Infinity;
      for (const t of targets) {
        const diff = Math.abs(((a - t + 540) % 360) - 180);
        if (diff < bestDiff) bestDiff = diff;
      }
      if (bestDiff <= 3) onAxis++;
    }
    strongAxisSignal = onAxis / walls.length >= 0.8;
  }

  for (const l of labels) {
    // Classification gaps
    if (l.type === 'floorplan_opening' || l.type === 'view_opening') {
      const k = (l.attributes as { opening_kind?: string }).opening_kind;
      if (!k || k === 'other') {
        out.push({
          labelId: l.id,
          kind: 'classify',
          description: 'Öffnung ohne Art — F/T/G/D klassifizieren',
        });
      }
    }
    if (l.type === 'component_line') {
      const k = (l.attributes as { line_kind?: string }).line_kind;
      if (!k || k === 'other') {
        out.push({
          labelId: l.id,
          kind: 'classify',
          description: 'Linie ohne Typ — W/D klassifizieren',
        });
      }
    }
    // Missing value
    if (l.type === 'dimensioned_distance') {
      const v = (l.attributes as { value_mm?: number | null }).value_mm;
      if (v == null) {
        out.push({
          labelId: l.id,
          kind: 'no_value',
          description: 'Bemaßung ohne Maßzahl',
        });
      }
    }
    // Missing datum
    if (l.type === 'height_mark') {
      const datum = (l.attributes as { datum?: string | null }).datum;
      const value = (l.attributes as { value_mm?: number | null }).value_mm;
      // Bezugshöhe (value=0) doesn't need a datum — it labels itself.
      if (!datum && value !== 0) {
        out.push({
          labelId: l.id,
          kind: 'no_datum',
          description: 'Höhenkote ohne Datum',
        });
      }
    }
    // Off-axis walls
    if (l.type === 'wall' && strongAxisSignal) {
      const a = lineAngleDeg(l.geometry.start, l.geometry.end);
      const targets = [refAngle, refAngle + 90, refAngle - 90, refAngle + 180, refAngle - 180];
      let bestDiff = Infinity;
      let bestTarget = refAngle;
      for (const t of targets) {
        const diff = Math.abs(((a - t + 540) % 360) - 180);
        if (diff < bestDiff) { bestDiff = diff; bestTarget = t; }
      }
      // 5°..30° off → flag. >30° → likely intentional non-ortho, leave alone.
      if (bestDiff > 5 && bestDiff < 30) {
        // Relative angle from the building axis, normalized to [-90, 90].
        let rel = bestTarget - refAngle;
        rel = ((rel % 180) + 180) % 180;
        if (rel > 90) rel -= 180;
        out.push({
          labelId: l.id,
          kind: 'off_axis',
          description: `Wand ${bestDiff.toFixed(1)}° schief`,
          fixHint: `→ ${Math.round(rel)}°`,
          autoFix: { type: 'snap_to_axis', targetAngleDeg: bestTarget },
        });
      }
    }
    // Non-readable status surfaced for re-triage
    if (l.status === 'not_readable' || l.status === 'missing' || l.status === 'uncertain') {
      const label =
        l.status === 'not_readable' ? 'Markiert als „nicht lesbar"'
        : l.status === 'missing'    ? 'Markiert als „fehlt"'
        :                              'Markiert als „unsicher"';
      out.push({
        labelId: l.id,
        kind: 'not_readable',
        description: label,
        fixHint: '→ readable',
        autoFix: { type: 'set_status', status: 'readable' },
      });
    }
  }

  // N8 — cross-scene height conflict detection. When the house knows
  // First = +12.5 m (from another scene) but THIS scene labels First
  // at +12.3 m, surface as a conflict the user should resolve. Threshold
  // 1 % so within-rounding diffs don't generate noise.
  if (context) {
    const facts = loadHouseFacts(context.scope, context.houseKey);
    const datumKeyFor = (d: string | null | undefined): keyof typeof facts.heights | null => {
      if (!d || d === 'other') return null;
      switch (d) {
        case 'first': return 'first_mm';
        case 'traufe': return 'traufe_mm';
        case 'gelaende': return 'gelaende_mm';
        case 'sockel': return 'sockel_mm';
        case 'kniestock': return 'kniestock_mm';
        case 'geschoss': return 'geschoss_mm';
        case 'ok_ffb':
          if (context.sceneLevel === 'og') return 'ok_ffb_og_mm';
          if (context.sceneLevel === 'dg') return 'ok_ffb_dg_mm';
          return 'ok_ffb_eg_mm';
        default: return null;
      }
    };
    for (const l of labels) {
      if (l.type !== 'height_mark') continue;
      const v = l.attributes.value_mm;
      if (v == null) continue;
      const k = datumKeyFor(l.attributes.datum);
      if (!k) continue;
      const houseVal = facts.heights[k] as number | undefined;
      if (typeof houseVal !== 'number') continue;
      const denom = Math.max(Math.abs(houseVal), 100);
      if (Math.abs(houseVal - v) / denom < 0.01) continue;
      const fmt = (mm: number) => mm === 0 ? '±0,00' : `${(mm / 1000).toFixed(2).replace('.', ',')} m`;
      out.push({
        labelId: l.id,
        kind: 'height_conflict',
        description: `Konflikt: ${l.attributes.datum} = ${fmt(v)} hier vs ${fmt(houseVal)} im Haus`,
        fixHint: `→ ${fmt(houseVal)}`,
        autoFix: undefined,    // resolution is a user decision — no auto-fix
      });
    }

    // W8 — extent_mismatch: an is_reference dim whose value differs from
    // the house's derived extent by >5%. Catches typos and Phase 4 dims
    // that were typed before Phase 2 was finalized.
    if (context.sceneTag === 'ansicht' || context.sceneTag === 'schnitt') {
      for (const l of labels) {
        if (l.type !== 'dimensioned_distance' || !l.attributes.is_reference) continue;
        const v = l.attributes.value_mm;
        if (v == null || v <= 0) continue;
        const dx = l.geometry.end[0] - l.geometry.start[0];
        const dy = l.geometry.end[1] - l.geometry.start[1];
        const ang = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
        const isH = ang < 15 || ang > 165;
        const isV = ang > 75 && ang < 105;
        if (!isH && !isV) continue;
        let derived: number | undefined;
        let derivedName = '';
        if (isV) {
          if (typeof facts.heights.first_mm === 'number' && typeof facts.heights.gelaende_mm === 'number') {
            derived = facts.heights.first_mm - facts.heights.gelaende_mm;
            derivedName = 'First − Gelände';
          } else if (typeof facts.heights.first_mm === 'number') {
            derived = facts.heights.first_mm;
            derivedName = 'First';
          } else if (typeof facts.extent.height_mm === 'number') {
            derived = facts.extent.height_mm;
            derivedName = 'Hausgröße';
          }
        } else if (isH && context.sceneOrientation) {
          const o = context.sceneOrientation;
          const useEast = context.sceneTag === 'ansicht'
            ? (o === 'north' || o === 'south')
            : (o === 'east' || o === 'west');
          const fact = useEast ? facts.extent.width_mm : facts.extent.depth_mm;
          if (typeof fact === 'number') {
            derived = fact;
            derivedName = useEast ? 'Hausbreite (ê)' : 'Haustiefe (n̂)';
          }
        }
        if (typeof derived !== 'number') continue;
        const denom = Math.max(derived, 100);
        if (Math.abs(derived - v) / denom < 0.05) continue;
        const fmt = (mm: number) => `${(mm / 1000).toFixed(2).replace('.', ',')} m`;
        out.push({
          labelId: l.id,
          kind: 'extent_mismatch',
          description: `Maß-Konflikt: ${fmt(v)} hier vs ${fmt(derived)} (${derivedName})`,
          fixHint: `→ ${fmt(derived)}`,
          autoFix: undefined,
        });
      }
    }
  }

  return out;
}
