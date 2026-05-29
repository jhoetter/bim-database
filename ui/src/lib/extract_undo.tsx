import { createContext, useCallback, useContext, useRef } from 'react';
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
  }, [undo, redo]);

  const popUndo = useCallback((houseKey: string) => get(undo, houseKey).pop(), [undo]);
  const popRedo = useCallback((houseKey: string) => get(redo, houseKey).pop(), [redo]);
  const pushRedo = useCallback((houseKey: string, action: unknown) => {
    const s = get(redo, houseKey);
    s.push(action);
    if (s.length > RING_LIMIT) s.shift();
  }, [redo]);
  const undoDepth = useCallback((houseKey: string) => get(undo, houseKey).length, [undo]);
  const redoDepth = useCallback((houseKey: string) => get(redo, houseKey).length, [redo]);

  return (
    <Ctx.Provider value={{ push, popUndo, popRedo, pushRedo, undoDepth, redoDepth }}>
      {children}
    </Ctx.Provider>
  );
}
