import {
  type JSX,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { fetchHouse, fetchLabels, fetchSynthetic, saveLabels, useResource } from '../api/client';
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
  labelCentroid,
  moveHandle,
  translateLabelGeometry,
  type HandleSpec,
} from '../lib/labelGeometry';
import { findSnap, SNAP_COLOR, type SnapTarget, type SnapTool } from '../lib/snap';
import { clearDefaults, getDefaults, rememberDefaults } from '../lib/defaults';
import {
  CentreLineIcon, DimensionIcon, DoorIcon, ElevationViewIcon, KeynoteIcon,
  LayersIcon, OpeningIcon, PlanViewIcon, QuestionIcon,
  SectionViewIcon, SelectIcon, SpotElevationIcon, WallIcon,
} from '../lib/icons';

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

type Tool =
  | 'select'
  | 'dimensioned_distance'
  | 'dimension_number'
  | 'wall'
  | 'floorplan_opening'
  | 'view_opening'
  | 'component_line'
  | 'height_mark';

// Tag → which tools are available. The 'link' tool was removed once
// linking became implicit: placing a dim_distance now auto-creates a
// dim_number at its midpoint with the relation already set, and the
// inspector exposes a manual link picker when you want to relink.
// 'dimension_number' is kept as a tool but only useful for the rare case
// of a number text whose stroke isn't being labeled.
// 'dimension_number' is intentionally OMITTED from the default per-tag
// lists — it's the standalone Maßzahl tool which 99% of the time
// shouldn't be reached for separately (the Bemaßung tool auto-creates a
// paired Maßzahl). Only the 'sonstiges' / 'alle anzeigen' modes expose
// it for the rare OCR-only case.
const TOOLS_BY_TAG: Record<SceneTag, Tool[]> = {
  grundriss: [
    'select', 'wall', 'floorplan_opening',
    'dimensioned_distance',
  ],
  ansicht: [
    'select', 'view_opening', 'component_line', 'height_mark',
    'dimensioned_distance',
  ],
  schnitt: [
    'select', 'view_opening', 'component_line', 'height_mark',
    'dimensioned_distance',
  ],
  sonstiges: [
    'select', 'wall', 'floorplan_opening', 'view_opening',
    'component_line', 'height_mark',
    'dimensioned_distance', 'dimension_number',
  ],
  nicht_klassifiziert: ['select'],
};

type ToolMeta = {
  label: string;
  hotkey: string;
  Icon: (props: { size?: number; strokeWidth?: number }) => React.JSX.Element;
};

const TOOL_META: Record<Tool, ToolMeta> = {
  select: { label: 'Auswählen', hotkey: 'S', Icon: SelectIcon },
  wall: { label: 'Wand', hotkey: 'W', Icon: WallIcon },
  floorplan_opening: { label: 'Öffnung (Grundriss)', hotkey: 'O', Icon: DoorIcon },
  view_opening: { label: 'Öffnung (Ansicht)', hotkey: 'O', Icon: OpeningIcon },
  component_line: { label: 'Bauteillinie', hotkey: 'L', Icon: CentreLineIcon },
  height_mark: { label: 'Höhenkote', hotkey: 'H', Icon: SpotElevationIcon },
  dimensioned_distance: { label: 'Bemaßte Strecke', hotkey: 'D', Icon: DimensionIcon },
  dimension_number: { label: 'Maßzahl', hotkey: 'N', Icon: KeynoteIcon },
};

const TAG_META: Record<SceneTag, { label: string; Icon: (props: { size?: number }) => React.JSX.Element }> = {
  grundriss: { label: 'Grundriss', Icon: PlanViewIcon },
  ansicht: { label: 'Ansicht', Icon: ElevationViewIcon },
  schnitt: { label: 'Schnitt', Icon: SectionViewIcon },
  sonstiges: { label: 'Sonstiges', Icon: LayersIcon },
  nicht_klassifiziert: { label: '(nicht klassifiziert)', Icon: QuestionIcon },
};

// Tool families: tools whose primary attribute is a categorical choice get
// their subtypes shown as inline children of the parent tool button. Picking
// a subtype activates the parent tool AND writes the subtype as the
// per-house default — so the next-drawn label is pre-classified.
//
// Why this matters: e.g. component_line MUST be typed (First/Traufe/…) to
// be a useful training signal — an unclassified line is dead label weight.
// The family UX makes "pick the type" the same gesture as "pick the tool",
// instead of being a separate inspector edit after the fact.
type ToolFamilyOption = {
  value: string;          // stored as a string; for boolean attrs we map
                          // 'true'/'false' to the boolean at commit time.
  label: string;
  hint?: string;          // shown in title= tooltip when hovered
};
type ToolFamily = {
  parentTool: Tool;
  familyLabel: string;
  Icon: (props: { size?: number; strokeWidth?: number }) => React.JSX.Element;
  hotkey: string;
  attrName: string;       // the attribute name on the parent tool's label
  attrIsBoolean?: boolean;
  applicableTags: SceneTag[];
  options: ToolFamilyOption[];
  helpText?: string;      // 1-line guidance shown under expanded subtypes
};

const TOOL_FAMILIES: ToolFamily[] = [
  {
    parentTool: 'floorplan_opening',
    familyLabel: 'Öffnung',
    Icon: DoorIcon,
    hotkey: 'O',
    attrName: 'opening_kind',
    applicableTags: ['grundriss', 'sonstiges'],
    options: [
      { value: 'window', label: 'Fenster', hint: 'Fenster im Grundriss — die Brüstung wird im Inspektor optional gepflegt.' },
      { value: 'door', label: 'Tür', hint: 'Tür mit Schwingflügel. Swing-Side + Swing-Richtung steuern die Bogen-Darstellung.' },
      { value: 'passage', label: 'Durchgang', hint: 'Offener Durchgang ohne Tür/Fenster.' },
      { value: 'garage_door', label: 'Tor', hint: 'Garagen-/Hoftor.' },
      { value: 'other', label: 'Sonstige', hint: 'Sonstige Wandöffnung — möglichst spezifischen Typ wählen.' },
    ],
    helpText: 'Klick 1: an die Wand (Snap), Klick 2: Öffnungsbreite festlegen. Drehung folgt automatisch der Wandachse.',
  },
  {
    parentTool: 'view_opening',
    familyLabel: 'Öffnung',
    Icon: OpeningIcon,
    hotkey: 'O',
    attrName: 'opening_kind',
    applicableTags: ['ansicht', 'schnitt', 'sonstiges'],
    options: [
      { value: 'window', label: 'Fenster', hint: 'Fenster in der Fassadenansicht.' },
      { value: 'door', label: 'Tür', hint: 'Eingangs-/Terrassentür.' },
      { value: 'skylight', label: 'Dachfenster', hint: 'Dachflächenfenster (Velux o. ä.).' },
      { value: 'dormer', label: 'Gaube', hint: 'Dachgaube als geometrische Box.' },
      { value: 'garage_door', label: 'Tor', hint: 'Garagentor.' },
      { value: 'other', label: 'Sonstige', hint: 'Sonstige Öffnung.' },
    ],
    helpText: 'Zwei Klicks für die Diagonale der Öffnungs-Box.',
  },
  {
    parentTool: 'component_line',
    familyLabel: 'Linie',
    Icon: CentreLineIcon,
    hotkey: 'L',
    attrName: 'line_kind',
    applicableTags: ['ansicht', 'schnitt', 'sonstiges'],
    options: [
      // ── horizontale Höhenbezugslinien (M2 / Höhen-Anker) ───────────
      { value: 'first',         label: 'First',         hint: 'Horizontale Bezugslinie am Dachfirst (oft mit "+12,5 m" beschriftet). Aus ihr folgt die Firsthöhe.' },
      { value: 'traufe',        label: 'Traufe',        hint: 'Horizontale Bezugslinie an der Traufe.' },
      { value: 'gelaende',      label: 'Gelände',       hint: 'Horizontale Bezugslinie am Geländeniveau (±0,00).' },
      { value: 'geschoss',      label: 'Geschoss',      hint: 'Horizontaler Geschossübergang (Decke/Fußboden-Linie).' },
      { value: 'ok_ffb',        label: 'OK FFB',        hint: 'Oberkante Fertigfußboden — horizontale Bezugslinie pro Geschoss.' },
      { value: 'sockel',        label: 'Sockel',        hint: 'Horizontale Bezugslinie am Gebäudesockel.' },
      { value: 'kniestock',     label: 'Kniestock',     hint: 'Horizontale Bezugslinie am Kniestock (Drempel).' },
      // ── geometrische Kanten (M2 / Gebäude-Geometrie) ───────────────
      { value: 'dachschraege',  label: 'Dachschräge',   hint: 'Schräge Dachfläche / Dachkante (Linie von First nach Traufe in der Ansicht). Aus den Endpunkten folgen First- und Traufhöhe rechnerisch.' },
      { value: 'firstkante',    label: 'Firstkante',    hint: 'Geometrische Firstkante / Gratlinie (kurze Strecke entlang des Firsts in der Ansicht).' },
      { value: 'gebaeudekante', label: 'Gebäudekante',  hint: 'Vertikale oder seitliche Gebäudekante (z. B. linke/rechte Außenseite eines Giebels).' },
      { value: 'other',         label: 'Sonstige',      hint: 'Wenn keiner der Typen passt — vermeiden, möglichst spezifisch klassifizieren.' },
    ],
    helpText: 'Zeichne die Linie wie sie auf der Zeichnung ist. Der Typ sagt, was sie bedeutet — horizontale Höhenbezugslinie (First/Traufe…) oder geometrische Kante (Dachschräge, Firstkante, Gebäudekante).',
  },
  {
    parentTool: 'dimensioned_distance',
    familyLabel: 'Bemaßung',
    Icon: DimensionIcon,
    hotkey: 'D',
    attrName: 'is_reference',
    attrIsBoolean: true,
    applicableTags: ['grundriss', 'ansicht', 'schnitt', 'sonstiges'],
    options: [
      { value: 'false', label: 'Maß (M2 Bauteilmaß)',     hint: 'Längen-/Höhenmaß am Bauteil. Liefert die reale Größe — das primäre Trainingssignal für Wand-/Geschoss-/Fensterabmessungen.' },
      { value: 'true',  label: 'Bezug (M1 Entzerrung)',   hint: 'Bezugsstrecke für die Entzerrung der Zeichnung. Mindestens 1× horizontal + 1× vertikal pro Szene empfohlen. Wird visuell als amberfarbene gestrichelte Linie mit M1-Badge dargestellt.' },
    ],
    helpText: 'Nach 2 Klicks öffnet sich ein Eingabefeld am Mittelpunkt; auf Enter werden Strecke und passende Maßzahl gemeinsam erzeugt + verknüpft.',
  },
];

function findFamily(tool: Tool): ToolFamily | null {
  return TOOL_FAMILIES.find((f) => f.parentTool === tool) ?? null;
}

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

// Short label for a scene chip in the topbar. Picks the most informative
// floor (EG/OG/DG) or compass direction it can find in the filename, or
// falls back to the first few characters of the title.
function sceneShortLabel(file: string, title?: string): string {
  const f = file.toLowerCase();
  const FLOORS = ['eg', 'og', 'dg', 'kg', 'ug', '1og', '2og'];
  for (const fl of FLOORS) {
    if (f.includes(`-${fl}.`) || f.includes(`-${fl}-`) || f.endsWith(`-${fl}`)) {
      return fl.toUpperCase();
    }
  }
  const DIRS: Array<[string, string]> = [
    ['north', 'N'], ['nord', 'N'],
    ['south', 'S'], ['sued', 'S'], ['süd', 'S'],
    ['east', 'O'], ['ost', 'O'],
    ['west', 'W'],
    ['tal', 'Tal'], ['berg', 'Berg'],
    ['strasse', 'Str'], ['garten', 'Gtn'],
    ['linke-giebel', 'L-G'], ['rechte-giebel', 'R-G'],
  ];
  for (const [needle, short] of DIRS) {
    if (f.includes(needle)) return short;
  }
  // Kind hint
  if (f.includes('section') || f.includes('schnitt')) return 'Sch';
  if (f.includes('floorplan') || f.includes('grundriss')) return 'GR';
  if (f.includes('elevation') || f.includes('ansicht')) return 'An';
  // Fallback to title prefix or filename stem
  const t = (title ?? file).replace(/\.[^.]+$/, '');
  return t.length > 6 ? t.slice(0, 6) : t;
}

