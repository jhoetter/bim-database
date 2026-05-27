import {
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useLocation, useParams } from 'react-router';
import { fetchLabels, saveLabels, useResource } from '../api/client';
import type {
  DimensionNumberLabel,
  DimensionedDistanceLabel,
  Label,
  LabelScope,
  Point,
  SceneLabels,
  SceneTag,
} from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';

// M2 — Scene editor v0.
//
// Two label tools active here: dimensioned_distance (2-click line) and
// dimension_number (1-click anchor + text input). Pan with right-mouse-drag
// or shift+drag; zoom with mouse wheel. Tag chip in the left sidebar locks
// the scene's scene_tag. Right rail = inspector for the selected label.
// Save persists to disk via PUT /labels/{scope}/{key}/{file}; dirty
// indicator + Cmd/Ctrl+S + N=50 undo stack as specified in
// spec/annotation-tool.md §11.

const UNDO_LIMIT = 50;
const TAGS: SceneTag[] = ['grundriss', 'ansicht', 'schnitt', 'sonstiges', 'nicht_klassifiziert'];
const TAG_LABEL: Record<SceneTag, string> = {
  grundriss: 'Grundriss',
  ansicht: 'Ansicht',
  schnitt: 'Schnitt',
  sonstiges: 'Sonstiges',
  nicht_klassifiziert: '(nicht klassifiziert)',
};

type Tool = 'select' | 'dimensioned_distance' | 'dimension_number';

interface Snapshot {
  labels: Label[];
  scene_tag: SceneTag;
}

