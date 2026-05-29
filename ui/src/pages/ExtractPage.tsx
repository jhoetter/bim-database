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
  resetHouse,
  type ExtractItem,
  type PdfInfo,
} from '../api/client';
import type { DatasetHouse, IncomingPdf } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { SceneThumb } from '../components/SceneThumb';
import { rememberLastStep } from '../lib/step_state';

const KINDS: ExtractItem['kind'][] = ['floorplan', 'elevation', 'section', 'detail'];
const KIND_LABEL: Record<ExtractItem['kind'], string> = {
  floorplan: 'Grundriss',
  elevation: 'Ansicht',
  section:   'Schnitt',
  detail:    'Detail',
};
const VIEWS = ['north', 'south', 'east', 'west'] as const;
const VIEW_LABEL: Record<typeof VIEWS[number], string> = {
  north: 'Nord', south: 'Süd', east: 'Ost', west: 'West',
};
const FLOORS = ['kg', 'ug', 'eg', 'og', 'dg', 'spitzboden'] as const;
const FLOOR_LABEL: Record<typeof FLOORS[number], string> = {
  kg: 'KG', ug: 'UG', eg: 'EG', og: 'OG', dg: 'DG', spitzboden: 'Spitzboden',
};
const DRAFT_KEY = (key: string) => `bim-db:extract-draft:dataset:${key}`;
const PAGE_DPI = 144;

