import { createContext, useCallback, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';

// auto-persist follow-up — extract-side action log lifted to a React
// context so the stack survives navigation between scenes. Previously
// it lived in ExtractPage state and died when the user opened a scene
// editor and came back. Now the stack is keyed by houseKey and lives
// for the whole session.

export interface ExtractUndoContextValue {
  push: (houseKey: string, action: unknown) => void;
  popUndo: (houseKey: string) => unknown | undefined;
  popRedo: (houseKey: string) => unknown | undefined;
  pushRedo: (houseKey: string, action: unknown) => void;
  /** Reactive — re-renders the consumer when the stack changes. */
  undoDepth: (houseKey: string) => number;
  redoDepth: (houseKey: string) => number;
}

const Ctx = createContext<ExtractUndoContextValue | null>(null);

export function useExtractUndo(): ExtractUndoContextValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useExtractUndo() outside ExtractUndoProvider');
  return ctx;
}

const RING_LIMIT = 200;

export function ExtractUndoProvider({ children }: { children: ReactNode }) {
  const undo = useRef(new Map<string, unknown[]>()).current;
  const redo = useRef(new Map<string, unknown[]>()).current;
  // Per-house revision counter; bumped on every mutation so consumers
  // re-render. Plain Map in a state cell — only the wrapper is replaced
  // on bump.
  const [rev, setRev] = useState<Map<string, number>>(new Map());
  const bump = useCallback((houseKey: string) => {
    setRev((prev) => {
      const next = new Map(prev);
      next.set(houseKey, (prev.get(houseKey) ?? 0) + 1);
      return next;
    });
  }, []);

  const get = (m: Map<string, unknown[]>, k: string): unknown[] => {
    let s = m.get(k);
    if (!s) { s = []; m.set(k, s); }
    return s;
  };

  const push = useCallback((houseKey: string, action: unknown) => {
    const s = get(undo, houseKey);
    s.push(action);
    if (s.length > RING_LIMIT) s.shift();
    redo.set(houseKey, []);
    bump(houseKey);
  }, [undo, redo, bump]);

  const popUndo = useCallback((houseKey: string) => {
    const out = get(undo, houseKey).pop();
    bump(houseKey);
    return out;
  }, [undo, bump]);
  const popRedo = useCallback((houseKey: string) => {
    const out = get(redo, houseKey).pop();
    bump(houseKey);
    return out;
  }, [redo, bump]);
  const pushRedo = useCallback((houseKey: string, action: unknown) => {
    const s = get(redo, houseKey);
    s.push(action);
    if (s.length > RING_LIMIT) s.shift();
    bump(houseKey);
  }, [redo, bump]);
  // The rev read keeps the closure observed by useState, so consumers
  // re-render after every bump above.
  const undoDepth = useCallback((houseKey: string) => {
    void rev.get(houseKey);
    return get(undo, houseKey).length;
  }, [undo, rev]);
  const redoDepth = useCallback((houseKey: string) => {
    void rev.get(houseKey);
    return get(redo, houseKey).length;
  }, [redo, rev]);

  return (
    <Ctx.Provider value={{ push, popUndo, popRedo, pushRedo, undoDepth, redoDepth }}>
      {children}
    </Ctx.Provider>
  );
}
