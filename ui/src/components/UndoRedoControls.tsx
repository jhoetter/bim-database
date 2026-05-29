// Small inline ↶ / ↷ controls. Designed for the breadcrumb area
// next to the SaveStateDot so the user can see the stack at a glance
// AND fire undo / redo without remembering Cmd+Z.

interface UndoRedoControlsProps {
  undoDepth: number;
  redoDepth: number;
  onUndo: () => void;
  onRedo: () => void;
  /** Description appended to the title — e.g. "Label" or "Szene". */
  what?: string;
}

export function UndoRedoControls({
  undoDepth, redoDepth, onUndo, onRedo, what = 'Aktion',
}: UndoRedoControlsProps) {
  return (
    <span className="inline-flex items-center gap-0.5 select-none">
      <button
        type="button"
        onClick={onUndo}
        disabled={undoDepth === 0}
        title={`Letzte ${what} rückgängig (Cmd+Z) — ${undoDepth} im Stapel`}
        aria-label="Rückgängig"
        className="inline-flex items-center gap-0.5 text-[0.65rem] px-1.5 py-0.5 rounded hover:bg-zinc-100 text-zinc-600 disabled:opacity-30 disabled:cursor-not-allowed tabular-nums"
      >
        <span>↶</span>
        <span>{undoDepth}</span>
      </button>
      <button
        type="button"
        onClick={onRedo}
        disabled={redoDepth === 0}
        title={`${what} wiederherstellen (Cmd+Shift+Z) — ${redoDepth} im Stapel`}
        aria-label="Wiederherstellen"
        className="inline-flex items-center gap-0.5 text-[0.65rem] px-1.5 py-0.5 rounded hover:bg-zinc-100 text-zinc-600 disabled:opacity-30 disabled:cursor-not-allowed tabular-nums"
      >
        <span>↷</span>
        <span>{redoDepth}</span>
      </button>
    </span>
  );
}
