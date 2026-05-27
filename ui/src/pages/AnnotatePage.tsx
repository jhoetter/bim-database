import {
  type JSX,
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
  ComponentLineLabel,
  DimensionNumberLabel,
  DimensionedDistanceLabel,
  FloorplanOpeningLabel,
  HeightMarkLabel,
  Label,
  LabelScope,
  Point,
  Quad,
  SceneLabels,
  SceneTag,
  ViewOpeningLabel,
  WallLabel,
} from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import {
  handlesFor,
  moveHandle,
  translateLabelGeometry,
  type HandleSpec,
} from '../lib/labelGeometry';
import { findSnap, SNAP_COLOR, type SnapTarget, type SnapTool } from '../lib/snap';

const SNAP_SCREEN_RADIUS = 14;  // pixels of screen feel — see spec/annotation-ux.md §4

// M2+M3 — Scene editor. All 7 label types implemented; tool palette is
// gated by scene_tag (Grundriss vs Ansicht/Schnitt vs Sonstiges).
//
// Pan with right-mouse-drag or shift+drag; zoom with mouse wheel. Tag chip
// in the left sidebar locks the scene's scene_tag. Right rail = inspector
// for the selected label. Save persists to disk via PUT /labels/{scope}/...
// Dirty indicator + Cmd/Ctrl+S + N=50 undo stack (see
// spec/annotation-tool.md §11).

const UNDO_LIMIT = 200;                    // raised from 50 per spec §17 M9
const WALL_PX_PER_MM = 0.05;               // visual scale for wall band; pragmatic for h-1's ~10 m × 1024 px
const STANDARD_THICKNESS_MM = [115, 175, 240, 300, 365] as const;
const TAGS: SceneTag[] = ['grundriss', 'ansicht', 'schnitt', 'sonstiges', 'nicht_klassifiziert'];
const TAG_LABEL: Record<SceneTag, string> = {
  grundriss: 'Grundriss',
  ansicht: 'Ansicht',
  schnitt: 'Schnitt',
  sonstiges: 'Sonstiges',
  nicht_klassifiziert: '(nicht klassifiziert)',
};

type Tool =
  | 'select'
  | 'dimensioned_distance'
  | 'dimension_number'
  | 'wall'
  | 'floorplan_opening'
  | 'view_opening'
  | 'component_line'
  | 'height_mark'
  | 'link';

// Tag → which tools are available. Dimensioned_distance + dimension_number
// + select + link are always available where labels can exist; the rest
// depend on the scene's tag.
const TOOLS_BY_TAG: Record<SceneTag, Tool[]> = {
  grundriss: [
    'select', 'wall', 'floorplan_opening',
    'dimensioned_distance', 'dimension_number', 'link',
  ],
  ansicht: [
    'select', 'view_opening', 'component_line', 'height_mark',
    'dimensioned_distance', 'dimension_number', 'link',
  ],
  schnitt: [
    'select', 'view_opening', 'component_line', 'height_mark',
    'dimensioned_distance', 'dimension_number', 'link',
  ],
  sonstiges: [
    'select', 'wall', 'floorplan_opening', 'view_opening',
    'component_line', 'height_mark',
    'dimensioned_distance', 'dimension_number', 'link',
  ],
  nicht_klassifiziert: ['select'],
};

