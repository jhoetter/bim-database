// R3 — cross-step navigation. Renders the four-step pipeline at the top
// of every per-house page so the user never wonders "where am I?".
//
//   Hochladen ─→ Extrahieren ─→ Annotieren ─→ Export
//
// Each step's state comes from on-disk facts:
//   - Hochladen: the intake bundle exists with state >= partial
//   - Extrahieren: ≥1 scene is committed to the dataset manifest
//   - Annotieren: workflow phase reaches detail OR ≥1 labels JSON exists
//   - Export: a /exports/<key>/ directory exists (R6)
//
// We don't fetch any of that here — the parent passes the four booleans.
// Each step links to the corresponding route; clicking the current step
// is a no-op (signalled by the cursor).

import { NavLink } from 'react-router';

export type StepId = 'intake' | 'extract' | 'annotate' | 'export';

export interface StepState {
  id: StepId;
  label: string;
  done: boolean;
  href: string;
}

export function StepperBar({
  houseKey,
  current,
  intakeDone,
  extractDone,
  annotateDone,
  exportDone,
  /** Optional override for the "go to annotate" target. By default we
   *  link to /dataset/<key>; the annotation editor itself is per-scene
   *  so the caller may want to point at last-visited if known. */
  annotateHref,
}: {
  houseKey: string;
  current: StepId;
  intakeDone: boolean;
  extractDone: boolean;
  annotateDone: boolean;
  exportDone: boolean;
  annotateHref?: string;
}) {
  // When houseKey is empty (e.g. on /dataset/intake before a bundle is
  // selected) the per-house links collapse to /dataset so the user picks
  // a house first.
  const houseHref = (rest: string) => houseKey ? `/dataset/${houseKey}${rest}` : '/dataset';
  const steps: StepState[] = [
    { id: 'intake',   label: 'Hochladen',     done: intakeDone,   href: '/dataset/intake' },
    { id: 'extract',  label: 'Extrahieren',   done: extractDone,  href: houseHref('/extract') },
    { id: 'annotate', label: 'Annotieren',    done: annotateDone, href: annotateHref ?? houseHref('') },
    { id: 'export',   label: 'Export',        done: exportDone,   href: houseHref('/export') },
  ];
  return (
    <div className="flex items-center gap-1.5 px-3 py-1.5 bg-white border-b border-border">
      {steps.map((s, i) => {
        const isCurrent = s.id === current;
        const cls = isCurrent
          ? 'bg-accent text-white font-semibold'
          : s.done
            ? 'bg-emerald-100 text-emerald-900 hover:bg-emerald-200'
            : 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200';
        return (
          <div key={s.id} className="flex items-center gap-1.5">
            {i > 0 && (
              <span className={`text-[0.7rem] ${steps[i - 1].done ? 'text-emerald-600' : 'text-zinc-300'}`}>
                ─→
              </span>
            )}
            <NavLink
              to={s.href}
              className={`text-[0.72rem] px-2.5 py-0.5 rounded-full transition ${cls} ${
                isCurrent ? 'cursor-default pointer-events-none' : ''
              }`}
              title={`${i + 1}. ${s.label}${s.done ? ' ✓' : ''}`}
            >
              {s.done && !isCurrent && <span className="mr-1">✓</span>}
              {s.label}
            </NavLink>
          </div>
        );
      })}
    </div>
  );
}
