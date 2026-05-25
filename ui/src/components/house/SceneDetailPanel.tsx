import type { FactEntry, SceneImage } from '../../api/types';
import { ontoLabel, useOntology } from '../../api/ontology';
import { FactValueRenderer } from './FactValueRenderer';

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v != null && typeof v === 'object' && !Array.isArray(v);
}

// The slide-in right-rail panel that shows full detail for one scene.
// Image at top, then orientation chip + caption + provenance + facts + anomalies.
export function SceneDetailPanel({ img }: { img: SceneImage }) {
  const factEntries = Object.entries(img.facts ?? {});
  const anomalies = img.anomaly_flags ?? [];

  return (
    <div className="flex flex-col">
      <div className="bg-zinc-800 flex items-center justify-center p-3">
        <img
          src={img.url}
          alt={img.caption ?? img.file}
          className="max-w-full max-h-[40vh] object-contain bg-white"
        />
      </div>
      <div className="p-4">
        <div className="text-[0.85rem] font-semibold mb-1 break-all">{img.file}</div>
        <SceneOrientation img={img} />
        {img.caption && (
          <p className="text-[0.8125rem] text-muted mb-4 leading-relaxed">{img.caption}</p>
        )}
        {img.source_ref && <SourceSection src={img.source_ref} />}
        <FactsSection entries={factEntries} />
        {anomalies.length > 0 && <SceneAnomalies flags={anomalies} />}
      </div>
    </div>
  );
}

function SceneOrientation({ img }: { img: SceneImage }) {
  const onto = useOntology();
  const isCompassView = (v?: string | null) =>
    v != null && ['north', 'south', 'east', 'west'].includes(v);

  let label: string | null = null;
  let kind: string | null = null;
  let tone: 'blue' | 'amber' | 'zinc' = 'zinc';

  if (img.category === 'floorplan' && img.floor) {
    label = ontoLabel(onto, 'levels', img.floor) || img.floor;
    kind = 'Geschoss';
    tone = 'blue';
  } else if (img.category === 'elevation' && img.view) {
    label = ontoLabel(onto, 'image_views', img.view) || img.view;
    kind = isCompassView(img.view) ? 'Himmelsrichtung' : 'Ansicht';
    tone = isCompassView(img.view) ? 'amber' : 'zinc';
  }

  if (!label) return null;
  const toneClass =
    tone === 'blue'
      ? 'bg-blue-100 text-blue-900 border-blue-200'
      : tone === 'amber'
      ? 'bg-amber-100 text-amber-900 border-amber-200'
      : 'bg-zinc-100 text-zinc-800 border-zinc-200';
  return (
    <div className="mb-3 flex items-center gap-2 flex-wrap">
      <span className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">
        {kind}
      </span>
      <span
        className={`text-[0.85rem] font-semibold px-2 py-0.5 rounded border ${toneClass}`}
      >
        {label}
      </span>
    </div>
  );
}

function SourceSection({ src }: { src: NonNullable<SceneImage['source_ref']> }) {
  const rows: [string, string | null][] = [
    ['Datei', src.file],
    ['Seite', src.page != null ? String(src.page) : null],
    [
      'Crop',
      src.crop_box_pct ? `[${src.crop_box_pct.map((n) => n.toFixed(2)).join(', ')}]` : null,
    ],
    ['Titel', src.page_title],
    ['Maßstab', src.scale],
  ];
  const filled = rows.filter(([, v]) => v) as [string, string][];
  if (filled.length === 0) return null;
  return (
    <section className="mb-5">
      <h5 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-2">
        Herkunft
      </h5>
      <div className="text-[0.8rem] leading-relaxed bg-zinc-50 rounded-md px-3 py-2.5">
        {filled.map(([k, v]) => (
          <div key={k} className="flex gap-2 mb-0.5 last:mb-0">
            <span className="text-muted min-w-[80px]">{k}</span>
            <span className="font-mono text-[0.74rem] break-all">{v}</span>
          </div>
        ))}
      </div>
    </section>
  );
}

function FactsSection({ entries }: { entries: [string, FactEntry][] }) {
  return (
    <section className="mb-5">
      <h5 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-2">
        Fakten{' '}
        {entries.length > 0 && (
          <span className="text-muted font-normal normal-case tracking-normal ml-1">
            ({entries.length})
          </span>
        )}
      </h5>
      {entries.length === 0 ? (
        <p className="text-[0.8125rem] text-muted italic">Noch keine Fakten extrahiert.</p>
      ) : (
        <dl className="m-0">
          {entries.map(([k, f]) => (
            <div key={k} className="mt-3 first:mt-0 min-w-0">
              <dt className="font-mono text-[0.7rem] text-muted break-all">
                {k}
                {f.unit && <span className="text-zinc-400"> [{f.unit}]</span>}
              </dt>
              <dd className="mt-0.5 pl-3 text-[0.825rem] border-l-2 border-zinc-200 leading-snug min-w-0 break-words">
                <FactValueRenderer
                  value={f.value}
                  unit={f.unit}
                  defaultOpen={!isPlainObject(f.value)}
                />
                {f.evidence && (
                  <span className="block mt-0.5 font-normal italic text-muted text-[0.7rem]">
                    {f.evidence}
                  </span>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </section>
  );
}

function SceneAnomalies({ flags }: { flags: string[] }) {
  return (
    <section className="mb-5">
      <h5 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-2">
        ⚠ Szene-Anomalien
      </h5>
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
    </section>
  );
}