const TOOL_LABEL: Record<Tool, { label: string; hotkey: string }> = {
  select: { label: 'Auswählen', hotkey: 'S' },
  wall: { label: 'Wand', hotkey: 'W' },
  floorplan_opening: { label: 'Öffnung (Grundriss)', hotkey: 'O' },
  view_opening: { label: 'Öffnung (Ansicht)', hotkey: 'O' },
  component_line: { label: 'Bauteillinie', hotkey: 'L' },
  height_mark: { label: 'Höhenkote', hotkey: 'H' },
  dimensioned_distance: { label: 'Bemaßte Strecke', hotkey: 'D' },
  dimension_number: { label: 'Maßzahl', hotkey: 'N' },
  link: { label: 'Verknüpfen 🔗', hotkey: 'K' },
};

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
  const redoStackRef = useRef<Snapshot[]>([]);

  // Drawing state.
  // - pendingStart: first click of a 2-click tool (wall, dim-distance,
  //   floorplan_opening, view_opening).
  // - pendingPolyline: in-progress polyline being assembled click-by-click
  //   (component_line). Enter finishes; Esc cancels.
  // - hoverPt: cursor position in image-pixel coords, used for live preview.
  // - linkSource: the first label id clicked in link mode; the second click
  //   on the complementary type creates the labels-relation.
  const [pendingStart, setPendingStart] = useState<Point | null>(null);
  const [pendingPolyline, setPendingPolyline] = useState<Point[]>([]);
  const [hoverPt, setHoverPt] = useState<Point | null>(null);
  const [linkSource, setLinkSource] = useState<string | null>(null);
  // Snap target computed on every pointermove during drawing — render at
  // §15's green circle if non-null, and use as the actual commit point on
  // the next click instead of the raw cursor.
  const [snap, setSnap] = useState<SnapTarget | null>(null);

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

  // ── snapshot / undo / redo helpers ────────────────────────────────────────
  // Any new action that pushes onto undo clears the redo stack — standard
  // "branch on edit" semantics.
  const pushUndo = useCallback(() => {
    undoStackRef.current.push({ labels: [...labels], scene_tag: sceneTag });
    if (undoStackRef.current.length > UNDO_LIMIT) {
      undoStackRef.current.shift();
    }
    redoStackRef.current = [];
  }, [labels, sceneTag]);

  const undo = useCallback(() => {
    const snap = undoStackRef.current.pop();
    if (!snap) return;
    redoStackRef.current.push({ labels: [...labels], scene_tag: sceneTag });
    setLabels(snap.labels);
    setSceneTag(snap.scene_tag);
    setDirty(true);
    setSelectedId(null);
    setPendingStart(null);
  }, [labels, sceneTag]);

  const redo = useCallback(() => {
    const snap = redoStackRef.current.pop();
    if (!snap) return;
    undoStackRef.current.push({ labels: [...labels], scene_tag: sceneTag });
    setLabels(snap.labels);
    setSceneTag(snap.scene_tag);
    setDirty(true);
    setSelectedId(null);
    setPendingStart(null);
  }, [labels, sceneTag]);

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
      const rawPt = eventToSvgPoint(e);
      if (!rawPt) return;
      // Always prefer the snapped point if the snap engine produced one.
      // §4 of spec/annotation-ux.md — snap is the default; Alt is the
      // universal "no snap" override and is already baked into snap = null.
      const pt = snap?.pt ?? rawPt;

      // ── 2-click tools (line) ─────────────────────────────────────────────
      if (tool === 'dimensioned_distance' || tool === 'wall') {
        if (pendingStart == null) {
          setPendingStart(pt);
          return;
        }
        pushUndo();
        let label: Label;
        if (tool === 'dimensioned_distance') {
          label = {
            id: uuid(),
            type: 'dimensioned_distance',
            geometry: { start: pendingStart, end: pt },
            attributes: { value_mm: null, target_orientation: 'unknown', is_reference: false },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as DimensionedDistanceLabel;
        } else {
          label = {
            id: uuid(),
            type: 'wall',
            geometry: { start: pendingStart, end: pt },
            attributes: { thickness_mm: 365 },  // sensible residential default
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as WallLabel;
        }
        setLabels([...labels, label]);
        setDirty(true);
        setPendingStart(null);
        setSelectedId(label.id);
        return;
      }

      // ── 2-click tools (rectangle) ────────────────────────────────────────
      if (tool === 'floorplan_opening' || tool === 'view_opening') {
        if (pendingStart == null) {
          setPendingStart(pt);
          return;
        }
        pushUndo();
        // Build axis-aligned rectangle from the diagonal {pendingStart → pt}.
        // First-pass simplification: openings are usually rectangular; the
        // schema's polyline variants stay available for later cleanup.
        const x0 = Math.min(pendingStart[0], pt[0]);
        const y0 = Math.min(pendingStart[1], pt[1]);
        const x1 = Math.max(pendingStart[0], pt[0]);
        const y1 = Math.max(pendingStart[1], pt[1]);
        let label: Label;
        if (tool === 'floorplan_opening') {
          const quad: Quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
          label = {
            id: uuid(),
            type: 'floorplan_opening',
            geometry: { quad },
            attributes: {
              opening_kind: 'window',
              width_mm: null,
              swing: 'none',
              swing_side: 'none',
            },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as FloorplanOpeningLabel;
        } else {
          // View opening: degenerate-polyline pair (the rectangle's top + bottom edges).
          label = {
            id: uuid(),
            type: 'view_opening',
            geometry: {
              top_edge: [[x0, y0], [x1, y0]],
              bottom_edge: [[x0, y1], [x1, y1]],
            },
            attributes: { opening_kind: 'window', frame_visible: true },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as ViewOpeningLabel;
        }
        setLabels([...labels, label]);
        setDirty(true);
        setPendingStart(null);
        setSelectedId(label.id);
        return;
      }

      // ── Polyline tool (component_line) ────────────────────────────────────
      if (tool === 'component_line') {
        // Each click appends a vertex. Enter finishes; Esc cancels.
        setPendingPolyline((prev) => [...prev, pt]);
        return;
      }

      // ── 1-click tools ─────────────────────────────────────────────────────
      if (tool === 'height_mark') {
        const text = window.prompt('Höhenkote-Wert (m oder mm, z. B. "+ 2,75"):');
        if (text == null) return;
        const parsed = parseGermanNumber(text);
        pushUndo();
        const label: HeightMarkLabel = {
          id: uuid(),
          type: 'height_mark',
          geometry: { anchor: pt },
          attributes: { value_mm: parsed, reference_line_id: null },
          status: 'readable',
          relations: [],
          notes: text.trim() || undefined,
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels([...labels, label]);
        setDirty(true);
        setSelectedId(label.id);
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
    [tool, pendingStart, labels, pushUndo, eventToSvgPoint, view, snap],
  );

  const onCanvasPointerMove = useCallback(
    (e: ReactPointerEvent<SVGSVGElement>) => {
      const previewableWithStart =
        (tool === 'dimensioned_distance' || tool === 'wall' ||
         tool === 'floorplan_opening' || tool === 'view_opening') && pendingStart != null;
      const previewablePoly = tool === 'component_line' && pendingPolyline.length > 0;
      const previewSingle =
        (tool === 'dimension_number' || tool === 'height_mark' ||
         tool === 'floorplan_opening' || tool === 'view_opening' ||
         tool === 'component_line') && pendingStart == null;

      const pt = eventToSvgPoint(e);
      if (!pt) return;

      if (previewableWithStart || previewablePoly || previewSingle) {
        setHoverPt(pt);
      }

      // Snap evaluation — even for single-click tools we want a snap target.
      // For 'select' tool, skip (M11 will handle snap-on-drag).
      const drawingTools: SnapTool[] = [
        'wall', 'dimensioned_distance', 'dimension_number',
        'floorplan_opening', 'view_opening', 'component_line', 'height_mark',
      ];
      if (drawingTools.includes(tool as SnapTool)) {
        const svg = svgRef.current;
        const screenW = svg?.clientWidth ?? 1;
        const imageRadiusPx = (SNAP_SCREEN_RADIUS * view.w) / Math.max(1, screenW);
        const target = findSnap({
          cursor: pt,
          pendingStart,
          tool: tool as SnapTool,
          labels,
          imageRadiusPx,
          modifiers: { shift: e.shiftKey, alt: e.altKey },
        });
        setSnap(target);
      } else if (snap) {
        setSnap(null);
      }
    },
    [tool, pendingStart, pendingPolyline, eventToSvgPoint, labels, view.w, snap],
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
    setLabels((prev) =>
      prev
        .filter((l) => l.id !== id)
        // Also strip any relations pointing at the deleted label.
        .map((l) => ({
          ...l,
          relations: (l.relations ?? []).filter((r) => r.other_id !== id),
        }) as Label),
    );
    if (selectedId === id) setSelectedId(null);
    setDirty(true);
  }, [pushUndo, selectedId]);

  // Establish a labels-relation between a dimension_number and a
  // dimensioned_distance. The relation always lives on the number side, so
  // the source ↔ target asymmetry doesn't matter — we figure out which is
  // which and put the relation on the number.
  const linkPair = useCallback(
    (idA: string, idB: string) => {
      if (idA === idB) return;
      const a = labels.find((l) => l.id === idA);
      const b = labels.find((l) => l.id === idB);
      if (!a || !b) return;
      const allowedTypes: Label['type'][] = ['dimension_number', 'dimensioned_distance'];
      if (!allowedTypes.includes(a.type) || !allowedTypes.includes(b.type)) return;
      if (a.type === b.type) return;
      const numberId = a.type === 'dimension_number' ? a.id : b.id;
      const distanceId = a.type === 'dimensioned_distance' ? a.id : b.id;
      pushUndo();
      setLabels((prev) =>
        prev.map((l) => {
          if (l.id !== numberId) return l;
          const existing = l.relations ?? [];
          // Idempotent: don't add a duplicate.
          if (existing.some((r) => r.other_id === distanceId && r.kind === 'labels')) {
            return l;
          }
          return { ...l, relations: [...existing, { other_id: distanceId, kind: 'labels' }] } as Label;
        }),
      );
      setDirty(true);
    },
    [labels, pushUndo],
  );

  const unlinkPair = useCallback(
    (numberId: string, distanceId: string) => {
      pushUndo();
      setLabels((prev) =>
        prev.map((l) =>
          l.id === numberId
            ? ({
                ...l,
                relations: (l.relations ?? []).filter(
                  (r) => !(r.other_id === distanceId && r.kind === 'labels'),
                ),
              } as Label)
            : l,
        ),
      );
      setDirty(true);
    },
    [pushUndo],
  );

  // ── save ──────────────────────────────────────────────────────────────────
  const save = useCallback(async () => {
    if (!data) return;
    setSaving(true);
    try {
      const anomalies = collectAnomalies(labels);
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
        anomalies: anomalies.length > 0 ? anomalies : undefined,
      };
      await saveLabels(scope, key, decodedFile, payload);
      setDirty(false);
    } catch (e) {
      window.alert(`Speichern fehlgeschlagen: ${(e as Error).message}`);
    } finally {
      setSaving(false);
    }
  }, [data, key, decodedFile, sceneTag, labels, imageSize, scope]);

  // Anomaly extractor — currently only the dim_number ↔ dim_distance
  // value-mismatch check. Other rules can pile in here later.
  function collectAnomalies(ls: Label[]): string[] {
    const out: string[] = [];
    for (const l of ls) {
      if (l.type !== 'dimension_number') continue;
      for (const r of l.relations ?? []) {
        if (r.kind !== 'labels') continue;
        const other = ls.find((x) => x.id === r.other_id);
        if (!other || other.type !== 'dimensioned_distance') continue;
        const numV = l.attributes.parsed_value_mm;
        const distV = other.attributes.value_mm;
        if (numV == null || distV == null) continue;
        const rel = Math.abs(numV - distV) / Math.max(1, Math.abs(distV));
        if (rel >= 0.05) {
          out.push(
            `dimension_number "${l.attributes.text}" (${numV} mm) ↔ dimensioned_distance ${distV} mm — Differenz ${Math.abs(numV - distV)} mm`,
          );
        }
      }
    }
    return out;
  }

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
        if (e.shiftKey) redo();
        else undo();
        return;
      }
      if (e.key === 'Escape') {
        setPendingStart(null);
        setPendingPolyline([]);
        setLinkSource(null);
        setSelectedId(null);
        setSnap(null);
        return;
      }
      if (e.key === 'Enter' && tool === 'component_line' && pendingPolyline.length >= 2) {
        e.preventDefault();
        pushUndo();
        const label: ComponentLineLabel = {
          id: uuid(),
          type: 'component_line',
          geometry: { polyline: pendingPolyline },
          attributes: { line_kind: 'other' },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels((prev) => [...prev, label]);
        setDirty(true);
        setSelectedId(label.id);
        setPendingPolyline([]);
        return;
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        if (selectedId) {
          e.preventDefault();
          deleteLabel(selectedId);
        }
      }
      // Wall-only: ← / → adjusts thickness (10 mm step, 50 with Shift).
      // For other types these will fall through to drawing-tool hotkeys
      // (none of which currently use bare arrow keys).
      if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && selectedId) {
        const sel = labels.find((l) => l.id === selectedId);
        if (sel?.type === 'wall') {
          e.preventDefault();
          const step = e.shiftKey ? 50 : 10;
          const delta = e.key === 'ArrowRight' ? step : -step;
          const next = Math.max(50, Math.min(800, (sel.attributes.thickness_mm ?? 365) + delta));
          updateLabel(sel.id, { attributes: { thickness_mm: next } } as Partial<Label>);
        }
      }
      // Tool hotkeys — only switch if the new tool is allowed under the
      // current scene_tag.
      const allowed = TOOLS_BY_TAG[sceneTag];
      const trySetTool = (t: Tool) => {
        if (allowed.includes(t)) setTool(t);
      };
      if (e.key === 'd') trySetTool('dimensioned_distance');
      if (e.key === 'n') trySetTool('dimension_number');
      if (e.key === 'w') trySetTool('wall');
      if (e.key === 'o') trySetTool(sceneTag === 'grundriss' ? 'floorplan_opening' : 'view_opening');
      if (e.key === 'l') trySetTool('component_line');
      if (e.key === 'h' && !e.metaKey && !e.ctrlKey) trySetTool('height_mark');
      if (e.key === 'k' && !e.metaKey && !e.ctrlKey) trySetTool('link');
      if (e.key === 's' && !e.metaKey && !e.ctrlKey) trySetTool('select');
      if (e.key === 'r') resetView();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [save, undo, redo, selectedId, deleteLabel, resetView, tool, pendingPolyline, pushUndo, sceneTag, labels, updateLabel]);

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
          <a
            href={location.pathname.replace('/annotate', '/preview')}
            className="px-3 py-1 rounded-md text-[0.78rem] font-medium bg-white text-zinc-900 border border-border hover:border-zinc-400"
            title="Vorschau der beiden Ground Truths + ZIP-Export"
          >
            Vorschau & Export →
          </a>
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
      rightRailMode="overlay-pinnable"
      rightRail={
        selectedLabel ? (
          <Inspector
            label={selectedLabel}
            allLabels={labels}
            onChange={(patch) => updateLabel(selectedLabel.id, patch)}
            onDelete={() => deleteLabel(selectedLabel.id)}
            onUnlink={(otherId) => {
              // unlink: figure out which side carries the relation
              const sel = selectedLabel;
              if (sel.type === 'dimension_number') {
                unlinkPair(sel.id, otherId);
              } else if (sel.type === 'dimensioned_distance') {
                unlinkPair(otherId, sel.id);
              }
            }}
            onSelectId={setSelectedId}
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
          {/* Linking visuals — dashed lines between number ↔ distance pairs */}
          <LinkVisuals labels={labels} selectedId={selectedId} />
          {/* Existing labels */}
          {labels.map((l) => (
            <LabelGlyph
              key={l.id}
              label={l}
              selected={l.id === selectedId}
              linkSource={linkSource}
              tool={tool}
              eventToSvgPoint={eventToSvgPoint}
              onSelect={() => {
                if (tool === 'link') {
                  // Linking flow: first eligible click = source; second eligible click = target.
                  const eligible = l.type === 'dimension_number' || l.type === 'dimensioned_distance';
                  if (!eligible) return;
                  if (linkSource == null) {
                    setLinkSource(l.id);
                    return;
                  }
                  if (linkSource === l.id) {
                    setLinkSource(null);
                    return;
                  }
                  // Must be the complementary type
                  const a = labels.find((x) => x.id === linkSource);
                  if (!a) {
                    setLinkSource(null);
                    return;
                  }
                  if (a.type === l.type) {
                    // Same type — swap source rather than try to link
                    setLinkSource(l.id);
                    return;
                  }
                  linkPair(linkSource, l.id);
                  setLinkSource(null);
                  return;
                }
                setSelectedId(l.id);
                setTool('select');
              }}
              onMutateGeometry={(newGeom) => {
                setLabels((prev) =>
                  prev.map((x) =>
                    x.id === l.id
                      ? ({ ...x, geometry: newGeom, updated_at: nowIso() } as Label)
                      : x,
                  ),
                );
                setDirty(true);
              }}
              onMutateAttributes={(newAttrs) => {
                setLabels((prev) =>
                  prev.map((x) =>
                    x.id === l.id
                      ? ({ ...x, attributes: { ...x.attributes, ...newAttrs }, updated_at: nowIso() } as Label)
                      : x,
                  ),
                );
                setDirty(true);
              }}
              onStartDrag={pushUndo}
            />
          ))}
          {/* Snap target — green circle + optional alignment guide */}
          {snap && (
            <g pointerEvents="none">
              {snap.guide?.type === 'horizontal' && (
                <line
                  x1={0} y1={snap.guide.value}
                  x2={imageSize[0]} y2={snap.guide.value}
                  stroke="#94a3b8" strokeWidth={1 / Math.max(0.1, view.w / imageSize[0])}
                  strokeDasharray="6,4" opacity={0.6}
                />
              )}
              {snap.guide?.type === 'vertical' && (
                <line
                  x1={snap.guide.value} y1={0}
                  x2={snap.guide.value} y2={imageSize[1]}
                  stroke="#94a3b8" strokeWidth={1 / Math.max(0.1, view.w / imageSize[0])}
                  strokeDasharray="6,4" opacity={0.6}
                />
              )}
              <circle
                cx={snap.pt[0]} cy={snap.pt[1]}
                r={9 * (view.w / imageSize[0])}
                fill="none" stroke={SNAP_COLOR[snap.kind]} strokeWidth={2.5 * (view.w / imageSize[0])}
              />
              <circle
                cx={snap.pt[0]} cy={snap.pt[1]}
                r={3 * (view.w / imageSize[0])}
                fill={SNAP_COLOR[snap.kind]}
              />
            </g>
          )}
          {/* In-progress preview — 2-click line tools. Use snap point if
              available so the preview tracks what the click will actually
              commit. */}
          {pendingStart && hoverPt && (tool === 'dimensioned_distance' || tool === 'wall') && (
            <line
              x1={pendingStart[0]} y1={pendingStart[1]}
              x2={snap?.pt[0] ?? hoverPt[0]} y2={snap?.pt[1] ?? hoverPt[1]}
              stroke="#f59e0b" strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
              strokeDasharray="6,4"
            />
          )}
          {/* In-progress preview — 2-click rectangle tools */}
          {pendingStart && hoverPt && (tool === 'floorplan_opening' || tool === 'view_opening') && (
            <rect
              x={Math.min(pendingStart[0], snap?.pt[0] ?? hoverPt[0])}
              y={Math.min(pendingStart[1], snap?.pt[1] ?? hoverPt[1])}
              width={Math.abs((snap?.pt[0] ?? hoverPt[0]) - pendingStart[0])}
              height={Math.abs((snap?.pt[1] ?? hoverPt[1]) - pendingStart[1])}
              fill="rgba(245, 158, 11, 0.15)"
              stroke="#f59e0b" strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
              strokeDasharray="6,4"
            />
          )}
          {/* In-progress preview — polyline */}
          {tool === 'component_line' && pendingPolyline.length > 0 && (
            <>
              <polyline
                points={[
                  ...pendingPolyline.map((p) => p.join(',')),
                  ...(hoverPt ? [hoverPt.join(',')] : []),
                ].join(' ')}
                fill="none" stroke="#f59e0b" strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
                strokeDasharray="6,4"
              />
              {pendingPolyline.map((p, i) => (
                <circle key={i} cx={p[0]} cy={p[1]} r={5} fill="#f59e0b" />
              ))}
              <text
                x={pendingPolyline[0][0] + 10}
                y={pendingPolyline[0][1] - 10}
                fill="#f59e0b" fontFamily="ui-monospace, monospace" fontSize={14}
                style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}
              >
                {pendingPolyline.length} pts — Enter to finish
              </text>
            </>
          )}
        </svg>
        <div className="absolute bottom-3 left-3 text-[0.7rem] text-zinc-300 bg-black/50 px-2 py-1 rounded leading-snug pointer-events-none">
          [S] Select · [D] Bemaßte Strecke · [N] Maßzahl · [W] Wand · [O] Öffnung · [L] Linie · [H] Höhenkote
          <br />
          Enter = Polylinie beenden · Esc = abbrechen · Shift/Right-Drag = Pan · Wheel = Zoom · R = Reset
        </div>
        <DrawHUD tool={tool} pendingStart={pendingStart} hoverPt={hoverPt} pendingPolyline={pendingPolyline} snap={snap} />
        {tool === 'link' && (
          <div className="absolute top-3 right-3 bg-black/75 text-white px-3 py-2 rounded text-[0.78rem] leading-snug pointer-events-none min-w-[240px]">
            <div className="font-semibold mb-1">Verknüpfen 🔗</div>
            <div className="text-[0.72rem] text-zinc-300">
              {linkSource == null
                ? 'Maßzahl oder Bemaßung anklicken…'
                : 'Jetzt das Gegenstück anklicken'}
            </div>
            <div className="text-[0.65rem] text-zinc-400 mt-1">
              Esc = abbrechen · S = zurück zu Select
            </div>
          </div>
        )}
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
          {TOOLS_BY_TAG[sceneTag].map((t) => (
            <ToolBtn key={t} current={tool} onSet={setTool} value={t} hotkey={TOOL_LABEL[t].hotkey}>
              {TOOL_LABEL[t].label}
            </ToolBtn>
          ))}
        </div>
        {sceneTag === 'nicht_klassifiziert' && (
          <p className="text-[0.7rem] text-muted mt-2 leading-snug">
            Setze einen Szenen-Tag oben, damit Werkzeuge verfügbar werden.
          </p>
        )}
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
  linkSource,
  tool,
  eventToSvgPoint,
  onSelect,
  onMutateGeometry,
  onMutateAttributes,
  onStartDrag,
}: {
  label: Label;
  selected: boolean;
  linkSource: string | null;
  tool: Tool;
  eventToSvgPoint: (e: ReactPointerEvent<SVGSVGElement> | PointerEvent) => Point | null;
  onSelect: () => void;
  onMutateGeometry: (newGeom: Label['geometry']) => void;
  onMutateAttributes: (newAttrs: Record<string, unknown>) => void;
  onStartDrag: () => void;
}) {
  // Color per type — selected always takes precedence; in link mode the
  // source label gets a magenta outline so it's obvious which one is staged.
  const baseColor = LABEL_COLORS[label.type] ?? '#16a34a';
  const isLinkSource = tool === 'link' && linkSource === label.id;
  const stroke = selected ? '#dc2626' : isLinkSource ? '#a21caf' : baseColor;
  const fill = selected ? '#dc262633' : isLinkSource ? '#a21caf33' : `${baseColor}1a`;
  const sw = selected || isLinkSource ? 3 : 2;

  // Pointer-down on the glyph body: select on quick click, body-translate on
  // drag-after-move-threshold. Drag uses raw SVG point math so it works at
  // any zoom level.
  const onPointerDownBody = (e: React.PointerEvent<SVGElement>) => {
    e.stopPropagation();
    if (e.button !== 0) return;          // only left click
    if (tool !== 'select' && tool !== 'link') {
      // For drawing tools, body click does nothing — let canvas handle it
      return;
    }
    const start = eventToSvgPoint(e as unknown as ReactPointerEvent<SVGSVGElement>);
    if (!start) return;
    const origin = label.geometry;
    let dragged = false;
    let pushedUndo = false;
    const target = (e.currentTarget as SVGElement).ownerSVGElement!;
    target.setPointerCapture?.(e.pointerId);

    const onMove = (mv: PointerEvent) => {
      const pt = eventToSvgPoint(mv);
      if (!pt) return;
      const dx = pt[0] - start[0];
      const dy = pt[1] - start[1];
      if (!dragged && Math.hypot(dx, dy) > 4) {
        dragged = true;
        if (!pushedUndo) {
          onStartDrag();
          pushedUndo = true;
        }
        // Make sure the label is selected before we start moving it.
        if (tool === 'select' && !selected) onSelect();
      }
      if (!dragged) return;
      const newGeom = translateLabelGeometry({ ...label, geometry: origin } as Label, dx, dy);
      onMutateGeometry(newGeom);
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      if (!dragged) onSelect();
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Handle dragging a single endpoint / corner / vertex — same pattern but
  // updates one specific point on the geometry block via moveHandle().
  const onHandlePointerDown = (handleId: string) => (e: React.PointerEvent<SVGElement>) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    const start = eventToSvgPoint(e as unknown as ReactPointerEvent<SVGSVGElement>);
    if (!start) return;
    let pushedUndo = false;
    const onMove = (mv: PointerEvent) => {
      const pt = eventToSvgPoint(mv);
      if (!pt) return;
      if (!pushedUndo) {
        onStartDrag();
        pushedUndo = true;
      }
      onMutateGeometry(moveHandle(label, handleId, pt));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect();
  };
  const bodyProps = {
    onClick,
    onPointerDown: onPointerDownBody,
    style: { cursor: selected ? 'move' : 'pointer' as const },
  };

  // Body geometry varies per type; selection handles are rendered uniformly
  // by handlesFor() below.
  let body: JSX.Element | null = null;

  switch (label.type) {
    case 'dimensioned_distance': {
      const { start, end } = label.geometry;
      const isRef = label.attributes.is_reference;
      const refMark = isRef ? '#f59e0b' : stroke;
      const refWidth = isRef ? sw + 1 : sw;
      body = (
        <g {...bodyProps}>
          <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]}
                stroke={refMark} strokeWidth={refWidth} />
          <Tick x={start[0]} y={start[1]} stroke={refMark} sw={refWidth} large={isRef} />
          <Tick x={end[0]} y={end[1]} stroke={refMark} sw={refWidth} large={isRef} />
        </g>
      );
      break;
    }
    case 'dimension_number': {
      const anchor = label.geometry.anchor;
      if (!anchor) break;
      const [x, y] = anchor;
      body = (
        <g {...bodyProps}>
          <circle cx={x} cy={y} r={6} fill={fill} stroke={stroke} strokeWidth={sw} />
          <text x={x + 10} y={y - 6} fill={stroke} fontFamily="ui-monospace, monospace"
                fontSize={14} style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}>
            {label.attributes.text}
          </text>
        </g>
      );
      break;
    }
    case 'wall': {
      // M9: walls render as a perpendicular BAND, not a fat stroke. The
      // band's width = thickness_mm * WALL_PX_PER_MM, computed perpendicular
      // to the wall axis. The axis line is drawn on top so the wall direction
      // stays legible at any thickness.
      const { start, end } = label.geometry;
      const thicknessMm = label.attributes.thickness_mm ?? 365;
      const path = wallBandPath(start, end, thicknessMm, WALL_PX_PER_MM);
      body = (
        <g {...bodyProps}>
          {path && (
            <path d={path} fill={stroke} fillOpacity={0.18} stroke={stroke} strokeWidth={sw} />
          )}
          <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke={stroke} strokeWidth={sw} />
        </g>
      );
      break;
    }
    case 'floorplan_opening': {
      const [a, b, c, d] = label.geometry.quad;
      body = (
        <g {...bodyProps}>
          <polygon points={`${a[0]},${a[1]} ${b[0]},${b[1]} ${c[0]},${c[1]} ${d[0]},${d[1]}`}
                   fill={fill} stroke={stroke} strokeWidth={sw} />
        </g>
      );
      break;
    }
    case 'view_opening': {
      const { top_edge, bottom_edge } = label.geometry;
      const path = `M ${top_edge.map(p => p.join(',')).join(' L ')}` +
                   ` L ${[...bottom_edge].reverse().map(p => p.join(',')).join(' L ')} Z`;
      body = (
        <g {...bodyProps}>
          <path d={path} fill={fill} stroke={stroke} strokeWidth={sw} />
        </g>
      );
      break;
    }
    case 'component_line': {
      const pts = label.geometry.polyline;
      body = (
        <g {...bodyProps}>
          <polyline points={pts.map(p => p.join(',')).join(' ')}
                    fill="none" stroke={stroke} strokeWidth={sw + 1} />
          {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={3} fill={stroke} />)}
        </g>
      );
      break;
    }
    case 'height_mark': {
      const [x, y] = label.geometry.anchor;
      body = (
        <g {...bodyProps}>
          <polygon points={`${x},${y} ${x - 10},${y - 16} ${x + 10},${y - 16}`}
                   fill={fill} stroke={stroke} strokeWidth={sw} />
          {label.attributes.value_mm != null && (
            <text x={x + 14} y={y - 6} fill={stroke} fontFamily="ui-monospace, monospace"
                  fontSize={12} style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}>
              {(label.attributes.value_mm / 1000).toFixed(2)} m
            </text>
          )}
        </g>
      );
      break;
    }
  }

  // Selection handles — small circles at each draggable point. Only rendered
  // when this label is selected, and tool is 'select' (otherwise drawing
  // tools shouldn't show edit chrome).
  const handles: HandleSpec[] = selected && tool === 'select' ? handlesFor(label) : [];

  // Wall-specific thickness handle: perpendicular drag adjusts thickness_mm.
  const showThicknessHandle = selected && tool === 'select' && label.type === 'wall';
  const thicknessHandlePos = showThicknessHandle
    ? wallThicknessHandlePos(
        label.geometry.start,
        label.geometry.end,
        label.attributes.thickness_mm ?? 365,
        WALL_PX_PER_MM,
      )
    : null;

  const onThicknessHandleDown = (e: React.PointerEvent<SVGElement>) => {
    if (label.type !== 'wall') return;
    e.stopPropagation();
    if (e.button !== 0) return;
    const start = eventToSvgPoint(e as unknown as ReactPointerEvent<SVGSVGElement>);
    if (!start) return;
    const wall = label;
    const dx = wall.geometry.end[0] - wall.geometry.start[0];
    const dy = wall.geometry.end[1] - wall.geometry.start[1];
    const len = Math.hypot(dx, dy);
    if (len === 0) return;
    const perpX = -dy / len;
    const perpY = dx / len;
    const startThickness = wall.attributes.thickness_mm ?? 365;
    let pushed = false;
    const onMove = (mv: PointerEvent) => {
      const pt = eventToSvgPoint(mv);
      if (!pt) return;
      // Projection of cursor delta onto the perpendicular axis.
      const ddx = pt[0] - start[0];
      const ddy = pt[1] - start[1];
      const projPx = ddx * perpX + ddy * perpY;
      // Δthickness_mm = 2 × projection / pxPerMm (handle sits at half-thickness)
      const deltaMm = (2 * projPx) / WALL_PX_PER_MM;
      let next = Math.max(50, Math.min(800, startThickness + deltaMm));
      // Snap to standard residential thicknesses if within 5 mm.
      for (const std of STANDARD_THICKNESS_MM) {
        if (Math.abs(next - std) < 5) {
          next = std;
          break;
        }
      }
      if (!pushed) {
        onStartDrag();
        pushed = true;
      }
      onMutateAttributes({ thickness_mm: Math.round(next) });
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
      {body}
      {handles.map((h) => (
        <g
          key={h.id}
          style={{ cursor: h.cursor ?? 'move' }}
          onPointerDown={onHandlePointerDown(h.id)}
        >
          {/* outer ring (hit area) */}
          <circle cx={h.pt[0]} cy={h.pt[1]} r={9} fill="white" stroke="#dc2626" strokeWidth={2.5} />
          {/* inner dot */}
          <circle cx={h.pt[0]} cy={h.pt[1]} r={3} fill="#dc2626" />
        </g>
      ))}
      {thicknessHandlePos && (
        <g style={{ cursor: 'ns-resize' }} onPointerDown={onThicknessHandleDown}>
          {/* small square so it reads as "different from endpoint handles" */}
          <rect
            x={thicknessHandlePos[0] - 8} y={thicknessHandlePos[1] - 8}
            width={16} height={16}
            fill="white" stroke="#7c3aed" strokeWidth={2.5} rx={2}
          />
          <rect
            x={thicknessHandlePos[0] - 3} y={thicknessHandlePos[1] - 3}
            width={6} height={6}
            fill="#7c3aed"
          />
        </g>
      )}
    </g>
  );
}

