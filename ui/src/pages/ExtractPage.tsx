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
import { Link, useNavigate, useParams } from 'react-router';
import {
  extractScenes,
  fetchDataset,
  getIncomingPdf,
  getPdfInfo,
  pdfPageUrl,
  pdfPageGridUrl,
  deleteExtractedScene,
  restoreExtractedScene,
  patchSceneAttrs,
  resetHouse,
  type ExtractItem,
  type PdfInfo,
} from '../api/client';
import type { DatasetHouse, IncomingPdf } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { PHASE_IDS, syncHouseFactsFromServer, type HouseFacts } from '../lib/house_facts';
import { SceneDetailsCard } from '../components/scene/SceneDetailsCard';
import { Cheatsheet, CHEATSHEET_SECTIONS_EXTRACT } from '../components/Cheatsheet';
import { useToast } from '../lib/toast';
import { useExtractUndo } from '../lib/extract_undo';

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
  // L5 + L10 — Cheatsheet toggled via ? (renders below in the Shell
  // children). Shared component, page contributes its own sections.
  const [cheatsheetOpen, setCheatsheetOpen] = useState(false);
  // Agent-grid overlay — swaps the PDF page image for the same image
  // the bim-database MCP server returns to a labeling agent
  // (image @ 0.5 opacity + 3-tier coordinate grid). Useful for spot-
  // checking what the agent sees when its extracted bboxes look off.
  const [showGrid, setShowGrid] = useState<boolean>(() => {
    try { return window.localStorage.getItem('bim-db:extract:show-grid') === 'true'; }
    catch { return false; }
  });
  useEffect(() => {
    try { window.localStorage.setItem('bim-db:extract:show-grid', String(showGrid)); }
    catch { /* no-op */ }
  }, [showGrid]);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // The extracted-scene action menu (Annotieren / Bbox anpassen /
  // Löschen) lives here so the SceneStrip can open it for off-canvas
  // chip clicks the same way the canvas click on a green rect does.
  // L4 D3 — menu anchor is the user's click coords (viewport space).
  // Falls back to the bbox centroid when opened from a chip click in
  // the SceneStrip (no specific gesture coordinate).
  const [menuForExtracted, setMenuForExtracted] = useState<
    { file: string; clientX?: number; clientY?: number } | null
  >(null);
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

  // A3 — undo/redo for extract-side mutations. House-scoped (Q6 ★) but
  // stays in memory only; the user can undo within their session, not
  // after navigating away from the house. Action kinds:
  //   - extract  : a draft became a green scene. Undo deletes the
  //                scene (server moves it to recycle) and restores the
  //                draft. Redo extracts again.
  //   - delete   : a green scene moved to recycle. Undo calls restore;
  //                redo re-deletes.
  //   - classify : a PATCH on the drawing's kind/floor/view/title.
  //                Undo PATCHes back; redo re-applies.
  type ExtractAction =
    | { kind: 'extract'; file: string; previousDraft: DraftBbox }
    | { kind: 'delete'; file: string }
    | { kind: 'adjust'; file: string; newDraftId: string }
    | { kind: 'classify'; file: string;
        before: { kind: string | null; floor: string | null; view: string | null; title: string | null };
        after:  { kind: string | null; floor: string | null; view: string | null; title: string | null };
      };
  // auto-persist follow-up — the action log is now house-scoped via
  // ExtractUndoProvider, so the stack survives navigation to / from
  // the annotation editor and back.
  const undoCtx = useExtractUndo();
  const pushUndo = useCallback((a: ExtractAction) => undoCtx.push(key, a), [undoCtx, key]);
  const applyAction = useCallback(async (a: ExtractAction, reverse: boolean): Promise<ExtractAction | null> => {
    if (a.kind === 'extract') {
      if (reverse) {
        // Undo extract = delete the scene + put the bbox back as a draft.
        await deleteExtractedScene(key, a.file);
        const d = await fetchDataset(key);
        setDataset(d);
        setDraft((curr) => ({ ...curr, bboxes: [...curr.bboxes, a.previousDraft] }));
        return a;
      }
      // Redo extract = call extract again with the same draft.
      await extractScenes(key, [{
        page: a.previousDraft.page,
        bbox_pdf_units: a.previousDraft.bbox_pdf,
        kind: a.previousDraft.kind as ExtractItem['kind'],
        view: a.previousDraft.view ?? null,
        floor: a.previousDraft.floor ?? null,
        title: a.previousDraft.title ?? null,
      }]);
      const d = await fetchDataset(key);
      setDataset(d);
      setDraft((curr) => ({ ...curr, bboxes: curr.bboxes.filter((b) => b.id !== a.previousDraft.id) }));
      return a;
    }
    if (a.kind === 'delete') {
      if (reverse) {
        const d = await restoreExtractedScene(key, a.file);
        setDataset(d);
        return a;
      }
      await deleteExtractedScene(key, a.file);
      const d = await fetchDataset(key);
      setDataset(d);
      return a;
    }
    if (a.kind === 'adjust') {
      if (reverse) {
        // Undo adjust = restore the original extracted scene AND drop
        // the new draft we created.
        const d = await restoreExtractedScene(key, a.file);
        setDataset(d);
        setDraft((curr) => ({ ...curr, bboxes: curr.bboxes.filter((b) => b.id !== a.newDraftId) }));
        return a;
      }
      // Redo adjust = re-trigger onAdjustExtracted's effect. The
      // simpler reach: delete the scene again (it'll re-enter the
      // recycle bin) and re-create the draft.
      const target = (await fetchDataset(key)).drawings.find((d) => d.file === a.file);
      const cf = target?.crop_from;
      if (!target || !cf?.bbox_pdf_units) return null;
      await deleteExtractedScene(key, a.file);
      const fresh = await fetchDataset(key);
      setDataset(fresh);
      const draftKind: ExtractItem['kind'] | null =
        target.kind === 'floorplan' || target.kind === 'elevation' ||
        target.kind === 'section'   || target.kind === 'detail'
          ? target.kind
          : null;
      setDraft((curr) => ({
        ...curr,
        bboxes: [...curr.bboxes, {
          id: a.newDraftId,
          page: cf.page,
          bbox_pdf: cf.bbox_pdf_units,
          kind: draftKind,
          view: target.view ?? undefined,
          floor: target.floor ?? undefined,
          title: target.title ?? undefined,
        }],
      }));
      return a;
    }
    // classify
    await patchSceneAttrs(key, a.file, reverse ? (a.before as Parameters<typeof patchSceneAttrs>[2]) : (a.after as Parameters<typeof patchSceneAttrs>[2]));
    const d = await fetchDataset(key);
    setDataset(d);
    return a;
  }, [key]);
  // L8 / U15 — undo/redo feedback via the shared toast provider.
  const { addToast } = useToast();
  const showToast = useCallback((text: string) => addToast(text, 'info', 1800), [addToast]);
  const describeAction = (a: ExtractAction, reverse: boolean): string => {
    if (a.kind === 'extract')  return reverse ? '↶ Szene zurück in Entwurf' : '↷ Erneut extrahiert';
    if (a.kind === 'delete')   return reverse ? '↶ Szene wiederhergestellt' : '↷ Erneut gelöscht';
    if (a.kind === 'adjust')   return reverse ? '↶ Anpassung zurückgesetzt' : '↷ Erneut zum Entwurf gemacht';
    return reverse ? '↶ Klassifikation zurückgesetzt' : '↷ Klassifikation erneut geändert';
  };
  const runUndo = useCallback(async () => {
    const a = undoCtx.popUndo(key) as ExtractAction | undefined;
    if (!a) return;
    try {
      const applied = await applyAction(a, true);
      if (applied) { undoCtx.pushRedo(key, applied); showToast(describeAction(applied, true)); }
    } catch (e) {
      undoCtx.push(key, a);
      setError(`Undo fehlgeschlagen: ${(e as Error).message}`);
    }
  }, [applyAction, showToast, undoCtx, key]);
  const runRedo = useCallback(async () => {
    const a = undoCtx.popRedo(key) as ExtractAction | undefined;
    if (!a) return;
    try {
      const applied = await applyAction(a, false);
      if (applied) { undoCtx.push(key, applied); showToast(describeAction(applied, false)); }
    } catch (e) {
      undoCtx.pushRedo(key, a);
      setError(`Redo fehlgeschlagen: ${(e as Error).message}`);
    }
  }, [applyAction, showToast, undoCtx, key]);

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
            const draftId = postDraw.id;
            setDraft((d) => ({
              ...d,
              bboxes: d.bboxes.map((b) => b.id === draftId ? { ...b, kind } : b),
            }));
            setPostDraw(
              kind === 'floorplan' ? { id: draftId, step: 'floor' }
              : (kind === 'elevation' || kind === 'section') ? { id: draftId, step: 'view' }
              : null,
            );
            if (kind === 'detail') {
              // A1 keyboard parity — Detail completes on kind. Defer
              // one tick so the setDraft commits first.
              setTimeout(() => { void extractDraftNow(draftId); }, 0);
            }
            e.preventDefault();
            return;
          }
        }
        if (postDraw.step === 'floor') {
          const map: Record<string, typeof FLOORS[number]> = { k: 'kg', u: 'ug', e: 'eg', o: 'og', d: 'dg', s: 'spitzboden' };
          if (k in map) {
            const floor = map[k];
            const draftId = postDraw.id;
            setDraft((d) => ({
              ...d,
              bboxes: d.bboxes.map((b) => b.id === draftId ? { ...b, floor } : b),
            }));
            setPostDraw(null);
            setTimeout(() => { void extractDraftNow(draftId); }, 0);
            e.preventDefault();
            return;
          }
        }
        if (postDraw.step === 'view') {
          const map: Record<string, typeof VIEWS[number]> = { n: 'north', s: 'south', o: 'east', w: 'west' };
          if (k in map) {
            const view = map[k];
            const draftId = postDraw.id;
            setDraft((d) => ({
              ...d,
              bboxes: d.bboxes.map((b) => b.id === draftId ? { ...b, view } : b),
            }));
            setPostDraw(null);
            setTimeout(() => { void extractDraftNow(draftId); }, 0);
            e.preventDefault();
            return;
          }
        }
      }
      // A3 — Cmd/Ctrl+Z undoes the latest extract / delete / classify;
      // Shift+Z (or Y on Windows) redoes.
      if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault();
        if (e.shiftKey) { void runRedo(); } else { void runUndo(); }
        return;
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === 'y' || e.key === 'Y')) {
        e.preventDefault();
        void runRedo();
        return;
      }
      // L5 — page nav on both axes plus Home/End/Page Up/Down.
      if (e.key === 'ArrowLeft'  || e.key === 'ArrowUp'   || e.key === 'PageUp')   setPage(currentPage - 1);
      if (e.key === 'ArrowRight' || e.key === 'ArrowDown' || e.key === 'PageDown') setPage(currentPage + 1);
      if (e.key === 'Home') setPage(1);
      if (e.key === 'End')  setPage(info?.page_count ?? 1);
      // L5 — open the shared cheatsheet from this page too.
      if (e.key === '?' && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        setCheatsheetOpen((v) => !v);
        return;
      }
      if (e.key === 'Escape')     { setSelectedId(null); setCheatsheetOpen(false); }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedId) {
          setDraft((d) => ({ ...d, bboxes: d.bboxes.filter((b) => b.id !== selectedId) }));
          setSelectedId(null);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage, setPage, selectedId, postDraw, runUndo, runRedo, info]);

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

  // A1 — extract a single draft bbox the moment its classification
  // completes. The "Extract N scenes" batch button is gone; the post-
  // draw classifier writes straight through to the server, the orange
  // bbox becomes a green extracted scene as soon as the round-trip
  // returns. The chip stays in 'busy' state during the trip.
  // L9 — set of draft IDs currently mid-flight on a server extract.
  // Drives the in-progress visual on the bbox itself.
  const [extractingIds, setExtractingIds] = useState<Set<string>>(new Set());
  const extractDraftNow = useCallback(async (draftId: string) => {
    setError(null);
    const draftSnap = draft.bboxes.find((b) => b.id === draftId);
    if (!draftSnap || !draftSnap.kind) return;
    setBusy(true);
    setExtractingIds((s) => { const n = new Set(s); n.add(draftId); return n; });
    try {
      await extractScenes(key, [{
        page: draftSnap.page,
        bbox_pdf_units: draftSnap.bbox_pdf,
        kind: draftSnap.kind,
        view: draftSnap.view ?? null,
        floor: draftSnap.floor ?? null,
        title: draftSnap.title ?? null,
      }]);
      const [d, b] = await Promise.all([
        fetchDataset(key),
        getIncomingPdf(key).catch(() => null),
      ]);
      setDataset(d);
      setIntake(b);
      // A3 — record the action so Cmd+Z can reverse it. Locate the
      // freshly-created scene file via the new manifest entries.
      const newFile = d.drawings.find((dr) =>
        (dr.crop_from as { page?: number } | undefined)?.page === draftSnap.page
        && dr.kind === draftSnap.kind
        && (dr.floor ?? null) === (draftSnap.floor ?? null)
        && (dr.view ?? null) === (draftSnap.view ?? null),
      )?.file;
      if (newFile) {
        pushUndo({ kind: 'extract', file: newFile, previousDraft: draftSnap });
      }
      setDraft((curr) => {
        const next = { ...curr, bboxes: curr.bboxes.filter((b2) => b2.id !== draftId) };
        if (next.bboxes.length === 0) clearDraft(key);
        return next;
      });
      setSelectedId((sel) => sel === draftId ? null : sel);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setExtractingIds((s) => { const n = new Set(s); n.delete(draftId); return n; });
    }
  }, [draft, key, pushUndo]);

  const onDeleteScene = useCallback(async (file: string) => {
    // A3 — auto-persist + recycle means we don't need the confirm
    // dialog; Cmd+Z is the safety net.
    try {
      await deleteExtractedScene(key, file);
      pushUndo({ kind: 'delete', file });
      const d = await fetchDataset(key);
      setDataset(d);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [key, pushUndo]);

  // Convert an already-extracted scene back into an editable draft bbox.
  // The user gets the same bbox geometry + classification back as a draft
  // so they can reshape it and re-extract. The dataset entry is removed
  // in the same step so we don't end up with a duplicate file.
  const onAdjustExtracted = useCallback(async (file: string) => {
    // auto-persist follow-up — Adjust drops the window.confirm; Cmd+Z
    // restores the scene from the recycle bin AND drops the new draft.
    const target = (dataset?.drawings ?? []).find((d) => d.file === file);
    const cf = target?.crop_from;
    if (!target || !cf?.bbox_pdf_units) return;
    try {
      await deleteExtractedScene(key, file);
      const fresh = await fetchDataset(key);
      setDataset(fresh);
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
      pushUndo({ kind: 'adjust', file, newDraftId: id });
    } catch (e) {
      setError((e as Error).message);
    }
  }, [key, dataset, pushUndo]);

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
          <button
            type="button"
            onClick={() => setShowGrid(!showGrid)}
            className={`text-[0.7rem] px-2 py-1 rounded-md border ${
              showGrid
                ? 'bg-purple-600 text-white border-purple-600'
                : 'bg-white text-zinc-700 border-zinc-300 hover:bg-zinc-50'
            }`}
            title="Agenten-Raster überlagern: zeigt das Bild, das ein Labeling-Agent über den MCP-Server sieht (3-stufiges Pixelraster)"
            aria-label="Agenten-Raster umschalten"
          >
            {showGrid ? '🤖 Raster' : 'Raster'}
          </button>
          <button
            type="button"
            onClick={() => setCheatsheetOpen((v) => !v)}
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-muted hover:bg-zinc-100 hover:text-zinc-900"
            title="Tastaturkürzel (?)"
            aria-label="Tastaturkürzel"
          >
            ?
          </button>
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
          onDiscardDraft={() => {
            if (!window.confirm(`Alle ${draft.bboxes.length} Bbox-Entwürfe verwerfen?`)) return;
            clearDraft(key);
            setDraft(emptyDraft());
            setSelectedId(null);
          }}
        />
      }
    >
      {/* L10 — shared keyboard cheatsheet (open with ?). */}
      {cheatsheetOpen && (
        <Cheatsheet
          sections={CHEATSHEET_SECTIONS_EXTRACT}
          onClose={() => setCheatsheetOpen(false)}
        />
      )}
      {/* L8 toasts now use the shared ToastProvider at the app root. */}
      <div className="flex flex-col h-full">
        <SceneStrip
          scenes={dataset?.drawings ?? []}
          drafts={draft.bboxes}
          selectedDraftId={selectedId}
          menuForFile={menuForExtracted?.file ?? null}
          currentPage={currentPage}
          onSelectScene={(file) => {
            const d = (dataset?.drawings ?? []).find((x) => x.file === file);
            const p = (d?.crop_from as { page?: number } | undefined)?.page;
            if (p && p !== currentPage) setPage(p);
            // No client coords — the chip's centre is good enough.
            setMenuForExtracted({ file });
          }}
          onSelectDraft={(id) => {
            const d = draft.bboxes.find((b) => b.id === id);
            if (d && d.page !== currentPage) setPage(d.page);
            setSelectedId(id);
            setMenuForExtracted(null);
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
            showGrid={showGrid}
            draftBboxes={pageBboxes}
            extracted={extractedOnPage}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onCommit={onCommitBbox}
            onUpdate={onUpdateBbox}
            postDraw={postDraw}
            onPostDrawPick={(patch) => {
              if (!postDraw) return;
              const id = postDraw.id;
              setDraft((d) => ({
                ...d,
                bboxes: d.bboxes.map((b) => b.id === id ? { ...b, ...patch } : b),
              }));
              // A1 — figure out whether this pick *completes* the
              // classification. Detail: complete after kind. Grundriss:
              // complete after floor. Ansicht/Schnitt: complete after
              // view. When complete, fire-and-forget the auto-extract.
              const becomesComplete =
                (postDraw.step === 'kind' && patch.kind === 'detail') ||
                postDraw.step === 'floor' ||
                postDraw.step === 'view';
              if (patch.kind === 'floorplan' && postDraw.step === 'kind') {
                setPostDraw({ id, step: 'floor' });
              } else if ((patch.kind === 'elevation' || patch.kind === 'section') && postDraw.step === 'kind') {
                setPostDraw({ id, step: 'view' });
              } else {
                setPostDraw(null);
              }
              if (becomesComplete) {
                // Defer one tick so the setDraft above commits before
                // extractDraftNow's snapshot read.
                setTimeout(() => { void extractDraftNow(id); }, 0);
              }
            }}
            onPostDrawDismiss={() => setPostDraw(null)}
            extractBusy={busy}
            extractingIds={extractingIds}
            houseKey={key}
            onDeleteExtracted={onDeleteScene}
            onAdjustExtracted={onAdjustExtracted}
            menuFor={menuForExtracted}
            setMenuFor={setMenuForExtracted}
            onDatasetRefresh={setDataset}
            onClassifyAction={(file, before, after) => {
              pushUndo({ kind: 'classify', file, before, after });
            }}
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
type StripItem =
  | { kind: 'scene'; page: number; drawing: DatasetHouse['drawings'][number] }
  | { kind: 'draft'; page: number; draft: DraftBbox };

function SceneStrip({
  scenes, drafts, selectedDraftId, menuForFile, currentPage,
  onSelectScene, onSelectDraft, onDeleteScene, onDeleteDraft,
}: {
  scenes: DatasetHouse['drawings'];
  drafts: DraftBbox[];
  selectedDraftId: string | null;
  menuForFile: string | null;
  currentPage: number;
  onSelectScene: (file: string) => void;
  onSelectDraft: (id: string) => void;
  onDeleteScene: (file: string) => void;
  onDeleteDraft: (id: string) => void;
}) {
  const total = scenes.length + drafts.length;
  // Single unified list ordered by source page. Scenes and drafts
  // interleave by page index so the user sees the natural reading order
  // of the PDF; no category bucket.
  const items: StripItem[] = useMemo(() => {
    const out: StripItem[] = [];
    for (const d of scenes) {
      const p = (d.crop_from as { page?: number } | undefined)?.page ?? Number.MAX_SAFE_INTEGER;
      out.push({ kind: 'scene', page: p, drawing: d });
    }
    for (const b of drafts) {
      out.push({ kind: 'draft', page: b.page, draft: b });
    }
    out.sort((a, b) => a.page - b.page);
    return out;
  }, [scenes, drafts]);

  const scrollRef = useRef<HTMLDivElement>(null);
  const firstHereRef = useRef<HTMLSpanElement>(null);
  // When the current PDF page changes, slide the strip so the first chip
  // matching that page is visible without the user having to scroll.
  useEffect(() => {
    const el = firstHereRef.current;
    if (!el) return;
    el.scrollIntoView({ behavior: 'smooth', inline: 'start', block: 'nearest' });
  }, [currentPage, items.length]);

  if (total === 0) {
    return (
      <div className="px-3 py-1.5 border-b border-border bg-zinc-50 text-[0.72rem] text-zinc-500">
        Noch keine Szenen extrahiert — zieh eine Bbox auf die Seite oder klick „Ganze Seite als Szene".
      </div>
    );
  }
  let firstHereMarked = false;
  return (
    <div className="border-b border-border bg-zinc-50">
      <div ref={scrollRef} className="px-3 py-1.5 flex items-center gap-1.5 overflow-x-auto">
        {items.map((it) => {
          const isHere = it.page === currentPage;
          // ref only on the first chip whose source is the current page,
          // so scrollIntoView lands on the correct one.
          const isFirstHere = isHere && !firstHereMarked;
          if (isFirstHere) firstHereMarked = true;
          const wrapRef = isFirstHere ? firstHereRef : undefined;
          // Subtle "this is from the page you're looking at" highlight —
          // an accent underline + soft background tint. Different from
          // the "selected" state (which uses a stronger ring) so the
          // affordances don't read as the same thing.
          const hereCls = isHere ? 'bg-accent/5 ring-1 ring-accent/30' : '';
          if (it.kind === 'scene') {
            const d = it.drawing;
            const cf = d.crop_from as { page?: number } | undefined;
            const pageN = cf?.page ?? null;
            const label =
              d.kind === 'floorplan' && d.floor ? `${KIND_LABEL.floorplan} ${(FLOOR_LABEL as Record<string, string>)[d.floor] ?? d.floor}` :
              d.kind === 'elevation' && d.view ? `${KIND_LABEL.elevation} ${(VIEW_LABEL as Record<string, string>)[d.view] ?? d.view}` :
              (KIND_LABEL as Record<string, string>)[d.kind] ?? d.kind;
            const isMenu = menuForFile === d.file;
            return (
              <span ref={wrapRef} key={`s-${d.file}`} className={`relative inline-flex shrink-0 rounded-md ${hereCls}`}>
                <button
                  type="button"
                  onClick={() => onSelectScene(d.file)}
                  title={`${d.file} — Klick zeigt die Bbox auf der Seite${pageN != null ? ` ${pageN}` : ''}`}
                  className={`inline-flex items-center gap-1.5 pl-1 pr-2 py-1 rounded-md border transition ${
                    isMenu
                      ? 'bg-emerald-50 border-emerald-500'
                      : 'bg-white border-zinc-200 hover:border-zinc-400'
                  }`}
                >
                  <span className={`relative w-10 h-10 shrink-0 rounded overflow-hidden bg-zinc-100 border ${isMenu ? 'border-emerald-500' : 'border-zinc-200'}`}>
                    {d.url ? (
                      <img src={d.url} alt="" loading="lazy" className="absolute inset-0 w-full h-full object-cover" />
                    ) : (
                      <span className="absolute inset-0 flex items-center justify-center text-zinc-400 text-[0.55rem] font-semibold">?</span>
                    )}
                    {d.labeled && (
                      <span className="absolute bottom-0 right-0 bg-emerald-600 text-white text-[0.5rem] leading-none px-0.5 py-0.5 rounded-tl">✓</span>
                    )}
                  </span>
                  <span className="text-[0.72rem] font-medium whitespace-nowrap">{label}</span>
                  <span className="flex items-center gap-0.5 ml-0.5">
                    {pageN != null && (
                      <span
                        className={`text-[0.6rem] px-1 py-0.5 rounded ${
                          isHere ? 'text-accent font-semibold' : 'text-zinc-500'
                        }`}
                        title={isHere ? `Seite ${pageN} (aktuell)` : `Seite ${pageN}`}
                      >
                        S{pageN}
                      </span>
                    )}
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onDeleteScene(d.file); }}
                      className="text-[0.6rem] text-zinc-400 hover:text-red-700 px-1 py-0.5 rounded hover:bg-red-50"
                      title="Szene aus dem Datensatz entfernen"
                    >
                      ✕
                    </button>
                  </span>
                </button>
              </span>
            );
          }
          const b = it.draft;
          const kindLabel = b.kind == null
            ? 'Entwurf · ?'
            : `Entwurf · ${KIND_LABEL[b.kind]}${b.floor ? ` ${(FLOOR_LABEL as Record<string, string>)[b.floor] ?? b.floor}` : ''}${b.view ? ` ${(VIEW_LABEL as Record<string, string>)[b.view] ?? b.view}` : ''}`;
          const sel = b.id === selectedDraftId;
          return (
            <span ref={wrapRef} key={`d-${b.id}`} className={`relative inline-flex shrink-0 rounded-md ${hereCls}`}>
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
                      isHere ? 'text-accent font-semibold' : 'text-zinc-500'
                    }`}
                    title={isHere ? `Seite ${b.page} (aktuell)` : `Seite ${b.page}`}
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
  info, page, onPage, draftCount, extractedOnPage,
  pageInfo, onFullPage,
}: {
  info: PdfInfo | null;
  page: number;
  onPage: (n: number) => void;
  draftCount: number;
  extractedOnPage: number;
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
      {/* L1 D6 — keyboard/pointer hints folded into the shared cheatsheet
          (open with ?); auto-gespeichert timestamp dropped since A1 makes
          drafts transient. */}
    </div>
  );
}

function ExtractSidebar({
  info, currentPage, intake, dataset, draft, onPage,
  pageBboxes, selectedId, onSelectBbox, onUpdateBbox, onDeleteBbox,
  onDiscardDraft,
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
  onDiscardDraft: () => void;
}) {
  const totalDraft = draft.bboxes.length;
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

      {/* U11 — house-global facts the user already locked in. Visible at
          this level so they don't have to dive into a scene to check
          what's been captured. Read-only here; edits happen in
          AnnotatePage's workflow guide. */}
      <HouseFactsCard houseKey={intake?.key ?? ''} dataset={dataset} />

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

      {/* A1 — no more manual extract button. The post-draw classifier
          writes through to the server the moment the user picks the
          last required field. Unclassified drafts still need an
          escape hatch: classify them via the post-draw chip OR
          discard them. */}
      {totalDraft > 0 && (
        <section className="shrink-0 mt-3 border-t border-border pt-3 space-y-1">
          <p className="text-[0.65rem] text-zinc-500 text-center">
            {totalDraft} unklassifizierter Entwurf — Typ wählen, dann automatisch extrahiert.
          </p>
          <button
            type="button"
            onClick={onDiscardDraft}
            className="w-full text-[0.62rem] text-zinc-500 hover:text-red-700"
          >
            Entwürfe verwerfen
          </button>
        </section>
      )}
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
  postDraw, onPostDrawPick, onPostDrawDismiss, extractBusy, extractingIds,
  houseKey, onDeleteExtracted, onAdjustExtracted,
  menuFor, setMenuFor, onDatasetRefresh, onClassifyAction,
  showGrid,
}: {
  pdfKey: string;
  page: number;
  pageWidthPt: number;
  pageHeightPt: number;
  showGrid: boolean;
  draftBboxes: DraftBbox[];
  extracted: DatasetHouse['drawings'];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onCommit: (bbox: [number, number, number, number]) => void;
  onUpdate: (id: string, patch: Partial<DraftBbox>) => void;
  postDraw: { id: string; step: 'kind' | 'floor' | 'view' } | null;
  onPostDrawPick: (patch: Partial<DraftBbox>) => void;
  onPostDrawDismiss: () => void;
  extractBusy: boolean;
  extractingIds: Set<string>;
  houseKey: string;
  onDeleteExtracted: (file: string) => void;
  onAdjustExtracted: (file: string) => void;
  menuFor: { file: string; clientX?: number; clientY?: number } | null;
  setMenuFor: (m: { file: string; clientX?: number; clientY?: number } | null) => void;
  onDatasetRefresh: (manifest: DatasetHouse) => void;
  onClassifyAction: (
    file: string,
    before: { kind: string | null; floor: string | null; view: string | null; title: string | null },
    after:  { kind: string | null; floor: string | null; view: string | null; title: string | null },
  ) => void;
}) {
  const navigate = useNavigate();
  // Per-bbox single-click timers used to discriminate single click (open
  // menu) from double click (navigate to annotate). See the rect's
  // onClick / onDoubleClick handlers below.
  const clickTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
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
          src={showGrid
            ? pdfPageGridUrl(pdfKey, page, PAGE_DPI)
            : pdfPageUrl(pdfKey, page, PAGE_DPI)}
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
              back to a draft for re-cropping / delete). The "active" rect
              (matching menuFor) renders LAST so it sits on top of any
              overlapping siblings — SVG z-order is document order. */}
          {[...extracted].sort((a, b) => {
            const aSel = menuFor?.file === a.file ? 1 : 0;
            const bSel = menuFor?.file === b.file ? 1 : 0;
            return aSel - bSel;
          }).map((d) => {
            const cf = d.crop_from as { bbox_pdf_units?: [number, number, number, number] } | undefined;
            if (!cf?.bbox_pdf_units) return null;
            const [x0, y0, x1, y1] = cf.bbox_pdf_units;
            const isMenu = menuFor?.file === d.file;
            return (
              <g key={d.file}>
                <rect
                  x={x0} y={y0} width={x1 - x0} height={y1 - y0}
                  fill={isMenu ? 'rgba(16, 185, 129, 0.22)' : 'rgba(16, 185, 129, 0.10)'}
                  stroke={isMenu ? '#047857' : '#059669'}
                  strokeWidth={isMenu ? 3 : 1.5}
                  strokeDasharray="4 3"
                  style={{ pointerEvents: 'auto', cursor: 'pointer' }}
                  data-bbox-handle="extracted"
                  onPointerDown={(e) => {
                    e.stopPropagation();
                  }}
                  onClick={(e) => {
                    e.stopPropagation();
                    if (e.button !== 0) return;
                    // L4 D3 — capture click coords so the popover opens at
                    // the gesture, not at the bbox centroid.
                    const cx = e.clientX;
                    const cy = e.clientY;
                    const pending = clickTimers.current.get(d.file);
                    if (pending) clearTimeout(pending);
                    const t = setTimeout(() => {
                      clickTimers.current.delete(d.file);
                      setMenuFor(menuFor?.file === d.file ? null : { file: d.file, clientX: cx, clientY: cy });
                    }, 280);
                    clickTimers.current.set(d.file, t);
                  }}
                  onDoubleClick={(e) => {
                    e.stopPropagation();
                    const pending = clickTimers.current.get(d.file);
                    if (pending) { clearTimeout(pending); clickTimers.current.delete(d.file); }
                    setMenuFor(null);
                    navigate(`/${houseKey}/scene/${encodeURIComponent(d.file)}/annotate`);
                  }}
                >
                  <title>{`${d.file} — Klick = Aktionen · Doppelklick = Annotieren`}</title>
                </rect>
              </g>
            );
          })}
          {/* Draft bboxes. */}
          {draftBboxes.map((b) => {
            const [x0, y0, x1, y1] = b.bbox_pdf;
            const sel = b.id === selectedId;
            const extracting = extractingIds.has(b.id);
            return (
              <g key={b.id}>
                <BboxOverlay
                  bbox={[x0, y0, x1, y1]}
                  pageWidthPt={pageWidthPt}
                  pageHeightPt={pageHeightPt}
                  selected={sel}
                  onSelect={() => onSelect(b.id)}
                  onUpdate={(nx) => onUpdate(b.id, { bbox_pdf: nx })}
                />
                {/* L9 — in-flight extract decoration: pulsing emerald
                    fill on top of the orange draft, plus a ↻ glyph in
                    the centre. */}
                {extracting && (
                  <g pointerEvents="none">
                    <rect
                      x={x0} y={y0} width={x1 - x0} height={y1 - y0}
                      fill="rgba(16, 185, 129, 0.18)"
                      stroke="#10b981"
                      strokeWidth={2}
                      strokeDasharray="6 4"
                      className="animate-pulse"
                    />
                    <text
                      x={(x0 + x1) / 2}
                      y={(y0 + y1) / 2}
                      textAnchor="middle"
                      dominantBaseline="middle"
                      fontSize={Math.max(12, Math.min(x1 - x0, y1 - y0) * 0.10)}
                      fill="#047857"
                      style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}
                    >
                      ↻ extrahiere…
                    </text>
                  </g>
                )}
              </g>
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
              busy={extractBusy}
            />
          );
        })()}
        {/* Action menu for a clicked extracted-scene overlay. Anchored to
            the bbox's top edge; flips below for bboxes near y=0 so it's
            never clipped. */}
        {menuFor && (() => {
          const d = extracted.find((x) => x.file === menuFor.file);
          const cf = d?.crop_from as { bbox_pdf_units?: [number, number, number, number] } | undefined;
          if (!d || !cf?.bbox_pdf_units) return null;
          // L4 D3 — anchor at the click coords (viewport space) if we
          // have them; fall back to the bbox-centroid model otherwise
          // (e.g. when the menu was opened from a chip click).
          if (menuFor.clientX != null && menuFor.clientY != null) {
            return (
              <ExtractedSceneMenu
                anchor={{ kind: 'viewport', x: menuFor.clientX, y: menuFor.clientY }}
                houseKey={houseKey}
                drawing={d}
                onClose={() => setMenuFor(null)}
                onDelete={() => { setMenuFor(null); onDeleteExtracted(d.file); }}
                onAdjust={() => { setMenuFor(null); onAdjustExtracted(d.file); }}
                onUpdated={(manifest, change) => {
                  onDatasetRefresh(manifest);
                  if (change) onClassifyAction(d.file, change.before, change.after);
                }}
              />
            );
          }
          const [x0, y0, x1] = cf.bbox_pdf_units;
          const leftPct = (((x0 + x1) / 2) / pageWidthPt) * 100;
          const topPct = (y0 / pageHeightPt) * 100;
          const placement: 'above' | 'below' = topPct < 12 ? 'below' : 'above';
          return (
            <ExtractedSceneMenu
              anchor={{ kind: 'centroid', leftPct, topPct, placement }}
              houseKey={houseKey}
              drawing={d}
              onClose={() => setMenuFor(null)}
              onDelete={() => { setMenuFor(null); onDeleteExtracted(d.file); }}
              onAdjust={() => { setMenuFor(null); onAdjustExtracted(d.file); }}
              onUpdated={onDatasetRefresh}
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
// U11 — Read-only summary of house_facts (extent / heights / wall
// thickness / north orientation / workflow phase). Visible at the house
// level so the user doesn't have to dive into a scene to remember
// what's already locked in. Editing happens in AnnotatePage's
// WorkflowGuide; the ✏ link jumps there.
function HouseFactsCard({
  houseKey, dataset,
}: {
  houseKey: string;
  dataset: DatasetHouse | null;
}) {
  const [facts, setFacts] = useState<HouseFacts | null>(null);
  useEffect(() => {
    if (!houseKey) { setFacts(null); return; }
    let cancelled = false;
    void syncHouseFactsFromServer('dataset', houseKey).then((f) => {
      if (!cancelled) setFacts(f);
    });
    return () => { cancelled = true; };
  }, [houseKey]);
  if (!facts) return null;

  const extent = facts.extent;
  const haveExtent = extent.width_mm != null || extent.depth_mm != null || extent.height_mm != null;
  const heights = facts.heights ?? {};
  const heightRows: Array<[string, number]> = [];
  if (heights.bezug_mm != null)        heightRows.push(['±0', heights.bezug_mm]);
  if (heights.first_mm != null)        heightRows.push(['First', heights.first_mm]);
  if (heights.traufe_mm != null)       heightRows.push(['Traufe', heights.traufe_mm]);
  if (heights.gelaende_mm != null)     heightRows.push(['Gelände', heights.gelaende_mm]);
  if (heights.ok_ffb_eg_mm != null)    heightRows.push(['OK FFB EG', heights.ok_ffb_eg_mm]);
  if (heights.ok_ffb_og_mm != null)    heightRows.push(['OK FFB OG', heights.ok_ffb_og_mm]);
  if (heights.ok_ffb_dg_mm != null)    heightRows.push(['OK FFB DG', heights.ok_ffb_dg_mm]);
  const outerWall = facts.wall_thickness?.outer_mm ?? null;
  const orientationLabel = facts.orientation
    ? (facts.orientation.north_angle_deg != null
        ? `${facts.orientation.north_angle_deg.toFixed(0)}° (${facts.orientation.source_grundriss_file})`
        : `gesetzt (${facts.orientation.source_grundriss_file})`)
    : null;
  const wf = facts.workflow;
  const phaseIdx = wf ? PHASE_IDS.indexOf(wf.phase) : -1;
  const phaseLabel = wf && phaseIdx >= 0
    ? `Phase ${phaseIdx + 1} / ${PHASE_IDS.length} — ${wf.phase}`
    : null;

  // Pick a "jump into annotation" target — the most-recently-touched
  // scene of the house, falling back to the EG Grundriss or first
  // drawing. The pencil ✏ on each row drops the user into AnnotatePage
  // where each fact has its editor in the WorkflowGuide.
  const editTarget = dataset?.drawings?.[0]?.file;
  const editHref = editTarget
    ? `/${houseKey}/scene/${encodeURIComponent(editTarget)}/annotate`
    : null;

  const empty = !haveExtent && heightRows.length === 0 && outerWall == null && orientationLabel == null;
  if (empty) return null;
  return (
    <section className="shrink-0 mb-3 px-2.5 py-2 rounded-md border border-border bg-zinc-50/70">
      <div className="flex items-center justify-between mb-1.5">
        <h3 className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">
          Haus-Fakten
        </h3>
        {editHref && (
          <Link
            to={editHref}
            className="text-[0.6rem] text-zinc-500 hover:text-accent"
            title="Im Annotations-Editor bearbeiten"
          >
            ✏ bearbeiten
          </Link>
        )}
      </div>
      <dl className="space-y-0.5 text-[0.72rem]">
        {haveExtent && (
          <FactRow
            label="Ausdehnung"
            value={`${formatMm(extent.width_mm)} × ${formatMm(extent.depth_mm)}${
              extent.height_mm != null ? ` × ${formatMm(extent.height_mm)}` : ''
            }`}
          />
        )}
        {heightRows.length > 0 && (
          <FactRow
            label="Höhen"
            value={heightRows
              .map(([id, mm]) => `${id} ${formatMm(mm)}`)
              .join(' · ')}
          />
        )}
        {outerWall != null && (
          <FactRow label="Außenwand" value={`${(outerWall / 10).toFixed(1)} cm`} />
        )}
        {orientationLabel && (
          <FactRow label="Nord" value={orientationLabel} />
        )}
        {phaseLabel && (
          <FactRow label="Workflow" value={phaseLabel} />
        )}
      </dl>
    </section>
  );
}

function FactRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-muted shrink-0 w-[5.5rem]">{label}</dt>
      <dd className="font-medium truncate" title={value}>{value}</dd>
    </div>
  );
}

function formatMm(v: number | null | undefined): string {
  if (v == null) return '–';
  if (v >= 1000) return `${(v / 1000).toFixed(2).replace('.', ',')} m`;
  if (v >= 10) return `${(v / 10).toFixed(1)} cm`;
  return `${v} mm`;
}

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

type MenuAnchor =
  | { kind: 'viewport'; x: number; y: number }
  | { kind: 'centroid'; leftPct: number; topPct: number; placement: 'above' | 'below' };

// U9 + L4 D3 — Floating details popover for an already-extracted scene.
// Anchored to the user's click coordinate when available (viewport
// anchor); falls back to the bbox centroid (chip-click cases) so the
// chip-strip flow still works without a click coord.
function ExtractedSceneMenu({
  anchor, houseKey, drawing, onClose, onDelete, onAdjust, onUpdated,
}: {
  anchor: MenuAnchor;
  houseKey: string;
  drawing: DatasetHouse['drawings'][number];
  onClose: () => void;
  onDelete: () => void;
  onAdjust: () => void;
  onUpdated: (manifest: DatasetHouse, change?: {
    before: { kind: string | null; floor: string | null; view: string | null; title: string | null };
    after:  { kind: string | null; floor: string | null; view: string | null; title: string | null };
  }) => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  const positionStyle: React.CSSProperties = anchor.kind === 'viewport'
    ? { position: 'fixed', left: anchor.x, top: anchor.y, transform: 'translate(-50%, 8px)' }
    : {
        position: 'absolute',
        left: `${anchor.leftPct}%`,
        top: `${anchor.topPct}%`,
        transform: anchor.placement === 'above'
          ? 'translate(-50%, calc(-100% - 8px))'
          : 'translate(-50%, 8px)',
      };
  return (
    <>
      <button
        type="button"
        onClick={onClose}
        onPointerDown={(e) => e.stopPropagation()}
        className={anchor.kind === 'viewport' ? 'fixed inset-0 z-30 cursor-default' : 'absolute inset-0 z-20 cursor-default'}
        aria-label="Menü schließen"
        data-bbox-handle="menu"
      />
      <div
        className={`${anchor.kind === 'viewport' ? 'z-40' : 'z-30'} bg-white border border-zinc-300 rounded-md shadow-xl text-[0.78rem] min-w-[16rem]`}
        data-bbox-handle="menu"
        onPointerDown={(e) => e.stopPropagation()}
        style={positionStyle}
      >
        <SceneDetailsCard
          houseKey={houseKey}
          drawing={drawing}
          onAnnotateHref={`/${houseKey}/scene/${encodeURIComponent(drawing.file)}/annotate`}
          onAdjust={onAdjust}
          onDelete={onDelete}
          onUpdated={onUpdated}
          variant="full"
        />
      </div>
    </>
  );
}

function PostDrawChip({
  step, leftPct, topPct, placement, onPick, onDismiss, busy,
}: {
  step: 'kind' | 'floor' | 'view';
  leftPct: number;
  topPct: number;
  placement: 'above' | 'inside';
  onPick: (patch: Partial<DraftBbox>) => void;
  onDismiss: () => void;
  busy: boolean;
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
            disabled={busy}
            onClick={() => onPick(opt.patch)}
            className="px-2 py-0.5 rounded bg-zinc-100 hover:bg-accent hover:text-white text-[0.72rem] inline-flex items-center gap-1 disabled:opacity-40 disabled:cursor-wait"
          >
            <span>{opt.label}</span>
            <kbd className="text-[0.58rem] font-mono text-zinc-400">{opt.key}</kbd>
          </button>
        ))}
      </div>
      {busy && (
        <p className="text-[0.62rem] text-zinc-500 text-center -mt-0.5">
          ↻ extrahiere…
        </p>
      )}
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
