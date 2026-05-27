import { Link, useNavigate, useParams } from 'react-router';
import { useState } from 'react';
import { fetchSynthetic, useResource } from '../api/client';
import type { SyntheticComposite, SyntheticDrawing } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';

// Per-house view: left sidebar with manifest metadata + cross-link to the real
// house record; main area shows all drawings as a Pinterest grid; clicking a
// drawing opens the right rail with its scene-detail panel.

export function SyntheticHousePage() {
  const { key = '', file } = useParams();
  const navigate = useNavigate();
  const { data, error, loading } = useResource(() => fetchSynthetic(key), [key]);

  const decodedFile = file ? decodeURIComponent(file) : null;
  const activeDrawing = decodedFile
    ? data?.drawings.find((d) => d.file === decodedFile) ?? null
    : null;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Synthetisch', to: '/synthetic' },
            { label: data?.model ?? key },
            ...(activeDrawing ? [{ label: activeDrawing.title ?? activeDrawing.file }] : []),
          ]}
        />
      }
      leftSidebar={
        <div className="px-4 py-4 space-y-5 min-w-0">
          <header className="min-w-0">
            <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">
              Synthetisch · {key}
            </div>
            <h1 className="text-[1rem] font-semibold leading-snug mt-0.5 break-words">
              {data?.model ?? key}
            </h1>
            {data?.linked_house_meta && (
              <Link
                to={`/house/${data.linked_house_meta.key}`}
                className="inline-block mt-2.5 px-2.5 py-1 rounded-md text-[0.7rem] bg-white text-zinc-900 border border-border hover:border-zinc-400"
              >
                → echtes Haus öffnen
              </Link>
            )}
          </header>

          {loading && <p className="text-[0.78rem] text-muted">Lade…</p>}
          {error && <p className="text-[0.78rem] text-red-700">Fehler: {error.message}</p>}

          {data && (
            <>
              <CoverageSummary drawings={data.drawings} />
              <ManifestMeta drawings={data.drawings} />
            </>
          )}
        </div>
      }
      rightRail={activeDrawing ? <DrawingDetail d={activeDrawing} /> : null}
      rightRailLabel={activeDrawing ? 'Zeichnung' : undefined}
      onCloseRightRail={() => navigate(`/synthetic/${key}`)}
    >
      <div className="px-6 py-5">
        {data?.composite && (
          <CompositeSection composite={data.composite} houseKey={key} />
        )}
        {data && data.drawings.length === 0 && (
          <p className="text-muted text-sm">Noch keine Zeichnungen generiert.</p>
        )}
        {data && data.drawings.length > 0 && <DrawingsGallery drawings={data.drawings} houseKey={key} />}
        {decodedFile && !activeDrawing && data && (
          <div className="mt-4 px-3 py-2 bg-amber-50 border border-amber-200 rounded-md text-[0.8125rem] text-amber-900">
            Zeichnung <code className="font-mono">{decodedFile}</code> nicht in der Manifest-Liste.
          </div>
        )}
      </div>
    </Shell>
  );
}