// Live in-canvas HUD shown while drawing a stroke or polyline. Reads out the
// current pixel angle + length so the annotator can sanity-check that what
// they're drawing matches the target_orientation they'll later assign.
function DrawHUD({
  tool,
  pendingStart,
  hoverPt,
  pendingPolyline,
  snap,
}: {
  tool: Tool;
  pendingStart: Point | null;
  hoverPt: Point | null;
  pendingPolyline: Point[];
  snap: SnapTarget | null;
}) {
  // 2-click line tools: angle from pendingStart → hoverPt
  if ((tool === 'dimensioned_distance' || tool === 'wall') && pendingStart && hoverPt) {
    const dx = hoverPt[0] - pendingStart[0];
    const dy = hoverPt[1] - pendingStart[1];
    const angle = (Math.atan2(-dy, dx) * 180) / Math.PI;
    const length = Math.hypot(dx, dy);
    // Snap-to-axis hint within ±5°
    const nearH = Math.abs(angle) < 5 || Math.abs(Math.abs(angle) - 180) < 5;
    const nearV = Math.abs(Math.abs(angle) - 90) < 5;
    return (
      <div className="absolute top-3 right-3 bg-black/75 text-white px-3 py-2 rounded font-mono text-[0.78rem] leading-snug pointer-events-none min-w-[180px]">
        <div className="flex justify-between gap-3">
          <span className="text-zinc-400">Pixel-Winkel</span>
          <span className="text-amber-300">{angle.toFixed(1)}°</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-zinc-400">Länge</span>
          <span className="text-amber-300">{length.toFixed(0)} px</span>
        </div>
        {(nearH || nearV) && (
          <div className="mt-1 text-[0.65rem] text-green-300 leading-tight">
            ≈ {nearH ? 'horizontal' : 'vertical'}
          </div>
        )}
        {snap && (
          <div className="mt-1 text-[0.65rem] text-green-300 leading-tight">
            Snap → {snap.hint}
          </div>
        )}
      </div>
    );
  }
  // Polyline: show segment-by-segment + last segment angle
  if (tool === 'component_line' && pendingPolyline.length > 0 && hoverPt) {
    const last = pendingPolyline[pendingPolyline.length - 1];
    const dx = hoverPt[0] - last[0];
    const dy = hoverPt[1] - last[1];
    const angle = (Math.atan2(-dy, dx) * 180) / Math.PI;
    const length = Math.hypot(dx, dy);
    return (
      <div className="absolute top-3 right-3 bg-black/75 text-white px-3 py-2 rounded font-mono text-[0.78rem] leading-snug pointer-events-none min-w-[200px]">
        <div className="flex justify-between gap-3">
          <span className="text-zinc-400">Punkte</span>
          <span className="text-amber-300">{pendingPolyline.length}</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-zinc-400">Letzter Winkel</span>
          <span className="text-amber-300">{angle.toFixed(1)}°</span>
        </div>
        <div className="flex justify-between gap-3">
          <span className="text-zinc-400">Letzte Länge</span>
          <span className="text-amber-300">{length.toFixed(0)} px</span>
        </div>
        <div className="mt-1 text-[0.65rem] text-zinc-300">Enter = beenden</div>
      </div>
    );
  }
  return null;
}

