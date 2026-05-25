import { type ReactNode, createContext, useContext } from 'react';
import { fetchOntology, useResource } from './client';
import type { Ontology } from './types';

const OntologyCtx = createContext<Ontology | null>(null);

export function OntologyProvider({ children }: { children: ReactNode }) {
  const { data, error, loading } = useResource(fetchOntology, []);
  if (loading) return <div className="p-12 text-sm text-muted">Lade Ontologie…</div>;
  if (error)
    return (
      <div className="p-12 text-sm text-red-700">
        Ontologie konnte nicht geladen werden: {error.message}
      </div>
    );
  return <OntologyCtx.Provider value={data}>{children}</OntologyCtx.Provider>;
}

export function useOntology(): Ontology {
  const onto = useContext(OntologyCtx);
  if (!onto) throw new Error('useOntology must be used inside <OntologyProvider>');
  return onto;
}

// Look up the display label for an enum value. Falls back to the raw key,
// so adding a new ontology entry never crashes the UI.
export function ontoLabel(onto: Ontology | null, group: string, key: string | null | undefined): string {
  if (!key) return '';
  return onto?.[group]?.[key] ?? key;
}