function CoverageSummary({ drawings }: { drawings: SyntheticDrawing[] }) {
  const byKind: Record<string, number> = {};
  const byLabel: Record<string, number> = {};
  for (const d of drawings) {
    byKind[d.kind] = (byKind[d.kind] ?? 0) + 1;
    const l = d.label_status ?? 'unlabeled';
    byLabel[l] = (byLabel[l] ?? 0) + 1;
  }
  const entries = Object.entries(byKind).sort();
  return (
    <section>
      <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        Übersicht
      </h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.78rem]">
        <dt className="text-muted">Total</dt>
        <dd className="font-medium tabular-nums">{drawings.length}</dd>
        {entries.map(([k, n]) => (
          <div key={k} className="contents">
            <dt className="text-muted capitalize">{k}</dt>
            <dd className="font-medium tabular-nums">{n}</dd>
          </div>
        ))}
        {Object.entries(byLabel).map(([k, n]) => (
          <div key={`l-${k}`} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd className="font-medium tabular-nums">{n}</dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

function ManifestMeta({ drawings }: { drawings: SyntheticDrawing[] }) {
  const newest = drawings
    .map((d) => d.generated_at)
    .filter((x): x is string => x != null)
    .sort()
    .at(-1);
  const models = [...new Set(drawings.map((d) => d.model).filter(Boolean))];
  return (
    <section className="text-[0.72rem] text-muted leading-snug">
      <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        Manifest
      </h3>
      {newest && (
        <p>
          Zuletzt generiert: <span className="font-mono text-[0.7rem]">{newest}</span>
        </p>
      )}
      {models.length > 0 && (
        <p className="mt-1">
          Modell: <span className="font-mono text-[0.7rem]">{models.join(', ')}</span>
        </p>
      )}
    </section>
  );
}

function DrawingsGallery({
  drawings,
  houseKey,
}: {
  drawings: SyntheticDrawing[];
  houseKey: string;
}) {
  // Group by kind so the page reads as "Ansichten / Grundrisse / Schnitte".
  const groups: Record<string, SyntheticDrawing[]> = {};
  for (const d of drawings) (groups[d.kind] ??= []).push(d);
  const labels: Record<string, string> = {
    elevation: 'Ansichten',
    floorplan: 'Grundrisse',
    section: 'Schnitte',
  };
  const order = ['elevation', 'floorplan', 'section'];

  return (
    <div className="space-y-6">
      {order
        .concat(Object.keys(groups).filter((k) => !order.includes(k)))
        .map((kind) => {
          const list = groups[kind];
          if (!list || list.length === 0) return null;
          return (
            <section key={kind}>
              <h2 className="text-[0.85rem] font-semibold text-zinc-900 mb-2.5">
                {labels[kind] ?? kind}{' '}
                <span className="text-muted font-normal">({list.length})</span>
              </h2>
              <div className="columns-[280px] gap-3">
                {list.map((d) => (
                  <DrawingTile key={d.file} houseKey={houseKey} d={d} />
                ))}
              </div>
            </section>
          );
        })}
    </div>
  );
}

// M0: composite "fake whole document" — the per-house sheet that arranges
// all scene PNGs as a single architect's drawing. Bbox overlays per scene
// make each scene click-targetable in the same way the gallery does.
function CompositeSection({
  composite,
  houseKey,
}: {
  composite: SyntheticComposite;
  houseKey: string;
}) {
  const [hoveredScene, setHoveredScene] = useState<string | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);
  const [sheetW, sheetH] = composite.sheet_size_px;

  return (
    <section className="mb-8">
      <header className="flex items-baseline justify-between gap-3 mb-3">
        <h2 className="text-[0.95rem] font-semibold text-zinc-900">
          Gesamtdokument{' '}
          <span className="text-muted font-normal">
            ({composite.scenes.length} Szenen auf einem Blatt)
          </span>
        </h2>
        <label className="text-[0.75rem] text-muted inline-flex items-center gap-1.5 cursor-pointer">
          <input
            type="checkbox"
            checked={showBoxes}
            onChange={(e) => setShowBoxes(e.target.checked)}
            className="accent-accent"
          />
          Szenen-Boxen anzeigen
        </label>
      </header>

      <div className="relative inline-block w-full bg-zinc-100 rounded-lg overflow-hidden border border-border shadow-sm">
        {/* The wrapper carries the sheet's aspect ratio so the SVG overlay
            scales with the image. Bbox percentages computed against sheet
            size so the math is layout-independent. */}
        <div
          className="relative w-full"
          style={{ paddingTop: `${(sheetH / sheetW) * 100}%` }}
        >
          <img
            src={composite.url}
            alt={`${houseKey} composite sheet`}
            className="absolute inset-0 w-full h-full object-contain"
            loading="lazy"
          />
          {showBoxes && (
            <svg
              className="absolute inset-0 w-full h-full"
              viewBox={`0 0 ${sheetW} ${sheetH}`}
              preserveAspectRatio="xMidYMid meet"
            >
              {composite.scenes.map((s) => {
                const [x, y, w, h] = s.bbox_px;
                const isHovered = hoveredScene === s.file;
                return (
                  <Link
                    key={s.file}
                    to={`/synthetic/${houseKey}/scene/${encodeURIComponent(s.file)}`}
                  >
                    <rect
                      x={x}
                      y={y}
                      width={w}
                      height={h}
                      fill={isHovered ? 'rgba(245, 158, 11, 0.15)' : 'rgba(0,0,0,0)'}
                      stroke={isHovered ? '#d97706' : 'rgba(0,0,0,0.25)'}
                      strokeWidth={isHovered ? 6 : 2}
                      strokeDasharray={isHovered ? '0' : '8 8'}
                      onMouseEnter={() => setHoveredScene(s.file)}
                      onMouseLeave={() => setHoveredScene(null)}
                      style={{ cursor: 'pointer' }}
                    />
                  </Link>
                );
              })}
            </svg>
          )}
        </div>
      </div>

      <p className="text-[0.7rem] text-muted mt-1.5 leading-snug">
        Erzeugt aus den Einzelszenen via{' '}
        <code className="font-mono">scripts/compose_house_sheet.py</code>.
        Seed: <code className="font-mono">{composite.seed ?? '–'}</code>
        {composite.generated_at && (
          <>
            {' '}
            · generiert{' '}
            <span className="font-mono">{composite.generated_at}</span>
          </>
        )}
        . Die Boxen sind die Ground-Truth für die spätere Szenen-Detektion (S-1).
      </p>
    </section>
  );
}

function DrawingTile({ houseKey, d }: { houseKey: string; d: SyntheticDrawing }) {
  const label =
    d.kind === 'floorplan' && d.floor
      ? d.floor
      : d.kind === 'elevation' && d.view
      ? d.view.charAt(0).toUpperCase() + d.view.slice(1)
      : d.kind;
  const kindTone =
    d.kind === 'floorplan'
      ? 'bg-blue-600/85'
      : d.kind === 'elevation'
      ? 'bg-amber-600/90'
      : 'bg-zinc-700/85';
  return (
    <Link
      to={`/synthetic/${houseKey}/scene/${encodeURIComponent(d.file)}`}
      className="relative block mb-3 break-inside-avoid rounded-lg overflow-hidden border border-border bg-white hover:shadow-md hover:border-zinc-300 transition"
    >
      <img
        src={d.url}
        alt={d.title ?? d.file}
        loading="lazy"
        className="w-full h-auto block bg-white"
      />
      <span
        className={`absolute top-1.5 left-1.5 ${kindTone} text-white text-[0.65rem] font-semibold px-1.5 py-0.5 rounded shadow`}
      >
        {label}
      </span>
      {d.label_status && d.label_status !== 'unlabeled' && (
        <span
          className={`absolute bottom-1.5 right-1.5 text-white text-[0.625rem] font-semibold px-1.5 py-0.5 rounded ${
            d.label_status === 'labeled'
              ? 'bg-green-700'
              : d.label_status === 'rejected'
              ? 'bg-red-700'
              : 'bg-zinc-600'
          }`}
        >
          {d.label_status}
        </span>
      )}
      {d.title && (
        <span className="absolute bottom-1.5 left-1.5 right-12 bg-black/65 text-white text-[0.7rem] px-2 py-0.5 rounded line-clamp-1">
          {d.title}
        </span>
      )}
    </Link>
  );
}

function DrawingDetail({ d }: { d: SyntheticDrawing }) {
  return (
    <div className="flex flex-col">
      <div className="bg-zinc-800 flex items-center justify-center p-3">
        <img
          src={d.url}
          alt={d.title ?? d.file}
          className="max-w-full max-h-[55vh] object-contain bg-white"
        />
      </div>
      <div className="p-4 space-y-4">
        <div>
          <div className="text-[0.85rem] font-semibold break-all">{d.file}</div>
          {d.title && <div className="text-[0.75rem] text-muted mt-0.5">{d.title}</div>}
        </div>

        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[0.78rem]">
          <dt className="text-muted">Typ</dt>
          <dd className="font-medium capitalize">{d.kind}</dd>
          {d.view && (
            <div className="contents">
              <dt className="text-muted">View</dt>
              <dd className="font-medium capitalize">{d.view}</dd>
            </div>
          )}
          {d.floor && (
            <div className="contents">
              <dt className="text-muted">Floor</dt>
              <dd className="font-medium">{d.floor}</dd>
            </div>
          )}
          {d.model && (
            <div className="contents">
              <dt className="text-muted">Modell</dt>
              <dd className="font-mono text-[0.72rem]">{d.model}</dd>
            </div>
          )}
          {d.generated_at && (
            <div className="contents">
              <dt className="text-muted">Generiert</dt>
              <dd className="font-mono text-[0.72rem]">{d.generated_at}</dd>
            </div>
          )}
          <dt className="text-muted">Label</dt>
          <dd className="font-medium">{d.label_status ?? 'unlabeled'}</dd>
        </dl>

        {d.style_refs && d.style_refs.length > 0 && (
          <section>
            <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
              Style-Referenzen
            </h4>
            <ul className="text-[0.7rem] text-muted leading-snug space-y-0.5 font-mono break-all">
              {d.style_refs.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </section>
        )}

        {d.content_refs && d.content_refs.length > 0 && (
          <section>
            <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
              Content-Referenzen
            </h4>
            <ul className="text-[0.7rem] text-muted leading-snug space-y-0.5 font-mono break-all">
              {d.content_refs.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </section>
        )}

        <a
          href={d.url}
          target="_blank"
          rel="noreferrer"
          className="inline-block text-[0.75rem] text-accent hover:underline"
        >
          Original PNG öffnen ↗
        </a>
      </div>
    </div>
  );
}