// Center-point of a label, for drawing link visuals between pairs.
function labelCenter(l: Label): Point | null {
  switch (l.type) {
    case 'dimensioned_distance': {
      const { start, end } = l.geometry;
      return [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    }
    case 'dimension_number':
      return l.geometry.anchor ?? null;
    case 'wall': {
      const { start, end } = l.geometry;
      return [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    }
    case 'floorplan_opening': {
      const [a, , c] = l.geometry.quad;
      return [(a[0] + c[0]) / 2, (a[1] + c[1]) / 2];
    }
    case 'view_opening': {
      const t = l.geometry.top_edge;
      const b = l.geometry.bottom_edge;
      const cx = (t[0][0] + b[b.length - 1][0]) / 2;
      const cy = (t[0][1] + b[b.length - 1][1]) / 2;
      return [cx, cy];
    }
    case 'component_line': {
      const pts = l.geometry.polyline;
      const mid = pts[Math.floor(pts.length / 2)];
      return mid;
    }
    case 'height_mark':
      return l.geometry.anchor;
  }
  return null;
}

// Render every labels-relation as a dashed line between the related label
// centers. Selected pairs get a thicker, more visible link.
function LinkVisuals({ labels, selectedId }: { labels: Label[]; selectedId: string | null }) {
  const links: Array<{ a: Point; b: Point; selected: boolean; id: string }> = [];
  for (const l of labels) {
    for (const r of l.relations ?? []) {
      if (r.kind !== 'labels') continue;
      const other = labels.find((x) => x.id === r.other_id);
      if (!other) continue;
      const a = labelCenter(l);
      const b = labelCenter(other);
      if (!a || !b) continue;
      links.push({
        a, b,
        selected: l.id === selectedId || other.id === selectedId,
        id: `${l.id}-${other.id}`,
      });
    }
  }
  return (
    <g>
      {links.map(({ a, b, selected, id }) => (
        <line
          key={id}
          x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]}
          stroke={selected ? '#0ea5e9' : '#94a3b8'}
          strokeWidth={selected ? 2.5 : 1.5}
          strokeDasharray={selected ? '8,4' : '4,4'}
          opacity={selected ? 1 : 0.65}
        />
      ))}
    </g>
  );
}