function uuid(): string {
  // RFC4122-ish; good enough for label ids
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// "1,75" → 1750 ; "1.75" → 1750 ; "9,40 m" → 9400 ; "1750" → 1750
function parseGermanNumber(text: string): number | null {
  const m = text.match(/(-?[\d.,]+)/);
  if (!m) return null;
  let s = m[1].replace(/\./g, '');     // drop thousands sep
  s = s.replace(',', '.');             // German decimal → ASCII
  const n = parseFloat(s);
  if (Number.isNaN(n)) return null;
  // Heuristic: if magnitude looks like metres, convert to mm.
  return n < 100 ? Math.round(n * 1000) : Math.round(n);
}

function nowIso(): string {
  return new Date().toISOString();
}

export function AnnotatePage() {
  const location = useLocation();
  const { key = '', file = '' } = useParams();
  const decodedFile = decodeURIComponent(file);

  // Scope: derived from URL prefix. /synthetic/... → synthetic; /house/... → house.
  const scope: LabelScope = location.pathname.startsWith('/synthetic/') ? 'synthetic' : 'house';
  const imageUrl =
    scope === 'synthetic'
      ? `/static/synthetic/${key}/${decodedFile}`
      : `/scene/${key}/${encodeURIComponent(decodedFile)}`;

  const { data, loading, error } = useResource(() => fetchLabels(scope, key, decodedFile), [scope, key, decodedFile]);

  // Editable state — initialised from `data` once it loads.
  const [labels, setLabels] = useState<Label[]>([]);
  const [sceneTag, setSceneTag] = useState<SceneTag>('nicht_klassifiziert');
  const [imageSize, setImageSize] = useState<[number, number]>([1024, 1024]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tool, setTool] = useState<Tool>('select');
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const undoStackRef = useRef<Snapshot[]>([]);

  // Drawing state — first click point for dimensioned_distance.
  const [pendingStart, setPendingStart] = useState<Point | null>(null);
  const [hoverPt, setHoverPt] = useState<Point | null>(null);

  // Pan/zoom on the SVG viewBox.
  const [view, setView] = useState({ x: 0, y: 0, w: 1024, h: 1024 });
  const svgRef = useRef<SVGSVGElement | null>(null);
  const panStateRef = useRef<{ startX: number; startY: number; vx: number; vy: number } | null>(null);

  useEffect(() => {
    if (data) {
      setLabels(data.labels ?? []);
      setSceneTag(data.scene_tag ?? 'nicht_klassifiziert');
      setImageSize(data.image_size_px ?? [1024, 1024]);
      setView({ x: 0, y: 0, w: data.image_size_px?.[0] ?? 1024, h: data.image_size_px?.[1] ?? 1024 });
      undoStackRef.current = [];
      setDirty(false);
    }
  }, [data]);

  // Warn on close if dirty
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
        e.returnValue = '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [dirty]);

  // ── snapshot / undo helpers ───────────────────────────────────────────────
  const pushUndo = useCallback(() => {
    undoStackRef.current.push({ labels: [...labels], scene_tag: sceneTag });
    if (undoStackRef.current.length > UNDO_LIMIT) {
      undoStackRef.current.shift();
    }
  }, [labels, sceneTag]);

  const undo = useCallback(() => {
    const snap = undoStackRef.current.pop();
    if (!snap) return;
    setLabels(snap.labels);
    setSceneTag(snap.scene_tag);
    setDirty(true);
    setSelectedId(null);
    setPendingStart(null);
  }, []);

  // ── coordinate helpers ────────────────────────────────────────────────────
  const eventToSvgPoint = useCallback((e: ReactPointerEvent<SVGSVGElement> | PointerEvent): Point | null => {
    const svg = svgRef.current;
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = (e as PointerEvent).clientX;
    pt.y = (e as PointerEvent).clientY;
    const ctm = svg.getScreenCTM();
    if (!ctm) return null;
    const local = pt.matrixTransform(ctm.inverse());
    return [local.x, local.y];
  }, []);

  // ── canvas event handlers ─────────────────────────────────────────────────
  const onCanvasPointerDown = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      // Right-mouse or shift+left → pan
      if (e.button === 2 || (e.button === 0 && e.shiftKey)) {
        e.preventDefault();
        const start = { startX: e.clientX, startY: e.clientY, vx: view.x, vy: view.y };
        panStateRef.current = start;
        const onMove = (mv: PointerEvent) => {
          const s = panStateRef.current;
          if (!s) return;
          const svg = svgRef.current;
          if (!svg) return;
          // Convert screen-pixel delta into SVG-coord delta.
          const screenScale = svg.clientWidth / view.w;
          const dx = (mv.clientX - s.startX) / screenScale;
          const dy = (mv.clientY - s.startY) / screenScale;
          setView({ ...view, x: s.vx - dx, y: s.vy - dy });
        };
        const onUp = () => {
          panStateRef.current = null;
          window.removeEventListener('pointermove', onMove);
          window.removeEventListener('pointerup', onUp);
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
        return;
      }
      if (e.button !== 0) return;
      const pt = eventToSvgPoint(e);
      if (!pt) return;

      if (tool === 'dimensioned_distance') {
        if (pendingStart == null) {
          setPendingStart(pt);
        } else {
          pushUndo();
          const label: DimensionedDistanceLabel = {
            id: uuid(),
            type: 'dimensioned_distance',
            geometry: { start: pendingStart, end: pt },
            attributes: { value_mm: null, target_orientation: 'unknown', is_reference: false },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          };
          setLabels([...labels, label]);
          setDirty(true);
          setPendingStart(null);
          setSelectedId(label.id);
        }
        return;
      }

      if (tool === 'dimension_number') {
        const text = window.prompt('Maßzahl-Text (z. B. "1,75"):');
        if (text == null || text.trim() === '') return;
        pushUndo();
        const label: DimensionNumberLabel = {
          id: uuid(),
          type: 'dimension_number',
          geometry: { anchor: pt },
          attributes: { text: text.trim(), parsed_value_mm: parseGermanNumber(text) },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels([...labels, label]);
        setDirty(true);
        setSelectedId(label.id);
        return;
      }

      // tool === 'select' — clicking background deselects
      setSelectedId(null);
    },
    [tool, pendingStart, labels, pushUndo, eventToSvgPoint, view],
  );

  const onCanvasPointerMove = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      if (tool === 'dimensioned_distance' && pendingStart) {
        const pt = eventToSvgPoint(e);
        if (pt) setHoverPt(pt);
      }
    },
    [tool, pendingStart, eventToSvgPoint],
  );

  const onCanvasWheel = useCallback(
    (e: ReactWheelEvent<SVGSVGElement>) => {
      e.preventDefault();
      const svg = svgRef.current;
      if (!svg) return;
      const pt = svg.createSVGPoint();
      pt.x = e.clientX;
      pt.y = e.clientY;
      const ctm = svg.getScreenCTM();
      if (!ctm) return;
      const local = pt.matrixTransform(ctm.inverse());
      const scale = Math.exp(-e.deltaY * 0.0015);
      const newW = Math.max(50, Math.min(view.w * scale, imageSize[0] * 8));
      const newH = newW * (view.h / view.w);
      const ratioX = (local.x - view.x) / view.w;
      const ratioY = (local.y - view.y) / view.h;
      setView({
        x: local.x - ratioX * newW,
        y: local.y - ratioY * newH,
        w: newW,
        h: newH,
      });
    },
    [view, imageSize],
  );

  const resetView = useCallback(() => {
    setView({ x: 0, y: 0, w: imageSize[0], h: imageSize[1] });
  }, [imageSize]);

  // ── label mutation helpers ────────────────────────────────────────────────
  const updateLabel = useCallback((id: string, patch: Partial<Label>) => {
    pushUndo();
    setLabels((prev) =>
      prev.map((l) => (l.id === id ? ({ ...l, ...patch, updated_at: nowIso() } as Label) : l)),
    );
    setDirty(true);
  }, [pushUndo]);

  const deleteLabel = useCallback((id: string) => {
    pushUndo();
    setLabels((prev) => prev.filter((l) => l.id !== id));
    if (selectedId === id) setSelectedId(null);
    setDirty(true);
  }, [pushUndo, selectedId]);

  // ── save ──────────────────────────────────────────────────────────────────
  const save = useCallback(async () => {
    if (!data) return;
    setSaving(true);
    try {
      const payload: SceneLabels = {
        ...data,
        schema_version: '1.0',
        scene_key: key,
        scene_file: decodedFile,
        scene_tag: sceneTag,
        image_size_px: imageSize,
        annotated_by: data.annotated_by ?? 'jhoetter',
        annotated_at: nowIso(),
        labels,
      };
      await saveLabels(scope, key, decodedFile, payload);
      setDirty(false);
    } catch (e) {
      window.alert(`Speichern fehlgeschlagen: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }, [data, key, decodedFile, sceneTag, labels, imageSize, scope]);

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        save();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        undo();
        return;
      }
      if (e.key === 'Escape') {
        setPendingStart(null);
        setSelectedId(null);
        return;
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedId) {
          e.preventDefault();
          deleteLabel(selectedId);
        }
      }
      if (e.key === 'd') setTool('dimensioned_distance');
      if (e.key === 'n') setTool('dimension_number');
      if (e.key === 's' && !e.metaKey && !e.ctrlKey) setTool('select');
      if (e.key === 'r') resetView();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [save, undo, selectedId, deleteLabel, resetView]);

  const selectedLabel = labels.find((l) => l.id === selectedId) ?? null;
  const viewBox = `${view.x} ${view.y} ${view.w} ${view.h}`;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: scope === 'synthetic' ? 'Synthetisch' : 'Alle Häuser', to: scope === 'synthetic' ? '/synthetic' : '/' },
            { label: key, to: scope === 'synthetic' ? `/synthetic/${key}` : `/house/${key}` },
            { label: `Annotieren: ${decodedFile}` },
          ]}
        />
      }
      topbarTrailing={
        <div className="flex items-center gap-2">
          {dirty && (
            <span className="text-[0.7rem] text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full">
              ● ungespeichert
            </span>
          )}
          <button
            type="button"
            disabled={!dirty || saving}
            onClick={save}
            className={`px-3 py-1 rounded-md text-[0.78rem] font-medium ${
              dirty
                ? 'bg-accent text-white hover:opacity-90'
                : 'bg-zinc-200 text-zinc-500 cursor-not-allowed'
            }`}
          >
            {saving ? 'Speichern…' : 'Speichern (Cmd+S)'}
          </button>
        </div>
      }
      leftSidebar={
        <ToolPalette
          tool={tool}
          setTool={setTool}
          sceneTag={sceneTag}
          setSceneTag={(t) => {
            pushUndo();
            setSceneTag(t);
            setDirty(true);
          }}
          labels={labels}
          selectedId={selectedId}
          onSelectLabel={setSelectedId}
          onUndo={undo}
          undoDepth={undoStackRef.current.length}
          onResetView={resetView}
        />
      }
      rightRail={
        selectedLabel ? (
          <Inspector
            label={selectedLabel}
            onChange={(patch) => updateLabel(selectedLabel.id, patch)}
            onDelete={() => deleteLabel(selectedLabel.id)}
          />
        ) : null
      }
      rightRailLabel={selectedLabel ? 'Inspector' : undefined}
      onCloseRightRail={() => setSelectedId(null)}
    >
      <div className="h-full bg-zinc-800 relative overflow-hidden">
        {loading && <p className="absolute top-4 left-4 text-white text-sm">Lade Labels…</p>}
        {error && <p className="absolute top-4 left-4 text-red-300 text-sm">Fehler: {error.message}</p>}
        <svg
          ref={svgRef}
          viewBox={viewBox}
          preserveAspectRatio="xMidYMid meet"
          className="w-full h-full select-none"
          onContextMenu={(e) => e.preventDefault()}
          onPointerDown={onCanvasPointerDown}
          onPointerMove={onCanvasPointerMove}
          onWheel={onCanvasWheel}
          style={{ cursor: tool === 'select' ? 'default' : 'crosshair' }}
        >
          <image href={imageUrl} x={0} y={0} width={imageSize[0]} height={imageSize[1]} />
          {/* Existing labels */}
          {labels.map((l) => (
            <LabelGlyph
              key={l.id}
              label={l}
              selected={l.id === selectedId}
              onSelect={() => {
                setSelectedId(l.id);
                setTool('select');
              }}
            />
          ))}
          {/* In-progress preview line */}
          {pendingStart && hoverPt && (
            <line
              x1={pendingStart[0]}
              y1={pendingStart[1]}
              x2={hoverPt[0]}
              y2={hoverPt[1]}
              stroke="#f59e0b"
              strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
              strokeDasharray="6,4"
            />
          )}
        </svg>
        <div className="absolute bottom-3 left-3 text-[0.7rem] text-zinc-300 bg-black/50 px-2 py-1 rounded leading-snug pointer-events-none">
          [D] Bemaßte Strecke · [N] Maßzahl · [S] Auswählen · [R] Reset View · Shift/Right-Drag = Pan · Wheel = Zoom
        </div>
      </div>
    </Shell>
  );
}

// ── tool palette + scene tag + label list ───────────────────────────────────

function ToolPalette({
  tool,
  setTool,
  sceneTag,
  setSceneTag,
  labels,
  selectedId,
  onSelectLabel,
  onUndo,
  undoDepth,
  onResetView,
}: {
  tool: Tool;
  setTool: (t: Tool) => void;
  sceneTag: SceneTag;
  setSceneTag: (t: SceneTag) => void;
  labels: Label[];
  selectedId: string | null;
  onSelectLabel: (id: string | null) => void;
  onUndo: () => void;
  undoDepth: number;
  onResetView: () => void;
}) {
  return (
    <div className="px-3 py-3 space-y-4">
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Szenen-Tag
        </h3>
        <div className="grid grid-cols-1 gap-px">
          {TAGS.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setSceneTag(t)}
              className={`px-2 py-1 rounded text-[0.78rem] text-left transition ${
                sceneTag === t
                  ? 'bg-accent text-white font-semibold'
                  : 'hover:bg-zinc-100'
              }`}
            >
              {TAG_LABEL[t]}
            </button>
          ))}
        </div>
      </section>

      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Werkzeuge
        </h3>
        <div className="grid grid-cols-1 gap-px">
          <ToolBtn current={tool} onSet={setTool} value="select" hotkey="S">
            Auswählen
          </ToolBtn>
          <ToolBtn current={tool} onSet={setTool} value="dimensioned_distance" hotkey="D">
            Bemaßte Strecke
          </ToolBtn>
          <ToolBtn current={tool} onSet={setTool} value="dimension_number" hotkey="N">
            Maßzahl
          </ToolBtn>
        </div>
      </section>

      <section>
        <div className="flex items-center gap-2 mb-1.5">
          <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">
            Labels{' '}
            <span className="text-zinc-400 font-normal">({labels.length})</span>
          </h3>
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              onClick={onUndo}
              disabled={undoDepth === 0}
              className="px-1.5 py-0.5 rounded text-[0.65rem] bg-zinc-100 hover:bg-zinc-200 text-zinc-700 disabled:opacity-40"
              title={`Rückgängig (${undoDepth} Schritt(e))`}
            >
              ↶ {undoDepth}
            </button>
            <button
              type="button"
              onClick={onResetView}
              className="px-1.5 py-0.5 rounded text-[0.65rem] bg-zinc-100 hover:bg-zinc-200 text-zinc-700"
              title="Reset View (R)"
            >
              View
            </button>
          </div>
        </div>
        {labels.length === 0 ? (
          <p className="text-[0.72rem] text-muted italic">Noch keine Labels.</p>
        ) : (
          <ul className="space-y-px">
            {labels.map((l) => (
              <li key={l.id}>
                <button
                  type="button"
                  onClick={() => onSelectLabel(l.id)}
                  className={`w-full text-left px-2 py-1 rounded text-[0.7rem] font-mono truncate ${
                    selectedId === l.id ? 'bg-accent/10 text-accent font-semibold' : 'hover:bg-zinc-100'
                  }`}
                  title={l.id}
                >
                  {labelGlyph(l)} {l.type}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ToolBtn({
  current,
  onSet,
  value,
  hotkey,
  children,
}: {
  current: Tool;
  onSet: (t: Tool) => void;
  value: Tool;
  hotkey: string;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onSet(value)}
      className={`flex items-center justify-between px-2 py-1 rounded text-[0.78rem] transition ${
        active ? 'bg-accent text-white font-semibold' : 'hover:bg-zinc-100'
      }`}
    >
      <span>{children}</span>
      <kbd
        className={`text-[0.6rem] font-mono rounded px-1 py-px ${
          active ? 'bg-white/20 text-white' : 'bg-zinc-200 text-zinc-600'
        }`}
      >
        {hotkey}
      </kbd>
    </button>
  );
}

function labelGlyph(l: Label): string {
  switch (l.type) {
    case 'dimensioned_distance': return '↔';
    case 'dimension_number': return '#';
    case 'wall': return '▭';
    case 'floorplan_opening': return '▢';
    case 'view_opening': return '⫷';
    case 'component_line': return '─';
    case 'height_mark': return '▲';
    default: return '·';
  }
}

// ── canvas glyph rendering ─────────────────────────────────────────────────

function LabelGlyph({
  label,
  selected,
  onSelect,
}: {
  label: Label;
  selected: boolean;
  onSelect: () => void;
}) {
  const stroke = selected ? '#dc2626' : '#16a34a';
  const fill = selected ? '#dc262633' : 'transparent';
  const sw = selected ? 3 : 2;

  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect();
  };

  if (label.type === 'dimensioned_distance') {
    const { start, end } = label.geometry;
    const refMark = label.attributes.is_reference ? '#f59e0b' : stroke;
    return (
      <g style={{ cursor: 'pointer' }} onClick={onClick}>
        <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke={refMark} strokeWidth={sw} />
        <Tick x={start[0]} y={start[1]} stroke={refMark} sw={sw} />
        <Tick x={end[0]} y={end[1]} stroke={refMark} sw={sw} />
      </g>
    );
  }
  if (label.type === 'dimension_number' && label.geometry.anchor) {
    const [x, y] = label.geometry.anchor;
    return (
      <g style={{ cursor: 'pointer' }} onClick={onClick}>
        <circle cx={x} cy={y} r={6} fill={fill} stroke={stroke} strokeWidth={sw} />
        <text
          x={x + 10}
          y={y - 6}
          fill={stroke}
          fontFamily="ui-monospace, monospace"
          fontSize={14}
          style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}
        >
          {label.attributes.text}
        </text>
      </g>
    );
  }
  return null;
}

function Tick({ x, y, stroke, sw }: { x: number; y: number; stroke: string; sw: number }) {
  return <circle cx={x} cy={y} r={4} fill={stroke} stroke={stroke} strokeWidth={sw} />;
}

// ── right-rail inspector for selected label ────────────────────────────────

function Inspector({
  label,
  onChange,
  onDelete,
}: {
  label: Label;
  onChange: (patch: Partial<Label>) => void;
  onDelete: () => void;
}) {
  return (
    <div className="p-4 space-y-4">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Label</div>
        <div className="text-[0.95rem] font-semibold">{label.type}</div>
        <div className="text-[0.65rem] text-zinc-400 font-mono break-all">{label.id}</div>
      </header>

      <section>
        <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">Status</h4>
        <select
          value={label.status}
          onChange={(e) => onChange({ status: e.target.value as Label['status'] })}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="readable">readable</option>
          <option value="not_readable">not_readable</option>
          <option value="missing">missing</option>
          <option value="uncertain">uncertain</option>
        </select>
      </section>

      {label.type === 'dimensioned_distance' && <DimensionedDistanceFields label={label} onChange={onChange} />}
      {label.type === 'dimension_number' && <DimensionNumberFields label={label} onChange={onChange} />}

      <section>
        <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">Notizen</h4>
        <textarea
          value={label.notes ?? ''}
          onChange={(e) => onChange({ notes: e.target.value || undefined } as Partial<Label>)}
          rows={3}
          className="w-full px-2 py-1 rounded border border-border text-[0.78rem]"
          placeholder="optional"
        />
      </section>

      <button
        type="button"
        onClick={onDelete}
        className="w-full px-3 py-1.5 rounded-md text-[0.78rem] font-medium bg-red-50 text-red-700 hover:bg-red-100 border border-red-200"
      >
        Löschen (Del)
      </button>
    </div>
  );
}

function DimensionedDistanceFields({
  label,
  onChange,
}: {
  label: DimensionedDistanceLabel;
  onChange: (patch: Partial<Label>) => void;
}) {
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Bemaßung</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Wert (mm)</span>
        <input
          type="number"
          value={label.attributes.value_mm ?? ''}
          onChange={(e) =>
            onChange({
              attributes: { ...label.attributes, value_mm: e.target.value === '' ? null : Number(e.target.value) },
            } as Partial<Label>)
          }
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
        />
      </label>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Soll-Orientierung</span>
        <select
          value={label.attributes.target_orientation}
          onChange={(e) =>
            onChange({ attributes: { ...label.attributes, target_orientation: e.target.value as DimensionedDistanceLabel['attributes']['target_orientation'] } } as Partial<Label>)
          }
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="horizontal">horizontal</option>
          <option value="vertical">vertical</option>
          <option value="unknown">unknown</option>
        </select>
      </label>
      <label className="flex items-center gap-2 text-[0.78rem]">
        <input
          type="checkbox"
          checked={label.attributes.is_reference}
          onChange={(e) =>
            onChange({ attributes: { ...label.attributes, is_reference: e.target.checked } } as Partial<Label>)
          }
        />
        Referenz-Strecke (anchors Homographie)
      </label>
    </section>
  );
}

function DimensionNumberFields({
  label,
  onChange,
}: {
  label: DimensionNumberLabel;
  onChange: (patch: Partial<Label>) => void;
}) {
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Maßzahl</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Text (wie geschrieben)</span>
        <input
          type="text"
          value={label.attributes.text ?? ''}
          onChange={(e) =>
            onChange({
              attributes: {
                ...label.attributes,
                text: e.target.value,
                parsed_value_mm: parseGermanNumber(e.target.value),
              },
            } as Partial<Label>)
          }
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
        />
      </label>
      <p className="text-[0.7rem] text-muted">
        Geparst: <span className="font-mono">{label.attributes.parsed_value_mm ?? '–'}</span> mm
      </p>
    </section>
  );
}
