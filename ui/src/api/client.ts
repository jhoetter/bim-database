// Thin fetch wrappers + a couple of helper hooks. All endpoints come from
// the FastAPI in api/main.py — dev proxies to :2500, prod is same-origin.
//
// R0 — catalog ("houses") endpoints removed. Only the dataset path remains.
import { useEffect, useState } from 'react';
import type {
  LabelScope,
  SceneLabels,
  DatasetHouse,
  IncomingPdf,
  IncomingSubmission,
} from './types';

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${url}`);
  return r.json() as Promise<T>;
}

export function fetchDatasets(): Promise<DatasetHouse[]> {
  return get<DatasetHouse[]>('/datasets');
}

export function fetchDataset(key: string): Promise<DatasetHouse> {
  return get<DatasetHouse>(`/datasets/${key}`);
}

// R1 — PDF intake.

export function listIncomingPdfs(): Promise<IncomingPdf[]> {
  return get<IncomingPdf[]>('/pdfs/incoming');
}

export function getIncomingPdf(key: string): Promise<IncomingPdf> {
  return get<IncomingPdf>(`/pdfs/incoming/${encodeURIComponent(key)}`);
}

export async function uploadPdfs(
  files: File[], houseKey?: string, notes?: string,
): Promise<IncomingPdf> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  const qs = new URLSearchParams();
  if (houseKey) qs.set('house_key', houseKey);
  if (notes) qs.set('notes', notes);
  const url = `/pdfs?${qs}`;
  const r = await fetch(url, { method: 'POST', body: fd });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

// Customer submission queue (developer review).

export function listSubmissions(): Promise<IncomingSubmission[]> {
  return get<IncomingSubmission[]>('/pdfs/submissions');
}

export function getSubmission(id: string): Promise<IncomingSubmission> {
  return get<IncomingSubmission>(`/pdfs/submissions/${encodeURIComponent(id)}`);
}

export async function promoteSubmission(
  id: string,
  body: { house_key?: string; redact_title_block?: boolean; user_notes?: string } = {},
): Promise<{ promoted_to: string; consolidated_url: string | null; redacted: boolean }> {
  const r = await fetch(`/pdfs/submissions/${encodeURIComponent(id)}/promote`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

export async function deleteSubmission(id: string): Promise<void> {
  const r = await fetch(`/pdfs/submissions/${encodeURIComponent(id)}`, { method: 'DELETE' });
  if (!r.ok && r.status !== 204) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
}

export async function updateIncomingNotes(
  key: string, patch: { user_notes?: string; state?: string },
): Promise<IncomingPdf> {
  const r = await fetch(`/pdfs/incoming/${encodeURIComponent(key)}/manifest`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

export interface PdfInfo {
  key: string;
  page_count: number;
  pages: Array<{ page: number; width_pt: number; height_pt: number }>;
}

export function getPdfInfo(key: string): Promise<PdfInfo> {
  return get<PdfInfo>(`/pdfs/${encodeURIComponent(key)}/info`);
}

export function pdfPageUrl(key: string, page: number, dpi = 144): string {
  return `/pdfs/${encodeURIComponent(key)}/page/${page}?dpi=${dpi}`;
}

// Same PDF page rendered through the agentic-labeling grid overlay
// (image @ 0.5 opacity + 3-tier coordinate grid). Used by ExtractPage's
// "Raster" toggle so the developer sees what the labeling agent sees.
export function pdfPageGridUrl(
  key: string,
  page: number,
  dpi = 144,
  tiers: string = 'broad,finer,detail',
): string {
  return `/pdfs/${encodeURIComponent(key)}/page/${page}/grid?dpi=${dpi}&tiers=${encodeURIComponent(tiers)}`;
}

export function sceneGridUrl(
  key: string,
  file: string,
  tiers: string = 'broad,finer,detail',
): string {
  return `/datasets/${encodeURIComponent(key)}/${encodeURIComponent(file)}/grid?tiers=${encodeURIComponent(tiers)}`;
}

export interface ExtractItem {
  page: number;
  bbox_pdf_units: [number, number, number, number];
  kind: 'floorplan' | 'elevation' | 'section' | 'detail';
  view?: string | null;
  floor?: string | null;
  title?: string | null;
  slug_override?: string | null;
  dpi?: number;
}

export async function extractScenes(key: string, items: ExtractItem[]): Promise<{
  extracted: Array<{ file: string; kind: string; view?: string | null; floor?: string | null; title?: string | null; crop_from: { page: number; bbox_pdf_units: number[]; dpi: number; pdf_file: string } }>;
  intake_state: string;
}> {
  const r = await fetch(`/pdfs/${encodeURIComponent(key)}/extract`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

export async function deleteExtractedScene(key: string, file: string): Promise<void> {
  const r = await fetch(`/pdfs/${encodeURIComponent(key)}/extract/${encodeURIComponent(file)}`, {
    method: 'DELETE',
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
}

// A3 — restore a soft-deleted scene from the 1 h recycle bin.
// 410 Gone if the bundle has expired.
export async function restoreExtractedScene(key: string, file: string): Promise<DatasetHouse> {
  const r = await fetch(`/pdfs/${encodeURIComponent(key)}/extract/${encodeURIComponent(file)}/restore`, {
    method: 'POST',
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

// R4 — per-scene export preview.

export interface ExportPreview {
  status: 'ok' | 'insufficient_references' | 'degenerate';
  reason?: string | null;
  homography: {
    matrix: number[][];
    computed_from: string[];
    rectified_size_px: [number, number];
    rms_residual_px: number;
  } | null;
  raw_url: string;
  rectified_url: string | null;
  set_a: unknown[];
  set_b: unknown[];
  computed_from: string[];
  rms_residual_px: number;
}

export async function fetchExportPreview(key: string, file: string): Promise<ExportPreview> {
  const r = await fetch(
    `/exports/${encodeURIComponent(key)}/${encodeURIComponent(file)}/preview`,
    { method: 'POST' },
  );
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

// U13 — server-backed house_facts. UI keeps a localStorage cache for
// synchronous reads; the server file at data/dataset/<key>/house_facts.json
// is canonical.
export async function fetchHouseFactsRaw(key: string): Promise<unknown | null> {
  const r = await fetch(`/datasets/${encodeURIComponent(key)}/house_facts`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

export async function putHouseFactsRaw(key: string, facts: unknown): Promise<void> {
  const r = await fetch(`/datasets/${encodeURIComponent(key)}/house_facts`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(facts),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
}

// U9 — patch a single drawing's classification (kind/floor/view/title).
// Returns the freshly-loaded dataset manifest so the caller can update
// its render without a follow-up GET.
export async function patchSceneAttrs(
  key: string,
  file: string,
  patch: { kind?: string | null; floor?: string | null; view?: string | null; title?: string | null },
): Promise<DatasetHouse> {
  const r = await fetch(`/datasets/${encodeURIComponent(key)}/drawings/${encodeURIComponent(file)}`, {
    method: 'PATCH',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

// House-level reset — wipes every extracted scene + every label,
// keeping the intake bundle so the user can re-extract from the
// same PDF. Server endpoint: DELETE /datasets/<key>.
export async function resetHouse(key: string): Promise<void> {
  const r = await fetch(`/datasets/${encodeURIComponent(key)}`, { method: 'DELETE' });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
}

export async function deleteIncomingPdf(key: string): Promise<void> {
  const r = await fetch(`/pdfs/incoming/${encodeURIComponent(key)}`, {
    method: 'DELETE',
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
}

// Annotation labels — dataset-scoped only post-R0. The GET endpoint
// returns a freshly-constructed skeleton if no labels file exists yet,
// so the editor never has to handle a separate "new" path.

export function fetchLabels(scope: LabelScope, key: string, file: string): Promise<SceneLabels> {
  return get<SceneLabels>(`/labels/${scope}/${key}/${encodeURIComponent(file)}`);
}

export async function saveLabels(
  scope: LabelScope,
  key: string,
  file: string,
  payload: SceneLabels,
): Promise<{ saved: string; bytes: number }> {
  const r = await fetch(`/labels/${scope}/${key}/${encodeURIComponent(file)}`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

// Tiny hook helpers — no react-query dep for this scale. Re-fetches when
// dependencies change; errors surface as `error` so the caller decides UX.
export function useResource<T>(loader: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    loader()
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e as Error);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error, loading };
}