// Compute a wall's perpendicular band as an SVG path. Returns '' for a
// degenerate zero-length wall (avoids NaN in the path string).
function wallBandPath(start: Point, end: Point, thicknessMm: number, pxPerMm: number): string {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy);
  if (len === 0) return '';
  const ux = dx / len;
  const uy = dy / len;
  // Perpendicular unit vector (90° CCW)
  const px = -uy;
  const py = ux;
  const half = (thicknessMm * pxPerMm) / 2;
  const a: Point = [start[0] + px * half, start[1] + py * half];
  const b: Point = [end[0] + px * half, end[1] + py * half];
  const c: Point = [end[0] - px * half, end[1] - py * half];
  const d: Point = [start[0] - px * half, start[1] - py * half];
  return `M ${a[0]},${a[1]} L ${b[0]},${b[1]} L ${c[0]},${c[1]} L ${d[0]},${d[1]} Z`;
}

// Position of the wall's perpendicular thickness handle: midpoint + perp
// vector × (current thickness / 2). The user drags this handle perpendicular
// to the wall axis to grow / shrink the band.
function wallThicknessHandlePos(start: Point, end: Point, thicknessMm: number, pxPerMm: number): Point | null {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy);
  if (len === 0) return null;
  const ux = dx / len;
  const uy = dy / len;
  const px = -uy;
  const py = ux;
  const half = (thicknessMm * pxPerMm) / 2;
  return [(start[0] + end[0]) / 2 + px * half, (start[1] + end[1]) / 2 + py * half];
}

