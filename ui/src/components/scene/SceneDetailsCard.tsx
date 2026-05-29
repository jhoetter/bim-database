import { useEffect, useState } from 'react';
import { Link } from 'react-router';
import { patchSceneAttrs } from '../../api/client';
import type { DatasetDrawing, DatasetHouse } from '../../api/types';
import {
  KIND_LABEL, FLOOR_LABEL, VIEW_LABEL,
  SCENE_KINDS, SCENE_FLOORS, SCENE_VIEWS,
  chipKindLabel, chipReadinessColor,
  type SceneKind,
} from './sceneChip';

// U9 + U10 — single details surface for one scene. Renders the known
// attributes (kind, floor, view, title, page source, labeled status,
// readiness) and, in edit mode, the dropdowns + text input that update
// the dataset manifest via PATCH /datasets/{key}/drawings/{file}.
//
// Two call sites:
//   - ExtractedSceneMenu on the canvas (U9) — opens on bbox click
//   - AnnotatePage header strip (U10) — mirrors the same data + editor
// Both use this component so the user sees the same shape and only
// learns one editing pattern.

export interface SceneDetailsCardProps {
  houseKey: string;
  drawing: DatasetDrawing;
  /** Optional readiness derived from a labels summary fetch. */
  readiness?: { hasH: boolean; hasV: boolean };
  /** Called after a successful PATCH so the parent can refresh. */
  onUpdated?: (manifest: DatasetHouse) => void;
  /** Annotation / Adjust / Delete callbacks supplied by the host. The
   *  Annotieren link is rendered as a Link if provided. */
  onAnnotateHref?: string;
  onAdjust?: () => void;
  onDelete?: () => void;
  /** Render compact (header strip on AnnotatePage) or roomy (canvas
   *  popover). Compact hides the action footer. */
  variant?: 'compact' | 'full';
}

