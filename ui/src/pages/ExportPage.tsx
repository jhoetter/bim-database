// P0.2 — per-house export overview page.
//
// Lives at /:key/export. Previously this route was 404 — the stepper's
// "Export" tile linked here but no page existed. Now:
//   - Lists every scene with a per-scene Set A / Set B health badge.
//   - Click a row to open the side-by-side export-preview.
//   - "Bulk-Export starten" button POSTs to /exports/<key> and shows
//     the resulting scenes_exported / scenes_skipped / anomalies.

import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { EmptyState } from '../components/EmptyState';
import { fetchDataset, fetchExportPreview, fetchLabels, getIncomingPdf } from '../api/client';
import type { DatasetHouse, IncomingPdf } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';

interface SceneRow {
  file: string;
  labeled: boolean;
  status: 'ok' | 'insufficient_references' | 'degenerate' | 'pending';
  rms: number | null;
  reason: string | null;
}

export function ExportPage() {
  const { key = '' } = useParams();
  const [dataset, setDataset] = useState<DatasetHouse | null>(null);
  const [intake, setIntake] = useState<IncomingPdf | null>(null);
  const [rows, setRows] = useState<SceneRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [bulkResult, setBulkResult] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setError(null);
    (async () => {
      try {
        const [d, b] = await Promise.all([
          fetchDataset(key),
          getIncomingPdf(key).catch(() => null),
        ]);
        if (cancelled) return;
        setDataset(d); setIntake(b);
        const initial: SceneRow[] = (d.drawings ?? []).map((dr) => ({
          file: dr.file,
          labeled: !!dr.labeled,
          status: 'pending',
          rms: null,
          reason: null,
        }));
        setRows(initial);
        // Fetch previews in parallel — each row's homography status only
        // resolves once that scene's preview comes back. Don't block the
        // initial render on this.
        await Promise.all((d.drawings ?? []).map(async (dr) => {
          try {
            // Skip the heavy preview when the scene has no labels yet.
            const labelsRes = await fetchLabels('dataset', key, dr.file).catch(() => null);
            if (!labelsRes || (labelsRes.labels ?? []).length === 0) {
              if (!cancelled) updateRow(dr.file, { status: 'pending', reason: 'keine Labels' });
              return;
            }
            const p = await fetchExportPreview(key, dr.file);
            if (!cancelled) updateRow(dr.file, {
              status: p.status,
              rms: p.rms_residual_px,
              reason: p.reason ?? null,
            });
          } catch (e) {
            if (!cancelled) updateRow(dr.file, {
              status: 'degenerate',
              reason: (e as Error).message,
            });
          }
        }));
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
    function updateRow(file: string, patch: Partial<SceneRow>) {
      setRows((prev) => prev.map((r) => r.file === file ? { ...r, ...patch } : r));
    }
  }, [key]);

  void intake; void dataset;

  const onBulkExport = async () => {
    setExporting(true); setBulkResult(null);
    try {
      const force = window.confirm(
        'Bulk-Export starten?\n\nOK = mit ?force=true (überspringt Sanity-Checks)\nAbbrechen = Standard-Export',
      );
      const r = await fetch(
        `/exports/${encodeURIComponent(key)}${force ? '?force=true' : ''}`,
        { method: 'POST' },
      );
      const txt = await r.text();
      if (!r.ok) throw new Error(`${r.status} ${r.statusText}: ${txt}`);
      setBulkResult(txt);
    } catch (e) {
      setBulkResult((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  const okCount = rows.filter((r) => r.status === 'ok').length;
  const flagCount = rows.filter((r) => r.status === 'degenerate' || r.status === 'insufficient_references').length;
  const pendingCount = rows.filter((r) => r.status === 'pending').length;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Datensatz', to: '/' },
            { label: key, to: `/${key}` },
            { label: 'Export' },
          ]}
        />
      }
      topbarTrailing={
        <button
          type="button"
          onClick={onBulkExport}
          disabled={exporting || rows.length === 0}
          className="text-[0.75rem] px-3 py-1 rounded-md bg-accent text-white font-medium hover:opacity-90 disabled:opacity-40"
          title="Alle Szenen exportieren (Set A + Set B + Diagnostics)"
        >
          {exporting ? 'Exportiere…' : '→ Bulk-Export'}
        </button>
      }
      leftSidebar={
        <div className="px-3 py-3 space-y-3 text-[0.78rem]">
          <header>
            <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Export</div>
            <h1 className="text-[1rem] font-semibold leading-snug">{key}</h1>
            <p className="text-[0.72rem] text-muted">{rows.length} Szenen</p>
          </header>
          <dl className="grid grid-cols-[1fr_auto] gap-x-3 gap-y-0.5 text-[0.72rem]">
            <dt className="text-muted">Bereit</dt><dd className="font-mono">{okCount}</dd>
            <dt className="text-muted">Probleme</dt><dd className="font-mono">{flagCount}</dd>
            <dt className="text-muted">Offen</dt><dd className="font-mono">{pendingCount}</dd>
          </dl>
          {bulkResult && (
            <pre className="text-[0.62rem] bg-zinc-100 p-2 rounded max-h-44 overflow-auto whitespace-pre-wrap break-all">
              {bulkResult}
            </pre>
          )}
        </div>
      }
    >
      <div className="flex flex-col h-full">
        <div className="px-4 py-4 flex-1 overflow-auto">
          {loading && <p className="text-[0.78rem] text-muted">Lade…</p>}
          {error && <p className="text-[0.78rem] text-red-700">{error}</p>}
          {!loading && rows.length === 0 && (
            <EmptyState
              size="page"
              title="Noch keine Szenen extrahiert."
              body="Schneide zuerst Bounding-Boxen aus der PDF, dann kommen sie hier zum Export."
              cta={{ label: '→ Szenen extrahieren', to: `/${key}` }}
            />
          )}
          {rows.length > 0 && (
            <ul className="space-y-1.5 max-w-2xl">
              {rows.map((r) => (
                <li key={r.file}>
                  <Link
                    to={`/${key}/scene/${encodeURIComponent(r.file)}/export`}
                    className="block rounded-md border border-border bg-white px-3 py-2 hover:border-zinc-400 hover:shadow-sm"
                  >
                    <div className="flex items-center gap-2 text-[0.78rem]">
                      <StatusDot status={r.status} />
                      <span className="flex-1 truncate font-mono text-[0.72rem]">{r.file}</span>
                      {r.rms != null && (
                        <span className="text-[0.62rem] tabular-nums text-zinc-500">
                          RMS {r.rms.toFixed(1)} px
                        </span>
                      )}
                      <span className={`text-[0.62rem] px-1.5 py-0.5 rounded-full font-semibold ${
                        r.labeled ? 'bg-emerald-100 text-emerald-900' : 'bg-zinc-100 text-zinc-700'
                      }`}>
                        {r.labeled ? 'annotiert' : 'unannotiert'}
                      </span>
                    </div>
                    {r.reason && (
                      <p className="text-[0.65rem] text-zinc-500 mt-0.5 ml-5 truncate">{r.reason}</p>
                    )}
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </Shell>
  );
}

function StatusDot({ status }: { status: SceneRow['status'] }) {
  const map = {
    ok: 'bg-emerald-500',
    insufficient_references: 'bg-amber-400',
    degenerate: 'bg-red-500',
    pending: 'bg-zinc-300',
  } as const;
  return <span className={`inline-block w-2.5 h-2.5 rounded-full ${map[status]}`} />;
}