const LABEL_COLORS: Record<Label['type'], string> = {
  dimensioned_distance: '#16a34a',
  dimension_number: '#0ea5e9',
  wall: '#7c3aed',
  floorplan_opening: '#ea580c',
  view_opening: '#ea580c',
  component_line: '#0891b2',
  height_mark: '#be185d',
};

function Tick({
  x, y, stroke, sw, large = false,
}: { x: number; y: number; stroke: string; sw: number; large?: boolean }) {
  // Larger tick for is_reference strokes so they read as "anchors" at a glance.
  const r = large ? 6 : 4;
  return <circle cx={x} cy={y} r={r} fill={stroke} stroke={stroke} strokeWidth={sw} />;
}

// ── right-rail inspector for selected label ────────────────────────────────

function Inspector({
  label,
  allLabels,
  onChange,
  onDelete,
  onUnlink,
  onSelectId,
}: {
  label: Label;
  allLabels: Label[];
  onChange: (patch: Partial<Label>) => void;
  onDelete: () => void;
  onUnlink: (otherId: string) => void;
  onSelectId: (id: string) => void;
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
      {label.type === 'wall' && <WallFields label={label} onChange={onChange} />}
      {label.type === 'floorplan_opening' && <FloorplanOpeningFields label={label} onChange={onChange} />}
      {label.type === 'view_opening' && <ViewOpeningFields label={label} onChange={onChange} />}
      {label.type === 'component_line' && <ComponentLineFields label={label} onChange={onChange} />}
      {label.type === 'height_mark' && <HeightMarkFields label={label} onChange={onChange} />}

      {(label.type === 'dimension_number' || label.type === 'dimensioned_distance') && (
        <LinksSection
          label={label}
          allLabels={allLabels}
          onUnlink={onUnlink}
          onSelectId={onSelectId}
        />
      )}

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

// Show all labels-relations attached to the selected label. For a
// dimension_number these are stored directly on label.relations; for a
// dimensioned_distance we scan all dimension_numbers in the scene for
// relations pointing at us.
function LinksSection({
  label,
  allLabels,
  onUnlink,
  onSelectId,
}: {
  label: DimensionNumberLabel | DimensionedDistanceLabel;
  allLabels: Label[];
  onUnlink: (otherId: string) => void;
  onSelectId: (id: string) => void;
}) {
  const linkedIds =
    label.type === 'dimension_number'
      ? (label.relations ?? []).filter((r) => r.kind === 'labels').map((r) => r.other_id)
      : allLabels
          .filter((l) => l.type === 'dimension_number')
          .filter((l) => (l.relations ?? []).some((r) => r.kind === 'labels' && r.other_id === label.id))
          .map((l) => l.id);

  const linked = linkedIds.map((id) => allLabels.find((l) => l.id === id)).filter((x): x is Label => !!x);

  return (
    <section>
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        Verknüpfungen{' '}
        <span className="text-zinc-400 font-normal">({linked.length})</span>
      </h4>
      {linked.length === 0 ? (
        <p className="text-[0.72rem] text-muted italic">
          {label.type === 'dimension_number'
            ? 'Diese Maßzahl ist noch keiner Strecke zugeordnet.'
            : 'Diese Strecke hat noch keine Maßzahl.'}
          {' '}Verwende das Verknüpfen-Werkzeug (K).
        </p>
      ) : (
        <ul className="space-y-1">
          {linked.map((other) => {
            const consistency = checkPairConsistency(label, other);
            return (
              <li
                key={other.id}
                className={`px-2 py-1.5 rounded border text-[0.72rem] ${
                  consistency.kind === 'ok'
                    ? 'bg-green-50 border-green-200'
                    : consistency.kind === 'warn'
                    ? 'bg-amber-50 border-amber-200'
                    : 'bg-zinc-50 border-border'
                }`}
              >
                <button
                  type="button"
                  onClick={() => onSelectId(other.id)}
                  className="font-mono text-zinc-900 hover:underline truncate w-full text-left"
                  title={other.id}
                >
                  {labelGlyph(other)} {other.type}
                </button>
                <div className="mt-0.5 text-[0.65rem] text-muted">{consistency.message}</div>
                <button
                  type="button"
                  onClick={() => onUnlink(other.id)}
                  className="mt-1 text-[0.65rem] text-red-700 hover:underline"
                >
                  Verknüpfung entfernen
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// Consistency check between a dim_number and a linked dim_distance.
// Returns 'ok' when both have values and they agree within 5%, 'warn' when
// they disagree, 'unknown' if values are missing.
function checkPairConsistency(
  a: DimensionNumberLabel | DimensionedDistanceLabel,
  b: Label,
): { kind: 'ok' | 'warn' | 'unknown'; message: string } {
  let numberVal: number | null | undefined = null;
  let distanceVal: number | null | undefined = null;
  if (a.type === 'dimension_number') {
    numberVal = a.attributes.parsed_value_mm;
    if (b.type === 'dimensioned_distance') distanceVal = b.attributes.value_mm;
  } else {
    distanceVal = a.attributes.value_mm;
    if (b.type === 'dimension_number') numberVal = b.attributes.parsed_value_mm;
  }
  if (numberVal == null || distanceVal == null) {
    return { kind: 'unknown', message: 'Beide Werte erforderlich für den Konsistenz-Check.' };
  }
  const rel = Math.abs(numberVal - distanceVal) / Math.max(1, Math.abs(distanceVal));
  if (rel < 0.05) {
    return { kind: 'ok', message: `✓ ${numberVal} mm ≈ ${distanceVal} mm` };
  }
  return {
    kind: 'warn',
    message: `⚠ Maßzahl ${numberVal} mm ≠ Strecke ${distanceVal} mm (Differenz ${Math.abs(numberVal - distanceVal)} mm)`,
  };
}

function DimensionedDistanceFields({
  label,
  onChange,
}: {
  label: DimensionedDistanceLabel;
  onChange: (patch: Partial<Label>) => void;
}) {
  const orient = label.attributes.target_orientation;
  const isCustomAngle = typeof orient === 'string' && orient.startsWith('angle_deg:');
  const orientKind: 'horizontal' | 'vertical' | 'unknown' | 'angle_deg' = isCustomAngle
    ? 'angle_deg'
    : (orient as 'horizontal' | 'vertical' | 'unknown');
  const customAngle = isCustomAngle ? parseFloat(orient.slice('angle_deg:'.length)) || 0 : 0;

  // Compute the actual pixel angle of the stroke (post-creation). Live angle
  // during drawing is shown in the canvas HUD; this is the static post-draw
  // value displayed in the inspector for sanity-checking.
  const { start, end } = label.geometry;
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const actualAngleDeg = (Math.atan2(-dy, dx) * 180) / Math.PI;
  const lengthPx = Math.hypot(dx, dy);

  // Deviation from target.
  const deviation = orientationDeviation(orient, actualAngleDeg);

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
          value={orientKind}
          onChange={(e) => {
            const k = e.target.value;
            const next: DimensionedDistanceLabel['attributes']['target_orientation'] =
              k === 'angle_deg' ? `angle_deg:${customAngle.toFixed(1)}` : (k as 'horizontal' | 'vertical' | 'unknown');
            onChange({ attributes: { ...label.attributes, target_orientation: next } } as Partial<Label>);
          }}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="horizontal">horizontal (0°)</option>
          <option value="vertical">vertical (±90°)</option>
          <option value="angle_deg">benutzerdef. Winkel…</option>
          <option value="unknown">unknown</option>
        </select>
      </label>

      {orientKind === 'angle_deg' && (
        <label className="block">
          <span className="text-[0.7rem] text-muted">Winkel (°)</span>
          <input
            type="number"
            step="0.1"
            value={customAngle}
            onChange={(e) =>
              onChange({
                attributes: {
                  ...label.attributes,
                  target_orientation: `angle_deg:${(Number(e.target.value) || 0).toFixed(1)}`,
                },
              } as Partial<Label>)
            }
            className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
          />
        </label>
      )}

      <label className="flex items-center gap-2 text-[0.78rem]">
        <input
          type="checkbox"
          checked={label.attributes.is_reference}
          onChange={(e) =>
            onChange({ attributes: { ...label.attributes, is_reference: e.target.checked } } as Partial<Label>)
          }
        />
        <span>
          Referenz-Strecke
          <span className="block text-[0.65rem] text-muted">anchors die Homographie (M6)</span>
        </span>
      </label>

      <div className="text-[0.7rem] text-muted bg-zinc-50 rounded px-2 py-1.5 leading-snug">
        Tatsächlicher Pixel-Winkel: <span className="font-mono text-zinc-900">{actualAngleDeg.toFixed(1)}°</span>
        <br />
        Pixel-Länge: <span className="font-mono text-zinc-900">{lengthPx.toFixed(0)}</span> px
        {deviation != null && (
          <>
            <br />
            Abweichung zum Soll: <span className={`font-mono ${
              Math.abs(deviation) < 2 ? 'text-green-700' :
              Math.abs(deviation) < 10 ? 'text-amber-700' :
              'text-red-700'
            }`}>{deviation > 0 ? '+' : ''}{deviation.toFixed(1)}°</span>
          </>
        )}
      </div>
    </section>
  );
}

// Deviation between the actual stroke angle and its declared target
// orientation. Returns null if no comparison is meaningful (e.g. target =
// 'unknown'). Uses min absolute difference modulo 180° since a 'horizontal'
// stroke is equally well-aimed at 0° or 180°.
function orientationDeviation(
  target: DimensionedDistanceLabel['attributes']['target_orientation'],
  actualDeg: number,
): number | null {
  let targetDeg: number | null = null;
  if (target === 'horizontal') targetDeg = 0;
  else if (target === 'vertical') targetDeg = 90;
  else if (typeof target === 'string' && target.startsWith('angle_deg:')) {
    const n = parseFloat(target.slice('angle_deg:'.length));
    if (!Number.isNaN(n)) targetDeg = n;
  }
  if (targetDeg == null) return null;
  // Wrap to [-90, 90] difference — a stroke parallel to the target axis is
  // equally good in either direction.
  let d = ((actualDeg - targetDeg + 90) % 180) - 90;
  if (d <= -90) d += 180;
  return d;
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

function WallFields({ label, onChange }: { label: WallLabel; onChange: (p: Partial<Label>) => void }) {
  const t = label.attributes.thickness_mm ?? 365;
  const set = (mm: number) => onChange({ attributes: { thickness_mm: mm } } as Partial<Label>);
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Wand</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted flex items-baseline gap-2">
          Wandstärke <span className="font-mono text-zinc-900">{t} mm</span>
        </span>
        <input
          type="range" min={50} max={500} step={5}
          value={t}
          onChange={(e) => set(Number(e.target.value))}
          className="w-full accent-violet-600"
        />
      </label>
      <input
        type="number"
        value={label.attributes.thickness_mm ?? ''}
        onChange={(e) => set(e.target.value === '' ? 0 : Number(e.target.value))}
        className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
        placeholder="365"
      />
      <div className="flex gap-1 flex-wrap">
        {STANDARD_THICKNESS_MM.map((mm) => (
          <button
            key={mm}
            type="button"
            onClick={() => set(mm)}
            className={`px-2 py-1 rounded text-[0.7rem] font-mono ${
              t === mm
                ? 'bg-violet-600 text-white font-semibold'
                : 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200'
            }`}
          >
            {mm}
          </button>
        ))}
      </div>
      <p className="text-[0.65rem] text-muted leading-snug">
        Tipp: Lila Vierecks-Handle perpendikulär zur Wandachse zieht die
        Wandstärke direkt auf der Zeichnung. ← / → ändert ±10 mm (Shift = 50 mm).
      </p>
    </section>
  );
}

function FloorplanOpeningFields({
  label, onChange,
}: { label: FloorplanOpeningLabel; onChange: (p: Partial<Label>) => void }) {
  const a = label.attributes;
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Öffnung (Grundriss)</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Art</span>
        <select
          value={a.opening_kind ?? 'window'}
          onChange={(e) => onChange({ attributes: { ...a, opening_kind: e.target.value as FloorplanOpeningLabel['attributes']['opening_kind'] } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="window">window</option>
          <option value="door">door</option>
          <option value="passage">passage</option>
          <option value="garage_door">garage_door</option>
          <option value="other">other</option>
        </select>
      </label>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Breite (mm)</span>
        <input
          type="number"
          value={a.width_mm ?? ''}
          onChange={(e) => onChange({ attributes: { ...a, width_mm: e.target.value === '' ? null : Number(e.target.value) } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
        />
      </label>
      {a.opening_kind === 'door' && (
        <>
          <label className="block">
            <span className="text-[0.7rem] text-muted">Schwenken</span>
            <select
              value={a.swing ?? 'none'}
              onChange={(e) => onChange({ attributes: { ...a, swing: e.target.value as FloorplanOpeningLabel['attributes']['swing'] } } as Partial<Label>)}
              className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
            >
              <option value="in">in</option>
              <option value="out">out</option>
              <option value="sliding">sliding</option>
              <option value="none">none</option>
            </select>
          </label>
          <label className="block">
            <span className="text-[0.7rem] text-muted">Anschlag</span>
            <select
              value={a.swing_side ?? 'none'}
              onChange={(e) => onChange({ attributes: { ...a, swing_side: e.target.value as FloorplanOpeningLabel['attributes']['swing_side'] } } as Partial<Label>)}
              className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
            >
              <option value="left">left</option>
              <option value="right">right</option>
              <option value="none">none</option>
            </select>
          </label>
        </>
      )}
    </section>
  );
}

function ViewOpeningFields({
  label, onChange,
}: { label: ViewOpeningLabel; onChange: (p: Partial<Label>) => void }) {
  const a = label.attributes;
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Öffnung (Ansicht)</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Art</span>
        <select
          value={a.opening_kind ?? 'window'}
          onChange={(e) => onChange({ attributes: { ...a, opening_kind: e.target.value as ViewOpeningLabel['attributes']['opening_kind'] } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="window">window</option>
          <option value="door">door</option>
          <option value="skylight">skylight</option>
          <option value="dormer">dormer</option>
          <option value="garage_door">garage_door</option>
          <option value="other">other</option>
        </select>
      </label>
      <label className="flex items-center gap-2 text-[0.78rem]">
        <input
          type="checkbox"
          checked={a.frame_visible ?? false}
          onChange={(e) => onChange({ attributes: { ...a, frame_visible: e.target.checked } } as Partial<Label>)}
        />
        Rahmen sichtbar
      </label>
    </section>
  );
}

function ComponentLineFields({
  label, onChange,
}: { label: ComponentLineLabel; onChange: (p: Partial<Label>) => void }) {
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Bauteillinie</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Art</span>
        <select
          value={label.attributes.line_kind ?? 'other'}
          onChange={(e) => onChange({ attributes: { line_kind: e.target.value as ComponentLineLabel['attributes']['line_kind'] } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="first">First</option>
          <option value="traufe">Traufe</option>
          <option value="gelaende">Gelände</option>
          <option value="geschoss">Geschoss</option>
          <option value="ok_ffb">OK FFB</option>
          <option value="sockel">Sockel</option>
          <option value="firstkante">Firstkante</option>
          <option value="kniestock">Kniestock</option>
          <option value="other">other</option>
        </select>
      </label>
      <p className="text-[0.65rem] text-muted">
        Polylinie mit {label.geometry.polyline.length} Punkten.
      </p>
    </section>
  );
}

function HeightMarkFields({
  label, onChange,
}: { label: HeightMarkLabel; onChange: (p: Partial<Label>) => void }) {
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Höhenkote</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Wert (mm)</span>
        <input
          type="number"
          value={label.attributes.value_mm ?? ''}
          onChange={(e) => onChange({ attributes: { ...label.attributes, value_mm: e.target.value === '' ? null : Number(e.target.value) } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
        />
      </label>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Bezugslinie (Bauteillinien-ID)</span>
        <input
          type="text"
          value={label.attributes.reference_line_id ?? ''}
          onChange={(e) => onChange({ attributes: { ...label.attributes, reference_line_id: e.target.value || null } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.78rem] font-mono"
          placeholder="(none)"
        />
      </label>
    </section>
  );
}