// localStorage key for the last-visited scene of a house. Scoped by
// (scope, key) so synthetic and real-house namespaces don't collide.
function lastSceneKey(scope: 'house' | 'synthetic', houseKey: string): string {
  return `bim-db:annotate:last-scene:${scope}:${houseKey}`;
}
export function getLastVisitedScene(scope: 'house' | 'synthetic', houseKey: string): string | null {
  try { return window.localStorage.getItem(lastSceneKey(scope, houseKey)); } catch { return null; }
}
function rememberLastVisitedScene(scope: 'house' | 'synthetic', houseKey: string, file: string): void {
  try { window.localStorage.setItem(lastSceneKey(scope, houseKey), file); } catch { /* no-op */ }
}

export function AnnotatePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { key = '', file = '' } = useParams();
  const decodedFile = decodeURIComponent(file);

  // Scope: derived from URL prefix. /synthetic/... → synthetic; /house/... → house.
  const scope: LabelScope = location.pathname.startsWith('/synthetic/') ? 'synthetic' : 'house';
  const imageUrl =
    scope === 'synthetic'
      ? `/static/synthetic/${key}/${decodedFile}`
      : `/scene/${key}/${encodeURIComponent(decodedFile)}`;

  const { data, loading, error } = useResource(() => fetchLabels(scope, key, decodedFile), [scope, key, decodedFile]);

  // Scene navigation (prev/next within the same house). Fetch the house's
  // full scene list once per (scope, key); compute index from the current
  // file. Re-fetches only when the house changes, not when the scene does.
  const { data: houseScenes } = useResource(
    async () => {
      if (scope === 'synthetic') {
        const h = await fetchSynthetic(key);
        return h.drawings.map((d) => ({ file: d.file, title: d.title ?? d.file }));
      }
      const h = await fetchHouse(key);
      return (h.images ?? []).map((i) => ({ file: i.file, title: i.caption ?? i.file }));
    },
    [scope, key],
  );
  const sceneList = houseScenes ?? [];
  const sceneIndex = sceneList.findIndex((s) => s.file === decodedFile);
  const prevScene = sceneIndex > 0 ? sceneList[sceneIndex - 1] : null;
  const nextScene = sceneIndex >= 0 && sceneIndex < sceneList.length - 1 ? sceneList[sceneIndex + 1] : null;

  // Editable state — initialised from `data` once it loads.
  const [labels, setLabels] = useState<Label[]>([]);
  const [sceneTag, setSceneTag] = useState<SceneTag>('nicht_klassifiziert');
  const [imageSize, setImageSize] = useState<[number, number]>([1024, 1024]);
  // Multi-select via Set (M11). Single-label code paths use the helper
  // `primarySelectedId` (the only id when size === 1, else null).
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [tool, setTool] = useState<Tool>('select');
  // Rubber-band selection rectangle (M11). null when not dragging.
  const [rubberBand, setRubberBand] = useState<{ start: Point; current: Point } | null>(null);

  const primarySelectedId = selectedIds.size === 1 ? Array.from(selectedIds)[0] : null;
  // Adapter for the rest of the code that still expects a single `selectedId`.
  const setSelectedId = useCallback((id: string | null) => {
    if (id == null) setSelectedIds(new Set());
    else setSelectedIds(new Set([id]));
  }, []);
  const selectedId = primarySelectedId;
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
  const [pendingStart, setPendingStart] = useState<Point | null>(null);
  const [pendingPolyline, setPendingPolyline] = useState<Point[]>([]);
  const [hoverPt, setHoverPt] = useState<Point | null>(null);
  // Wall chaining: when drawing walls, every committed wall's end becomes
  // the next wall's start so the user can outline a building footprint as
  // a sequence of connected walls without releasing Esc between them.
  // chainAnchor remembers the start of the current chain so we can offer
  // 'close the polygon' when the chain's tip returns to it.
  const [wallChainAnchor, setWallChainAnchor] = useState<Point | null>(null);
  // Bumped every time the chip bar writes a new default so the bar (which
  // reads from localStorage via getDefaults) re-renders to show the new
  // selection. Cheap; getDefaults is a synchronous read.
  const [defaultsRev, setDefaultsRev] = useState(0);
  // Snap target computed on every pointermove during drawing — render at
  // §15's green circle if non-null, and use as the actual commit point on
  // the next click instead of the raw cursor.
  const [snap, setSnap] = useState<SnapTarget | null>(null);
  // M10: when placing a floorplan_opening, the first click might land on a
  // wall (wall_line snap). We remember that wall's id so the second click
  // can attach the opening to it via a belongs_to relation.
  const [pendingAttachedWallId, setPendingAttachedWallId] = useState<string | null>(null);
  // M12 inline edit: render a floating <input> instead of using window.prompt.
  // `wasJustCreated` lets Esc delete the freshly-placed label as if the user
  // cancelled placement. `autoLinkAsDimNumber` is set when the inline edit
  // belongs to a dim_distance — on commit we additionally create a
  // dim_number at the given point with a labels-relation back to labelId.
  const [pendingInlineEdit, setPendingInlineEdit] = useState<{
    labelId: string;
    field: 'text' | 'value_mm';
    screenPos: [number, number];
    wasJustCreated: boolean;
    autoLinkAsDimNumber?: { at: Point };
  } | null>(null);
  // M13: keyboard cheatsheet overlay.
  const [cheatsheetOpen, setCheatsheetOpen] = useState(false);
  // M12 toasts.
  const [toasts, setToasts] = useState<Array<{ id: string; message: string; tone: 'info' | 'success' | 'warn' | 'error' }>>([]);
  const addToast = useCallback((message: string, tone: 'info' | 'success' | 'warn' | 'error' = 'info', ttl: number = 2500) => {
    const id = Math.random().toString(36).slice(2);
    setToasts((prev) => [...prev, { id, message, tone }]);
    if (ttl > 0) {
      window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), ttl);
    }
  }, []);
  // M12 auto-save (off by default).
  const [autosave, setAutosave] = useState<boolean>(() => {
    try { return window.localStorage.getItem('bim-db:annotate:autosave') === 'true'; }
    catch { return false; }
  });
  // Global drag flag — set true during any handle / body drag. The right
  // rail dims to near-invisible while it's true so a wall being dragged
  // doesn't disappear visually behind the inspector overlay.
  const [isDragging, setIsDragging] = useState(false);
  // "Alle Werkzeuge" override: ignore tag-gating and show every tool.
  // Useful when the user wants flexibility (e.g. tag=nicht_klassifiziert
  // but they still want to drop a dim_distance to bootstrap the homography).
  const [allTools, setAllTools] = useState<boolean>(() => {
    try { return window.localStorage.getItem('bim-db:annotate:all-tools') === 'true'; }
    catch { return false; }
  });

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
      redoStackRef.current = [];
      setSelectedIds(new Set());
      setDirty(false);
    }
  }, [data]);

  // Remember the last-visited scene for this house so opening the house
  // again (from the synthetic overview card) resumes here instead of
  // jumping back to the hero scene.
  useEffect(() => {
    if (key && decodedFile) {
      rememberLastVisitedScene(scope, key, decodedFile);
    }
  }, [scope, key, decodedFile]);

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
    addToast('↶ Rückgängig', 'info', 1500);
  }, [labels, sceneTag, setSelectedId, addToast]);

  const redo = useCallback(() => {
    const snap = redoStackRef.current.pop();
    if (!snap) return;
    undoStackRef.current.push({ labels: [...labels], scene_tag: sceneTag });
    setLabels(snap.labels);
    setSceneTag(snap.scene_tag);
    setDirty(true);
    setSelectedId(null);
    setPendingStart(null);
    addToast('↷ Wiederherstellen', 'info', 1500);
  }, [labels, sceneTag, setSelectedId, addToast]);

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
      const pt = snap?.pt ?? rawPt;
      const imageSnapRadiusForView =
        (SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1);

      // ── 2-click tools (line) ─────────────────────────────────────────────
      if (tool === 'dimensioned_distance' || tool === 'wall') {
        if (pendingStart == null) {
          setPendingStart(pt);
          // Mark this as the chain anchor — if the user keeps drawing
          // walls, they all originate from this point's polygon.
          if (tool === 'wall') setWallChainAnchor(pt);
          return;
        }
        pushUndo();
        let label: Label;
        if (tool === 'dimensioned_distance') {
          const def = getDefaults(scope, key, sceneTag, 'dimensioned_distance');
          label = {
            id: uuid(),
            type: 'dimensioned_distance',
            geometry: { start: pendingStart, end: pt },
            attributes: {
              value_mm: null,
              target_orientation: (def.target_orientation as DimensionedDistanceLabel['attributes']['target_orientation']) ?? 'unknown',
              is_reference: (def.is_reference as boolean) ?? false,
            },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as DimensionedDistanceLabel;
        } else {
          const def = getDefaults(scope, key, sceneTag, 'wall');
          label = {
            id: uuid(),
            type: 'wall',
            geometry: { start: pendingStart, end: pt },
            attributes: { thickness_mm: (def.thickness_mm as number) ?? 365 },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as WallLabel;
        }
        setLabels([...labels, label]);
        setDirty(true);
        // Wall chaining: keep drawing — the next wall starts where this one
        // ended. The 'closing the polygon' case (user clicked back near the
        // chain anchor) breaks the chain so the user isn't auto-extended
        // out of a closed shape.
        if (tool === 'wall') {
          const closedToAnchor = wallChainAnchor && Math.hypot(pt[0] - wallChainAnchor[0], pt[1] - wallChainAnchor[1])
              < (imageSnapRadiusForView * 2);
          if (closedToAnchor) {
            setPendingStart(null);
            setWallChainAnchor(null);
            addToast('Polygon geschlossen ✓', 'success', 1500);
          } else {
            setPendingStart(pt);
          }
        } else if (tool === 'dimensioned_distance') {
          // Break the pending start — each dim_distance is its own pair of
          // clicks, no auto-chain. (We tried chaining and the leftover
          // phantom preview line from the previous endpoint confused users.)
          setPendingStart(null);
          // Open an inline edit at the LINE MIDPOINT (in screen coords) so
          // the input visually attaches to the dimension. On commit, also
          // create a paired dim_number with a labels-relation — so users
          // never need to think about "Maßzahl" as a separate tool.
          const midSvg: Point = [(pendingStart[0] + pt[0]) / 2, (pendingStart[1] + pt[1]) / 2];
          let midScreen: [number, number] = [e.clientX, e.clientY];
          const svg = svgRef.current;
          const ctm = svg?.getScreenCTM();
          if (ctm) {
            midScreen = [
              midSvg[0] * ctm.a + midSvg[1] * ctm.c + ctm.e,
              midSvg[0] * ctm.b + midSvg[1] * ctm.d + ctm.f,
            ];
          }
          setPendingInlineEdit({
            labelId: label.id,
            field: 'value_mm',
            screenPos: midScreen,
            wasJustCreated: false,
            autoLinkAsDimNumber: { at: midSvg },
          });
        } else {
          setPendingStart(null);
        }
        setSelectedIds(new Set([label.id]));
        return;
      }

      // ── 2-click tools (rectangle) ────────────────────────────────────────
      if (tool === 'floorplan_opening' || tool === 'view_opening') {
        if (pendingStart == null) {
          setPendingStart(pt);
          // M10: if the first click landed on a wall (wall_line snap), the
          // opening will attach to that wall — remember its id for commit.
          if (tool === 'floorplan_opening' && snap?.kind === 'wall_line') {
            setPendingAttachedWallId(snap.source_label_id ?? null);
          } else {
            setPendingAttachedWallId(null);
          }
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
          const secondClickAttached =
            snap?.kind === 'wall_line' && snap.source_label_id === pendingAttachedWallId;
          const attachWallId = pendingAttachedWallId && (secondClickAttached || !snap?.source_label_id || snap?.kind !== 'wall_line')
            ? pendingAttachedWallId
            : (snap?.kind === 'wall_line' ? snap.source_label_id : null);
          const def = getDefaults(scope, key, sceneTag, 'floorplan_opening');

          // M10/UX fix: when attaching to a wall, ROTATE the rectangle to
          // align with the wall axis. Without this, the user has to manually
          // diagonal an axis-aligned rect against an angled wall — annoying
          // and ambiguous. We build the quad along/perpendicular to the
          // wall's unit vector. The two clicks define the along-wall extent
          // (= width); the perpendicular thickness comes from the wall's
          // thickness_mm (or a sane default if missing).
          let quad: Quad;
          if (attachWallId) {
            const parent = labels.find((l) => l.id === attachWallId && l.type === 'wall');
            if (parent && parent.type === 'wall') {
              const ws = parent.geometry.start;
              const we = parent.geometry.end;
              const wdx = we[0] - ws[0];
              const wdy = we[1] - ws[1];
              const wlen = Math.hypot(wdx, wdy);
              const ux = wlen ? wdx / wlen : 1;
              const uy = wlen ? wdy / wlen : 0;
              // Project both clicked points onto the wall axis (relative to
              // wall.start) to get along-wall positions. That gives the
              // opening's along-wall extent.
              const projA = (pendingStart[0] - ws[0]) * ux + (pendingStart[1] - ws[1]) * uy;
              const projB = (pt[0] - ws[0]) * ux + (pt[1] - ws[1]) * uy;
              const t0 = Math.min(projA, projB);
              const t1 = Math.max(projA, projB);
              // Center the opening on the wall axis; depth = wall thickness.
              const thicknessMm = parent.attributes.thickness_mm ?? 365;
              const depthHalfPx = (thicknessMm * WALL_PX_PER_MM) / 2;
              const px = -uy;
              const py = ux;
              const along = (t: number): Point => [ws[0] + ux * t, ws[1] + uy * t];
              const a: Point = [along(t0)[0] + px * depthHalfPx, along(t0)[1] + py * depthHalfPx];
              const b: Point = [along(t1)[0] + px * depthHalfPx, along(t1)[1] + py * depthHalfPx];
              const c: Point = [along(t1)[0] - px * depthHalfPx, along(t1)[1] - py * depthHalfPx];
              const d: Point = [along(t0)[0] - px * depthHalfPx, along(t0)[1] - py * depthHalfPx];
              quad = [a, b, c, d];
            } else {
              quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
            }
          } else {
            quad = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
          }

          // Derive the actual along-wall width in mm if attached + the
          // user didn't pre-set it via defaults. Helps cross-check later.
          let derivedWidthMm: number | null = null;
          if (attachWallId) {
            const al = Math.hypot(quad[1][0] - quad[0][0], quad[1][1] - quad[0][1]);
            derivedWidthMm = Math.round(al / WALL_PX_PER_MM);
          }

          label = {
            id: uuid(),
            type: 'floorplan_opening',
            geometry: { quad },
            attributes: {
              opening_kind: (def.opening_kind as FloorplanOpeningLabel['attributes']['opening_kind']) ?? 'window',
              width_mm: (def.width_mm as number | null) ?? derivedWidthMm,
              swing: (def.swing as FloorplanOpeningLabel['attributes']['swing']) ?? 'none',
              swing_side: (def.swing_side as FloorplanOpeningLabel['attributes']['swing_side']) ?? 'none',
            },
            status: 'readable',
            relations: attachWallId
              ? [{ other_id: attachWallId, kind: 'belongs_to' }]
              : [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as FloorplanOpeningLabel;
        } else {
          const def = getDefaults(scope, key, sceneTag, 'view_opening');
          label = {
            id: uuid(),
            type: 'view_opening',
            geometry: {
              top_edge: [[x0, y0], [x1, y0]],
              bottom_edge: [[x0, y1], [x1, y1]],
            },
            attributes: {
              opening_kind: (def.opening_kind as ViewOpeningLabel['attributes']['opening_kind']) ?? 'window',
              frame_visible: (def.frame_visible as boolean) ?? true,
            },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as ViewOpeningLabel;
        }
        setLabels([...labels, label]);
        setDirty(true);
        setPendingStart(null);
        setPendingAttachedWallId(null);
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
      // M12: inline-edit replaces window.prompt. Place the label first, then
      // open a floating <input> at the cursor's screen position.
      if (tool === 'height_mark') {
        pushUndo();
        const label: HeightMarkLabel = {
          id: uuid(),
          type: 'height_mark',
          geometry: { anchor: pt },
          attributes: { value_mm: null, reference_line_id: null },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels([...labels, label]);
        setDirty(true);
        setSelectedIds(new Set([label.id]));
        setPendingInlineEdit({
          labelId: label.id,
          field: 'value_mm',
          screenPos: [e.clientX, e.clientY],
          wasJustCreated: true,
        });
        return;
      }

      if (tool === 'dimension_number') {
        pushUndo();
        const label: DimensionNumberLabel = {
          id: uuid(),
          type: 'dimension_number',
          geometry: { anchor: pt },
          attributes: { text: '', parsed_value_mm: null },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels([...labels, label]);
        setDirty(true);
        setSelectedIds(new Set([label.id]));
        setPendingInlineEdit({
          labelId: label.id,
          field: 'text',
          screenPos: [e.clientX, e.clientY],
          wasJustCreated: true,
        });
        return;
      }

      // tool === 'select' — left-click + drag on empty canvas → rubber-band
      // multi-select. Plain click (no drag) clears selection unless Shift
      // is held (in which case we keep current selection — additive mode).
      if (tool === 'select') {
        const startPt = pt;
        let dragged = false;
        const onMove = (mv: PointerEvent) => {
          const next = eventToSvgPoint(mv);
          if (!next) return;
          if (!dragged && Math.hypot(next[0] - startPt[0], next[1] - startPt[1]) > 4) {
            dragged = true;
          }
          if (dragged) setRubberBand({ start: startPt, current: next });
        };
        const onUp = (mv: PointerEvent) => {
          window.removeEventListener('pointermove', onMove);
          window.removeEventListener('pointerup', onUp);
          if (dragged) {
            // Compute the rubber-band rectangle in image-pixel coords and
            // select every label whose centroid falls inside.
            const endPt = eventToSvgPoint(mv) ?? startPt;
            const x0 = Math.min(startPt[0], endPt[0]);
            const y0 = Math.min(startPt[1], endPt[1]);
            const x1 = Math.max(startPt[0], endPt[0]);
            const y1 = Math.max(startPt[1], endPt[1]);
            const inside = labels.filter((l) => {
              const c = labelCentroid(l);
              return c[0] >= x0 && c[0] <= x1 && c[1] >= y0 && c[1] <= y1;
            });
            if (mv.shiftKey) {
              // Additive: union with current selection.
              setSelectedIds((prev) => {
                const next = new Set(prev);
                for (const l of inside) next.add(l.id);
                return next;
              });
            } else {
              setSelectedIds(new Set(inside.map((l) => l.id)));
            }
          } else if (!mv.shiftKey) {
            // Plain click on empty area → deselect everything.
            setSelectedIds(new Set());
          }
          setRubberBand(null);
        };
        window.addEventListener('pointermove', onMove);
        window.addEventListener('pointerup', onUp);
      }
    },
    [tool, pendingStart, labels, pushUndo, eventToSvgPoint, view, snap, pendingAttachedWallId],
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

  // Wheel handling — zoom is EXPLICITLY only via the +/-/FIT buttons (or
  // their keyboard equivalents). Wheel events only pan, and pinch / ⌘+wheel
  // are swallowed (preventDefault) so the browser doesn't zoom the page
  // and the canvas stays put. This is by user request — the implicit
  // gestures kept zooming when they wanted to pan.
  const onCanvasWheel = useCallback(
    (e: ReactWheelEvent<SVGSVGElement>) => {
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        // Pinch (Mac trackpad sets ctrlKey=true) or ⌘/Ctrl+wheel — swallow.
        return;
      }
      const svg = svgRef.current;
      if (!svg) return;
      // Pan only — convert screen-pixel delta to image-pixel delta using the
      // current view-to-screen scale.
      const factor = view.w / Math.max(1, svg.clientWidth);
      setView((v) => ({
        x: v.x + e.deltaX * factor,
        y: v.y + e.deltaY * factor,
        w: v.w,
        h: v.h,
      }));
    },
    [view],
  );

  const resetView = useCallback(() => {
    setView({ x: 0, y: 0, w: imageSize[0], h: imageSize[1] });
  }, [imageSize]);

  // Zoom by a discrete factor centered on the SVG viewport center (used by
  // the +/- keyboard shortcuts and the in-canvas zoom buttons). Factor < 1
  // = zoom in; > 1 = zoom out.
  const zoomBy = useCallback((factor: number) => {
    setView((v) => {
      const cx = v.x + v.w / 2;
      const cy = v.y + v.h / 2;
      const newW = Math.max(50, Math.min(v.w * factor, imageSize[0] * 8));
      const newH = newW * (v.h / v.w);
      return { x: cx - newW / 2, y: cy - newH / 2, w: newW, h: newH };
    });
  }, [imageSize]);

  // ── label mutation helpers ────────────────────────────────────────────────
  const updateLabel = useCallback((id: string, patch: Partial<Label>) => {
    pushUndo();
    setLabels((prev) =>
      prev.map((l) => {
        if (l.id !== id) return l;
        const merged = { ...l, ...patch, updated_at: nowIso() } as Label;
        // M13: any attribute change is a signal — remember it as the default
        // for the next label of this type in this (scope, scene_tag).
        if (patch.attributes) {
          rememberDefaults(scope, key, sceneTag, merged.type, merged.attributes as Record<string, unknown>);
        }
        return merged;
      }),
    );
    setDirty(true);
  }, [pushUndo, scope, key, sceneTag]);

  const deleteLabel = useCallback((id: string) => {
    // M10: walls with attached openings prompt for cascade behaviour.
    const target = labels.find((l) => l.id === id);
    let deleteAlsoIds: string[] = [];
    if (target?.type === 'wall') {
      const attached = labels.filter((l) =>
        l.type === 'floorplan_opening' &&
        (l.relations ?? []).some((r) => r.kind === 'belongs_to' && r.other_id === id),
      );
      if (attached.length > 0) {
        const ans = window.prompt(
          `${attached.length} angehängte Öffnung(en) gefunden.\n\n` +
          'Tippe DELETE um die Öffnungen mitzulöschen,\n' +
          'oder OK / leer / ESC um nur den belongs_to-Bezug zu lösen.',
          '',
        );
        if (ans === null) return;  // explicit cancel
        if (ans.trim().toUpperCase() === 'DELETE') {
          deleteAlsoIds = attached.map((l) => l.id);
        }
      }
    }
    pushUndo();
    const idsToDelete = new Set([id, ...deleteAlsoIds]);
    setLabels((prev) =>
      prev
        .filter((l) => !idsToDelete.has(l.id))
        // Also strip any relations pointing at the deleted label(s).
        .map((l) => ({
          ...l,
          relations: (l.relations ?? []).filter((r) => !idsToDelete.has(r.other_id)),
        }) as Label),
    );
    if (selectedId && idsToDelete.has(selectedId)) setSelectedId(null);
    setDirty(true);
  }, [labels, pushUndo, selectedId]);

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
          if (existing.some((r) => r.other_id === distanceId && r.kind === 'labels')) {
            return l;
          }
          return { ...l, relations: [...existing, { other_id: distanceId, kind: 'labels' }] } as Label;
        }),
      );
      setDirty(true);
      addToast('🔗 Verknüpft', 'success', 1500);
    },
    [labels, pushUndo, addToast],
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

  // Scene navigation — prev/next within the same house. Dirty-state guard:
  // if there are unsaved changes, prompt before navigating away. Autosave
  // users skip the prompt (their work is/will be persisted).
  const goToScene = useCallback((targetFile: string) => {
    if (dirty && !autosave) {
      const ok = window.confirm(
        'Ungespeicherte Änderungen.\n\n' +
        'OK = trotzdem weiter (Änderungen gehen verloren),\n' +
        'Abbrechen = hier bleiben (Cmd+S um zu speichern).',
      );
      if (!ok) return;
    }
    const path = scope === 'synthetic'
      ? `/synthetic/${key}/scene/${encodeURIComponent(targetFile)}/annotate`
      : `/house/${key}/scene/${encodeURIComponent(targetFile)}/annotate`;
    navigate(path);
  }, [dirty, autosave, scope, key, navigate]);

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
      addToast(`✓ Gespeichert (${labels.length} Labels)`, 'success');
    } catch (e) {
      addToast(`✗ Speichern fehlgeschlagen: ${(e as Error).message}`, 'error', 6000);
    } finally {
      setSaving(false);
    }
  }, [data, key, decodedFile, sceneTag, labels, imageSize, scope, addToast]);

  // M12 auto-save: when enabled + dirty, schedule a save after 30 s of
  // inactivity. Any new edit resets the timer.
  useEffect(() => {
    if (!autosave || !dirty || saving) return;
    const t = window.setTimeout(() => save(), 30_000);
    return () => window.clearTimeout(t);
  }, [autosave, dirty, saving, save]);

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
        setSelectedId(null);
        setSnap(null);
        setWallChainAnchor(null);
        return;
      }
      if (e.key === 'Enter' && tool === 'component_line' && pendingPolyline.length >= 2) {
        e.preventDefault();
        pushUndo();
        const def = getDefaults(scope, key, sceneTag, 'component_line');
        const label: ComponentLineLabel = {
          id: uuid(),
          type: 'component_line',
          geometry: { polyline: pendingPolyline },
          attributes: { line_kind: (def.line_kind as ComponentLineLabel['attributes']['line_kind']) ?? 'other' },
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
        if (selectedIds.size > 0) {
          e.preventDefault();
          // Delete all selected. For walls with attached openings, the
          // cascade prompt fires on the first wall encountered (good enough
          // for now; could batch later if needed).
          for (const id of Array.from(selectedIds)) {
            deleteLabel(id);
          }
        }
      }
      // M11: Cmd/Ctrl+A selects all
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'a') {
        e.preventDefault();
        setSelectedIds(new Set(labels.map((l) => l.id)));
        return;
      }
      // M13: '?' opens the cheatsheet.
      if (e.key === '?') {
        e.preventDefault();
        setCheatsheetOpen((v) => !v);
        return;
      }
      // Scene navigation: ',' prev, '.' next (both with + without Shift).
      if (e.key === ',' || e.key === '<') {
        e.preventDefault();
        if (prevScene) goToScene(prevScene.file);
        return;
      }
      if (e.key === '.' || e.key === '>') {
        e.preventDefault();
        if (nextScene) goToScene(nextScene.file);
        return;
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
      // current scene_tag (or 'allTools' override is on).
      const allowed = allTools ? TOOLS_BY_TAG.sonstiges : TOOLS_BY_TAG[sceneTag];
      const trySetTool = (t: Tool) => {
        if (allowed.includes(t)) setTool(t);
      };
      if (e.key === 'd') trySetTool('dimensioned_distance');
      if (e.key === 'n') trySetTool('dimension_number');
      if (e.key === 'w') trySetTool('wall');
      if (e.key === 'o') trySetTool(sceneTag === 'grundriss' ? 'floorplan_opening' : 'view_opening');
      if (e.key === 'l') trySetTool('component_line');
      if (e.key === 'h' && !e.metaKey && !e.ctrlKey) trySetTool('height_mark');
      if (e.key === 's' && !e.metaKey && !e.ctrlKey) trySetTool('select');
      if (e.key === 'r') resetView();
      if (e.key === '+' || (e.key === '=' && !e.shiftKey)) { e.preventDefault(); zoomBy(0.7); }
      if (e.key === '-' || e.key === '_') { e.preventDefault(); zoomBy(1.4); }
      if (e.key === '0') { e.preventDefault(); resetView(); }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [save, undo, redo, selectedIds, selectedId, deleteLabel, resetView, zoomBy, tool, pendingPolyline, pushUndo, sceneTag, labels, updateLabel, prevScene, nextScene, goToScene, allTools]);

  const selectedLabel = labels.find((l) => l.id === selectedId) ?? null;
  const viewBox = `${view.x} ${view.y} ${view.w} ${view.h}`;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: scope === 'synthetic' ? 'Synthetisch' : 'Alle Häuser', to: scope === 'synthetic' ? '/synthetic' : '/' },
            // House name is plain text — the overview page is now the
            // entry point and the click-through goes straight to
            // annotation, so an intermediate "house detail" link doesn't
            // belong here.
            { label: key },
            { label: `Annotieren: ${decodedFile}` },
          ]}
        />
      }
      topbarTrailing={
        <div className="flex items-center gap-2">
          {sceneList.length > 1 && (
            <div className="flex items-center gap-1 border border-border rounded-md p-0.5 bg-white max-w-[44vw] overflow-x-auto"
                 title="Szene wechseln — , = vorige, . = nächste">
              <button
                type="button"
                onClick={() => prevScene && goToScene(prevScene.file)}
                disabled={!prevScene}
                aria-label="Vorige Szene (,)"
                className={`w-6 h-6 inline-flex items-center justify-center rounded shrink-0 ${
                  prevScene ? 'hover:bg-zinc-100 text-zinc-700' : 'text-zinc-300 cursor-not-allowed'
                }`}
              >
                ‹
              </button>
              {/* All scenes as small chips — gives the user an at-a-glance
                  overview of the house's scenes plus quick jump-to. The
                  active scene is the accent-coloured chip. */}
              <div className="flex items-center gap-0.5 px-0.5">
                {sceneList.map((s) => {
                  const isCurrent = s.file === decodedFile;
                  return (
                    <button
                      key={s.file}
                      type="button"
                      onClick={() => goToScene(s.file)}
                      title={s.title}
                      className={`px-1.5 py-0.5 rounded text-[0.65rem] font-medium tabular-nums whitespace-nowrap shrink-0 transition ${
                        isCurrent
                          ? 'bg-accent text-white'
                          : 'text-zinc-600 hover:bg-zinc-100'
                      }`}
                    >
                      {sceneShortLabel(s.file, s.title)}
                    </button>
                  );
                })}
              </div>
              <button
                type="button"
                onClick={() => nextScene && goToScene(nextScene.file)}
                disabled={!nextScene}
                aria-label="Nächste Szene (.)"
                className={`w-6 h-6 inline-flex items-center justify-center rounded shrink-0 ${
                  nextScene ? 'hover:bg-zinc-100 text-zinc-700' : 'text-zinc-300 cursor-not-allowed'
                }`}
              >
                ›
              </button>
            </div>
          )}
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
          <button
            type="button"
            onClick={() => setCheatsheetOpen(true)}
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-muted hover:bg-zinc-100 hover:text-zinc-900"
            title="Tastaturkürzel (?)"
            aria-label="Tastaturkürzel anzeigen"
          >
            ?
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
          autosave={autosave}
          onToggleAutosave={() => {
            setAutosave((v) => {
              const next = !v;
              try { window.localStorage.setItem('bim-db:annotate:autosave', String(next)); } catch { /* no-op */ }
              addToast(next ? 'Auto-Save aktiviert (30 s)' : 'Auto-Save deaktiviert', 'info');
              return next;
            });
          }}
          onResetDefaults={() => {
            clearDefaults(scope, key, sceneTag);
            addToast(`Defaults für "${sceneTag}" zurückgesetzt`, 'info');
          }}
          allTools={allTools}
          onToggleAllTools={() => {
            setAllTools((v) => {
              const next = !v;
              try { window.localStorage.setItem('bim-db:annotate:all-tools', String(next)); } catch { /* no-op */ }
              return next;
            });
          }}
          scope={scope}
          houseKey={key}
          defaultsRev={defaultsRev}
          onDefaultsChange={() => setDefaultsRev((v) => v + 1)}
        />
      }
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
          <defs>
            {/* Mauerwerk hatching for wall bands — diagonal lines at low
                opacity, applied as an overlay fill on top of the solid wall
                color. Sized in viewBox units so it scales with zoom. */}
            <pattern id="bim-wall-hatch" patternUnits="userSpaceOnUse"
                     width="9" height="9" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="9" stroke="#7c3aed"
                    strokeWidth="0.7" opacity="0.35" />
            </pattern>
          </defs>
          <image href={imageUrl} x={0} y={0} width={imageSize[0]} height={imageSize[1]} />
          {/* Linking visuals — dashed lines between number ↔ distance pairs */}
          <LinkVisuals labels={labels} selectedId={selectedId} />
          {/* Existing labels */}
          {labels.map((l) => (
            <LabelGlyph
              key={l.id}
              label={l}
              selected={selectedIds.has(l.id)}
              tool={tool}
              allLabels={labels}
              imageSnapRadius={(SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1)}
              eventToSvgPoint={eventToSvgPoint}
              onDragStateChange={setIsDragging}
              onSnapChange={setSnap}
              onSelect={(modifiers) => {
                // M11 multi-select: Shift+click toggles individual; plain
                // click replaces selection.
                if (modifiers?.shift) {
                  setSelectedIds((prev) => {
                    const next = new Set(prev);
                    if (next.has(l.id)) next.delete(l.id);
                    else next.add(l.id);
                    return next;
                  });
                } else {
                  setSelectedIds(new Set([l.id]));
                }
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
          {/* Close-polygon hint — when the wall chain anchor is set and the
              hover cursor approaches it, surface a green ring so the user
              knows clicking here will close + break the chain. */}
          {wallChainAnchor && hoverPt && tool === 'wall' && (() => {
            const d = Math.hypot(hoverPt[0] - wallChainAnchor[0], hoverPt[1] - wallChainAnchor[1]);
            const radius = (SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1) * 2;
            if (d > radius * 2) return null;
            const near = d < radius;
            return (
              <g pointerEvents="none">
                <circle
                  cx={wallChainAnchor[0]} cy={wallChainAnchor[1]} r={radius}
                  fill={near ? 'rgba(22, 163, 74, 0.20)' : 'none'}
                  stroke={near ? '#16a34a' : '#94a3b8'}
                  strokeWidth={(near ? 2.5 : 1.5) * (view.w / imageSize[0])}
                  strokeDasharray={near ? '0' : '4,3'}
                />
                {near && (
                  <text
                    x={wallChainAnchor[0] + radius + 8}
                    y={wallChainAnchor[1]}
                    fill="#16a34a"
                    fontSize={11 * (view.w / imageSize[0])}
                    fontFamily="ui-monospace, monospace"
                    style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 * (view.w / imageSize[0]) }}
                  >
                    Klick = Polygon schließen
                  </text>
                )}
              </g>
            );
          })()}
          {/* M11 rubber-band rectangle */}
          {rubberBand && (
            <rect
              x={Math.min(rubberBand.start[0], rubberBand.current[0])}
              y={Math.min(rubberBand.start[1], rubberBand.current[1])}
              width={Math.abs(rubberBand.current[0] - rubberBand.start[0])}
              height={Math.abs(rubberBand.current[1] - rubberBand.start[1])}
              fill="rgba(37, 99, 235, 0.10)"
              stroke="#2563eb"
              strokeWidth={1 / Math.max(0.1, view.w / imageSize[0])}
              strokeDasharray="6,4"
              pointerEvents="none"
            />
          )}
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
        {/* Sub-classification is now in the sidebar via FamilyToolButton —
            the previous top-of-canvas chip bar is gone (redundant). */}
        <div className="absolute bottom-3 left-3 text-[0.7rem] text-zinc-300 bg-black/50 px-2 py-1 rounded leading-snug pointer-events-none">
          [S] Select · [D] Bemaßung · [W] Wand · [O] Öffnung · [L] Linie · [H] Höhe
          <br />
          Zoom: nur +/-/FIT (oder Tasten +/-/0) · Pan: 2-Finger-Scroll oder Shift+Drag
        </div>
        {/* Zoom controls — bottom-right corner. Visible alternative to the
            wheel/trackpad gestures and the +/-/0 hotkeys. */}
        <div className="absolute bottom-3 right-3 flex flex-col gap-1 bg-white/95 border border-zinc-300 rounded shadow-md p-1">
          <button
            type="button"
            onClick={() => zoomBy(0.7)}
            className="w-7 h-7 inline-flex items-center justify-center rounded hover:bg-zinc-100 text-zinc-800 text-lg leading-none"
            title="Hereinzoomen (+)"
          >
            +
          </button>
          <button
            type="button"
            onClick={() => zoomBy(1.4)}
            className="w-7 h-7 inline-flex items-center justify-center rounded hover:bg-zinc-100 text-zinc-800 text-lg leading-none"
            title="Herauszoomen (−)"
          >
            −
          </button>
          <button
            type="button"
            onClick={resetView}
            className="w-7 h-7 inline-flex items-center justify-center rounded hover:bg-zinc-100 text-zinc-700 text-[0.6rem] font-semibold leading-none"
            title="Ansicht zurücksetzen (0/R)"
          >
            FIT
          </button>
        </div>
        <DrawHUD tool={tool} pendingStart={pendingStart} hoverPt={hoverPt} pendingPolyline={pendingPolyline} snap={snap} />
        {/* M12 inline edit. <input> floats at the cursor; Enter commits, Esc
            cancels (and deletes the freshly-created label so the user can
            click again). */}
        {pendingInlineEdit && (
          <InlineEditInput
            key={pendingInlineEdit.labelId}
            screenPos={pendingInlineEdit.screenPos}
            placeholder={pendingInlineEdit.field === 'text' ? 'z. B. 1,75' : 'z. B. 1,75 oder 1750'}
            onCommit={(value) => {
              const trimmed = value.trim();
              if (trimmed === '') {
                if (pendingInlineEdit.wasJustCreated) {
                  setLabels((prev) => prev.filter((l) => l.id !== pendingInlineEdit.labelId));
                  setSelectedIds(new Set());
                }
                setPendingInlineEdit(null);
                return;
              }
              const parsed = parseGermanNumber(trimmed);
              if (pendingInlineEdit.field === 'text') {
                updateLabel(pendingInlineEdit.labelId, {
                  attributes: { text: trimmed, parsed_value_mm: parsed },
                } as Partial<Label>);
              } else {
                // Update the dim_distance.
                const distanceLabel = labels.find((l) => l.id === pendingInlineEdit.labelId);
                const isDistance = distanceLabel?.type === 'dimensioned_distance';
                updateLabel(pendingInlineEdit.labelId, isDistance
                  ? ({ attributes: { value_mm: parsed } } as Partial<Label>)
                  : ({ attributes: { value_mm: parsed, reference_line_id: null }, notes: trimmed } as Partial<Label>));
                // If this inline edit belongs to a freshly-placed dim_distance,
                // automatically create a paired dim_number at the line's
                // midpoint with a labels-relation. That removes the need for
                // the user to think about "Maßzahl" or "Verknüpfen" as
                // separate tools.
                if (pendingInlineEdit.autoLinkAsDimNumber && isDistance) {
                  const numberLabel: DimensionNumberLabel = {
                    id: uuid(),
                    type: 'dimension_number',
                    geometry: { anchor: pendingInlineEdit.autoLinkAsDimNumber.at },
                    attributes: { text: trimmed, parsed_value_mm: parsed },
                    status: 'readable',
                    relations: [{ other_id: pendingInlineEdit.labelId, kind: 'labels' }],
                    created_at: nowIso(),
                    updated_at: nowIso(),
                  };
                  setLabels((prev) => [...prev, numberLabel]);
                }
              }
              setPendingInlineEdit(null);
            }}
            onCancel={() => {
              if (pendingInlineEdit.wasJustCreated) {
                setLabels((prev) => prev.filter((l) => l.id !== pendingInlineEdit.labelId));
                setSelectedIds(new Set());
              }
              setPendingInlineEdit(null);
            }}
          />
        )}
        {/* M12 toast stack — bottom-center over the canvas */}
        <ToastStack toasts={toasts} />
        {/* M13 keyboard cheatsheet (toggle with '?') */}
        {cheatsheetOpen && (
          <Cheatsheet onClose={() => setCheatsheetOpen(false)} />
        )}
        {/* Floating inspector popover — replaces the old fixed right-rail.
            Dragable header, position persists in localStorage. Fades during
            label-drag so it doesn't visually obscure the moving geometry. */}
        {/* Hide the popover while an inline edit is open — otherwise both
            pop up after a dim_distance commit and visually compete. */}
        {!pendingInlineEdit && (selectedIds.size > 1 || selectedLabel) && (
          <div style={{
            opacity: isDragging ? 0.05 : 1,
            pointerEvents: isDragging ? 'none' : 'auto',
            transition: 'opacity 120ms',
          }}>
            <FloatingPopover
              title={
                selectedIds.size > 1
                  ? `${selectedIds.size} Labels`
                  : selectedLabel ? `Inspector: ${selectedLabel.type}` : 'Inspector'
              }
              storageKey="inspector"
              onClose={() => setSelectedIds(new Set())}
            >
              {selectedIds.size > 1 ? (
                <MultiInspector
                  labels={labels.filter((l) => selectedIds.has(l.id))}
                  onBulkStatus={(status) => {
                    pushUndo();
                    setLabels((prev) =>
                      prev.map((l) =>
                        selectedIds.has(l.id) ? ({ ...l, status, updated_at: nowIso() } as Label) : l,
                      ),
                    );
                    setDirty(true);
                  }}
                  onBulkDelete={() => {
                    const ids = Array.from(selectedIds);
                    for (const id of ids) deleteLabel(id);
                  }}
                  onClear={() => setSelectedIds(new Set())}
                />
              ) : selectedLabel ? (
                <Inspector
                  label={selectedLabel}
                  allLabels={labels}
                  onChange={(patch) => updateLabel(selectedLabel.id, patch)}
                  onDelete={() => deleteLabel(selectedLabel.id)}
                  onUnlink={(otherId) => {
                    const sel = selectedLabel;
                    if (sel.type === 'dimension_number') {
                      unlinkPair(sel.id, otherId);
                    } else if (sel.type === 'dimensioned_distance') {
                      unlinkPair(otherId, sel.id);
                    }
                  }}
                  onSelectId={(id) => setSelectedIds(new Set([id]))}
                  onLinkTo={(otherId) => {
                    if (!selectedLabel) return;
                    if (selectedLabel.type === 'dimension_number') linkPair(selectedLabel.id, otherId);
                    else if (selectedLabel.type === 'dimensioned_distance') linkPair(otherId, selectedLabel.id);
                  }}
                />
              ) : null}
            </FloatingPopover>
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
  autosave,
  onToggleAutosave,
  onResetDefaults,
  allTools,
  onToggleAllTools,
  scope,
  houseKey,
  defaultsRev,
  onDefaultsChange,
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
  autosave: boolean;
  onToggleAutosave: () => void;
  onResetDefaults: () => void;
  allTools: boolean;
  onToggleAllTools: () => void;
  scope: LabelScope;
  houseKey: string;
  defaultsRev: number;
  onDefaultsChange: () => void;
}) {
  void defaultsRev;
  return (
    <div className="px-3 py-3 space-y-4">
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Szenen-Tag
        </h3>
        <div className="grid grid-cols-1 gap-px">
          {TAGS.map((t) => {
            const meta = TAG_META[t];
            const active = sceneTag === t;
            return (
              <button
                key={t}
                type="button"
                onClick={() => setSceneTag(t)}
                className={`flex items-center gap-2 px-2 py-1 rounded text-[0.78rem] text-left transition ${
                  active ? 'bg-accent text-white font-semibold' : 'hover:bg-zinc-100'
                }`}
              >
                <meta.Icon size={15} />
                <span>{meta.label}</span>
              </button>
            );
          })}
        </div>
      </section>

      <section>
        <div className="flex items-baseline gap-2 mb-1.5">
          <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">
            Werkzeuge
          </h3>
          <label className="ml-auto text-[0.65rem] text-muted inline-flex items-center gap-1 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={allTools}
              onChange={onToggleAllTools}
              className="accent-accent"
            />
            alle anzeigen
          </label>
        </div>
        <div className="grid grid-cols-1 gap-px">
          {(allTools ? TOOLS_BY_TAG.sonstiges : TOOLS_BY_TAG[sceneTag]).map((t) => {
            const family = findFamily(t);
            // For Öffnung families that exist for both grundriss + ansicht
            // tags, filter by applicableTags so the *wrong* opening tool
            // doesn't show up in the toolbar (e.g. floorplan_opening in
            // ansicht). 'allTools' bypasses this filter.
            if (family && !allTools && !family.applicableTags.includes(sceneTag)) {
              return null;
            }
            if (family) {
              return (
                <FamilyToolButton
                  key={t}
                  family={family}
                  active={tool === family.parentTool}
                  onActivate={() => setTool(family.parentTool)}
                  scope={scope}
                  houseKey={houseKey}
                  sceneTag={sceneTag}
                  onDefaultsChange={onDefaultsChange}
                />
              );
            }
            const meta = TOOL_META[t];
            return (
              <ToolBtn
                key={t}
                current={tool}
                onSet={setTool}
                value={t}
                hotkey={meta.hotkey}
                Icon={meta.Icon}
              >
                {meta.label}
              </ToolBtn>
            );
          })}
        </div>
        {sceneTag === 'nicht_klassifiziert' && !allTools && (
          <p className="text-[0.7rem] text-muted mt-2 leading-snug">
            Setze einen Szenen-Tag oben oder aktiviere „alle anzeigen", damit Werkzeuge verfügbar werden.
          </p>
        )}
      </section>

      <section className="space-y-2">
        <label className="flex items-center gap-2 text-[0.75rem] cursor-pointer">
          <input
            type="checkbox"
            checked={autosave}
            onChange={onToggleAutosave}
            className="accent-accent"
          />
          <span>Auto-Save (30 s, wenn dirty)</span>
        </label>
        <button
          type="button"
          onClick={onResetDefaults}
          className="text-[0.7rem] text-muted hover:text-accent hover:underline"
          title={`Defaults für scope+tag '${sceneTag}' zurücksetzen`}
        >
          Defaults für „{sceneTag}" zurücksetzen
        </button>
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

// A tool family in the sidebar — parent header + indented subtypes shown
// inline whenever the parent tool is active. Picking a subtype writes the
// per-house default so the next-drawn label is pre-classified.
function FamilyToolButton({
  family,
  active,
  onActivate,
  scope,
  houseKey,
  sceneTag,
  onDefaultsChange,
}: {
  family: ToolFamily;
  active: boolean;
  onActivate: () => void;
  scope: LabelScope;
  houseKey: string;
  sceneTag: SceneTag;
  onDefaultsChange: () => void;
}) {
  const def = getDefaults(scope, houseKey, sceneTag, family.parentTool as Label['type']);
  const stored = def[family.attrName];
  let currentValue: string;
  if (family.attrIsBoolean) {
    currentValue = stored === true ? 'true' : stored === false ? 'false' : family.options[0].value;
  } else {
    currentValue = (stored as string) ?? family.options[0].value;
  }
  const FamilyIcon = family.Icon;
  return (
    <div className={`rounded ${active ? 'bg-accent/5 ring-1 ring-accent/30' : ''}`}>
      <button
        type="button"
        onClick={onActivate}
        className={`w-full flex items-center gap-2 px-2 py-1 rounded text-[0.78rem] transition ${
          active ? 'bg-accent text-white font-semibold' : 'hover:bg-zinc-100'
        }`}
      >
        <FamilyIcon size={15} />
        <span className="flex-1 text-left">{family.familyLabel}</span>
        <kbd
          className={`text-[0.6rem] font-mono rounded px-1 py-px ${
            active ? 'bg-white/20 text-white' : 'bg-zinc-200 text-zinc-600'
          }`}
        >
          {family.hotkey}
        </kbd>
      </button>
      {active && (
        <div className="pl-3 pr-1 py-1 space-y-px">
          {family.options.map((opt) => {
            const isCurrent = opt.value === currentValue;
            return (
              <button
                key={opt.value}
                type="button"
                title={opt.hint ?? ''}
                onClick={() => {
                  const storedValue = family.attrIsBoolean ? (opt.value === 'true') : opt.value;
                  rememberDefaults(scope, houseKey, sceneTag, family.parentTool as Label['type'], {
                    [family.attrName]: storedValue,
                  });
                  onDefaultsChange();
                }}
                className={`w-full flex items-center gap-2 px-2 py-1 rounded text-[0.72rem] text-left transition ${
                  isCurrent
                    ? 'bg-accent/15 text-accent font-medium'
                    : 'text-zinc-700 hover:bg-zinc-100'
                }`}
              >
                <span className={`inline-block w-1.5 h-1.5 rounded-full ${isCurrent ? 'bg-accent' : 'bg-zinc-300'}`} />
                <span className="flex-1">{opt.label}</span>
              </button>
            );
          })}
          {family.helpText && (
            <p className="text-[0.62rem] text-muted leading-snug px-2 pt-1 pb-0.5 italic">
              {family.helpText}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function ToolBtn({
  current,
  onSet,
  value,
  hotkey,
  Icon,
  children,
}: {
  current: Tool;
  onSet: (t: Tool) => void;
  value: Tool;
  hotkey: string;
  Icon?: (props: { size?: number }) => React.JSX.Element;
  children: React.ReactNode;
}) {
  const active = current === value;
  return (
    <button
      type="button"
      onClick={() => onSet(value)}
      className={`flex items-center gap-2 px-2 py-1 rounded text-[0.78rem] transition ${
        active ? 'bg-accent text-white font-semibold' : 'hover:bg-zinc-100'
      }`}
    >
      {Icon && <Icon size={15} />}
      <span className="flex-1 text-left">{children}</span>
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
  tool,
  allLabels,
  imageSnapRadius,
  eventToSvgPoint,
  onSelect,
  onMutateGeometry,
  onMutateAttributes,
  onStartDrag,
  onDragStateChange,
  onSnapChange,
}: {
  label: Label;
  selected: boolean;
  tool: Tool;
  /** All labels in the scene — needed for cross-label constraints (e.g.
   *  attached floorplan_opening drag projected onto its parent wall axis)
   *  and snap-on-edit (handle drag snaps to other labels' endpoints). */
  allLabels: Label[];
  /** Per-image-pixel snap radius. Caller computes it from the screen radius
   *  divided by the current view-to-screen scale. */
  imageSnapRadius: number;
  eventToSvgPoint: (e: ReactPointerEvent<SVGSVGElement> | PointerEvent) => Point | null;
  onSelect: (modifiers?: { shift?: boolean }) => void;
  onMutateGeometry: (newGeom: Label['geometry']) => void;
  onMutateAttributes: (newAttrs: Record<string, unknown>) => void;
  onStartDrag: () => void;
  onDragStateChange: (dragging: boolean) => void;
  onSnapChange: (s: SnapTarget | null) => void;
}) {
  // Color per type — selected always takes precedence.
  const baseColor = LABEL_COLORS[label.type] ?? '#16a34a';
  const stroke = selected ? '#dc2626' : baseColor;
  const fill = selected ? '#dc262633' : `${baseColor}1a`;
  const sw = selected ? 3 : 2;

  // Pointer-down on the glyph body: select on quick click, body-translate on
  // drag-after-move-threshold. Drag uses raw SVG point math so it works at
  // any zoom level.
  const onPointerDownBody = (e: React.PointerEvent<SVGElement>) => {
    e.stopPropagation();
    if (e.button !== 0) return;          // only left click
    if (tool !== 'select') {
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

    // M10: attached opening → axis-constrained drag along the parent wall.
    let constraintAxis: { ux: number; uy: number } | null = null;
    if (label.type === 'floorplan_opening') {
      const parentRel = (label.relations ?? []).find((r) => r.kind === 'belongs_to');
      if (parentRel) {
        const wall = allLabels.find((l) => l.id === parentRel.other_id);
        if (wall?.type === 'wall') {
          const dx = wall.geometry.end[0] - wall.geometry.start[0];
          const dy = wall.geometry.end[1] - wall.geometry.start[1];
          const len = Math.hypot(dx, dy);
          if (len > 0) constraintAxis = { ux: dx / len, uy: dy / len };
        }
      }
    }

    const onMove = (mv: PointerEvent) => {
      const pt = eventToSvgPoint(mv);
      if (!pt) return;
      let dx = pt[0] - start[0];
      let dy = pt[1] - start[1];
      if (!dragged && Math.hypot(dx, dy) > 4) {
        dragged = true;
        onDragStateChange(true);
        if (!pushedUndo) {
          onStartDrag();
          pushedUndo = true;
        }
        if (tool === 'select' && !selected) onSelect();
      }
      if (!dragged) return;
      // Project (dx, dy) onto the parent wall axis so the opening slides
      // ONLY along the wall direction — never away from it.
      if (constraintAxis) {
        const proj = dx * constraintAxis.ux + dy * constraintAxis.uy;
        dx = proj * constraintAxis.ux;
        dy = proj * constraintAxis.uy;
      }
      const newGeom = translateLabelGeometry({ ...label, geometry: origin } as Label, dx, dy);
      onMutateGeometry(newGeom);
    };
    const onUp = (mv: PointerEvent) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      onDragStateChange(false);
      if (!dragged) onSelect({ shift: mv.shiftKey });
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  // Handle dragging a single endpoint / corner / vertex — same pattern but
  // updates one specific point on the geometry block via moveHandle().
  // Snap fires during the drag so endpoints snap to other walls' endpoints
  // (e.g. two walls meeting at a corner).
  const onHandlePointerDown = (handleId: string) => (e: React.PointerEvent<SVGElement>) => {
    e.stopPropagation();
    if (e.button !== 0) return;
    const start = eventToSvgPoint(e as unknown as ReactPointerEvent<SVGSVGElement>);
    if (!start) return;
    let pushedUndo = false;
    let dragging = false;
    const onMove = (mv: PointerEvent) => {
      const raw = eventToSvgPoint(mv);
      if (!raw) return;
      if (!dragging) {
        dragging = true;
        onDragStateChange(true);
      }
      if (!pushedUndo) {
        onStartDrag();
        pushedUndo = true;
      }
      // Snap-on-edit: enumerate snap candidates as if we were drawing a
      // new wall/dim_distance — endpoints of OTHER labels (excluding self).
      const snapped = findSnap({
        cursor: raw,
        pendingStart: null,
        tool: 'select-drag',
        labels: allLabels,
        imageRadiusPx: imageSnapRadius,
        modifiers: { shift: mv.shiftKey, alt: mv.altKey },
        excludeLabelId: label.id,
      });
      onSnapChange(snapped);
      onMutateGeometry(moveHandle(label, handleId, snapped?.pt ?? raw));
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      onDragStateChange(false);
      onSnapChange(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onSelect({ shift: e.shiftKey });
  };

  // Drawing tools (everything except select) must let clicks pass
  // straight through to the canvas — otherwise clicking ON an existing
  // wall while drawing a new wall would be intercepted by the wall's own
  // pointer handlers and the new wall never gets committed. The snap
  // engine still picks up the wall's endpoints because snap math runs in
  // the canvas-level pointermove handler, independent of pointer events.
  const isDrawingTool = tool !== 'select';
  const bodyProps = {
    onClick,
    onPointerDown: onPointerDownBody,
    style: {
      cursor: selected ? 'move' : 'pointer' as const,
      pointerEvents: (isDrawingTool ? 'none' : 'auto') as 'none' | 'auto',
    },
  };

  // Body geometry varies per type; selection handles are rendered uniformly
  // by handlesFor() below.
  let body: JSX.Element | null = null;

  switch (label.type) {
    case 'dimensioned_distance': {
      // M1 reference strokes (is_reference=true) get a distinct visual
      // language: amber color, dashed line, thicker ticks, and a small
      // "M1" badge at the midpoint. M2 building dimensions stay green/
      // solid. The value (e.g. "1,75 m" or "Bezug") is shown above the
      // line midpoint so the dim is readable without clicking through to
      // the paired Maßzahl.
      const { start, end } = label.geometry;
      const isRef = !!label.attributes.is_reference;
      const refColor = isRef ? '#f59e0b' : stroke;
      const refWidth = isRef ? sw + 1 : sw;
      const dash = isRef ? '6,4' : undefined;
      const mid: Point = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
      const dx = end[0] - start[0];
      const dy = end[1] - start[1];
      const len = Math.hypot(dx, dy) || 1;
      // Perpendicular unit, used to offset the text slightly above the line
      const perpX = -dy / len;
      const perpY = dx / len;
      const valueMm = label.attributes.value_mm;
      const valueText = valueMm != null
        ? (valueMm >= 1000
            ? `${(valueMm / 1000).toFixed(2).replace('.', ',')} m`
            : `${valueMm} mm`)
        : null;
      // Place value text 14px above the midpoint (in image coords).
      const txOff = 14;
      const tx = mid[0] + perpX * txOff;
      const ty = mid[1] + perpY * txOff;
      // Angle for text rotation — keep readable: limit to ±90° around 0.
      let angDeg = (Math.atan2(dy, dx) * 180) / Math.PI;
      if (angDeg > 90) angDeg -= 180;
      if (angDeg < -90) angDeg += 180;
      body = (
        <g {...bodyProps}>
          <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]}
                stroke={refColor} strokeWidth={refWidth}
                strokeDasharray={dash} />
          <Tick x={start[0]} y={start[1]} stroke={refColor} sw={refWidth} large={isRef} />
          <Tick x={end[0]} y={end[1]} stroke={refColor} sw={refWidth} large={isRef} />
          {valueText && (
            <text
              x={tx} y={ty}
              transform={`rotate(${angDeg} ${tx} ${ty})`}
              textAnchor="middle"
              fill={refColor}
              fontFamily="ui-monospace, monospace"
              fontSize={13}
              fontWeight={600}
              style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}
              pointerEvents="none"
            >
              {valueText}
            </text>
          )}
          {isRef && (
            <g pointerEvents="none" transform={`translate(${mid[0]} ${mid[1]})`}>
              <rect
                x={-13} y={-7} width={26} height={14}
                rx={3} fill="#f59e0b" stroke="white" strokeWidth={1}
              />
              <text
                x={0} y={4} textAnchor="middle"
                fill="white" fontFamily="ui-monospace, monospace"
                fontSize={9} fontWeight={700}
              >
                M1
              </text>
            </g>
          )}
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
      // Walls render as a perpendicular BAND. The band is extended past each
      // endpoint by half-thickness in wallBandPath() so adjacent walls'
      // fills overlap into the corner. No stroke on the band: a stroke
      // would draw visible borders that don't merge between separate walls
      // and re-introduce the corner gap we just fixed.
      const { start, end } = label.geometry;
      const thicknessMm = label.attributes.thickness_mm ?? 365;
      const path = wallBandPath(start, end, thicknessMm, WALL_PX_PER_MM);
      body = (
        <g {...bodyProps}>
          {path && (
            <>
              {/* Solid color base, then Mauerwerk hatching overlay. Both at
                  low opacity so the underlying drawing stays readable. */}
              <path d={path} fill={stroke} fillOpacity={0.20} stroke="none" />
              <path d={path} fill="url(#bim-wall-hatch)" stroke="none" />
            </>
          )}
          <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke={stroke} strokeWidth={sw} />
        </g>
      );
      break;
    }
    case 'floorplan_opening': {
      const [a, b, c, d] = label.geometry.quad;
      const attached = (label.relations ?? []).some((r) => r.kind === 'belongs_to');
      const inner = floorplanOpeningInner(label.geometry.quad, label.attributes, stroke);
      body = (
        <g {...bodyProps}>
          {attached && (
            <polygon
              points={`${a[0]},${a[1]} ${b[0]},${b[1]} ${c[0]},${c[1]} ${d[0]},${d[1]}`}
              fill="white" stroke="#a21caf" strokeWidth={sw + 1} strokeDasharray="4,3"
            />
          )}
          <polygon points={`${a[0]},${a[1]} ${b[0]},${b[1]} ${c[0]},${c[1]} ${d[0]},${d[1]}`}
                   fill={fill} stroke={stroke} strokeWidth={sw} />
          {inner}
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

// M13 keyboard cheatsheet — modal overlay, dismissed by Esc or click outside.
function Cheatsheet({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === '?') {
        e.preventDefault();
        onClose();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const sections: Array<[string, Array<[string, string]>]> = [
    ['Werkzeuge', [
      ['S', 'Auswählen'],
      ['D', 'Bemaßte Strecke'],
      ['N', 'Maßzahl'],
      ['W', 'Wand'],
      ['O', 'Öffnung (tag-abhängig)'],
      ['L', 'Bauteillinie'],
      ['H', 'Höhenkote'],
    ]],
    ['Zeichnen', [
      ['Shift (halten)', 'Achsen-/Winkel-Lock (0/45/90/135°)'],
      ['Alt (halten)', 'Snap deaktivieren'],
      ['Enter', 'Polylinie beenden'],
      ['Esc', 'Aktion abbrechen'],
      ['Backspace (Polylinie)', 'letzten Punkt entfernen'],
    ]],
    ['Auswahl', [
      ['Click', 'Auswahl ersetzen'],
      ['Shift+Click', 'Auswahl umschalten'],
      ['Drag auf leerer Fläche', 'Rubber-band Multi-Select'],
      ['⌘/Ctrl + A', 'alles auswählen'],
      ['Del / Backspace', 'Auswahl löschen'],
    ]],
    ['Wand (selected)', [
      ['← / →', '±10 mm Wandstärke'],
      ['Shift+← / →', '±50 mm Wandstärke'],
      ['Lila Handle ziehen', 'Wandstärke direkt zeichnen'],
    ]],
    ['Ansicht', [
      ['Mausrad', 'Zoom'],
      ['Shift/Right-Drag', 'Pan'],
      ['R', 'Ansicht zurücksetzen'],
    ]],
    ['Szenen-Navigation', [
      [',', 'Vorige Szene des Hauses'],
      ['.', 'Nächste Szene des Hauses'],
    ]],
    ['Wand-Polygon', [
      ['Klick … Klick …', 'Verkettete Wände — jeder Klick startet die nächste Wand'],
      ['Klick nahe Start', 'Polygon schließen + Kette beenden'],
      ['Esc', 'Kette manuell beenden'],
    ]],
    ['Speichern', [
      ['⌘/Ctrl + S', 'Speichern'],
      ['⌘/Ctrl + Z', 'Rückgängig'],
      ['⌘/Ctrl + Shift + Z', 'Wiederherstellen'],
    ]],
    ['Sonstiges', [
      ['?', 'Diese Übersicht'],
    ]],
  ];

  return (
    <div
      className="absolute inset-0 z-40 bg-black/40 flex items-center justify-center p-6"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-xl shadow-2xl max-w-3xl w-full max-h-[80vh] overflow-y-auto p-6"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between mb-4">
          <h2 className="text-[1rem] font-semibold">Tastaturkürzel</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-muted hover:text-zinc-900 text-xl leading-none w-7 h-7 flex items-center justify-center"
            aria-label="Schließen"
          >
            ×
          </button>
        </header>
        <div className="grid grid-cols-2 gap-x-6 gap-y-5">
          {sections.map(([title, rows]) => (
            <section key={title}>
              <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-2">
                {title}
              </h3>
              <dl className="space-y-1">
                {rows.map(([keys, desc]) => (
                  <div key={keys} className="flex justify-between gap-3 text-[0.8rem]">
                    <dt className="text-zinc-700">{desc}</dt>
                    <dd className="font-mono text-[0.75rem] text-zinc-900 bg-zinc-100 px-1.5 py-0.5 rounded">
                      {keys}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
        <p className="mt-5 text-[0.7rem] text-muted text-center">
          Esc oder ? schließt das Fenster.
        </p>
      </div>
    </div>
  );
}

// M12 inline edit input — floats above the canvas at a fixed screen
// position. Autofocused; Enter commits; Esc cancels (which may delete the
// freshly-placed label so the user can re-place); blur commits silently.
function InlineEditInput({
  screenPos,
  placeholder,
  onCommit,
  onCancel,
}: {
  screenPos: [number, number];
  placeholder: string;
  onCommit: (value: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    ref.current?.focus();
    ref.current?.select();
  }, []);
  return (
    <input
      ref={ref}
      type="text"
      placeholder={placeholder}
      defaultValue=""
      style={{
        position: 'fixed',
        left: screenPos[0],
        top: screenPos[1] + 18,
        transform: 'translateX(-50%)',
        zIndex: 9999,
        boxShadow: '0 0 0 4px rgba(255,255,255,0.95), 0 6px 16px rgba(0,0,0,0.18)',
      }}
      className="px-2 py-1 rounded-md border-2 border-accent bg-white text-[0.85rem] outline-none w-44"
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          onCommit(e.currentTarget.value);
        } else if (e.key === 'Escape') {
          e.preventDefault();
          onCancel();
        }
      }}
      onBlur={(e) => onCommit(e.currentTarget.value)}
    />
  );
}

// M12 toast stack — bottom-center, fades in/out, doesn't block the canvas.
function ToastStack({ toasts }: { toasts: Array<{ id: string; message: string; tone: string }> }) {
  return (
    <div className="absolute bottom-20 left-1/2 -translate-x-1/2 flex flex-col gap-1.5 pointer-events-none z-30">
      {toasts.map((t) => {
        const cls =
          t.tone === 'success' ? 'bg-emerald-700 text-white' :
          t.tone === 'warn' ? 'bg-amber-600 text-white' :
          t.tone === 'error' ? 'bg-red-700 text-white' :
                              'bg-zinc-800 text-white';
        return (
          <div
            key={t.id}
            className={`px-3 py-1.5 rounded shadow-lg text-[0.78rem] leading-snug ${cls}`}
          >
            {t.message}
          </div>
        );
      })}
    </div>
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
// degenerate zero-length wall (avoids NaN in the path string). The band is
// EXTENDED along the axis by half-thickness on each end so that adjacent
// walls meeting at a corner naturally overlap into the corner area
// (creating a clean visual L-join instead of a gap).
function wallBandPath(start: Point, end: Point, thicknessMm: number, pxPerMm: number): string {
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const len = Math.hypot(dx, dy);
  if (len === 0) return '';
  const ux = dx / len;
  const uy = dy / len;
  const px = -uy;
  const py = ux;
  const half = (thicknessMm * pxPerMm) / 2;
  // Extend each endpoint along the axis by half-thickness.
  const s: Point = [start[0] - ux * half, start[1] - uy * half];
  const e: Point = [end[0] + ux * half, end[1] + uy * half];
  const a: Point = [s[0] + px * half, s[1] + py * half];
  const b: Point = [e[0] + px * half, e[1] + py * half];
  const c: Point = [e[0] - px * half, e[1] - py * half];
  const d: Point = [s[0] - px * half, s[1] - py * half];
  return `M ${a[0]},${a[1]} L ${b[0]},${b[1]} L ${c[0]},${c[1]} L ${d[0]},${d[1]} Z`;
}

// Inner graphics for a floorplan_opening — door swing arc / window sashes.
// `quad` is ordered as built in onCanvasPointerDown: [a, b, c, d] where a→b
// runs along the opening's length and a→d runs perpendicular (the wall
// depth). The choice of hinge corner is driven by attributes.swing_side and
// the swing direction by attributes.swing.
function floorplanOpeningInner(
  quad: Quad,
  attrs: FloorplanOpeningLabel['attributes'],
  color: string,
): React.ReactNode {
  const [a, b, , d] = quad;
  const lenX = b[0] - a[0]; const lenY = b[1] - a[1];
  const lenMag = Math.hypot(lenX, lenY);
  if (lenMag < 1) return null;
  const depX = d[0] - a[0]; const depY = d[1] - a[1];
  const depMag = Math.hypot(depX, depY);
  if (depMag < 1) return null;
  const depUx = depX / depMag; const depUy = depY / depMag;

  if (attrs.opening_kind === 'door') {
    const swingSide = attrs.swing_side ?? 'left';
    const swing = attrs.swing ?? 'in';
    const hinge = swingSide === 'right' ? b : a;
    // Closed leaf endpoint = the opening corner opposite the hinge.
    const closedSign = swingSide === 'right' ? -1 : 1;
    const closedTip: Point = [hinge[0] + closedSign * lenX, hinge[1] + closedSign * lenY];
    // Open leaf endpoint = perpendicular from hinge into the room. We don't
    // know which side is "inside", so use depUx/depUy direction for
    // 'in' and the opposite for 'out'.
    const perpSign = swing === 'out' ? -1 : 1;
    const openTip: Point = [
      hinge[0] + perpSign * depUx * lenMag,
      hinge[1] + perpSign * depUy * lenMag,
    ];
    // Build the 90° arc as a polyline.
    const startAng = Math.atan2(closedTip[1] - hinge[1], closedTip[0] - hinge[0]);
    const endAng = Math.atan2(openTip[1] - hinge[1], openTip[0] - hinge[0]);
    let delta = endAng - startAng;
    while (delta > Math.PI) delta -= 2 * Math.PI;
    while (delta < -Math.PI) delta += 2 * Math.PI;
    const steps = 22;
    const arcPts: Point[] = [];
    for (let i = 0; i <= steps; i++) {
      const ang = startAng + delta * (i / steps);
      arcPts.push([hinge[0] + lenMag * Math.cos(ang), hinge[1] + lenMag * Math.sin(ang)]);
    }
    return (
      <g pointerEvents="none" opacity={0.75}>
        <line x1={hinge[0]} y1={hinge[1]} x2={openTip[0]} y2={openTip[1]}
              stroke={color} strokeWidth={1.6} />
        <polyline points={arcPts.map(p => `${p[0]},${p[1]}`).join(' ')}
                  fill="none" stroke={color} strokeWidth={1} strokeDasharray="3,2" />
      </g>
    );
  }

  if (attrs.opening_kind === 'window') {
    // Three parallel lines spanning the long axis at depth t = 0.25, 0.5, 0.75.
    const ts = [0.25, 0.5, 0.75];
    return (
      <g pointerEvents="none" opacity={0.65}>
        {ts.map((t, i) => {
          const x1 = a[0] + depX * t;
          const y1 = a[1] + depY * t;
          const x2 = x1 + lenX;
          const y2 = y1 + lenY;
          return (
            <line key={i} x1={x1} y1={y1} x2={x2} y2={y2}
                  stroke={color} strokeWidth={i === 1 ? 0.8 : 1.4} />
          );
        })}
      </g>
    );
  }
  return null;
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

// M11 multi-select inspector. Shown when ≥2 labels are selected; provides
// per-type breakdown + bulk status + bulk delete. Per-type bulk-edit
// (linearize, same-width, …) is on the M11 backlog from spec §11 but
// deferred for the initial cut — the most-used bulk ops are status and
// delete.
function MultiInspector({
  labels,
  onBulkStatus,
  onBulkDelete,
  onClear,
}: {
  labels: Label[];
  onBulkStatus: (s: Label['status']) => void;
  onBulkDelete: () => void;
  onClear: () => void;
}) {
  const byType: Record<string, number> = {};
  for (const l of labels) byType[l.type] = (byType[l.type] ?? 0) + 1;
  const types = Object.entries(byType).sort((a, b) => b[1] - a[1]);
  return (
    <div className="p-4 space-y-4">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Auswahl</div>
        <div className="text-[0.95rem] font-semibold">{labels.length} Labels</div>
        <button
          type="button"
          onClick={onClear}
          className="text-[0.7rem] text-accent hover:underline"
        >
          Auswahl aufheben
        </button>
      </header>

      <section>
        <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Typen
        </h4>
        <ul className="space-y-px text-[0.78rem]">
          {types.map(([t, n]) => (
            <li key={t} className="flex justify-between">
              <span className="font-mono text-zinc-700">{labelGlyphStr(t)} {t}</span>
              <span className="text-muted tabular-nums">{n}</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Bulk-Status
        </h4>
        <div className="grid grid-cols-2 gap-1">
          {(['readable', 'not_readable', 'missing', 'uncertain'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => onBulkStatus(s)}
              className="px-2 py-1 rounded text-[0.7rem] bg-zinc-100 hover:bg-zinc-200 text-zinc-800"
            >
              {s}
            </button>
          ))}
        </div>
      </section>

      <button
        type="button"
        onClick={onBulkDelete}
        className="w-full px-3 py-1.5 rounded-md text-[0.78rem] font-medium bg-red-50 text-red-700 hover:bg-red-100 border border-red-200"
      >
        Alle {labels.length} löschen
      </button>
    </div>
  );
}

function labelGlyphStr(t: string): string {
  return ({
    dimensioned_distance: '↔',
    dimension_number: '#',
    wall: '▭',
    floorplan_opening: '▢',
    view_opening: '⫷',
    component_line: '─',
    height_mark: '▲',
  } as Record<string, string>)[t] ?? '·';
}

function Inspector({
  label,
  allLabels,
  onChange,
  onDelete,
  onUnlink,
  onSelectId,
  onLinkTo,
}: {
  label: Label;
  allLabels: Label[];
  onChange: (patch: Partial<Label>) => void;
  onDelete: () => void;
  onUnlink: (otherId: string) => void;
  onSelectId: (id: string) => void;
  onLinkTo: (otherId: string) => void;
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
          onLinkTo={onLinkTo}
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
  onLinkTo,
}: {
  label: DimensionNumberLabel | DimensionedDistanceLabel;
  allLabels: Label[];
  onUnlink: (otherId: string) => void;
  onSelectId: (id: string) => void;
  onLinkTo: (otherId: string) => void;
}) {
  const linkedIds =
    label.type === 'dimension_number'
      ? (label.relations ?? []).filter((r) => r.kind === 'labels').map((r) => r.other_id)
      : allLabels
          .filter((l) => l.type === 'dimension_number')
          .filter((l) => (l.relations ?? []).some((r) => r.kind === 'labels' && r.other_id === label.id))
          .map((l) => l.id);

  const linked = linkedIds.map((id) => allLabels.find((l) => l.id === id)).filter((x): x is Label => !!x);

  // Eligible candidates for linking = labels of the complementary type that
  // aren't already linked to this one.
  const wantType: Label['type'] =
    label.type === 'dimension_number' ? 'dimensioned_distance' : 'dimension_number';
  const candidates = allLabels.filter(
    (l) => l.type === wantType && !linkedIds.includes(l.id),
  );

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
      {candidates.length > 0 && (
        <div className="mt-2">
          <label className="block">
            <span className="text-[0.65rem] text-muted">
              {wantType === 'dimensioned_distance' ? 'Mit Strecke verknüpfen…' : 'Mit Maßzahl verknüpfen…'}
            </span>
            <select
              value=""
              onChange={(e) => {
                if (e.target.value) onLinkTo(e.target.value);
              }}
              className="w-full mt-0.5 px-2 py-1 rounded border border-border text-[0.72rem] bg-white"
            >
              <option value="">— auswählen —</option>
              {candidates.map((c) => {
                const desc =
                  c.type === 'dimension_number'
                    ? `"${c.attributes.text ?? '?'}" (${c.attributes.parsed_value_mm ?? '?'} mm)`
                    : c.type === 'dimensioned_distance'
                    ? `↔ ${c.attributes.value_mm ?? '?'} mm`
                    : c.id;
                return (
                  <option key={c.id} value={c.id}>{desc}</option>
                );
              })}
            </select>
          </label>
        </div>
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
  const parentRel = (label.relations ?? []).find((r) => r.kind === 'belongs_to');
  const detach = () => {
    onChange({
      relations: (label.relations ?? []).filter(
        (r) => !(r.kind === 'belongs_to' && r.other_id === parentRel?.other_id),
      ),
    } as Partial<Label>);
  };
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Öffnung (Grundriss)</h4>
      {parentRel && (
        <div className="bg-fuchsia-50 border border-fuchsia-200 rounded px-2.5 py-1.5 text-[0.7rem] flex items-center justify-between gap-2">
          <span className="text-fuchsia-900">
            🔗 An Wand angeheftet
            <span className="block text-[0.65rem] text-fuchsia-700 font-mono break-all mt-0.5">
              {parentRel.other_id}
            </span>
          </span>
          <button
            type="button"
            onClick={detach}
            className="text-[0.65rem] px-1.5 py-0.5 rounded bg-white border border-fuchsia-300 text-fuchsia-800 hover:bg-fuchsia-100"
          >
            Lösen
          </button>
        </div>
      )}
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
          {/* Quick flip buttons — one click flips swing_side, another flips
              swing direction. The cycle through all 4 orientations is two
              clicks total. Drop-downs are still available below for the
              full enum (sliding, none). */}
          <div className="flex gap-1.5">
            <button
              type="button"
              onClick={() => onChange({
                attributes: {
                  ...a,
                  swing_side: a.swing_side === 'right' ? 'left' : 'right',
                },
              } as Partial<Label>)}
              className="flex-1 px-2 py-1 rounded border border-border text-[0.72rem] bg-white hover:bg-zinc-50 inline-flex items-center justify-center gap-1"
              title="Anschlag links ↔ rechts wechseln"
            >
              ⇋ Anschlag <span className="font-mono text-zinc-500">{a.swing_side ?? '–'}</span>
            </button>
            <button
              type="button"
              onClick={() => onChange({
                attributes: {
                  ...a,
                  swing: a.swing === 'out' ? 'in' : 'out',
                },
              } as Partial<Label>)}
              className="flex-1 px-2 py-1 rounded border border-border text-[0.72rem] bg-white hover:bg-zinc-50 inline-flex items-center justify-center gap-1"
              title="Schwenken nach innen ↔ außen"
            >
              ⇅ Schwenk <span className="font-mono text-zinc-500">{a.swing ?? '–'}</span>
            </button>
          </div>
          <label className="block">
            <span className="text-[0.7rem] text-muted">Schwenken (erweitert)</span>
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
            <span className="text-[0.7rem] text-muted">Anschlag (erweitert)</span>
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
          <option value="kniestock">Kniestock</option>
          <option value="dachschraege">Dachschräge</option>
          <option value="firstkante">Firstkante</option>
          <option value="gebaeudekante">Gebäudekante</option>
          <option value="other">Sonstige</option>
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

// Floating, draggable popover for the inspector. Replaces the fixed
// right-rail sidebar so the inspector can be moved out of the way of the
// label being edited, or collapsed entirely. Position persists in
// localStorage so it stays where the user put it across navigation.
function FloatingPopover({
  title,
  onClose,
  storageKey,
  hidden,
  children,
}: {
  title: string;
  onClose?: () => void;
  storageKey: string;
  hidden?: boolean;
  children: React.ReactNode;
}) {
  const STORAGE = `bim-db:annotate:popover:${storageKey}`;
  const [pos, setPos] = useState<{ x: number; y: number } | null>(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE);
      if (raw) return JSON.parse(raw);
    } catch { /* no-op */ }
    return null;
  });
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return window.localStorage.getItem(`${STORAGE}:collapsed`) === '1'; } catch { return false; }
  });
  const popRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ dx: number; dy: number } | null>(null);

  // Default position: top-right of parent on first paint. We need to wait
  // until the popover is mounted so we know its width and the parent's size.
  useEffect(() => {
    if (pos != null || hidden) return;
    const el = popRef.current;
    if (!el) return;
    const parent = el.offsetParent as HTMLElement | null;
    if (!parent) return;
    const pr = parent.getBoundingClientRect();
    const w = el.offsetWidth || 280;
    setPos({ x: Math.max(8, pr.width - w - 16), y: 16 });
  }, [pos, hidden]);

  const onHeaderDown = useCallback((e: React.PointerEvent) => {
    if (!popRef.current || !pos) return;
    const r = popRef.current.getBoundingClientRect();
    drag.current = { dx: e.clientX - r.left, dy: e.clientY - r.top };
    (e.target as Element).setPointerCapture(e.pointerId);
    e.preventDefault();
  }, [pos]);

  const onHeaderMove = useCallback((e: React.PointerEvent) => {
    if (!drag.current || !popRef.current) return;
    const parent = popRef.current.offsetParent as HTMLElement | null;
    if (!parent) return;
    const pr = parent.getBoundingClientRect();
    const w = popRef.current.offsetWidth;
    const h = popRef.current.offsetHeight;
    let x = e.clientX - pr.left - drag.current.dx;
    let y = e.clientY - pr.top - drag.current.dy;
    x = Math.max(8, Math.min(pr.width - w - 8, x));
    y = Math.max(8, Math.min(pr.height - h - 8, y));
    setPos({ x, y });
  }, []);

  const onHeaderUp = useCallback((e: React.PointerEvent) => {
    if (!drag.current) return;
    drag.current = null;
    try { (e.target as Element).releasePointerCapture(e.pointerId); } catch { /* no-op */ }
    if (pos) {
      try { window.localStorage.setItem(STORAGE, JSON.stringify(pos)); } catch { /* no-op */ }
    }
  }, [pos, STORAGE]);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => {
      const next = !v;
      try { window.localStorage.setItem(`${STORAGE}:collapsed`, next ? '1' : '0'); } catch { /* no-op */ }
      return next;
    });
  }, [STORAGE]);

  if (hidden) return null;
  return (
    <div
      ref={popRef}
      className="absolute z-30 bg-white border border-zinc-300 rounded-lg shadow-xl flex flex-col"
      style={{
        left: pos?.x ?? 16,
        top: pos?.y ?? 16,
        width: 280,
        maxHeight: 'calc(100% - 32px)',
        visibility: pos == null ? 'hidden' : 'visible',
      }}
    >
      <div
        className="px-3 py-2 border-b border-zinc-200 flex items-center justify-between gap-2 cursor-grab active:cursor-grabbing select-none rounded-t-lg bg-zinc-50"
        onPointerDown={onHeaderDown}
        onPointerMove={onHeaderMove}
        onPointerUp={onHeaderUp}
        onPointerCancel={onHeaderUp}
      >
        <span className="font-medium text-[0.78rem] text-zinc-800 truncate">{title}</span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); toggleCollapsed(); }}
            onPointerDown={(e) => e.stopPropagation()}
            className="w-5 h-5 inline-flex items-center justify-center rounded text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 text-[0.85rem] leading-none"
            title={collapsed ? 'Aufklappen' : 'Einklappen'}
            aria-label={collapsed ? 'Aufklappen' : 'Einklappen'}
          >
            {collapsed ? '+' : '–'}
          </button>
          {onClose && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); onClose(); }}
              onPointerDown={(e) => e.stopPropagation()}
              className="w-5 h-5 inline-flex items-center justify-center rounded text-zinc-500 hover:bg-zinc-200 hover:text-zinc-800 text-base leading-none"
              title="Schließen"
              aria-label="Schließen"
            >
              ×
            </button>
          )}
        </div>
      </div>
      {!collapsed && (
        <div className="overflow-y-auto flex-1 p-3 text-[0.8rem]">{children}</div>
      )}
    </div>
  );
}
