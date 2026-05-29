// R4 — per-scene export preview.
//
// Two side-by-side panes:
//   Set A: raw image + only dimensioned strokes overlaid (Model 1 input)
//   Set B: rectified image (homography from is_reference dims) + every
//          label transformed through H (Model 2 input)
//
// Pre-export validation: a health badge surfaces the homography status
// (ok / insufficient_references / degenerate) so the user fixes it
// before the bulk export runs.

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { fetchExportPreview, type ExportPreview } from '../api/client';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';

export function ExportPreviewPage() {
  const { key = '', file = '' } = useParams();
  const decodedFile = decodeURIComponent(file);
  const [preview, setPreview] = useState<ExportPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchExportPreview(key, decodedFile)
      .then((p) => { if (!cancelled) setPreview(p); })
      .catch((e) => { if (!cancelled) setError((e as Error).message); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [key, decodedFile]);

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Datensatz', to: '/' },
            { label: key, to: `/${key}` },
            { label: `Export-Vorschau: ${decodedFile}` },
          ]}
        />
      }
      leftSidebar={<SideInfo preview={preview} />}
    >
      <div className="px-4 py-4 space-y-4">
        {loading && <p className="text-[0.78rem] text-muted">Lade Vorschau…</p>}
        {error && <p className="text-[0.78rem] text-red-700">{error}</p>}
        {preview && <HealthBanner preview={preview} />}
        {preview && (
          <div className="grid grid-cols-2 gap-3">
            <Pane
              title="Set A — Roh + Bemaßungen (Model 1)"
              imgUrl={preview.raw_url}
              labelCount={preview.set_a.length}
              caption="Nur dimensionierte Strecken sichtbar. Das ist, was Model 1 finden muss."
            />
            <Pane
              title="Set B — Entzerrt + alle Labels (Model 2)"
              imgUrl={preview.rectified_url ?? preview.raw_url}
              labelCount={preview.set_b.length}
              caption={
                preview.rectified_url
                  ? 'Bild entzerrt via Homographie aus Set A. Geometrie aller Labels durch H transformiert.'
                  : 'Entzerrung nicht möglich — Bild bleibt im Rohzustand.'
              }
              warn={!preview.rectified_url}
            />
          </div>
        )}
        {preview && preview.set_a.length === 0 && (
          <p className="text-[0.78rem] text-amber-700">
            ⚠ Keine dimensionierte Bemaßung mit gesetztem Wert in dieser Szene — Set A wäre leer.
          </p>
        )}
      </div>
    </Shell>
  );
}

function Pane({
  title, imgUrl, labelCount, caption, warn,
}: {
  title: string; imgUrl: string; labelCount: number; caption: string; warn?: boolean;
}) {
  return (
    <div className={`rounded-lg overflow-hidden bg-white border ${warn ? 'border-amber-400' : 'border-border'}`}>
      <div className="px-3 py-1.5 text-[0.78rem] font-semibold bg-zinc-50 border-b border-border flex items-center gap-2">
        <span className="flex-1 truncate">{title}</span>
        <span className="text-[0.62rem] text-muted">{labelCount} Labels</span>
      </div>
      <div className="bg-zinc-900 flex items-center justify-center p-2 min-h-[40vh]">
        <img
          src={imgUrl}
          alt={title}
          loading="lazy"
          className="max-w-full max-h-[70vh] object-contain bg-white"
        />
      </div>
      <p className="px-3 py-1.5 text-[0.7rem] text-muted leading-snug border-t border-border">
        {caption}
      </p>
    </div>
  );
}

function HealthBanner({ preview }: { preview: ExportPreview }) {
  const { status, reason, rms_residual_px } = preview;
  if (status === 'ok') {
    const rmsTone = rms_residual_px < 4 ? 'emerald' : rms_residual_px < 8 ? 'amber' : 'red';
    const cls = {
      emerald: 'bg-emerald-50 border-emerald-300 text-emerald-900',
      amber:   'bg-amber-50 border-amber-300 text-amber-900',
      red:     'bg-red-50 border-red-300 text-red-900',
    }[rmsTone];
    return (
      <div className={`rounded-md px-3 py-2 border text-[0.78rem] ${cls}`}>
        ✓ Homographie ok · RMS-Residuum {rms_residual_px.toFixed(2)} px ·
        berechnet aus {preview.computed_from.length} Referenz-Strecken.
      </div>
    );
  }
  return (
    <div className="rounded-md px-3 py-2 border border-red-300 bg-red-50 text-red-900 text-[0.78rem]">
      ✗ {status === 'insufficient_references' ? 'Zu wenige Referenzen' : 'Homographie degenerate'} —{' '}
      {reason ?? 'unbekannt'}
    </div>
  );
}

function SideInfo({ preview }: { preview: ExportPreview | null }) {
  if (!preview) return <div className="px-4 py-4 text-[0.78rem] text-muted">Lade…</div>;
  return (
    <div className="px-3 py-3 space-y-3 text-[0.78rem]">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Export</div>
        <h2 className="text-[0.9rem] font-semibold leading-snug">Vorschau</h2>
      </header>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Homographie
        </h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[0.72rem]">
          <dt className="text-muted">Status</dt>
          <dd className="font-medium">{preview.status}</dd>
          <dt className="text-muted">RMS px</dt>
          <dd className="font-medium tabular-nums">{preview.rms_residual_px.toFixed(2)}</dd>
          <dt className="text-muted">Referenzen</dt>
          <dd className="font-medium font-mono text-[0.65rem] break-all">
            {preview.computed_from.join(', ') || '–'}
          </dd>
        </dl>
      </section>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Set-Größe
        </h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[0.72rem]">
          <dt className="text-muted">Set A</dt>
          <dd className="font-medium">{preview.set_a.length}</dd>
          <dt className="text-muted">Set B</dt>
          <dd className="font-medium">{preview.set_b.length}</dd>
        </dl>
      </section>
      <section>
        <Link to=".." className="block text-[0.72rem] text-accent hover:underline">
          ← Zurück
        </Link>
      </section>
    </div>
  );
}
