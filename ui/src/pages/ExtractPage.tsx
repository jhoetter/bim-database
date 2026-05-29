// R2 — Scene extraction.
//
// The user draws bounding boxes on each PDF page; each box becomes one
// scene in data/dataset/<key>/ on extract. Draft bboxes auto-save to
// localStorage (R2.8) so a tab crash never loses a session. Already-
// extracted scenes render as semi-transparent rectangles that can be
// clicked → resize → "Erneut extrahieren" (R2.9).
//
// PDF pages render server-side via PyMuPDF and are served as JPGs;
// we keep the client bundle small by NOT shipping PDF.js.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import {
  extractScenes,
  fetchDataset,
  getIncomingPdf,
  getPdfInfo,
  pdfPageUrl,
  deleteExtractedScene,
  type ExtractItem,
  type PdfInfo,
} from '../api/client';
import type { DatasetHouse, IncomingPdf } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { StepperBar } from '../components/StepperBar';
import { computePerHouseSteps, rememberLastStep } from '../lib/step_state';

const KINDS: ExtractItem['kind'][] = ['floorplan', 'elevation', 'section', 'detail'];
const VIEWS = ['north', 'south', 'east', 'west'] as const;
const FLOORS = ['kg', 'ug', 'eg', 'og', 'dg', 'spitzboden'] as const;
const DRAFT_KEY = (key: string) => `bim-db:extract-draft:dataset:${key}`;
const PAGE_DPI = 144;

interface DraftBbox {
  id: string;
  page: number;
  // bbox in PDF UNITS so cache-invalidating page renders don't reflow.
  bbox_pdf: [number, number, number, number];
  kind: ExtractItem['kind'];
  view?: string;
  floor?: string;
  title?: string;
}

interface DraftState {
  schema_version: '1.0';
  current_page: number;
  bboxes: DraftBbox[];
  updated_at: string;
}

function emptyDraft(): DraftState {
  return {
    schema_version: '1.0',
    current_page: 1,
    bboxes: [],
    updated_at: new Date().toISOString(),
  };
}

function loadDraft(key: string): DraftState | null {
  try {
    const raw = window.localStorage.getItem(DRAFT_KEY(key));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (parsed?.schema_version !== '1.0') return null;
    return parsed as DraftState;
  } catch { return null; }
}

function saveDraft(key: string, draft: DraftState) {
  try {
    draft.updated_at = new Date().toISOString();
    window.localStorage.setItem(DRAFT_KEY(key), JSON.stringify(draft));
  } catch { /* no-op */ }
}

function clearDraft(key: string) {
  try { window.localStorage.removeItem(DRAFT_KEY(key)); } catch { /* no-op */ }
}

function uuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export function ExtractPage() {
  const { key = '' } = useParams();
  const [info, setInfo] = useState<PdfInfo | null>(null);
  const [intake, setIntake] = useState<IncomingPdf | null>(null);
  const [dataset, setDataset] = useState<DatasetHouse | null>(null);
  const [draft, setDraft] = useState<DraftState>(() => loadDraft(key) ?? emptyDraft());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const draftRef = useRef(draft);
  draftRef.current = draft;

  // Persist draft on every change with a small debounce.
  useEffect(() => {
    const t = window.setTimeout(() => saveDraft(key, draftRef.current), 300);
    return () => window.clearTimeout(t);
  }, [draft, key]);

  // Fetch metadata.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [i, b, d] = await Promise.all([
          getPdfInfo(key),
          getIncomingPdf(key).catch(() => null),
          fetchDataset(key).catch(() => null),
        ]);
        if (cancelled) return;
        setInfo(i);
        setIntake(b);
        setDataset(d);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    })();
    return () => { cancelled = true; };
  }, [key]);

  const currentPage = Math.min(Math.max(1, draft.current_page), info?.page_count ?? 1);
  const pageInfo = info?.pages.find((p) => p.page === currentPage) ?? null;

  const setPage = useCallback((n: number) => {
    setDraft((d) => ({ ...d, current_page: Math.max(1, Math.min(n, info?.page_count ?? 1)) }));
  }, [info]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === 'ArrowLeft')  setPage(currentPage - 1);
      if (e.key === 'ArrowRight') setPage(currentPage + 1);
      if (e.key === 'Escape')     setSelectedId(null);
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedId) {
          setDraft((d) => ({ ...d, bboxes: d.bboxes.filter((b) => b.id !== selectedId) }));
          setSelectedId(null);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [currentPage, setPage, selectedId]);

  const onCommitBbox = useCallback((bbox: [number, number, number, number]) => {
    const id = uuid();
    setDraft((d) => ({
      ...d,
      bboxes: [...d.bboxes, {
        id, page: currentPage, bbox_pdf: bbox, kind: 'floorplan',
      }],
    }));
    setSelectedId(id);
  }, [currentPage]);

  const onUpdateBbox = useCallback((id: string, patch: Partial<DraftBbox>) => {
    setDraft((d) => ({
      ...d,
      bboxes: d.bboxes.map((b) => b.id === id ? { ...b, ...patch } : b),
    }));
  }, []);

  const onDeleteBbox = useCallback((id: string) => {
    setDraft((d) => ({ ...d, bboxes: d.bboxes.filter((b) => b.id !== id) }));
    if (selectedId === id) setSelectedId(null);
  }, [selectedId]);

  const onExtract = useCallback(async () => {
    if (draft.bboxes.length === 0) return;
    setBusy(true);
    setError(null);
    try {
      const items: ExtractItem[] = draft.bboxes.map((b) => ({
        page: b.page,
        bbox_pdf_units: b.bbox_pdf,
        kind: b.kind,
        view: b.view ?? null,
        floor: b.floor ?? null,
        title: b.title ?? null,
      }));
      await extractScenes(key, items);
      // Reload dataset + intake; wipe draft.
      const [d, b] = await Promise.all([fetchDataset(key), getIncomingPdf(key).catch(() => null)]);
      setDataset(d);
      setIntake(b);
      clearDraft(key);
      setDraft(emptyDraft());
      setSelectedId(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [draft, key]);

  const onDeleteScene = useCallback(async (file: string) => {
    if (!window.confirm(`Szene ${file} aus dem Datensatz entfernen?`)) return;
    try {
      await deleteExtractedScene(key, file);
      const d = await fetchDataset(key);
      setDataset(d);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [key]);

  // R3 — remember this house's last step + compute step flags for the
  // top stepper. Re-runs on every state change cheaply (localStorage is
  // synchronous + the inputs are already memoized).
  useEffect(() => { rememberLastStep(key, 'extract'); }, [key]);
  const stepState = useMemo(
    () => computePerHouseSteps(key, intake, dataset),
    [key, intake, dataset],
  );

  const pageBboxes = draft.bboxes.filter((b) => b.page === currentPage);
  const extractedOnPage = useMemo(
    () => (dataset?.drawings ?? []).filter((d) =>
      d.crop_from && (d.crop_from as { page?: number }).page === currentPage,
    ),
    [dataset, currentPage],
  );

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Datensatz', to: '/dataset' },
            { label: 'Hochladen', to: '/dataset/intake' },
            { label: key, to: `/dataset/${key}` },
            { label: 'Szenen extrahieren' },
          ]}
        />
      }
      leftSidebar={
        <ExtractSidebar
          info={info}
          currentPage={currentPage}
          intake={intake}
          dataset={dataset}
          draft={draft}
          onPage={setPage}
        />
      }
      rightRail={
        <ExtractInspector
          bboxes={pageBboxes}
          selectedId={selectedId}
          onSelect={setSelectedId}
          onUpdate={onUpdateBbox}
          onDelete={onDeleteBbox}
          extractedOnPage={extractedOnPage}
          onDeleteScene={onDeleteScene}
          onExtract={onExtract}
          busy={busy}
          totalDraft={draft.bboxes.length}
        />
      }
      rightRailLabel="Szenen"
    >
      <div className="flex flex-col h-full">
        <StepperBar
          houseKey={key}
          current="extract"
          intakeDone={stepState.intakeDone}
          extractDone={stepState.extractDone}
          annotateDone={stepState.annotateDone}
          exportDone={stepState.exportDone}
        />
        <div className="px-4 py-3 flex flex-col flex-1 min-h-0">
        <PageNav info={info} page={currentPage} onPage={setPage} />
        {error && <p className="text-[0.78rem] text-red-700 my-2">{error}</p>}
        {info && pageInfo && (
          <PageCanvas
            key={`${key}-${currentPage}`}
            pdfKey={key}
            page={currentPage}
            pageWidthPt={pageInfo.width_pt}
            pageHeightPt={pageInfo.height_pt}
            draftBboxes={pageBboxes}
            extracted={extractedOnPage}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onCommit={onCommitBbox}
            onUpdate={onUpdateBbox}
          />
        )}
        </div>
      </div>
    </Shell>
  );
}

