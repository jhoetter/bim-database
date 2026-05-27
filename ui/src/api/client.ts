// Thin fetch wrappers + a couple of helper hooks. All endpoints come from
// the FastAPI in api/main.py — dev proxies to :2500, prod is same-origin.
import { useEffect, useState } from 'react';
import type { House, Ontology, SyntheticHouse } from './types';

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

export function fetchSynthetics(): Promise<SyntheticHouse[]> {
  return get<SyntheticHouse[]>('/synthetics');
}

export function fetchSynthetic(key: string): Promise<SyntheticHouse> {
  return get<SyntheticHouse>(`/synthetics/${key}`);
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
