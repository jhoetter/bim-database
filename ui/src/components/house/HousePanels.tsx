import type { House } from '../../api/types';
import { formatFactValue } from '../../lib/format';
import { Badge } from '../Badge';

// Sidebar section primitive: small uppercase title + spacing + content.
// Lower visual weight than the (deleted) bordered card so sidebar reads as
// a single column rather than a stack of bordered boxes.
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h3 className="text-[0.65rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        {title}
      </h3>
      <div className="min-w-0">{children}</div>
    </section>
  );
}

export function AnomalyPanel({ flags }: { flags: string[] }) {
  if (flags.length === 0) return null;
  return (
    <Section title={`⚠ Anomalien (${flags.length})`}>
      <ul className="space-y-1.5">
        {flags.map((f, i) => (
          <li
            key={i}
            className="px-2.5 py-1.5 bg-amber-50 border-l-2 border-amber-400 rounded-r text-[0.75rem] text-amber-900 leading-snug break-words"
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
    <Section title={`Abgeleitete Fakten (${entries.length})`}>
      <ul className="space-y-1">
        {entries.map(([k, v]) => {
          const tone =
            v.ok === true
              ? 'border-l-green-400 bg-green-50/40'
              : v.ok === false
              ? 'border-l-orange-400 bg-orange-50/40'
              : 'border-l-zinc-300 bg-zinc-50/40';
          const status =
            v.ok === true ? '✓' : v.ok === false ? '✗' : '·';
          const valStr = formatFactValue(v.value, v.unit);
          const sources = v.sources ?? [];
          return (
            <li
              key={k}
              className={`px-2.5 py-1.5 rounded-r border-l-2 text-[0.75rem] ${tone}`}
            >
              <div className="flex items-start gap-1.5 min-w-0">
                <span className="text-muted font-mono shrink-0 mt-px">{status}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2 min-w-0">
                    <span className="font-mono text-[0.7rem] text-muted break-words min-w-0 flex-1">
                      {k}
                    </span>
                    <span className="font-semibold tabular-nums text-zinc-900 text-right break-words">
                      {valStr}
                    </span>
                  </div>
                  {sources.length > 0 && (
                    <div className="mt-0.5 text-[0.65rem] text-muted leading-snug break-words">
                      {sources.map((s) => s.replace(/^house-\d+-/, '')).join(' · ')}
                    </div>
                  )}
                </div>
              </div>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

export function ModelabilityPanel({ h }: { h: House }) {
  const refs = h.bim_ai_blocking_issues ?? [];
  const m = h.modelable_in_bim_ai;
  if (!h.assessed) return null;

  const tone =
    m === true ? 'ok' : m === false ? 'blocked' : 'unknown';
  const headline =
    m === true ? '✓ modellierbar' : m === false ? '✗ blockiert' : '? unbekannt';
  const sub =
    m === true ? 'Keine offenen Blocker.' :
    m === false ? 'Mindestens ein referenziertes Issue ist offen.' :
    'Issue-Cache fehlt — make refresh-issue-state ausführen.';

  return (
    <Section title="bim-ai Modellierbarkeit">
      <div className="flex items-center gap-2 mb-1.5">
        <Badge tone={tone}>{headline}</Badge>
      </div>
      <p className="text-[0.72rem] text-muted leading-snug break-words">{sub}</p>
      {refs.length > 0 && (
        <ul className="mt-2 space-y-0.5">
          {refs.map((r) => {
            const open = (h.blocking_open ?? []).some((b) => b.ref === r);
            const unknown = (h.blocking_unknown ?? []).some((b) => b.ref === r);
            const state = open ? '🟠' : unknown ? '⚪' : '🟢';
            const [repo, num] = r.split('#');
            return (
              <li key={r} className="text-[0.7rem] break-words">
                <span className="mr-1">{state}</span>
                <a
                  href={`https://github.com/${repo}/issues/${num}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-accent hover:underline font-mono"
                >
                  {r}
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
    <Section title={`Originaldateien (${h.source_pdfs.length})`}>
      <ul className="space-y-1">
        {h.source_pdfs.map((s) => {
          const fname = s.split('/').pop()!;
          const n = scenesPerSrc[fname] ?? 0;
          return (
            <li
              key={s}
              className="text-[0.7rem] min-w-0"
            >
              <a
                href={s}
                target="_blank"
                rel="noreferrer"
                className="flex items-start gap-1.5 px-2 py-1.5 bg-zinc-50 rounded hover:bg-zinc-100 transition min-w-0"
                title={fname}
              >
                <span aria-hidden="true">📄</span>
                <span className="flex-1 font-mono text-[0.68rem] break-all min-w-0 leading-snug">
                  {fname}
                </span>
                {n > 0 && (
                  <span className="shrink-0 inline-flex items-center gap-0.5 text-[0.62rem] text-green-700 font-semibold bg-green-100 px-1.5 py-0.5 rounded-full">
                    {n}
                  </span>
                )}
              </a>
            </li>
          );
        })}
      </ul>
    </Section>
  );
}