function PageNav({
  info, page, onPage,
}: {
  info: PdfInfo | null; page: number; onPage: (n: number) => void;
}) {
  if (!info) return <p className="text-[0.78rem] text-muted">Lade PDF…</p>;
  return (
    <div className="flex items-center gap-2 text-[0.78rem] mb-2">
      <button
        type="button"
        onClick={() => onPage(page - 1)}
        disabled={page <= 1}
        className="px-2 py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 disabled:opacity-40"
      >← prev</button>
      <span className="tabular-nums">Seite {page} / {info.page_count}</span>
      <button
        type="button"
        onClick={() => onPage(page + 1)}
        disabled={page >= info.page_count}
        className="px-2 py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 disabled:opacity-40"
      >next →</button>
      <span className="text-muted ml-3">Click-drag = neue Bbox · Esc = Auswahl aufheben · Del = Bbox entfernen</span>
    </div>
  );
}

function ExtractSidebar({
  info, currentPage, intake, dataset, draft, onPage,
}: {
  info: PdfInfo | null;
  currentPage: number;
  intake: IncomingPdf | null;
  dataset: DatasetHouse | null;
  draft: DraftState;
  onPage: (n: number) => void;
}) {
  return (
    <div className="px-3 py-3 space-y-4">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Extract</div>
        <h1 className="text-[1rem] font-semibold leading-snug mt-0.5">{intake?.key}</h1>
        <p className="text-[0.72rem] text-muted">
          {intake?.consolidated_pdf ?? '–'} · {info?.page_count ?? '?'} Seiten · {dataset?.drawings?.length ?? 0} Szenen extrahiert
        </p>
      </header>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Seiten
        </h3>
        <ul className="space-y-0.5 max-h-[60vh] overflow-auto">
          {info?.pages.map((p) => {
            const ds = (dataset?.drawings ?? []).filter((d) =>
              (d.crop_from as { page?: number } | undefined)?.page === p.page,
            ).length;
            const dr = draft.bboxes.filter((b) => b.page === p.page).length;
            return (
              <li key={p.page}>
                <button
                  type="button"
                  onClick={() => onPage(p.page)}
                  className={`w-full text-left text-[0.72rem] px-2 py-1 rounded flex items-center gap-2 ${
                    p.page === currentPage
                      ? 'bg-accent/10 text-accent font-semibold'
                      : 'hover:bg-zinc-100 text-zinc-800'
                  }`}
                >
                  <span className="font-mono">S{p.page}</span>
                  <span className="flex-1 text-right text-[0.62rem] text-muted">
                    {ds > 0 && <span className="text-emerald-700">✓{ds}</span>}
                    {ds > 0 && dr > 0 && ' · '}
                    {dr > 0 && <span className="text-amber-700">●{dr}</span>}
                    {ds === 0 && dr === 0 && '○'}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>
      <section>
        <Link to="/dataset/intake" className="block text-[0.72rem] text-accent hover:underline">
          ← Eingangsstapel
        </Link>
        <Link to={`/dataset/${intake?.key ?? ''}`} className="block text-[0.72rem] text-accent hover:underline mt-1">
          → Annotieren
        </Link>
      </section>
    </div>
  );
}

function ExtractInspector({
  bboxes, selectedId, onSelect, onUpdate, onDelete,
  extractedOnPage, onDeleteScene, onExtract, busy, totalDraft,
}: {
  bboxes: DraftBbox[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onUpdate: (id: string, patch: Partial<DraftBbox>) => void;
  onDelete: (id: string) => void;
  extractedOnPage: DatasetHouse['drawings'];
  onDeleteScene: (file: string) => void;
  onExtract: () => void;
  busy: boolean;
  totalDraft: number;
}) {
  const sel = bboxes.find((b) => b.id === selectedId) ?? null;
  return (
    <div className="px-3 py-3 space-y-4">
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Bbox-Entwürfe ({bboxes.length})
        </h3>
        {bboxes.length === 0 && (
          <p className="text-[0.72rem] text-muted italic">
            Click-drag auf die Seite, um eine Bbox zu zeichnen.
          </p>
        )}
        <ul className="space-y-0.5">
          {bboxes.map((b) => (
            <li key={b.id}>
              <button
                type="button"
                onClick={() => onSelect(b.id)}
                className={`w-full text-left text-[0.72rem] px-2 py-1 rounded flex items-center gap-2 ${
                  selectedId === b.id
                    ? 'bg-accent/10 text-accent font-semibold'
                    : 'hover:bg-zinc-100 text-zinc-800'
                }`}
              >
                <span className="font-mono">{b.kind.slice(0, 2)}</span>
                <span className="flex-1 truncate">
                  {b.title || `${b.kind}${b.floor ? ` · ${b.floor}` : ''}${b.view ? ` · ${b.view}` : ''}`}
                </span>
                <button
                  type="button"
                  onClick={(e) => { e.stopPropagation(); onDelete(b.id); }}
                  className="text-[0.7rem] text-red-700"
                >✕</button>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {sel && (
        <section className="space-y-2">
          <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">
            Auswahl
          </h3>
          <label className="block text-[0.72rem]">
            Typ
            <select
              value={sel.kind}
              onChange={(e) => onUpdate(sel.id, { kind: e.target.value as ExtractItem['kind'] })}
              className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.78rem]"
            >
              {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </label>
          {(sel.kind === 'elevation' || sel.kind === 'section') && (
            <label className="block text-[0.72rem]">
              Himmelsrichtung
              <select
                value={sel.view ?? ''}
                onChange={(e) => onUpdate(sel.id, { view: e.target.value || undefined })}
                className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.78rem]"
              >
                <option value="">–</option>
                {VIEWS.map((v) => <option key={v} value={v}>{v}</option>)}
              </select>
            </label>
          )}
          {sel.kind === 'floorplan' && (
            <label className="block text-[0.72rem]">
              Geschoss
              <select
                value={sel.floor ?? ''}
                onChange={(e) => onUpdate(sel.id, { floor: e.target.value || undefined })}
                className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.78rem]"
              >
                <option value="">–</option>
                {FLOORS.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
            </label>
          )}
          <label className="block text-[0.72rem]">
            Titel (optional)
            <input
              type="text"
              value={sel.title ?? ''}
              onChange={(e) => onUpdate(sel.id, { title: e.target.value || undefined })}
              className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.78rem]"
            />
          </label>
        </section>
      )}

      {extractedOnPage.length > 0 && (
        <section>
          <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
            Bereits extrahiert ({extractedOnPage.length})
          </h3>
          <ul className="space-y-0.5 text-[0.72rem]">
            {extractedOnPage.map((d) => (
              <li key={d.file} className="flex items-center gap-1.5 px-2 py-0.5">
                <span className="text-emerald-600">✓</span>
                <span className="flex-1 truncate font-mono text-[0.65rem]">{d.file}</span>
                <button
                  type="button"
                  onClick={() => onDeleteScene(d.file)}
                  className="text-[0.7rem] text-red-700"
                  title="Szene aus Datensatz entfernen"
                >✕</button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section>
        <button
          type="button"
          onClick={onExtract}
          disabled={busy || totalDraft === 0}
          className="w-full text-[0.8rem] px-3 py-2 rounded-md bg-emerald-600 text-white font-semibold hover:opacity-90 disabled:opacity-40"
        >
          {busy ? 'Extrahiere…' : `→ ${totalDraft} Szenen extrahieren`}
        </button>
      </section>
    </div>
  );
}

// The actual page canvas. SVG overlay tracks bboxes in PDF-unit coords;
// pointer interactions translate to PDF units via the page's true size.
function PageCanvas({
  pdfKey, page, pageWidthPt, pageHeightPt,
  draftBboxes, extracted, selectedId, onSelect, onCommit, onUpdate,
}: {
  pdfKey: string;
  page: number;
  pageWidthPt: number;
  pageHeightPt: number;
  draftBboxes: DraftBbox[];
  extracted: DatasetHouse['drawings'];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onCommit: (bbox: [number, number, number, number]) => void;
  onUpdate: (id: string, patch: Partial<DraftBbox>) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ start: [number, number]; end: [number, number] } | null>(null);

  const ptToPdf = (xPx: number, yPx: number): [number, number] => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return [0, 0];
    const rx = (xPx - rect.left) / rect.width;
    const ry = (yPx - rect.top)  / rect.height;
    return [
      Math.max(0, Math.min(1, rx)) * pageWidthPt,
      Math.max(0, Math.min(1, ry)) * pageHeightPt,
    ];
  };

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if ((e.target as HTMLElement).closest('[data-bbox-handle]')) return;
    onSelect(null);
    const pt = ptToPdf(e.clientX, e.clientY);
    setDrag({ start: pt, end: pt });
    (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    setDrag({ start: drag.start, end: ptToPdf(e.clientX, e.clientY) });
  };
  const onPointerUp = () => {
    if (!drag) return;
    const [sx, sy] = drag.start;
    const [ex, ey] = drag.end;
    const x0 = Math.min(sx, ex), y0 = Math.min(sy, ey);
    const x1 = Math.max(sx, ex), y1 = Math.max(sy, ey);
    setDrag(null);
    if ((x1 - x0) * (y1 - y0) > 100) {
      onCommit([x0, y0, x1, y1]);
    }
  };

  return (
    <div
      ref={containerRef}
      className="relative bg-zinc-900 flex-1 overflow-auto select-none"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      style={{ touchAction: 'none' }}
    >
      <div
        className="relative mx-auto my-2 bg-white shadow"
        style={{ width: `${pageWidthPt * (PAGE_DPI / 72) / window.devicePixelRatio}px`, aspectRatio: `${pageWidthPt}/${pageHeightPt}` }}
      >
        <img
          src={pdfPageUrl(pdfKey, page, PAGE_DPI)}
          alt={`Seite ${page}`}
          className="block w-full h-full select-none pointer-events-none"
          draggable={false}
        />
        <svg
          className="absolute inset-0 w-full h-full pointer-events-none"
          viewBox={`0 0 ${pageWidthPt} ${pageHeightPt}`}
          preserveAspectRatio="none"
        >
          {/* Already-extracted scenes — gray semi-transparent. */}
          {extracted.map((d) => {
            const cf = d.crop_from as { bbox_pdf_units?: [number, number, number, number] } | undefined;
            if (!cf?.bbox_pdf_units) return null;
            const [x0, y0, x1, y1] = cf.bbox_pdf_units;
            return (
              <g key={d.file} pointerEvents="none">
                <rect
                  x={x0} y={y0} width={x1 - x0} height={y1 - y0}
                  fill="rgba(16, 185, 129, 0.10)"
                  stroke="#059669"
                  strokeWidth={1.5}
                  strokeDasharray="4 3"
                />
              </g>
            );
          })}
          {/* Draft bboxes. */}
          {draftBboxes.map((b) => {
            const [x0, y0, x1, y1] = b.bbox_pdf;
            const sel = b.id === selectedId;
            return (
              <BboxOverlay
                key={b.id}
                bbox={[x0, y0, x1, y1]}
                pageWidthPt={pageWidthPt}
                pageHeightPt={pageHeightPt}
                selected={sel}
                onSelect={() => onSelect(b.id)}
                onUpdate={(nx) => onUpdate(b.id, { bbox_pdf: nx })}
              />
            );
          })}
          {/* In-flight drag preview. */}
          {drag && (() => {
            const [sx, sy] = drag.start;
            const [ex, ey] = drag.end;
            return (
              <rect
                x={Math.min(sx, ex)} y={Math.min(sy, ey)}
                width={Math.abs(ex - sx)} height={Math.abs(ey - sy)}
                fill="rgba(245, 158, 11, 0.15)"
                stroke="#d97706"
                strokeWidth={1.5}
                strokeDasharray="3 2"
              />
            );
          })()}
        </svg>
      </div>
    </div>
  );
}

function BboxOverlay({
  bbox, pageWidthPt, pageHeightPt, selected, onSelect, onUpdate,
}: {
  bbox: [number, number, number, number];
  pageWidthPt: number;
  pageHeightPt: number;
  selected: boolean;
  onSelect: () => void;
  onUpdate: (next: [number, number, number, number]) => void;
}) {
  const [x0, y0, x1, y1] = bbox;
  void pageWidthPt; void pageHeightPt;
  const handles: Array<{ key: string; x: number; y: number; cursor: string }> = [
    { key: 'nw', x: x0, y: y0, cursor: 'nwse-resize' },
    { key: 'ne', x: x1, y: y0, cursor: 'nesw-resize' },
    { key: 'sw', x: x0, y: y1, cursor: 'nesw-resize' },
    { key: 'se', x: x1, y: y1, cursor: 'nwse-resize' },
    { key: 'n',  x: (x0 + x1) / 2, y: y0, cursor: 'ns-resize' },
    { key: 's',  x: (x0 + x1) / 2, y: y1, cursor: 'ns-resize' },
    { key: 'w',  x: x0, y: (y0 + y1) / 2, cursor: 'ew-resize' },
    { key: 'e',  x: x1, y: (y0 + y1) / 2, cursor: 'ew-resize' },
  ];
  const handleSize = Math.max(2, Math.min(x1 - x0, y1 - y0) * 0.04);
  const onHandlePointerDown = (handleKey: string) => (e: React.PointerEvent<SVGRectElement>) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    const start = { x: e.clientX, y: e.clientY };
    const startBox: [number, number, number, number] = [...bbox];
    const target = e.currentTarget;
    const svg = target.ownerSVGElement;
    target.setPointerCapture(e.pointerId);
    const onMove = (ev: PointerEvent) => {
      const rect = svg?.getBoundingClientRect();
      if (!rect) return;
      const dx = (ev.clientX - start.x) / rect.width * pageWidthPt;
      const dy = (ev.clientY - start.y) / rect.height * pageHeightPt;
      let [nx0, ny0, nx1, ny1] = startBox;
      if (handleKey.includes('w')) nx0 = Math.min(nx1 - 1, startBox[0] + dx);
      if (handleKey.includes('e')) nx1 = Math.max(nx0 + 1, startBox[2] + dx);
      if (handleKey.includes('n')) ny0 = Math.min(ny1 - 1, startBox[1] + dy);
      if (handleKey.includes('s')) ny1 = Math.max(ny0 + 1, startBox[3] + dy);
      onUpdate([nx0, ny0, nx1, ny1]);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
  const onBodyPointerDown = (e: React.PointerEvent<SVGRectElement>) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    onSelect();
    const start = { x: e.clientX, y: e.clientY };
    const startBox: [number, number, number, number] = [...bbox];
    const target = e.currentTarget;
    const svg = target.ownerSVGElement;
    target.setPointerCapture(e.pointerId);
    const onMove = (ev: PointerEvent) => {
      const rect = svg?.getBoundingClientRect();
      if (!rect) return;
      const dx = (ev.clientX - start.x) / rect.width * pageWidthPt;
      const dy = (ev.clientY - start.y) / rect.height * pageHeightPt;
      onUpdate([startBox[0] + dx, startBox[1] + dy, startBox[2] + dx, startBox[3] + dy]);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };
  return (
    <g>
      <rect
        x={x0} y={y0} width={x1 - x0} height={y1 - y0}
        fill={selected ? 'rgba(245, 158, 11, 0.18)' : 'rgba(245, 158, 11, 0.08)'}
        stroke={selected ? '#d97706' : '#f59e0b'}
        strokeWidth={selected ? 2 : 1.5}
        style={{ cursor: 'move', pointerEvents: 'auto' }}
        onPointerDown={onBodyPointerDown}
        data-bbox-handle="body"
      />
      {selected && handles.map((h) => (
        <rect
          key={h.key}
          x={h.x - handleSize / 2}
          y={h.y - handleSize / 2}
          width={handleSize}
          height={handleSize}
          fill="white"
          stroke="#d97706"
          strokeWidth={1}
          style={{ cursor: h.cursor, pointerEvents: 'auto' }}
          onPointerDown={onHandlePointerDown(h.key)}
          data-bbox-handle={h.key}
        />
      ))}
    </g>
  );
}