interface DraftBbox {
  id: string;
  page: number;
  // bbox in PDF UNITS so cache-invalidating page renders don't reflow.
  bbox_pdf: [number, number, number, number];
  // null until the user picks. Pre-labeling everything as 'floorplan'
  // was wrong more often than right; rather have "Typ wählen" than a
  // false default.
  kind: ExtractItem['kind'] | null;
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
  // Post-draw classifier chip: appears next to the freshly-committed bbox
  // and walks the user through kind → sub-classification with keyboard
  // hints. step='kind' → pick Grundriss/Ansicht/Schnitt/Detail; then
  // step='floor' for Grundriss or step='view' for Ansicht/Schnitt.
  // Esc dismisses; any pointer-down outside also dismisses.
  const [postDraw, setPostDraw] = useState<{
    id: string;
    step: 'kind' | 'floor' | 'view';
  } | null>(null);
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
      // Post-draw chip first — it owns the keyboard until dismissed.
      if (postDraw) {
        if (e.key === 'Escape') { setPostDraw(null); return; }
        const k = e.key.toLowerCase();
        if (postDraw.step === 'kind') {
          const map: Record<string, ExtractItem['kind']> = { g: 'floorplan', a: 'elevation', s: 'section', d: 'detail' };
          if (k in map) {
            const kind = map[k];
            setDraft((d) => ({
              ...d,
              bboxes: d.bboxes.map((b) => b.id === postDraw.id ? { ...b, kind } : b),
            }));
            setPostDraw(
              kind === 'floorplan' ? { id: postDraw.id, step: 'floor' }
              : (kind === 'elevation' || kind === 'section') ? { id: postDraw.id, step: 'view' }
              : null,
            );
            e.preventDefault();
            return;
          }
        }
        if (postDraw.step === 'floor') {
          const map: Record<string, typeof FLOORS[number]> = { k: 'kg', u: 'ug', e: 'eg', o: 'og', d: 'dg', s: 'spitzboden' };
          if (k in map) {
            const floor = map[k];
            setDraft((d) => ({
              ...d,
              bboxes: d.bboxes.map((b) => b.id === postDraw.id ? { ...b, floor } : b),
            }));
            setPostDraw(null);
            e.preventDefault();
            return;
          }
        }
        if (postDraw.step === 'view') {
          const map: Record<string, typeof VIEWS[number]> = { n: 'north', s: 'south', o: 'east', w: 'west' };
          if (k in map) {
            const view = map[k];
            setDraft((d) => ({
              ...d,
              bboxes: d.bboxes.map((b) => b.id === postDraw.id ? { ...b, view } : b),
            }));
            setPostDraw(null);
            e.preventDefault();
            return;
          }
        }
      }
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
  }, [currentPage, setPage, selectedId, postDraw]);

  const onCommitBbox = useCallback((bbox: [number, number, number, number]) => {
    const id = uuid();
    setDraft((d) => ({
      ...d,
      bboxes: [...d.bboxes, {
        id, page: currentPage, bbox_pdf: bbox, kind: null,
      }],
    }));
    setSelectedId(id);
    setPostDraw({ id, step: 'kind' });
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
        // Typeguard upstream: every kind is non-null before this point
        // (the extract button is disabled otherwise).
        kind: b.kind as ExtractItem['kind'],
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

  // Convert an already-extracted scene back into an editable draft bbox.
  // The user gets the same bbox geometry + classification back as a draft
  // so they can reshape it and re-extract. The dataset entry is removed
  // in the same step so we don't end up with a duplicate file.
  const onAdjustExtracted = useCallback(async (file: string) => {
    const target = (dataset?.drawings ?? []).find((d) => d.file === file);
    const cf = target?.crop_from;
    if (!target || !cf?.bbox_pdf_units) return;
    if (!window.confirm(
      `Szene ${file} wieder zum Entwurf machen?\n\n` +
      `Die Bbox wandert zurück in die Entwürfe, du kannst sie anpassen ` +
      `und erneut extrahieren. Bisherige Annotationen für diese Szene ` +
      `gehen verloren.`,
    )) return;
    try {
      await deleteExtractedScene(key, file);
      const fresh = await fetchDataset(key);
      setDataset(fresh);
      // Restore as a draft bbox so the user can drag handles to refine.
      const draftKind: ExtractItem['kind'] | null =
        target.kind === 'floorplan' || target.kind === 'elevation' ||
        target.kind === 'section'   || target.kind === 'detail'
          ? target.kind
          : null;
      const id = `b-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
      const newDraft: DraftBbox = {
        id,
        page: cf.page,
        bbox_pdf: cf.bbox_pdf_units,
        kind: draftKind,
        view: target.view ?? undefined,
        floor: target.floor ?? undefined,
        title: target.title ?? undefined,
      };
      setDraft((d) => ({ ...d, bboxes: [...d.bboxes, newDraft], updated_at: new Date().toISOString() }));
      setSelectedId(id);
      setPage(cf.page);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [key, dataset]);

  // The house IS the extract view now; mark it as the user's
  // "last step" so /dataset list cards resume here.
  useEffect(() => { rememberLastStep(key, 'extract'); }, [key]);

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
        <Breadcrumb items={[{ label: 'Datensatz', to: '/' }, { label: key }]} />
      }
      topbarTrailing={
        <div className="flex items-center gap-1.5">
          <Link
            to={`/${key}/export`}
            className="text-[0.75rem] px-2.5 py-1 rounded-md border border-zinc-300 text-zinc-700 hover:bg-zinc-100"
            title="Export-Übersicht (alle Szenen) + Bulk-Export"
          >
            Export ▸
          </Link>
          <Link
            to={`/${key}/3d`}
            className="text-[0.75rem] px-2.5 py-1 rounded-md border border-zinc-300 text-zinc-700 hover:bg-zinc-100"
            title="3D-Vorschau der annotierten Geometrie"
          >
            3D ▸
          </Link>
          <HouseMenu
            houseKey={key}
            sceneCount={dataset?.drawings?.length ?? 0}
            draftCount={draft.bboxes.length}
            onReset={async () => {
              const total = (dataset?.drawings?.length ?? 0);
              const labeled = (dataset?.drawings ?? []).filter((d) => d.labeled).length;
              const ok = window.confirm(
                `${key} wirklich zurücksetzen?\n\n` +
                `Wird gelöscht:\n` +
                `  • ${total} extrahierte Szene${total === 1 ? '' : 'n'}\n` +
                `  • ${labeled} annotierte Datei${labeled === 1 ? '' : 'en'}\n` +
                `  • Alle Bbox-Entwürfe dieser Sitzung\n\n` +
                `Bleibt erhalten:\n` +
                `  • Die hochgeladene PDF\n\n` +
                `Diese Aktion kann NICHT rückgängig gemacht werden.`,
              );
              if (!ok) return;
              try {
                await resetHouse(key);
                clearDraft(key);
                // Also wipe house_facts in localStorage so the next
                // annotation session starts from a clean slate.
                try {
                  for (let i = window.localStorage.length - 1; i >= 0; i--) {
                    const k = window.localStorage.key(i);
                    if (k && (k.includes(`:house-facts:dataset:${key}`)
                              || k.includes(`:last-step:dataset:${key}`)
                              || k.includes(`:extract-draft:dataset:${key}`))) {
                      window.localStorage.removeItem(k);
                    }
                  }
                } catch { /* localStorage unavailable */ }
                setDraft(emptyDraft());
                setSelectedId(null);
                // Reload dataset + intake.
                const [d, b] = await Promise.all([
                  fetchDataset(key).catch(() => null),
                  getIncomingPdf(key).catch(() => null),
                ]);
                setDataset(d);
                setIntake(b);
              } catch (e) {
                window.alert(`Reset fehlgeschlagen: ${(e as Error).message}`);
              }
            }}
          />
        </div>
      }
      leftSidebar={
        <ExtractSidebar
          info={info}
          currentPage={currentPage}
          intake={intake}
          dataset={dataset}
          draft={draft}
          onPage={setPage}
          pageBboxes={pageBboxes}
          selectedId={selectedId}
          onSelectBbox={setSelectedId}
          onUpdateBbox={onUpdateBbox}
          onDeleteBbox={onDeleteBbox}
          onExtract={onExtract}
          onDiscardDraft={() => {
            if (!window.confirm(`Alle ${draft.bboxes.length} Bbox-Entwürfe verwerfen?`)) return;
            clearDraft(key);
            setDraft(emptyDraft());
            setSelectedId(null);
          }}
          busy={busy}
        />
      }
    >
      <div className="flex flex-col h-full">
        <SceneStrip
          houseKey={key}
          scenes={dataset?.drawings ?? []}
          drafts={draft.bboxes}
          selectedDraftId={selectedId}
          currentPage={currentPage}
          onJumpToPage={setPage}
          onSelectDraft={(id) => {
            const d = draft.bboxes.find((b) => b.id === id);
            if (d && d.page !== currentPage) setPage(d.page);
            setSelectedId(id);
          }}
          onDeleteScene={onDeleteScene}
          onDeleteDraft={onDeleteBbox}
        />
        <div className="px-4 py-3 flex flex-col flex-1 min-h-0">
        <PageNav
          info={info}
          page={currentPage}
          onPage={setPage}
          draftCount={pageBboxes.length}
          extractedOnPage={extractedOnPage.length}
          totalDraft={draft.bboxes.length}
          lastSaved={new Date(draft.updated_at).toLocaleTimeString('de-DE')}
          pageInfo={pageInfo}
          onFullPage={() => {
            if (!pageInfo) return;
            onCommitBbox([0, 0, pageInfo.width_pt, pageInfo.height_pt]);
          }}
        />
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
            postDraw={postDraw}
            onPostDrawPick={(patch) => {
              if (!postDraw) return;
              setDraft((d) => ({
                ...d,
                bboxes: d.bboxes.map((b) => b.id === postDraw.id ? { ...b, ...patch } : b),
              }));
              if (patch.kind === 'floorplan') setPostDraw({ id: postDraw.id, step: 'floor' });
              else if (patch.kind === 'elevation' || patch.kind === 'section') setPostDraw({ id: postDraw.id, step: 'view' });
              else setPostDraw(null);
            }}
            onPostDrawDismiss={() => setPostDraw(null)}
            houseKey={key}
            onDeleteExtracted={onDeleteScene}
            onAdjustExtracted={onAdjustExtracted}
          />
        )}
        </div>
      </div>
    </Shell>
  );
}

// Horizontal strip of every scene in this house. Two kinds of chip:
//   - Extracted scene → thumbnail + KIND label, click → annotate.
//   - Draft bbox (not extracted yet) → amber chip without thumbnail,
//     click → jump to source page + select bbox so the user can refine
//     the geometry or classify it before extraction.
// The "active" outline only fires for the selected draft so the user
// can't misread "this scene's source is on the page you're viewing" as
// "this chip is selected".
function SceneStrip({
  houseKey, scenes, drafts, selectedDraftId, currentPage,
  onJumpToPage, onSelectDraft, onDeleteScene, onDeleteDraft,
}: {
  houseKey: string;
  scenes: DatasetHouse['drawings'];
  drafts: DraftBbox[];
  selectedDraftId: string | null;
  currentPage: number;
  onJumpToPage: (n: number) => void;
  onSelectDraft: (id: string) => void;
  onDeleteScene: (file: string) => void;
  onDeleteDraft: (id: string) => void;
}) {
  const total = scenes.length + drafts.length;
  if (total === 0) {
    return (
      <div className="px-3 py-1.5 border-b border-border bg-zinc-50 text-[0.72rem] text-zinc-500">
        Noch keine Szenen extrahiert — zieh eine Bbox auf die Seite oder klick „Ganze Seite als Szene".
      </div>
    );
  }
  return (
    <div className="border-b border-border bg-zinc-50">
      <div className="px-3 py-1.5 flex items-center gap-1.5 overflow-x-auto">
        <span className="text-[0.62rem] uppercase tracking-wider text-muted shrink-0">
          {scenes.length} extrahiert{drafts.length > 0 ? ` · ${drafts.length} Entwurf` : ''}
        </span>
        {scenes.map((d) => {
          const cf = d.crop_from as { page?: number } | undefined;
          const pageN = cf?.page ?? null;
          const isOnPage = pageN === currentPage;
          const label =
            d.kind === 'floorplan' && d.floor ? `${KIND_LABEL.floorplan} ${(FLOOR_LABEL as Record<string, string>)[d.floor] ?? d.floor}` :
            d.kind === 'elevation' && d.view ? `${KIND_LABEL.elevation} ${(VIEW_LABEL as Record<string, string>)[d.view] ?? d.view}` :
            (KIND_LABEL as Record<string, string>)[d.kind] ?? d.kind;
          return (
            <SceneThumb
              key={d.file}
              to={`/${houseKey}/scene/${encodeURIComponent(d.file)}/annotate`}
              url={d.url}
              shortLabel={label}
              title={`${d.file} — Klick öffnet Annotation${pageN != null ? ` · von Seite ${pageN}` : ''}`}
              labeled={d.labeled}
              size="md"
              trailing={
                <span className="flex items-center gap-0.5 ml-0.5">
                  {pageN != null && (
                    <button
                      type="button"
                      onClick={(e) => { e.preventDefault(); e.stopPropagation(); onJumpToPage(pageN); }}
                      className={`text-[0.6rem] px-1 py-0.5 rounded hover:bg-zinc-100 ${
                        isOnPage ? 'text-accent font-semibold' : 'text-zinc-500 hover:text-accent'
                      }`}
                      title={isOnPage ? `Quelle: Seite ${pageN} (aktuell)` : `Bbox auf Seite ${pageN} zeigen`}
                    >
                      S{pageN}
                    </button>
                  )}
                  <button
                    type="button"
                    onClick={(e) => { e.preventDefault(); e.stopPropagation(); onDeleteScene(d.file); }}
                    className="text-[0.6rem] text-zinc-400 hover:text-red-700 px-1 py-0.5 rounded hover:bg-red-50"
                    title="Szene aus dem Datensatz entfernen"
                  >
                    ✕
                  </button>
                </span>
              }
            />
          );
        })}
        {drafts.map((b) => {
          const kindLabel = b.kind == null
            ? 'Entwurf · ?'
            : `Entwurf · ${KIND_LABEL[b.kind]}${b.floor ? ` ${(FLOOR_LABEL as Record<string, string>)[b.floor] ?? b.floor}` : ''}${b.view ? ` ${(VIEW_LABEL as Record<string, string>)[b.view] ?? b.view}` : ''}`;
          const sel = b.id === selectedDraftId;
          const isOnPage = b.page === currentPage;
          return (
            <span key={b.id} className="relative inline-flex shrink-0">
              <button
                type="button"
                onClick={() => onSelectDraft(b.id)}
                title={`Entwurf auf Seite ${b.page}${b.kind == null ? ' — Typ noch nicht gewählt' : ''}`}
                className={`inline-flex items-center gap-1.5 pl-1 pr-2 py-1 rounded-md border-2 border-dashed transition ${
                  sel
                    ? 'bg-amber-100 border-amber-500 text-amber-900'
                    : 'bg-amber-50 border-amber-300 text-amber-900 hover:border-amber-500'
                }`}
              >
                <span className="w-10 h-10 shrink-0 rounded bg-amber-100 border border-amber-300 inline-flex items-center justify-center text-[0.7rem] font-bold text-amber-700">
                  {b.kind ? KIND_LABEL[b.kind].slice(0, 2) : '?'}
                </span>
                <span className="text-[0.72rem] font-medium whitespace-nowrap">{kindLabel}</span>
                <span className="flex items-center gap-0.5 ml-0.5">
                  <span
                    className={`text-[0.6rem] px-1 py-0.5 rounded ${
                      isOnPage ? 'text-accent font-semibold' : 'text-zinc-500'
                    }`}
                    title={isOnPage ? `Seite ${b.page} (aktuell)` : `Seite ${b.page}`}
                  >
                    S{b.page}
                  </span>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onDeleteDraft(b.id); }}
                    className="text-[0.6rem] text-zinc-400 hover:text-red-700 px-1 py-0.5 rounded hover:bg-red-50"
                    title="Entwurf verwerfen"
                  >
                    ✕
                  </button>
                </span>
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
}

function PageNav({
  info, page, onPage, draftCount, extractedOnPage, totalDraft, lastSaved,
  pageInfo, onFullPage,
}: {
  info: PdfInfo | null;
  page: number;
  onPage: (n: number) => void;
  draftCount: number;
  extractedOnPage: number;
  totalDraft: number;
  lastSaved: string;
  pageInfo: PdfInfo['pages'][number] | null;
  onFullPage: () => void;
}) {
  if (!info) return <p className="text-[0.78rem] text-muted">Lade PDF…</p>;
  return (
    <div className="flex items-center gap-2 text-[0.78rem] mb-2 flex-wrap">
      <button
        type="button"
        onClick={() => onPage(page - 1)}
        disabled={page <= 1}
        className="px-2 py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 disabled:opacity-40"
        title="Vorherige Seite (←)"
      >←</button>
      <input
        type="number"
        min={1}
        max={info.page_count}
        value={page}
        onChange={(e) => {
          const n = parseInt(e.target.value, 10);
          if (!Number.isNaN(n)) onPage(n);
        }}
        className="w-12 px-1 py-0.5 text-center font-mono border border-zinc-300 rounded"
      />
      <span className="tabular-nums text-zinc-500">/ {info.page_count}</span>
      <button
        type="button"
        onClick={() => onPage(page + 1)}
        disabled={page >= info.page_count}
        className="px-2 py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 disabled:opacity-40"
        title="Nächste Seite (→)"
      >→</button>
      <button
        type="button"
        onClick={onFullPage}
        disabled={!pageInfo}
        className="px-2 py-0.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-40"
        title="Ganze Seite als eine Szene (Doppelklick auf Seite tut dasselbe)"
      >
        🗋 Ganze Seite als Szene
      </button>
      <span className="ml-3 inline-flex items-center gap-1">
        {extractedOnPage > 0 && (
          <span className="text-[0.7rem] px-1.5 py-0.5 rounded-full bg-emerald-100 text-emerald-900 font-semibold">
            ✓ {extractedOnPage} extrahiert
          </span>
        )}
        {draftCount > 0 && (
          <span className="text-[0.7rem] px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-900 font-semibold">
            ● {draftCount} Entwurf
          </span>
        )}
      </span>
      <span className="text-muted ml-auto text-[0.7rem]">
        {totalDraft > 0 ? (
          <>Auto-gespeichert · {lastSaved}</>
        ) : (
          <>Drag = Bbox · Doppelklick = ganze Seite · ← → Seiten · Esc deselect · Del löschen</>
        )}
      </span>
    </div>
  );
}

function ExtractSidebar({
  info, currentPage, intake, dataset, draft, onPage,
  pageBboxes, selectedId, onSelectBbox, onUpdateBbox, onDeleteBbox,
  onExtract, onDiscardDraft, busy,
}: {
  info: PdfInfo | null;
  currentPage: number;
  intake: IncomingPdf | null;
  dataset: DatasetHouse | null;
  draft: DraftState;
  onPage: (n: number) => void;
  pageBboxes: DraftBbox[];
  selectedId: string | null;
  onSelectBbox: (id: string | null) => void;
  onUpdateBbox: (id: string, patch: Partial<DraftBbox>) => void;
  onDeleteBbox: (id: string) => void;
  onExtract: () => void;
  onDiscardDraft: () => void;
  busy: boolean;
}) {
  const totalDraft = draft.bboxes.length;
  const missingKinds = draft.bboxes.filter((b) => b.kind == null).length;
  const sel = pageBboxes.find((b) => b.id === selectedId) ?? null;
  return (
    <div className="px-3 py-3 flex flex-col h-full">
      <header className="mb-3 shrink-0">
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">PDF</div>
        <h1 className="text-[1rem] font-semibold leading-snug mt-0.5">{intake?.key}</h1>
        <p className="text-[0.72rem] text-muted">
          {info?.page_count ?? '?'} Seiten · {dataset?.drawings?.length ?? 0} Szenen extrahiert
        </p>
      </header>

      {/* Page list — primary navigation. Most-important section so it gets
          the top spot and as much height as it needs. */}
      <section className="flex-1 min-h-0 flex flex-col">
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5 shrink-0">
          Seiten
        </h3>
        <ul className="overflow-auto flex-1 space-y-0.5 min-h-0">
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
                  <span className="font-mono w-8 shrink-0">S{p.page}</span>
                  <span className="flex-1 inline-flex items-center justify-end gap-1.5 text-[0.62rem]">
                    {ds > 0 && (
                      <span className="px-1 rounded bg-emerald-100 text-emerald-900 font-semibold">
                        ✓ {ds}
                      </span>
                    )}
                    {dr > 0 && (
                      <span className="px-1 rounded bg-amber-100 text-amber-900 font-semibold">
                        ● {dr}
                      </span>
                    )}
                    {ds === 0 && dr === 0 && <span className="text-zinc-300">○</span>}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Per-page draft bboxes — only relevant on the current page; small. */}
      {pageBboxes.length > 0 && (
        <section className="shrink-0 mt-3 border-t border-border pt-3">
          <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1">
            Diese Seite — Entwürfe ({pageBboxes.length})
          </h3>
          <ul className="space-y-0.5">
            {pageBboxes.map((b) => (
              <li key={b.id}>
                <button
                  type="button"
                  onClick={() => onSelectBbox(b.id)}
                  className={`w-full text-left text-[0.72rem] px-2 py-1 rounded flex items-center gap-2 ${
                    selectedId === b.id
                      ? 'bg-accent/10 text-accent font-semibold'
                      : 'hover:bg-zinc-100 text-zinc-800'
                  }`}
                >
                  <span className={`font-mono ${b.kind ? '' : 'text-amber-700'}`}>
                    {b.kind ? KIND_LABEL[b.kind].slice(0, 2) : '?'}
                  </span>
                  <span className="flex-1 truncate">
                    {b.kind == null
                      ? <span className="italic text-amber-700">Typ wählen…</span>
                      : (b.title || `${KIND_LABEL[b.kind]}${b.floor ? ` · ${FLOOR_LABEL[b.floor as keyof typeof FLOOR_LABEL] ?? b.floor}` : ''}${b.view ? ` · ${VIEW_LABEL[b.view as keyof typeof VIEW_LABEL] ?? b.view}` : ''}`)
                    }
                  </span>
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onDeleteBbox(b.id); }}
                    className="text-[0.7rem] text-red-700"
                  >✕</button>
                </button>
              </li>
            ))}
          </ul>
          {sel && (
            <div className="mt-2 space-y-1.5 px-2 py-1.5 bg-zinc-50 rounded text-[0.72rem] border border-border">
              <div className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">
                Auswahl
              </div>
              <label className="block">
                Typ
                <select
                  value={sel.kind ?? ''}
                  onChange={(e) => onUpdateBbox(sel.id, {
                    kind: e.target.value ? (e.target.value as ExtractItem['kind']) : null,
                  })}
                  className={`w-full mt-0.5 px-2 py-1 border rounded text-[0.72rem] ${
                    sel.kind == null ? 'border-amber-500 bg-amber-50' : 'border-zinc-300'
                  }`}
                >
                  <option value="">Typ wählen…</option>
                  {KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
                </select>
              </label>
              {(sel.kind === 'elevation' || sel.kind === 'section') && (
                <label className="block">
                  Himmelsrichtung
                  <select
                    value={sel.view ?? ''}
                    onChange={(e) => onUpdateBbox(sel.id, { view: e.target.value || undefined })}
                    className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
                  >
                    <option value="">–</option>
                    {VIEWS.map((v) => <option key={v} value={v}>{VIEW_LABEL[v]}</option>)}
                  </select>
                </label>
              )}
              {sel.kind === 'floorplan' && (
                <label className="block">
                  Geschoss
                  <select
                    value={sel.floor ?? ''}
                    onChange={(e) => onUpdateBbox(sel.id, { floor: e.target.value || undefined })}
                    className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
                  >
                    <option value="">–</option>
                    {FLOORS.map((f) => <option key={f} value={f}>{FLOOR_LABEL[f]}</option>)}
                  </select>
                </label>
              )}
              <label className="block">
                Titel (optional)
                <input
                  type="text"
                  value={sel.title ?? ''}
                  onChange={(e) => onUpdateBbox(sel.id, { title: e.target.value || undefined })}
                  className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
                />
              </label>
            </div>
          )}
        </section>
      )}

      {/* Sticky bottom: the primary commit action. Always visible so the
          user knows where they are heading. */}
      <section className="shrink-0 mt-3 border-t border-border pt-3 space-y-1.5">
        <button
          type="button"
          onClick={onExtract}
          disabled={busy || totalDraft === 0}
          className="w-full text-[0.85rem] px-3 py-2 rounded-md bg-emerald-600 text-white font-semibold hover:opacity-90 disabled:opacity-40"
        >
          {busy ? 'Extrahiere…' : totalDraft === 0
            ? 'Bbox zeichnen, dann extrahieren'
            : `→ ${totalDraft} Szene${totalDraft === 1 ? '' : 'n'} extrahieren`}
        </button>
        {missingKinds > 0 && !busy && (
          <p className="text-[0.65rem] text-amber-700 text-center">
            {missingKinds} ohne Typ — werden als „Detail" abgelegt.
          </p>
        )}
        {totalDraft > 0 && !busy && (
          <button
            type="button"
            onClick={onDiscardDraft}
            className="w-full text-[0.62rem] text-zinc-500 hover:text-red-700"
          >
            Entwurf verwerfen
          </button>
        )}
      </section>
    </div>
  );
}


// The actual page canvas. SVG overlay tracks bboxes in PDF-unit coords;
// pointer interactions translate to PDF units via the INNER page div's
// bounding rect (NOT the outer scroll container — the inner div is
// centred via mx-auto, so its left edge is offset from the container's
// left edge by the centering margin).
function PageCanvas({
  pdfKey, page, pageWidthPt, pageHeightPt,
  draftBboxes, extracted, selectedId, onSelect, onCommit, onUpdate,
  postDraw, onPostDrawPick, onPostDrawDismiss,
  houseKey, onDeleteExtracted, onAdjustExtracted,
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
  postDraw: { id: string; step: 'kind' | 'floor' | 'view' } | null;
  onPostDrawPick: (patch: Partial<DraftBbox>) => void;
  onPostDrawDismiss: () => void;
  houseKey: string;
  onDeleteExtracted: (file: string) => void;
  onAdjustExtracted: (file: string) => void;
}) {
  const [menuFor, setMenuFor] = useState<string | null>(null);
  // The pageRef tracks the WHITE PAGE DIV (not the outer dark scroll
  // container) — its bbox is what we measure against for pointer-to-PDF
  // unit conversion. Otherwise the coords are off by however much
  // horizontal margin the centering (mx-auto) introduces.
  const pageRef = useRef<HTMLDivElement>(null);
  const [drag, setDrag] = useState<{ start: [number, number]; end: [number, number] } | null>(null);

  const ptToPdf = (clientX: number, clientY: number): [number, number] => {
    const rect = pageRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return [0, 0];
    const rx = (clientX - rect.left) / rect.width;
    const ry = (clientY - rect.top)  / rect.height;
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
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
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

  // The page fills the available canvas space, preserving aspect ratio.
  // We let CSS do the math: aspect-ratio + max-width:100% + max-height:100%
  // makes the browser pick the largest size that fits both axes. The
  // outer container is a centered flex box so the page is centred both
  // horizontally and vertically.

  return (
    <div
      className="relative bg-zinc-800 flex-1 overflow-hidden select-none flex items-center justify-center p-2"
      style={{ touchAction: 'none' }}
    >
      <div
        ref={pageRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onDoubleClick={(e) => {
          // Full-page bbox shortcut. Don't fire when the user double-
          // clicked an existing handle (resize-bbox), only on the bare
          // page area.
          if ((e.target as HTMLElement).closest('[data-bbox-handle]')) return;
          onCommit([0, 0, pageWidthPt, pageHeightPt]);
        }}
        className="relative bg-white shadow-lg cursor-crosshair ring-1 ring-zinc-700"
        style={{
          // aspect-ratio + the two max constraints lets the page fill
          // whichever axis bounds it (no fixed width = no leftover space).
          aspectRatio: `${pageWidthPt}/${pageHeightPt}`,
          maxWidth: '100%',
          maxHeight: '100%',
          // Without an explicit height, some browsers shrink a div that
          // only has aspect-ratio + max-width. Setting height:100% gives
          // the aspect-ratio rule something to apply against, then max-
          // width clamps the resulting width.
          height: '100%',
          width: 'auto',
        }}
      >
        {/* First-use hint, fades to opacity-0 once a draft bbox exists. */}
        {draftBboxes.length === 0 && extracted.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none text-zinc-400">
            <div className="bg-white/85 px-4 py-2 rounded-md text-[0.75rem] shadow-sm">
              🖱 Click-drag um eine Szene zu ziehen
            </div>
          </div>
        )}
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
          {/* Already-extracted scenes — emerald semi-transparent. Click
              opens the contextual action menu (go to annotation / convert
              back to a draft for re-cropping / delete). */}
          {extracted.map((d) => {
            const cf = d.crop_from as { bbox_pdf_units?: [number, number, number, number] } | undefined;
            if (!cf?.bbox_pdf_units) return null;
            const [x0, y0, x1, y1] = cf.bbox_pdf_units;
            const isMenu = menuFor === d.file;
            return (
              <g key={d.file}>
                <rect
                  x={x0} y={y0} width={x1 - x0} height={y1 - y0}
                  fill={isMenu ? 'rgba(16, 185, 129, 0.18)' : 'rgba(16, 185, 129, 0.10)'}
                  stroke="#059669"
                  strokeWidth={isMenu ? 2 : 1.5}
                  strokeDasharray="4 3"
                  style={{ pointerEvents: 'auto', cursor: 'pointer' }}
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuFor((m) => m === d.file ? null : d.file);
                  }}
                >
                  <title>{`${d.file} — Klick für Aktionen`}</title>
                </rect>
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
        {/* Post-draw classifier chip — anchored to the bbox. When the
            bbox starts near the top of the page (e.g. a full-page bbox
            at y=0), an above-anchor would be clipped by the parent's
            overflow-hidden, so we flip it INSIDE the bbox instead. */}
        {postDraw && (() => {
          const target = draftBboxes.find((b) => b.id === postDraw.id);
          if (!target) return null;
          const [x0, y0, x1] = target.bbox_pdf;
          const leftPct = ((x0 + x1) / 2 / pageWidthPt) * 100;
          const topPct = (y0 / pageHeightPt) * 100;
          // < 18 % means there isn't room above; render the chip just
          // INSIDE the top of the bbox in that case so it's never clipped.
          const placement: 'above' | 'inside' = topPct < 18 ? 'inside' : 'above';
          return (
            <PostDrawChip
              step={postDraw.step}
              leftPct={leftPct}
              topPct={topPct}
              placement={placement}
              onPick={onPostDrawPick}
              onDismiss={onPostDrawDismiss}
            />
          );
        })()}
        {/* Action menu for a clicked extracted-scene overlay. Anchored to
            the bbox's top edge; flips below for bboxes near y=0 so it's
            never clipped. */}
        {menuFor && (() => {
          const d = extracted.find((x) => x.file === menuFor);
          const cf = d?.crop_from as { bbox_pdf_units?: [number, number, number, number] } | undefined;
          if (!d || !cf?.bbox_pdf_units) return null;
          const [x0, y0, x1] = cf.bbox_pdf_units;
          const leftPct = (((x0 + x1) / 2) / pageWidthPt) * 100;
          const topPct = (y0 / pageHeightPt) * 100;
          const placement: 'above' | 'below' = topPct < 12 ? 'below' : 'above';
          return (
            <ExtractedSceneMenu
              leftPct={leftPct}
              topPct={topPct}
              placement={placement}
              houseKey={houseKey}
              file={d.file}
              onClose={() => setMenuFor(null)}
              onDelete={() => { setMenuFor(null); onDeleteExtracted(d.file); }}
              onAdjust={() => { setMenuFor(null); onAdjustExtracted(d.file); }}
            />
          );
        })()}
      </div>
    </div>
  );
}

// Floating classifier chip — appears next to the just-committed bbox so
// the user picks kind (and floor / view if applicable) without leaving
// the canvas. Keyboard hints mirror the same letters AnnotatePage uses
// for its post-draw chip so the muscle memory carries over.
// Topbar action menu — surfaces destructive house-level actions
// (reset everything) under a `⋯` button so they're discoverable but
// out of the primary path.
function HouseMenu({
  houseKey, sceneCount, draftCount, onReset,
}: {
  houseKey: string;
  sceneCount: number;
  draftCount: number;
  onReset: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-[0.75rem] px-2 py-1 rounded-md border border-zinc-300 text-zinc-700 hover:bg-zinc-100"
        title={`${houseKey} — Aktionen`}
        aria-label="Menü"
      >
        ⋯
      </button>
      {open && (
        <>
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="fixed inset-0 z-30 cursor-default"
            aria-label="Menü schließen"
          />
          <div
            className="absolute right-0 mt-1 z-40 bg-white border border-zinc-300 rounded-md shadow-xl min-w-[16rem] py-1 text-[0.78rem]"
          >
            <div className="px-3 py-1.5 text-[0.62rem] uppercase tracking-wider text-muted">
              Diese PDF zurücksetzen
            </div>
            <button
              type="button"
              onClick={() => { setOpen(false); onReset(); }}
              disabled={sceneCount === 0 && draftCount === 0}
              className="block w-full text-left px-3 py-1.5 hover:bg-red-50 text-red-700 disabled:text-zinc-400 disabled:hover:bg-transparent"
            >
              <div className="font-semibold">⚠ Alle Szenen löschen</div>
              <div className="text-[0.65rem] text-zinc-500">
                Löscht {sceneCount} Szene{sceneCount === 1 ? '' : 'n'}
                {draftCount > 0 ? ` und ${draftCount} Entwurf` : ''}.
                PDF bleibt erhalten.
              </div>
            </button>
          </div>
        </>
      )}
    </div>
  );
}

// Floating action menu anchored to an already-extracted scene's bbox on
// the PDF page. Tightly mirrors PostDrawChip's positioning model so the
// visual rhythm is consistent. Esc closes; clicking the backdrop closes.
function ExtractedSceneMenu({
  leftPct, topPct, placement, houseKey, file, onClose, onDelete, onAdjust,
}: {
  leftPct: number;
  topPct: number;
  placement: 'above' | 'below';
  houseKey: string;
  file: string;
  onClose: () => void;
  onDelete: () => void;
  onAdjust: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <>
      {/* Click-away backdrop. */}
      <button
        type="button"
        onClick={onClose}
        className="absolute inset-0 z-20 cursor-default"
        aria-label="Menü schließen"
      />
      <div
        className="absolute z-30 -translate-x-1/2 bg-white border border-zinc-300 rounded-md shadow-xl text-[0.78rem] min-w-[12rem]"
        style={{
          left: `${leftPct}%`,
          top: `${topPct}%`,
          transform: placement === 'above'
            ? 'translate(-50%, calc(-100% - 8px))'
            : 'translate(-50%, 8px)',
        }}
      >
        <div className="px-3 py-1.5 text-[0.62rem] uppercase tracking-wider text-muted border-b border-border truncate" title={file}>
          {file}
        </div>
        <Link
          to={`/${houseKey}/scene/${encodeURIComponent(file)}/annotate`}
          className="block px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
          onClick={onClose}
        >
          ↗ Annotieren
        </Link>
        <button
          type="button"
          onClick={onAdjust}
          className="block w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
        >
          ↔ Bbox anpassen
          <div className="text-[0.62rem] text-zinc-500">Wird zum Entwurf — danach erneut extrahieren.</div>
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="block w-full text-left px-3 py-1.5 hover:bg-red-50 text-red-700"
        >
          ✕ Szene löschen
        </button>
      </div>
    </>
  );
}

function PostDrawChip({
  step, leftPct, topPct, placement, onPick, onDismiss,
}: {
  step: 'kind' | 'floor' | 'view';
  leftPct: number;
  topPct: number;
  placement: 'above' | 'inside';
  onPick: (patch: Partial<DraftBbox>) => void;
  onDismiss: () => void;
}) {
  let opts: Array<{ label: string; key: string; patch: Partial<DraftBbox> }>;
  if (step === 'kind') {
    opts = [
      { label: 'Grundriss', key: 'G', patch: { kind: 'floorplan' } },
      { label: 'Ansicht',   key: 'A', patch: { kind: 'elevation' } },
      { label: 'Schnitt',   key: 'S', patch: { kind: 'section' } },
      { label: 'Detail',    key: 'D', patch: { kind: 'detail' } },
    ];
  } else if (step === 'floor') {
    opts = [
      { label: 'KG',         key: 'K', patch: { floor: 'kg' } },
      { label: 'UG',         key: 'U', patch: { floor: 'ug' } },
      { label: 'EG',         key: 'E', patch: { floor: 'eg' } },
      { label: 'OG',         key: 'O', patch: { floor: 'og' } },
      { label: 'DG',         key: 'D', patch: { floor: 'dg' } },
      { label: 'Spitzboden', key: 'S', patch: { floor: 'spitzboden' } },
    ];
  } else {
    opts = [
      { label: 'Nord', key: 'N', patch: { view: 'north' } },
      { label: 'Süd',  key: 'S', patch: { view: 'south' } },
      { label: 'Ost',  key: 'O', patch: { view: 'east' } },
      { label: 'West', key: 'W', patch: { view: 'west' } },
    ];
  }
  const headline =
    step === 'kind' ? 'Was zeigt diese Bbox?' :
    step === 'floor' ? 'Welches Geschoss?' :
    'Welche Himmelsrichtung?';
  return (
    <div
      className={`absolute z-30 -translate-x-1/2 bg-white border border-zinc-300 rounded-md shadow-xl p-2 flex flex-col gap-1.5 ${
        placement === 'above' ? '-translate-y-full mt-[-6px]' : 'mt-2'
      }`}
      style={{
        left: `${Math.max(8, Math.min(92, leftPct))}%`,
        top: `${Math.max(0, topPct)}%`,
        pointerEvents: 'auto',
      }}
      onPointerDown={(e) => e.stopPropagation()}
      onDoubleClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-2 text-[0.62rem] uppercase tracking-wider text-muted">
        <span>{headline}</span>
        <button
          type="button"
          onClick={onDismiss}
          className="text-zinc-400 hover:text-zinc-700 text-base leading-none px-1"
          title="Schließen (Esc) — Bbox bleibt unklassifiziert"
        >×</button>
      </div>
      <div className="flex flex-wrap gap-1">
        {opts.map((opt) => (
          <button
            key={opt.key}
            type="button"
            onClick={() => onPick(opt.patch)}
            className="px-2 py-0.5 rounded bg-zinc-100 hover:bg-accent hover:text-white text-[0.72rem] inline-flex items-center gap-1"
          >
            <span>{opt.label}</span>
            <kbd className="text-[0.58rem] font-mono text-zinc-400">{opt.key}</kbd>
          </button>
        ))}
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
