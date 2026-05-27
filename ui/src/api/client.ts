// Thin fetch wrappers + a couple of helper hooks. All endpoints come from
// the FastAPI in api/main.py — dev proxies to :2500, prod is same-origin.
import { useEffect, useState } from 'react';
import type { House, LabelScope, Ontology, SceneLabels, DatasetHouse } from './types';

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} on ${url}`);
  return r.json() as Promise<T>;
}

export function fetchHouses(params: Record<string, string>): Promise<House[]> {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== '') p.set(k, v);
  return get<House[]>(`/houses?${p}`);
}

export function fetchHouse(key: string): Promise<House> {
  return get<House>(`/houses/${key}`);
}

export function fetchOntology(): Promise<Ontology> {
  return get<Ontology>('/ontology');
}

export async function setDatasetStarred(
  key: string,
  starred: boolean,
): Promise<{ key: string; dataset_starred: boolean; materialized: string | null }> {
  const r = await fetch(`/houses/${key}/dataset_starred`, {
    method: 'PUT',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ starred }),
  });
  if (!r.ok) {
    const detail = await r.text().catch(() => '');
    throw new Error(`${r.status} ${r.statusText}: ${detail}`);
  }
  return r.json();
}

export function fetchDatasets(): Promise<DatasetHouse[]> {
  return get<DatasetHouse[]>('/datasets');
}

export function fetchDataset(key: string): Promise<DatasetHouse> {
  return get<DatasetHouse>(`/datasets/${key}`);
}

// Annotation labels — works for both 'dataset' and 'house' scopes. The
// GET endpoint returns a freshly-constructed skeleton if no labels file
// exists yet, so the editor never has to handle a separate "new" path.

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
