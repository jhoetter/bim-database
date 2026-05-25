import type { House } from '../../api/types';
import { formatFactValue } from '../../lib/format';
import { Badge } from '../Badge';

export function AnomalyPanel({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <Section title="⚠ Anomalien für Review">
      <ul className="bg-amber-100 border border-amber-200 rounded-md text-[0.8125rem] text-amber-900 leading-snug">
        {flags.map((f, i) => (
          <li
            key={i}
            className="px-3 py-2 border-b border-amber-200 last:border-b-0"
          >
            {f}
          </li>
        ))}
      </ul>
    </Section>
  );
}

export function DerivedFactsPanel({ derived }: { derived: House['derived_facts'] }) {
  const entries = Object.entries(derived ?? {});
  if (entries.length === 0) return null;
  return (
    <Section title="Abgeleitete Fakten (verifiziert)">
      <div className="grid grid-cols-1 gap-1.5">
        {entries.map(([k, v]) => {
          const tone =
            v.ok === true ? 'border-green-200 bg-green-50' :
            v.ok === false ? 'border-orange-200 bg-orange-50' :
            'border-border bg-zinc-50';
          const status =
            v.ok === true ? <span className="text-green-700">✓</span> :
            v.ok === false ? <span className="text-red-700">✗</span> :
            null;
          const valStr = formatFactValue(v.value, v.unit);
          const sources = v.sources ?? [];
          const exp = v.expected != null ? `Erwartet: ${String(v.expected)}` : '';
          return (
            <div
              key={k}
              className={`grid grid-cols-[1fr_auto] gap-x-2.5 gap-y-1 px-3 py-2 rounded-md border text-[0.8125rem] ${tone}`}
            >
              <div className="font-mono text-[0.72rem] text-muted">
                {status} <span className={status ? 'ml-1' : ''}>{k}</span>
              </div>
              <div className="font-semibold tabular-nums whitespace-nowrap text-right">
                {valStr}
              </div>
              {(sources.length > 0 || exp) && (
                <div className="col-span-2 text-[0.7rem] text-muted mt-0.5 leading-snug">
                  {exp && <>{exp} · </>}
                  <span>
                    aus{' '}
                    {sources.map((s) => (
                      <code
                        key={s}
                        className="bg-zinc-200 rounded-sm px-1 mr-0.5 text-[0.65rem] font-mono"
                      >
                        {s.replace(/^house-\d+-/, '')}
                      </code>
                    ))}
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Section>
  );
}

export function ModelabilityPanel({ h }: { h: House }) {
  const refs = h.bim_ai_blocking_issues ?? [];
  const m = h.modelable_in_bim_ai;

  // Hide for not-yet-assessed houses (no field).
  if (!h.assessed) return null;

  const headline =
    m === true ? (
      <>
        <Badge tone="ok">✓ modellierbar</Badge> Keine offenen Blocker.
      </>
    ) : m === false ? (
      <>
        <Badge tone="blocked">✗ blockiert</Badge> Mindestens ein referenziertes Issue ist
        noch offen.
      </>
    ) : (
      <>
        <Badge tone="unknown">? unbekannt</Badge> Cache fehlt für mindestens ein Issue —{' '}
        <code className="font-mono text-[0.75rem] bg-zinc-100 px-1 rounded">
          make refresh-issue-state
        </code>{' '}
        ausführen.
      </>
    );

  return (
    <Section title="bim-ai Modellierbarkeit">
      <div className="text-[0.85rem] flex items-center gap-2">{headline}</div>
      {refs.length > 0 && (
        <ul className="mt-2 pl-4 list-disc text-[0.85rem] space-y-0.5">
          {refs.map((r) => {
            const open = (h.blocking_open ?? []).some((b) => b.ref === r);
            const unknown = (h.blocking_unknown ?? []).some((b) => b.ref === r);
            const state = open
              ? '🟠 open'
              : unknown
              ? '⚪ unknown'
              : '🟢 closed';
            const [repo, num] = r.split('#');
            return (
              <li key={r}>
                {state} ·{' '}
                <a
                  href={`https://github.com/${repo}/issues/${num}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline"
                >
                  <code className="font-mono text-[0.8rem]">{r}</code>
                </a>
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

export function SourcePdfsPanel({ h }: { h: House }) {
  if (h.source_pdfs.length === 0) return null;
  const scenesPerSrc: Record<string, number> = {};
  for (const i of h.images) {
    const f = i.source_ref?.file;
    if (f) scenesPerSrc[f] = (scenesPerSrc[f] ?? 0) + 1;
  }
  return (
    <Section title="Originaldateien (Quellen)">
      <div className="grid grid-cols-1 gap-1">
        {h.source_pdfs.map((s) => {
          const fname = s.split('/').pop()!;
          const n = scenesPerSrc[fname] ?? 0;
          return (
            <div
              key={s}
              className="flex items-center gap-2.5 px-2.5 py-1.5 bg-zinc-50 rounded-md text-xs"
            >
              <span
                className="flex-1 font-mono text-[0.72rem] overflow-hidden whitespace-nowrap text-ellipsis"
                title={fname}
              >
                📄 {fname}
              </span>
              <span
                className={`text-[0.7rem] px-2 py-px rounded-full ${
                  n > 0
                    ? 'text-green-700 font-semibold bg-green-100'
                    : 'text-muted bg-zinc-100'
                }`}
              >
                {n > 0 ? `→ ${n} Szene${n !== 1 ? 'n' : ''}` : 'noch nicht zerlegt'}
              </span>
              <a
                href={s}
                target="_blank"
                rel="noreferrer"
                className="text-accent hover:underline text-[0.7rem]"
              >
                öffnen ↗
              </a>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-5 border-t border-border pt-3.5">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-2.5">
        {title}
      </h4>
      {children}
    </section>
  );
}