export function SceneDetailsCard({
  houseKey, drawing, readiness, onUpdated,
  onAnnotateHref, onAdjust, onDelete, variant = 'full',
}: SceneDetailsCardProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({
    kind: (drawing.kind ?? '') as '' | SceneKind,
    floor: drawing.floor ?? '',
    view: drawing.view ?? '',
    title: drawing.title ?? '',
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset draft when the underlying drawing changes (e.g. switching scenes).
  useEffect(() => {
    setDraft({
      kind: (drawing.kind ?? '') as '' | SceneKind,
      floor: drawing.floor ?? '',
      view: drawing.view ?? '',
      title: drawing.title ?? '',
    });
    setEditing(false);
  }, [drawing.file, drawing.kind, drawing.floor, drawing.view, drawing.title]);

  const onSave = async () => {
    setSaving(true);
    setError(null);
    try {
      const payload: Record<string, string | null> = {
        kind: draft.kind || null,
        title: draft.title || null,
      };
      payload.floor = draft.kind === 'floorplan' ? (draft.floor || null) : null;
      payload.view = (draft.kind === 'elevation' || draft.kind === 'section') ? (draft.view || null) : null;
      const fresh = await patchSceneAttrs(houseKey, drawing.file, payload);
      onUpdated?.(fresh);
      setEditing(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const cf = (drawing.crop_from as { page?: number } | undefined);
  const pageN = cf?.page ?? null;
  const labelText = chipKindLabel({
    kind: (drawing.kind as SceneKind | null) ?? null,
    floor: drawing.floor ?? null,
    view: drawing.view ?? null,
  });
  const labelCount = drawing.label_count ?? 0;
  const readinessColor = chipReadinessColor({ readiness });

  const headerCls = variant === 'compact'
    ? 'px-2 py-1 text-[0.7rem]'
    : 'px-3 py-1.5 text-[0.62rem] uppercase tracking-wider text-muted border-b border-border';

  return (
    <div className={variant === 'compact' ? '' : 'text-[0.78rem]'}>
      <div className={`${headerCls} flex items-center gap-2 truncate`} title={drawing.file}>
        <span className="truncate font-mono text-zinc-500">{drawing.file}</span>
        {pageN != null && (
          <span className="shrink-0 text-zinc-400">· Quelle S{pageN}</span>
        )}
      </div>
      {!editing && (
        <dl className={`${variant === 'compact' ? 'px-2 py-1 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 text-[0.7rem]' : 'px-3 py-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5'}`}>
          <dt className="text-muted">Typ</dt>
          <dd className="font-medium">{labelText}</dd>
          {drawing.title && (
            <>
              <dt className="text-muted">Titel</dt>
              <dd className="font-medium truncate" title={drawing.title}>{drawing.title}</dd>
            </>
          )}
          <dt className="text-muted">Status</dt>
          <dd className="font-medium">
            {drawing.labeled
              ? <span className="text-emerald-700">✓ annotiert{labelCount > 0 ? ` · ${labelCount} Labels` : ''}</span>
              : <span className="text-zinc-500">○ unannotiert</span>}
          </dd>
          {readiness && (
            <>
              <dt className="text-muted">Bezug</dt>
              <dd className="font-medium inline-flex items-center gap-1.5">
                {readinessColor && (
                  <span className="inline-block w-1.5 h-1.5 rounded-full" style={{ backgroundColor: readinessColor }} />
                )}
                <span>
                  {readiness.hasH && readiness.hasV ? 'H + V gesetzt'
                    : readiness.hasH ? 'nur H — V fehlt'
                    : readiness.hasV ? 'nur V — H fehlt'
                    : 'keine Bezugsmaße'}
                </span>
              </dd>
            </>
          )}
          <dt className="text-muted col-span-2 pt-1">
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="text-[0.65rem] text-accent hover:underline"
              title="Klassifikation bearbeiten"
            >
              ✏ Typ ändern
            </button>
          </dt>
        </dl>
      )}
      {editing && (
        <div className={`${variant === 'compact' ? 'px-2 py-1.5 space-y-1.5' : 'px-3 py-2 space-y-2'}`}>
          <label className="block">
            <span className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">Typ</span>
            <select
              value={draft.kind}
              onChange={(e) => setDraft((d) => ({ ...d, kind: e.target.value as '' | SceneKind, floor: '', view: '' }))}
              className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
            >
              <option value="">Typ wählen…</option>
              {SCENE_KINDS.map((k) => <option key={k} value={k}>{KIND_LABEL[k]}</option>)}
            </select>
          </label>
          {draft.kind === 'floorplan' && (
            <label className="block">
              <span className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">Geschoss</span>
              <select
                value={draft.floor}
                onChange={(e) => setDraft((d) => ({ ...d, floor: e.target.value }))}
                className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
              >
                <option value="">–</option>
                {SCENE_FLOORS.map((f) => <option key={f} value={f}>{FLOOR_LABEL[f] ?? f}</option>)}
              </select>
            </label>
          )}
          {(draft.kind === 'elevation' || draft.kind === 'section') && (
            <label className="block">
              <span className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">Himmelsrichtung</span>
              <select
                value={draft.view}
                onChange={(e) => setDraft((d) => ({ ...d, view: e.target.value }))}
                className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
              >
                <option value="">–</option>
                {SCENE_VIEWS.map((v) => <option key={v} value={v}>{VIEW_LABEL[v] ?? v}</option>)}
              </select>
            </label>
          )}
          <label className="block">
            <span className="text-[0.62rem] uppercase tracking-wider text-muted font-semibold">Titel (optional)</span>
            <input
              type="text"
              value={draft.title}
              onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
              className="w-full mt-0.5 px-2 py-1 border border-zinc-300 rounded text-[0.72rem]"
              placeholder="Freitext"
            />
          </label>
          {error && <p className="text-[0.65rem] text-red-700">{error}</p>}
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={onSave}
              disabled={saving}
              className="text-[0.7rem] px-2 py-1 rounded-md bg-accent text-white font-medium hover:opacity-90 disabled:opacity-40"
            >
              {saving ? 'Speichere…' : 'Übernehmen'}
            </button>
            <button
              type="button"
              onClick={() => { setEditing(false); setError(null); }}
              className="text-[0.65rem] text-zinc-500 hover:text-zinc-900"
            >
              Abbrechen
            </button>
          </div>
        </div>
      )}
      {variant === 'full' && !editing && (
        <div className="border-t border-border">
          {onAnnotateHref && (
            <Link
              to={onAnnotateHref}
              className="block px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
            >
              ↗ Annotieren
            </Link>
          )}
          {onAdjust && (
            <button
              type="button"
              onClick={onAdjust}
              className="block w-full text-left px-3 py-1.5 hover:bg-zinc-100 text-zinc-800"
            >
              ↔ Bbox anpassen
              <div className="text-[0.62rem] text-zinc-500">Wird zum Entwurf — danach erneut extrahieren.</div>
            </button>
          )}
          {onDelete && (
            <button
              type="button"
              onClick={onDelete}
              className="block w-full text-left px-3 py-1.5 hover:bg-red-50 text-red-700"
            >
              ✕ Szene löschen
            </button>
          )}
        </div>
      )}
    </div>
  );
}
