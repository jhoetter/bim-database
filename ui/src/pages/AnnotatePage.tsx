import {
  Fragment,
  type JSX,
  type PointerEvent as ReactPointerEvent,
  type WheelEvent as ReactWheelEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { Link, useLocation, useNavigate, useParams } from 'react-router';
import { fetchLabels, fetchDataset, saveLabels, useResource } from '../api/client';
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
  SceneLevel,
  SceneOrientation,
  SceneTag,
  ViewOpeningLabel,
  WallLabel,
} from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { rememberLastStep } from '../lib/step_state';
import {
  handlesFor,
  labelCentroid,
  moveHandle,
  translateLabelGeometry,
  type HandleSpec,
} from '../lib/labelGeometry';
import { findSnap, pointToSegment, referenceAngle, SNAP_COLOR, type SnapTarget, type SnapTool } from '../lib/snap';
import { tidyLineLabel } from '../lib/tidy';
import { applyLengthMatch, findLengthMatch, type LengthMatch } from '../lib/length_match';
import { labelColor, LEGEND } from '../lib/colors';
import {
  WindowPaneGlyph, DoorSwingGlyph, DoorHandleGlyph, GarageDoorGlyph,
  PassageGlyph, SkylightGlyph, DormerGlyph, WindowCircleGlyph, WindowArchedGlyph,
  RoofSlopeGlyph, RidgeGlyph, EaveGlyph, GroundGlyph, LevelTickGlyph,
  BezugGlyph, DimRefStarGlyph, QuestionGlyph, WallBodyGlyph, GableGlyph,
} from '../lib/glyphs';
import { inferRegionKind, polygonCentroid, regionKindLabel } from '../lib/region_kind';
import type { RegionKind } from '../lib/region_kind';
import { detectRooms } from '../lib/rooms';
import { detectClosedRegions } from '../lib/closed_regions';
import { buildConnectivity, endpointPointsOfLabel, jointMembersAt } from '../lib/connectivity';
import { inferLineKind, inferOpeningKind, inferOpeningWidthMm, inferWallThicknessMm } from '../lib/auto_infer';
import { dimOrientation, getBuildingDim, rememberBuildingDim } from '../lib/building_dims';
import { autoTSplit } from '../lib/auto_split';
import { loadHouseFacts, promoteToFacts, saveHouseFacts, type HouseFacts } from '../lib/house_facts';
import {
  advanceWorkflow,
  currentPhase as workflowCurrentPhase,
  phaseStatusSnapshot as workflowPhaseStatusSnapshot,
  recommendSceneFor as workflowRecommendSceneFor,
  PHASE_LABEL_DE,
  type PhaseId,
  type SceneSummary as WorkflowSceneSummary,
} from '../lib/workflow';

function workflowPhaseLabelDe(p: PhaseId): string {
  return PHASE_LABEL_DE[p];
}
import { collectRefineIssues, type RefineIssue } from '../lib/refine';
import { clearDefaults, getDefaults, rememberDefaults } from '../lib/defaults';
import {
  CentreLineIcon, DimensionIcon, DoorIcon, ElevationViewIcon, KeynoteIcon,
  LayersIcon, OpeningIcon, PlanViewIcon, QuestionIcon,
  SectionViewIcon, SelectIcon, SpotElevationIcon, WallIcon,
} from '../lib/icons';

const SNAP_SCREEN_RADIUS = 14;  // pixels of screen feel

// Scene editor. All 7 label types implemented; tool palette is gated by
// scene_tag (Grundriss vs Ansicht/Schnitt vs Sonstiges). Pan with
// right-mouse-drag or shift+drag; zoom with mouse wheel. Tag chip in the
// left sidebar locks the scene's scene_tag. Save persists to disk via PUT
// /labels/{scope}/...

const UNDO_LIMIT = 200;
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
  dimensioned_distance: { label: 'Bemaßung', hotkey: 'D', Icon: DimensionIcon },
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

// Tool families intentionally do NOT carry opening kind (Fenster/Tür/Gaube/…).
// Kind is a classification decision, not a gesture decision — picking a
// kind before drawing forces a modal switch every time the user wants a
// different opening, which kills flow. Instead:
//
//   • Pre-draw: only the gesture matters → shape picker for view_opening
//     (rectangle / circle / polygon). floorplan_opening has no submenu — it
//     always builds a wall-aligned quad from 2 clicks.
//   • Default kind on commit: 'window' (the overwhelmingly common case).
//   • Post-draw reclassification: hotkeys F/T/G/D/Z while the label is
//     selected, plus a prominent picker as the FIRST control in the
//     inspector. Bulk reclassify works the same way on multi-selection.
// Empty per M3: no pre-draw kind submenus anywhere. Kept as an empty array
// so the rendering code that consults findFamily() still works (returns null
// for every tool, falling back to plain ToolBtns).
const TOOL_FAMILIES: ToolFamily[] = [];

function findFamily(tool: Tool): ToolFamily | null {
  return TOOL_FAMILIES.find((f) => f.parentTool === tool) ?? null;
}

interface Snapshot {
  labels: Label[];
  scene_tag: SceneTag;
}

// Save-time guard: reject labels whose geometry contains NaN / Infinity.
// `JSON.stringify(NaN)` emits `null`, which then fails the Point schema's
// `items: { type: "number" }` and the server returns 422. The few code
// paths that could ever produce NaN (V0.1 auto-apply when bezugHM exists
// but its anchor is corrupt, drag off-screen, etc.) get caught here.
function pointIsFinite(p: unknown): boolean {
  return Array.isArray(p) && p.length >= 2 &&
    typeof p[0] === 'number' && Number.isFinite(p[0]) &&
    typeof p[1] === 'number' && Number.isFinite(p[1]);
}
function labelGeometryIsFinite(label: Label): boolean {
  const g = label.geometry as Record<string, unknown>;
  if (label.type === 'wall' || label.type === 'dimensioned_distance') {
    return pointIsFinite(g.start) && pointIsFinite(g.end);
  }
  if (label.type === 'floorplan_opening') {
    const q = g.quad as unknown[];
    return Array.isArray(q) && q.length === 4 && q.every(pointIsFinite);
  }
  if (label.type === 'view_opening') {
    if (g.shape === 'circle') {
      const r = g.radius_px;
      return pointIsFinite(g.center) && typeof r === 'number' && Number.isFinite(r);
    }
    if (g.shape === 'polygon') {
      return Array.isArray(g.polygon) && (g.polygon as unknown[]).every(pointIsFinite);
    }
    return Array.isArray(g.top_edge) && (g.top_edge as unknown[]).every(pointIsFinite)
        && Array.isArray(g.bottom_edge) && (g.bottom_edge as unknown[]).every(pointIsFinite);
  }
  if (label.type === 'component_line') {
    return Array.isArray(g.polyline) && (g.polyline as unknown[]).every(pointIsFinite);
  }
  if (label.type === 'height_mark') {
    return pointIsFinite(g.anchor);
  }
  if (label.type === 'dimension_number') {
    // anchor optional, bbox optional — both must be finite if present
    if (g.anchor !== undefined && !pointIsFinite(g.anchor)) return false;
    if (g.bbox !== undefined) {
      const b = g.bbox as unknown[];
      if (!Array.isArray(b) || b.length !== 4 || !b.every(pointIsFinite)) return false;
    }
    return true;
  }
  return true;
}

// Enum guards mirroring scene_labels.schema.json — used at save time so a
// label with a stale attribute (e.g. an old datum value removed from the
// enum) doesn't blow up the server with 422. When invalid, we coerce to
// null/'other' which the schema accepts.
const VALID_OPENING_KINDS_FLOORPLAN = new Set(['door', 'window', 'passage', 'garage_door', 'other']);
const VALID_OPENING_KINDS_VIEW = new Set(['door', 'window', 'skylight', 'dormer', 'garage_door', 'other']);
const VALID_LINE_KINDS = new Set(['first', 'traufe', 'gelaende', 'geschoss', 'ok_ffb', 'sockel', 'firstkante', 'dachschraege', 'kniestock', 'gebaeudekante', 'other']);
const VALID_DATUMS = new Set(['first', 'traufe', 'gelaende', 'geschoss', 'ok_ffb', 'sockel', 'kniestock', 'other']);
const VALID_STATUSES = new Set(['readable', 'not_readable', 'missing', 'uncertain']);

function sanitizeLabelForSave(label: Label): Label {
  const next: Label = { ...label, attributes: { ...label.attributes } } as Label;
  if (!VALID_STATUSES.has(next.status as string)) {
    (next as Label).status = 'readable';
  }
  if (next.type === 'floorplan_opening') {
    const k = (next.attributes as { opening_kind?: string }).opening_kind;
    if (k && !VALID_OPENING_KINDS_FLOORPLAN.has(k)) {
      (next.attributes as { opening_kind?: string }).opening_kind = 'other';
    }
  } else if (next.type === 'view_opening') {
    const k = (next.attributes as { opening_kind?: string }).opening_kind;
    if (k && !VALID_OPENING_KINDS_VIEW.has(k)) {
      (next.attributes as { opening_kind?: string }).opening_kind = 'other';
    }
  } else if (next.type === 'component_line') {
    const k = (next.attributes as { line_kind?: string }).line_kind;
    if (k && !VALID_LINE_KINDS.has(k)) {
      (next.attributes as { line_kind?: string }).line_kind = 'other';
    }
  } else if (next.type === 'height_mark') {
    const a = next.attributes as { datum?: string | null };
    if (a.datum && !VALID_DATUMS.has(a.datum)) {
      a.datum = 'other';
    }
  }
  return next;
}

// N2 helper: find the nearest existing label endpoint to a point, within
// snap radius. Used by the anchored-polyline auto-commit + the close-anchor
// visual hint. Excludes nothing — labels are scene-local, no cross-scene.
function nearestEndpointOfAny(
  pt: Point,
  labels: Label[],
  snapPx: number,
): { labelId: string; endpoint: Point; dist: number } | null {
  let best: { labelId: string; endpoint: Point; dist: number } | null = null;
  for (const l of labels) {
    for (const ep of endpointPointsOfLabel(l)) {
      const d = Math.hypot(ep[0] - pt[0], ep[1] - pt[1]);
      if (d < snapPx && (!best || d < best.dist)) {
        best = { labelId: l.id, endpoint: ep, dist: d };
      }
    }
  }
  return best;
}

// N2 (extended): find the nearest anchor — either an existing endpoint OR a
// projection onto an existing structural edge — within snap radius. Picks
// the closest of the two. Structural edges are wall segments and
// component_line segments (the things a new polygon "sits on", e.g. a roof
// landing on a gable wall edge). Returns the snapped point itself so the
// caller can replace its click coordinate with the on-line projection.
function nearestStructuralAnchor(
  pt: Point,
  labels: Label[],
  snapPx: number,
): { labelId: string; point: Point; dist: number } | null {
  let best: { labelId: string; point: Point; dist: number } | null = null;
  // 1) Endpoints (any label).
  const ep = nearestEndpointOfAny(pt, labels, snapPx);
  if (ep) best = { labelId: ep.labelId, point: ep.endpoint, dist: ep.dist };
  // 2) Edges of walls + component_lines.
  const projectOntoSegment = (a: Point, b: Point, p: Point): { proj: Point; dist: number } | null => {
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const lenSq = dx * dx + dy * dy;
    if (lenSq < 1) return null;
    let t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lenSq;
    t = Math.max(0, Math.min(1, t));
    const proj: Point = [a[0] + t * dx, a[1] + t * dy];
    const dist = Math.hypot(proj[0] - p[0], proj[1] - p[1]);
    return { proj, dist };
  };
  for (const l of labels) {
    if (l.type === 'wall') {
      const r = projectOntoSegment(l.geometry.start, l.geometry.end, pt);
      if (r && r.dist < snapPx && (!best || r.dist < best.dist)) {
        best = { labelId: l.id, point: r.proj, dist: r.dist };
      }
    } else if (l.type === 'component_line') {
      const poly = l.geometry.polyline;
      for (let i = 0; i < poly.length - 1; i++) {
        const r = projectOntoSegment(poly[i], poly[i + 1], pt);
        if (r && r.dist < snapPx && (!best || r.dist < best.dist)) {
          best = { labelId: l.id, point: r.proj, dist: r.dist };
        }
      }
    }
  }
  return best;
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

// Topbar widget — at-a-glance "does this scene have the M1 references
// homography needs?" Reads the same is_reference flag the auto-picker
// writes. Three states: green (1H + 1V), amber (one direction missing),
// hidden (no dims yet — nothing to evaluate).
function BezugStatus({ labels }: { labels: Label[] }) {
  let hasH = false;
  let hasV = false;
  let dimCount = 0;
  for (const l of labels) {
    if (l.type !== 'dimensioned_distance') continue;
    dimCount++;
    if (!l.attributes.is_reference) continue;
    const dx = l.geometry.end[0] - l.geometry.start[0];
    const dy = l.geometry.end[1] - l.geometry.start[1];
    const a = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
    if (a < 15 || a > 165) hasH = true;
    else if (a > 75 && a < 105) hasV = true;
  }
  if (dimCount === 0) return null;
  const complete = hasH && hasV;
  return (
    <span
      className={`text-[0.7rem] px-2 py-0.5 rounded-full font-medium tabular-nums ${
        complete
          ? 'bg-emerald-100 text-emerald-800'
          : 'bg-amber-100 text-amber-900'
      }`}
      title={
        complete
          ? 'Beide Bezugsrichtungen für Entzerrung gesetzt'
          : 'Für die Entzerrung braucht es 1× horizontale + 1× vertikale Bemaßung'
      }
    >
      Bezug: {hasH ? '✓ H' : '– H'} · {hasV ? '✓ V' : '– V'}
    </span>
  );
}

// Recompute is_reference for every dim_distance in `labels`: flag the
// LONGEST horizontal and LONGEST vertical as M1 (Bezug for homography),
// clear is_reference on the rest. Longest wins because a 1-pixel
// annotation error on a 1000-pixel reference is 0.1%, on a 100-pixel
// reference it's 1% — pick the more reliable scale. 'Horizontal' /
// 'vertical' here means the line angle is within ±15° of an axis;
// diagonal dims are never M1.
//
// Returns a new labels array if anything changed; the original array
// (referentially) otherwise, so callers can cheaply early-out on no-op.
function recomputeM1References(labels: Label[]): Label[] {
  type Scored = { id: string; len: number; orient: 'h' | 'v' | 'other' };
  const scored: Scored[] = [];
  for (const l of labels) {
    if (l.type !== 'dimensioned_distance') continue;
    const dx = l.geometry.end[0] - l.geometry.start[0];
    const dy = l.geometry.end[1] - l.geometry.start[1];
    const len = Math.hypot(dx, dy);
    const ang = (Math.atan2(dy, dx) * 180) / Math.PI;
    let orient: Scored['orient'] = 'other';
    if (Math.abs(ang) < 15 || Math.abs(ang - 180) < 15 || Math.abs(ang + 180) < 15) orient = 'h';
    else if (Math.abs(ang - 90) < 15 || Math.abs(ang + 90) < 15) orient = 'v';
    scored.push({ id: l.id, len, orient });
  }
  if (scored.length === 0) return labels;
  const longestH = scored.filter((s) => s.orient === 'h').sort((a, b) => b.len - a.len)[0];
  const longestV = scored.filter((s) => s.orient === 'v').sort((a, b) => b.len - a.len)[0];
  const m1 = new Set<string>();
  if (longestH) m1.add(longestH.id);
  if (longestV) m1.add(longestV.id);
  let changed = false;
  const next = labels.map((l) => {
    if (l.type !== 'dimensioned_distance') return l;
    const want = m1.has(l.id);
    if (l.attributes.is_reference === want) return l;
    changed = true;
    return {
      ...l,
      attributes: { ...l.attributes, is_reference: want },
      updated_at: nowIso(),
    } as Label;
  });
  return changed ? next : labels;
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
// (scope, key) so dataset and source-house namespaces don't collide.
function lastSceneKey(scope: 'house' | 'dataset', houseKey: string): string {
  return `bim-db:annotate:last-scene:${scope}:${houseKey}`;
}
export function getLastVisitedScene(scope: 'house' | 'dataset', houseKey: string): string | null {
  try { return window.localStorage.getItem(lastSceneKey(scope, houseKey)); } catch { return null; }
}
function rememberLastVisitedScene(scope: 'house' | 'dataset', houseKey: string, file: string): void {
  try { window.localStorage.setItem(lastSceneKey(scope, houseKey), file); } catch { /* no-op */ }
}

// House-wide Höhenkote knowledge — once you've labeled First=+12,5m in
// scene 1, the same datum in any other scene of the same house should
// pre-fill to the same value. Stored as { [datum]: value_mm } per house
// in localStorage; populated on save, consumed on Höhenkote datum-pick.
function houseHeightsKey(scope: 'house' | 'dataset', houseKey: string): string {
  return `bim-db:annotate:house-heights:${scope}:${houseKey}`;
}
export function getHouseHeights(
  scope: 'house' | 'dataset', houseKey: string,
): Record<string, number> {
  try {
    const raw = window.localStorage.getItem(houseHeightsKey(scope, houseKey));
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch { return {}; }
}
// M4.3 + X3 cross-scene Bezugsachse X: remember the X position (as ratio of
// image width) of the Bezugsachse Höhenkote per (house, sceneTag). New
// scenes of the SAME sceneTag default their first Höhenkote to that X, so
// stacked elevations share a vertical reference column without the user
// having to eyeball it. The sceneTag scoping (X3) prevents a Schnitt's
// column from leaking into an Ansicht (they aren't the same view) and
// silences the fallback entirely in Grundriss (where Höhenkote isn't used).
function bezugXRatioKey(scope: 'house' | 'dataset', houseKey: string, sceneTag: SceneTag): string {
  return `bim-db:annotate:bezug-x-ratio:${scope}:${houseKey}:${sceneTag}`;
}
export function getHouseBezugXRatio(
  scope: 'house' | 'dataset', houseKey: string, sceneTag: SceneTag,
): number | null {
  // Only meaningful for sceneTags where Höhenkote is a thing.
  if (sceneTag !== 'ansicht' && sceneTag !== 'schnitt' && sceneTag !== 'sonstiges') return null;
  try {
    const v = window.localStorage.getItem(bezugXRatioKey(scope, houseKey, sceneTag));
    if (v == null) return null;
    const n = parseFloat(v);
    return Number.isFinite(n) && n > 0 && n < 1 ? n : null;
  } catch { return null; }
}
function rememberHouseBezugXRatio(
  scope: 'house' | 'dataset', houseKey: string, sceneTag: SceneTag,
  labels: Label[], imageWidth: number,
): void {
  if (imageWidth < 1) return;
  if (sceneTag !== 'ansicht' && sceneTag !== 'schnitt' && sceneTag !== 'sonstiges') return;
  const first = labels.find((l) => l.type === 'height_mark');
  if (!first) return;
  const ratio = first.geometry.anchor[0] / imageWidth;
  if (!Number.isFinite(ratio) || ratio <= 0 || ratio >= 1) return;
  try { window.localStorage.setItem(bezugXRatioKey(scope, houseKey, sceneTag), String(ratio)); }
  catch { /* no-op */ }
}

function rememberHouseHeights(
  scope: 'house' | 'dataset', houseKey: string, labels: Label[],
): void {
  const existing = getHouseHeights(scope, houseKey);
  const next: Record<string, number> = { ...existing };
  let changed = false;
  for (const l of labels) {
    if (l.type !== 'height_mark') continue;
    const datum = l.attributes.datum;
    const v = l.attributes.value_mm;
    if (datum && datum !== 'other' && v != null) {
      if (next[datum] !== v) {
        next[datum] = v;
        changed = true;
      }
    }
  }
  if (!changed) return;
  try { window.localStorage.setItem(houseHeightsKey(scope, houseKey), JSON.stringify(next)); }
  catch { /* no-op */ }
}

export function AnnotatePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { key = '', file = '' } = useParams();
  const decodedFile = decodeURIComponent(file);

  // R0 — scope is always 'dataset' now (catalog path removed). Kept as a
  // const for callers that still take it through; the type alias enforces
  // the single-value invariant.
  void location;
  const scope: LabelScope = 'dataset';
  const imageUrl = `/static/dataset/${key}/${decodedFile}`;

  const { data, loading, error } = useResource(() => fetchLabels(scope, key, decodedFile), [scope, key, decodedFile]);

  // Scene navigation (prev/next within the same house). Fetch the house's
  // full scene list once per (scope, key); compute index from the current
  // file. Re-fetches only when the house changes, not when the scene does.
  const { data: houseScenes } = useResource(
    async () => {
      const h = await fetchDataset(key);
      return h.drawings.map((d) => ({ file: d.file, title: d.title ?? d.file }));
    },
    [scope, key],
  );
  const sceneList = houseScenes ?? [];
  const sceneIndex = sceneList.findIndex((s) => s.file === decodedFile);

  useEffect(() => { rememberLastStep(key, 'annotate'); }, [key]);
  const prevScene = sceneIndex > 0 ? sceneList[sceneIndex - 1] : null;
  const nextScene = sceneIndex >= 0 && sceneIndex < sceneList.length - 1 ? sceneList[sceneIndex + 1] : null;

  // Per-scene label summary for the chip bar: count + whether the scene has
  // the M1 references it needs for Skalierung/Entzerrung (≥1 horizontal +
  // ≥1 vertical is_reference=true dim_distance). One paralle fetch per
  // sibling scene of this house; cheap (typically ≤10 scenes) and avoids
  // a server-side aggregate endpoint. Refreshed when houseScenes change OR
  // when the current scene saves (sceneSummaryRev).
  const [sceneSummaryRev, setSceneSummaryRev] = useState(0);
  const { data: sceneSummaries } = useResource<Map<string, { count: number; hasH: boolean; hasV: boolean }>>(
    async () => {
      const out = new Map<string, { count: number; hasH: boolean; hasV: boolean }>();
      const list = houseScenes ?? [];
      const results = await Promise.all(
        list.map(async (s) => {
          try {
            const lbl = await fetchLabels(scope, key, s.file);
            return [s.file, lbl.labels ?? []] as const;
          } catch {
            return [s.file, []] as const;
          }
        }),
      );
      for (const [file, lbls] of results) {
        let hasH = false;
        let hasV = false;
        for (const l of lbls) {
          if (l.type !== 'dimensioned_distance') continue;
          const dd = l as unknown as { attributes: { is_reference?: boolean }; geometry: { start: Point; end: Point } };
          if (!dd.attributes.is_reference) continue;
          const dx = dd.geometry.end[0] - dd.geometry.start[0];
          const dy = dd.geometry.end[1] - dd.geometry.start[1];
          const a = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
          if (a < 15 || a > 165) hasH = true;
          else if (a > 75 && a < 105) hasV = true;
        }
        out.set(file, { count: lbls.length, hasH, hasV });
      }
      return out;
    },
    [scope, key, houseScenes, sceneSummaryRev],
  );

  // Editable state — initialised from `data` once it loads.
  const [labels, setLabels] = useState<Label[]>([]);
  // X5: transient client-side provenance for cross-scene auto-fills.
  // `labelId → "{kind} aus {sourceSceneFile}"`. Cleared when (a) the user
  // edits the label's value (so the badge disappears once they verify or
  // override), (b) the label is deleted, (c) the scene navigates away.
  // Not persisted — lives only for the current annotation session so the
  // user can SEE that a value came from elsewhere and undo if they want.
  const [crossSceneProvenance, setCrossSceneProvenance] = useState<Map<string, string>>(() => new Map());
  // W7 — per-scene visibility filter. Hydrated from SceneLabels.display
  // on load; written back on save. Labels in this set still exist in JSON,
  // they just don't render or appear in the main label list.
  const [hiddenLabelIds, setHiddenLabelIds] = useState<Set<string>>(() => new Set());
  const [sceneTag, setSceneTag] = useState<SceneTag>('nicht_klassifiziert');
  // N6: scene_orientation (Ansicht/Schnitt) + scene_level (Grundriss). Both
  // persist into SceneLabels on save. Drive cross-scene cache scoping —
  // Nordansicht's M1 references only pre-fill future Nordansichten, EG
  // grundriss' wall thickness only pre-fills EG, etc.
  const [sceneOrientation, setSceneOrientation] = useState<SceneOrientation | null>(null);
  const [sceneLevel, setSceneLevel] = useState<SceneLevel | null>(null);
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
  // Length-quantize hint: populated during pointermove with the closest
  // matching existing-label length, when within 5%. Shown as a badge so the
  // user knows they're about to repeat an existing dimension.
  const [lengthMatch, setLengthMatch] = useState<LengthMatch | null>(null);
  // Shape mode for view_opening — rectangle (default, 2-click diagonal),
  // circle (2-click: center + radius), polygon (polyline-stops, Enter
  // commits). Persisted to localStorage so the choice is sticky between
  // sessions for the same scope+house.
  type OpeningShape = 'rectangle' | 'circle' | 'polygon';
  const [viewOpeningShape, setViewOpeningShape] = useState<OpeningShape>(() => {
    try {
      const v = window.localStorage.getItem('bim-db:annotate:view-opening-shape');
      if (v === 'circle' || v === 'polygon' || v === 'rectangle') return v;
    } catch { /* no-op */ }
    return 'rectangle';
  });
  const persistViewOpeningShape = (s: OpeningShape) => {
    setViewOpeningShape(s);
    try { window.localStorage.setItem('bim-db:annotate:view-opening-shape', s); } catch { /* no-op */ }
  };
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
  // Auto-save (ON by default — 30 s debounce while dirty). User can flip
  // it off in the sidebar; the explicit setting wins, otherwise true.
  const [autosave, setAutosave] = useState<boolean>(() => {
    try {
      const v = window.localStorage.getItem('bim-db:annotate:autosave');
      if (v === null) return true;
      return v === 'true';
    } catch { return true; }
  });

  // Image display tweaks — opacity slider (helps verify wall placement
  // against busy/colored drawings) and color/grayscale toggle. Both
  // persist across images and houses.
  const [imgOpacity, setImgOpacity] = useState<number>(() => {
    try {
      const v = window.localStorage.getItem('bim-db:annotate:img-opacity');
      const n = v != null ? Number(v) : NaN;
      return Number.isFinite(n) && n >= 0.05 && n <= 1 ? n : 1;
    } catch { return 1; }
  });
  const [imgGrayscale, setImgGrayscale] = useState<boolean>(() => {
    try { return window.localStorage.getItem('bim-db:annotate:img-grayscale') === 'true'; }
    catch { return false; }
  });
  useEffect(() => {
    try { window.localStorage.setItem('bim-db:annotate:img-opacity', String(imgOpacity)); }
    catch { /* no-op */ }
  }, [imgOpacity]);
  useEffect(() => {
    try { window.localStorage.setItem('bim-db:annotate:img-grayscale', String(imgGrayscale)); }
    catch { /* no-op */ }
  }, [imgGrayscale]);
  // Global drag flag — set true during any handle / body drag. The right
  // rail dims to near-invisible while it's true so a wall being dragged
  // doesn't disappear visually behind the inspector overlay.
  const [isDragging, setIsDragging] = useState(false);
  // Closed-wall regions, recomputed when labels change. Lightweight even
  // for ~100 walls; if it becomes hot we'd debounce. Tolerance is
  // generous: post-commit fuse should have aligned endpoints, but be
  // defensive about slight drift.
  const closedRegions = useMemo(
    () => detectClosedRegions(labels, 6),
    [labels],
  );
  // Connectivity graph — joints (clustered endpoints) + connected components.
  // Used by joint-aware drag (M1.2), wall split (M1.3), select-connected
  // (M1.4), and the refine queue (M5.1). Tolerance generous (6 px) so
  // post-commit fuse alignments and minor drift still cluster.
  const connectivity = useMemo(
    () => buildConnectivity(labels, 6),
    [labels],
  );
  // V3.3 — detected rooms on Grundriss only. Cheap enough to recompute on
  // every label change (n is small); skip when we know the user isn't on a
  // floorplan.
  const rooms = useMemo(
    () => sceneTag === 'grundriss' ? detectRooms(labels) : [],
    [labels, sceneTag],
  );
  // W0 — workflow snapshot for the WorkflowGuide panel. Re-computes when the
  // facts cache or the scene list changes (sceneSummaryRev increments on
  // save → loadHouseFacts returns fresh data → memo recomputes).
  const workflowSnapshot = useMemo(() => {
    const facts = loadHouseFacts(scope, key);
    const scenes: WorkflowSceneSummary[] = (houseScenes ?? []).map((s) => ({
      file: s.file,
      tag: facts.scene_metadata[s.file]?.kind ?? null,
    }));
    return { facts, scenes };
  }, [scope, key, houseScenes, sceneSummaryRev]);
  // V3 — refine kinds per label, used on the canvas to draw issue rings,
  // ? corners, etc. We pass house context for cross-scene checks; the
  // refine module returns at most one issue per label, picking the most
  // critical kind via append order (height_conflict > off_axis > classify ...).
  const refineKindByLabel = useMemo(() => {
    const m = new Map<string, RefineIssue['kind']>();
    const issues = collectRefineIssues(labels, {
      scope, houseKey: key, sceneLevel,
      sceneTag, sceneOrientation,
    });
    for (const i of issues) {
      // height_conflict trumps everything else on the same label.
      const prev = m.get(i.labelId);
      if (!prev || i.kind === 'height_conflict') m.set(i.labelId, i.kind);
    }
    return m;
  }, [labels, scope, key, sceneLevel, sceneTag, sceneOrientation]);
  // Adaptive building axis. Detected from existing walls/dim-distances —
  // photographed-paper plans are commonly tilted 1-3°, and snapping to
  // image axes then fights the user. The "Q" key toggles back to image
  // axes if the inference is wrong (e.g. early in a session, or for a
  // plan with no dominant rectilinear axis).
  const detectedAxisDeg = useMemo(() => referenceAngle(labels), [labels]);
  // Confidence: do we have enough lines on the page to TRUST the detected
  // axis? Below this threshold we don't aggressively snap to image axes
  // either — the building might be tilted, we just don't know yet.
  const axisConfident = useMemo(() => {
    let n = 0;
    for (const l of labels) {
      if (l.type === 'wall' || l.type === 'dimensioned_distance') n++;
      else if (l.type === 'component_line') n += Math.max(0, l.geometry.polyline.length - 1);
      if (n >= 2) return true;
    }
    return false;
  }, [labels]);
  // K5 + K8: track held modifier state at the app level so the
  // visible-indicator chip can render based on it AND so the snap engine
  // can respect Alt/Shift consistently across pointer events. Reset on
  // window blur / visibilitychange so a window-switch with Alt held
  // doesn't leave the app stuck in "snap off" forever.
  const [heldMods, setHeldMods] = useState({ alt: false, shift: false, meta: false });
  useEffect(() => {
    const sync = (e: KeyboardEvent | MouseEvent | PointerEvent) => {
      setHeldMods({
        alt: 'altKey' in e ? e.altKey : false,
        shift: 'shiftKey' in e ? e.shiftKey : false,
        meta: ('metaKey' in e && e.metaKey) || ('ctrlKey' in e && e.ctrlKey),
      });
    };
    const reset = () => setHeldMods({ alt: false, shift: false, meta: false });
    document.addEventListener('keydown', sync);
    document.addEventListener('keyup', sync);
    document.addEventListener('mousemove', sync);    // re-sync from mouse events as a defensive backstop
    window.addEventListener('blur', reset);
    document.addEventListener('visibilitychange', reset);
    return () => {
      document.removeEventListener('keydown', sync);
      document.removeEventListener('keyup', sync);
      document.removeEventListener('mousemove', sync);
      window.removeEventListener('blur', reset);
      document.removeEventListener('visibilitychange', reset);
    };
  }, []);

  const [adaptiveAxisEnabled, setAdaptiveAxisEnabled] = useState<boolean>(() => {
    try {
      return window.localStorage.getItem('bim-db:annotate:adaptive-axis') !== 'false';
    } catch { return true; }
  });
  const effectiveAxisDeg = adaptiveAxisEnabled ? detectedAxisDeg : 0;
  // M3.2 inline post-draw classifier chip: floats at the just-drawn label's
  // centroid for 3 s, offering one-click / one-tap kind options with their
  // hotkey letters. Cleared by any next click, after the timer, or when
  // the user actually picks a kind.
  type PostDrawChipKind = 'floorplan_opening' | 'view_opening' | 'component_line';
  const [postDrawChip, setPostDrawChip] = useState<{
    labelId: string;
    kindFamily: PostDrawChipKind;
    /** Image-coord anchor; screen coord computed at render time so it
     *  follows pan/zoom. */
    anchor: Point;
  } | null>(null);
  // Auto-dismiss timer. 6s gives the user time to reach for the keyboard or
  // the pill row without losing the chip mid-reach. Hovering the chip
  // pauses the timer (handled inside PostDrawChip).
  const [postDrawChipPaused, setPostDrawChipPaused] = useState(false);
  useEffect(() => {
    if (!postDrawChip || postDrawChipPaused) return;
    const t = window.setTimeout(() => setPostDrawChip(null), 6000);
    return () => window.clearTimeout(t);
  }, [postDrawChip, postDrawChipPaused]);
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

  // CRITICAL — immediate per-scene state reset on URL change. Without this,
  // the labels from the PREVIOUS scene stay in local state during the async
  // refetch window, and drawing during that window would: (a) treat
  // previous-scene labels as neighbors for joint-snap / inherit-thickness /
  // refine-queue heuristics, and (b) save the previous-scene labels back
  // into the new scene's file. Both observable as "annotations from another
  // scene leaking in."
  useEffect(() => {
    setLabels([]);
    setSelectedIds(new Set());
    setDirty(false);
    undoStackRef.current = [];
    redoStackRef.current = [];
    setPostDrawChip(null);
    setPendingStart(null);
    setPendingPolyline([]);
    setWallChainAnchor(null);
    setCrossSceneProvenance(new Map());
    setHiddenLabelIds(new Set());
    setSceneOrientation(null);
    setSceneLevel(null);
  }, [scope, key, decodedFile]);
  // X7: clear half-drawn state when the user switches tools. A pending
  // wall chain or polyline shouldn't leak into the next tool's behavior.
  useEffect(() => {
    setPendingStart(null);
    setPendingPolyline([]);
    setWallChainAnchor(null);
    setSnap(null);
    setLengthMatch(null);
    setPostDrawChip(null);
  }, [tool]);
  // K11: when sceneTag changes, validate the current tool against the new
  // tag's allowed set (subject to the allTools override). If invalid,
  // fall back to 'select' — otherwise the user can be stuck in a tool
  // whose palette button isn't visible.
  useEffect(() => {
    const allowed = allTools ? TOOLS_BY_TAG.sonstiges : TOOLS_BY_TAG[sceneTag];
    if (!allowed.includes(tool)) {
      setTool('select');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sceneTag, allTools]);
  useEffect(() => {
    if (data) {
      const initialLabels = data.labels ?? [];
      const tag = data.scene_tag ?? 'nicht_klassifiziert';
      const imgSize = data.image_size_px ?? [1024, 1024];

      // V0.1 (was N5 auto-apply): when opening an Ansicht/Schnitt, inject
      // *any* house-wide known datum that's missing from this scene.
      // Previously gated on "scene has zero height_marks" so a scene with
      // just ±0,00 already labeled blocked First/Traufe from coming in.
      // - Y resolves real-world: prefers an existing Bezugshöhe (value=0)
      //   anchor + calibration when both present; else viewport-Y +
      //   calibration; else stacked rows.
      // - X defaults to existing Bezugshöhe X if any, then the per-tag
      //   ratio cache, then image centre.
      const isHeightTag = tag === 'ansicht' || tag === 'schnitt';
      let hydratedLabels: Label[] = initialLabels;
      let autoAppliedCount = 0;
      const autoProvenance = new Map<string, string>();
      if (isHeightTag) {
        const known = getHouseHeights(scope, key);
        const knownEntries = Object.entries(known);
        if (knownEntries.length > 0) {
          // Which datums already exist locally (any value_mm — we don't
          // overwrite). Bezugshöhe (value=0, no datum) doesn't claim a datum.
          const datumsHere = new Set<string>();
          for (const l of initialLabels) {
            if (l.type !== 'height_mark') continue;
            const d = l.attributes.datum;
            if (d && d !== 'other') datumsHere.add(d);
          }
          const missingEntries = knownEntries.filter(
            ([d, mm]) => !datumsHere.has(d) && typeof mm === 'number' && Number.isFinite(mm),
          );
          if (missingEntries.length > 0) {
            const facts = loadHouseFacts(scope, key);
            const calib = facts.calibration_per_scene[decodedFile];
            // Anchor to existing Bezugshöhe (±0,00) if present.
            const existingHMs = initialLabels.filter(
              (l) => l.type === 'height_mark',
            ) as HeightMarkLabel[];
            const bezugHM = existingHMs.find((h) => h.attributes.value_mm === 0);
            const xRatio = getHouseBezugXRatio(scope, key, tag);
            const bezugX = bezugHM
              ? bezugHM.geometry.anchor[0]
              : xRatio != null ? xRatio * imgSize[0] : imgSize[0] / 2;
            const newHMs: HeightMarkLabel[] = [];
            let row = 0;
            for (const [datum, mm] of missingEntries) {
              let y: number;
              if (calib && bezugHM) {
                // Real-world placement: bezug_y - value_mm × px_per_mm.
                y = bezugHM.geometry.anchor[1] - mm * calib.px_per_mm;
              } else if (calib) {
                y = imgSize[1] / 2 - mm * calib.px_per_mm;
              } else {
                y = imgSize[1] / 2 - row * 30;
              }
              const hm: HeightMarkLabel = {
                id: uuid(),
                type: 'height_mark',
                geometry: { anchor: [bezugX, y] },
                attributes: {
                  value_mm: mm,
                  datum: datum as HeightMarkLabel['attributes']['datum'],
                  reference_line_id: null,
                },
                status: 'readable',
                relations: [],
                created_at: nowIso(),
                updated_at: nowIso(),
              };
              newHMs.push(hm);
              autoProvenance.set(
                hm.id,
                calib && bezugHM
                  ? `${datum} mit Y aus Bezug + Kalibrierung`
                  : `${datum} aus anderer Szene`,
              );
              row++;
            }
            hydratedLabels = [...initialLabels, ...newHMs];
            autoAppliedCount = newHMs.length;
          }
        }
      }

      setLabels(hydratedLabels);
      setSceneTag(tag);
      setSceneOrientation(data.scene_orientation ?? null);
      setSceneLevel(data.scene_level ?? null);
      setHiddenLabelIds(new Set(data.display?.hidden_label_ids ?? []));
      setImageSize(imgSize);
      setView({ x: 0, y: 0, w: imgSize[0], h: imgSize[1] });
      undoStackRef.current = [];
      redoStackRef.current = [];
      setSelectedIds(new Set());
      // Auto-applied heights count as a change the user should save.
      setDirty(autoAppliedCount > 0);
      if (autoAppliedCount > 0) {
        setCrossSceneProvenance(autoProvenance);
        addToast(
          `↻ ${autoAppliedCount} Haus-Höhen automatisch eingesetzt — bei Bedarf verschieben`,
          'info',
          2600,
        );
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  // Remember the last-visited scene for this house so opening the house
  // again (from the dataset overview card) resumes here instead of
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
      // K2 unified model: Alt held during a commit = "bypass every smart
      // helper" for this gesture. Snap (already), length-quantize, neighbor-
      // inherit thickness/width, auto-infer kind, post-commit tidy, the
      // post-draw classifier chip all respect this single flag.
      const altHeld = e.altKey;
      const pt = altHeld ? rawPt : (snap?.pt ?? rawPt);
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
        // Length-quantize: if the live preview matched an existing label's
        // length within tight (1.5%) tolerance, lock to that exact length.
        // The 5% hint-tolerance only shows the badge; we only adjust the
        // committed endpoint when we're VERY close, so the user can still
        // deliberately draw a similar-but-different length.
        let commitPt = pt;
        if (!altHeld && lengthMatch?.withinSnapTolerance) {
          commitPt = applyLengthMatch(pendingStart, pt, lengthMatch.matchedLength);
          addToast(`↹ Länge an existierendes Label angeglichen`, 'success', 1500);
        }
        let label: Label;
        if (tool === 'dimensioned_distance') {
          const def = getDefaults(scope, key, sceneTag, 'dimensioned_distance');
          label = {
            id: uuid(),
            type: 'dimensioned_distance',
            geometry: { start: pendingStart, end: commitPt },
            attributes: {
              value_mm: null,
              target_orientation: (def.target_orientation as DimensionedDistanceLabel['attributes']['target_orientation']) ?? 'unknown',
              // is_reference is recomputed AFTER add (see setLabels below).
              // The longest H + longest V dims win — placement order is
              // irrelevant.
              is_reference: false,
            },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as DimensionedDistanceLabel;
        } else {
          const def = getDefaults(scope, key, sceneTag, 'wall');
          // M4.2 inherit from neighbors: median thickness of nearby walls.
          // K2: Alt held → skip neighbor-inherit (user wants a fresh default).
          const newMid: Point = [(pendingStart[0] + commitPt[0]) / 2, (pendingStart[1] + commitPt[1]) / 2];
          const inheritedT = altHeld
            ? null
            : inferWallThicknessMm(newMid, labels, imageSnapRadiusForView * 8);
          // W3 — fall back to house-wide outer_mm when no neighbor inherit
          // applies. Phase 2's promotion writes this value; subsequent
          // outer-wall draws on any scene of the house pick it up.
          const houseOuterT = altHeld ? undefined : loadHouseFacts(scope, key).wall_thickness.outer_mm;
          label = {
            id: uuid(),
            type: 'wall',
            geometry: { start: pendingStart, end: commitPt },
            attributes: {
              thickness_mm: inheritedT ?? houseOuterT ?? (def.thickness_mm as number) ?? 365,
            },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          } as WallLabel;
        }
        // Post-commit tidy: align near-ortho lines to exact ortho + fuse
        // endpoints within snap-radius of existing endpoints.
        //   • Alt held → skip BOTH ortho-tidy and endpoint-fuse (per K2,
        //     user wants no helpers at all).
        //   • Q-off (adaptiveAxisEnabled=false) → skip ortho-tidy only;
        //     endpoint-fuse still runs because it's structural (wall corners
        //     meeting) and has nothing to do with ortho preference.
        const tidyResult = altHeld
          ? { label, orthoChanged: false, endpointFused: false }
          : tidyLineLabel(label, labels, imageSnapRadiusForView, effectiveAxisDeg, !adaptiveAxisEnabled);
        label = tidyResult.label;
        if (tidyResult.orthoChanged || tidyResult.endpointFused) {
          const bits: string[] = [];
          if (tidyResult.orthoChanged) bits.push('begradigt');
          if (tidyResult.endpointFused) bits.push('Endpunkt verbunden');
          addToast(`✨ ${bits.join(' + ')} (⌘Z zum Rückgängig machen)`, 'success', 1800);
        }
        // N3.2 — T-intersection auto-split. When a NEW wall's endpoint
        // landed mid-edge of an existing wall (within snap radius, not at
        // its endpoints), split that wall in two at the projected point.
        // The three involved labels then share a joint at the T and
        // joint-aware drag inherits. Skipped on Alt (per K2).
        let splitOverlay: { remove: Set<string>; add: WallLabel[] } = { remove: new Set(), add: [] };
        if (!altHeld && tool === 'wall') {
          const ts = autoTSplit(label, labels, imageSnapRadiusForView);
          label = ts.newLabel;
          for (const [oldId, [left, right]] of ts.splits) {
            splitOverlay.remove.add(oldId);
            splitOverlay.add.push(left, right);
          }
          if (ts.splits.size > 0) {
            addToast(`✂ ${ts.splits.size}× T-Verbindung — Wand geteilt`, 'success', 1800);
          }
        }
        // After tidy, the effective end-point may differ from the raw click —
        // use it for the wall-chain anchor + the dim midpoint so downstream
        // geometry stays consistent with the saved label.
        const effEnd = (label.geometry as { end: Point }).end;
        const effStart = (label.geometry as { start: Point }).start;
        // For dim_distance, re-run the M1 pick over ALL dims in the scene
        // after adding the new one — the new dim might be the new longest
        // in its orientation. Toast if THIS dim ended up flagged.
        let labelsAfterAdd: Label[] =
          splitOverlay.remove.size > 0
            ? [...labels.filter((l) => !splitOverlay.remove.has(l.id)), ...splitOverlay.add, label]
            : [...labels, label];
        if (tool === 'dimensioned_distance') {
          labelsAfterAdd = recomputeM1References(labelsAfterAdd);
        }
        // X4: when the new dim is the now-promoted M1 reference AND the
        // user hasn't entered value_mm yet, pre-fill from the house-wide
        // building dims cache (same sceneTag + orientation).
        let crossSceneNote: string | null = null;
        if (tool === 'dimensioned_distance') {
          const me = labelsAfterAdd.find((l) => l.id === label.id) as DimensionedDistanceLabel | undefined;
          // N5.4 — height-mark endpoint inference. When BOTH endpoints of
          // the new dim sit at (or near) existing Höhenkoten with known
          // value_mm, the dim's value_mm is just |their diff|. Runs before
          // the X4 building-dim cache fallback. Skipped on Alt (per K2).
          if (!altHeld && me && me.attributes.value_mm == null) {
            const hms = labels.filter(
              (l) => l.type === 'height_mark' && l.attributes.value_mm != null,
            ) as HeightMarkLabel[];
            const yTol = imageSnapRadiusForView * 2;
            const findNearY = (y: number) =>
              hms.find((h) => Math.abs(h.geometry.anchor[1] - y) < yTol);
            const startHm = findNearY(effStart[1]);
            const endHm = findNearY(effEnd[1]);
            if (startHm && endHm && startHm.id !== endHm.id) {
              const v = Math.abs((startHm.attributes.value_mm as number) - (endHm.attributes.value_mm as number));
              if (v > 0) {
                labelsAfterAdd = labelsAfterAdd.map((l) =>
                  l.id === label.id
                    ? ({ ...l, attributes: { ...l.attributes, value_mm: v } } as Label)
                    : l,
                );
                const da = startHm.attributes.datum ?? 'Bezug';
                const db = endHm.attributes.datum ?? 'Bezug';
                crossSceneNote = `${v} mm = ${da} ↔ ${db} (aus Höhenkoten)`;
              }
            }
          }
          if (me?.attributes.is_reference && me.attributes.value_mm == null && !crossSceneNote) {
            const orient = dimOrientation(effStart, effEnd);
            if (orient) {
              // W5 — Phase 4 derivation: prefer house_facts.extent over the
              // X4 per-tag cache when extent + orientation graph are set.
              // The W4 geometric formula picks width vs depth correctly for
              // gable-facing-N houses.
              const facts = loadHouseFacts(scope, key);
              let derivedMm: number | null = null;
              let derivedSource = '';
              if (sceneTag === 'ansicht' || sceneTag === 'schnitt') {
                if (orient === 'horizontal' && sceneOrientation && facts.orientation?.north_edge_label_id) {
                  // For Ansicht: 'north'/'south' face → faceLengthAlong('east')
                  // For Schnitt: 'north'/'south' → faceLengthAlong('north')
                  const along: 'north' | 'east' =
                    sceneTag === 'ansicht'
                      ? (sceneOrientation === 'north' || sceneOrientation === 'south' ? 'east' : 'north')
                      : (sceneOrientation === 'north' || sceneOrientation === 'south' ? 'north' : 'east');
                  const fb = along === 'east' ? facts.extent.width_mm : facts.extent.depth_mm;
                  if (typeof fb === 'number') {
                    derivedMm = fb;
                    derivedSource = `Hausbreite/-tiefe (${along === 'east' ? 'ê' : 'n̂'}-Achse)`;
                  }
                } else if (orient === 'vertical') {
                  if (typeof facts.heights.first_mm === 'number' && typeof facts.heights.gelaende_mm === 'number') {
                    derivedMm = facts.heights.first_mm - facts.heights.gelaende_mm;
                    derivedSource = 'First − Gelände';
                  } else if (typeof facts.heights.first_mm === 'number') {
                    derivedMm = facts.heights.first_mm;
                    derivedSource = 'First';
                  } else if (typeof facts.extent.height_mm === 'number') {
                    derivedMm = facts.extent.height_mm;
                    derivedSource = 'Hausgröße';
                  }
                }
              }
              if (derivedMm == null) {
                const cached = getBuildingDim(scope, key, sceneTag, orient);
                if (cached) {
                  derivedMm = cached.value_mm;
                  derivedSource = `aus „${cached.from_scene_file}"`;
                }
              }
              if (derivedMm != null) {
                const finalMm = derivedMm;
                labelsAfterAdd = labelsAfterAdd.map((l) =>
                  l.id === label.id
                    ? ({ ...l, attributes: { ...l.attributes, value_mm: finalMm } } as Label)
                    : l,
                );
                crossSceneNote = `Bezug ${orient === 'horizontal' ? 'H' : 'V'} = ${(finalMm / 1000).toFixed(2).replace('.', ',')} m (${derivedSource})`;
              }
            }
          }
        }
        const finalLabels = labelsAfterAdd;
        setLabels(finalLabels);
        setDirty(true);
        if (crossSceneNote) {
          setCrossSceneProvenance((m) => {
            const next = new Map(m);
            next.set(label.id, crossSceneNote!);
            return next;
          });
          addToast(`↻ ${crossSceneNote}`, 'success', 2500);
        }
        if (tool === 'dimensioned_distance') {
          const me = finalLabels.find((l) => l.id === label.id) as
            | DimensionedDistanceLabel | undefined;
          if (me?.attributes.is_reference) {
            const dx2 = effEnd[0] - effStart[0];
            const dy2 = effEnd[1] - effStart[1];
            const a2 = Math.abs((Math.atan2(dy2, dx2) * 180) / Math.PI);
            const horiz = a2 < 15 || a2 > 165;
            if (!crossSceneNote) {
              addToast(
                horiz ? '↔ längste horizontale → Bezug (M1)' : '↕ längste vertikale → Bezug (M1)',
                'success',
                2500,
              );
            }
          }
        }
        // Wall chaining: keep drawing — the next wall starts where this one
        // ended. The 'closing the polygon' case (user clicked back near the
        // chain anchor) breaks the chain so the user isn't auto-extended
        // out of a closed shape.
        if (tool === 'wall') {
          const closedToAnchor = wallChainAnchor && Math.hypot(effEnd[0] - wallChainAnchor[0], effEnd[1] - wallChainAnchor[1])
              < (imageSnapRadiusForView * 2);
          if (closedToAnchor) {
            setPendingStart(null);
            setWallChainAnchor(null);
            addToast('Polygon geschlossen ✓', 'success', 1500);
          } else {
            setPendingStart(effEnd);
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
          const midSvg: Point = [(effStart[0] + effEnd[0]) / 2, (effStart[1] + effEnd[1]) / 2];
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

      // ── Polygon-shape view_opening (polyline-stops) ─────────────────────
      // Reuses the pendingPolyline state, gated on tool+shape, so we don't
      // collide with the regular component_line behavior.
      if (tool === 'view_opening' && viewOpeningShape === 'polygon') {
        // P3 close-to-first-vertex — same radius as the visual hint so the
        // click commits exactly when the green ring is showing.
        if (pendingPolyline.length >= 3) {
          const first = pendingPolyline[0];
          if (Math.hypot(pt[0] - first[0], pt[1] - first[1]) <= imageSnapRadiusForView * 2) {
            pushUndo();
            const def = getDefaults(scope, key, sceneTag, 'view_opening');
            const polyLabel: ViewOpeningLabel = {
              id: uuid(),
              type: 'view_opening',
              geometry: { shape: 'polygon', polygon: pendingPolyline },
              attributes: {
                opening_kind: (def.opening_kind as ViewOpeningLabel['attributes']['opening_kind']) ?? 'window',
                frame_visible: (def.frame_visible as boolean) ?? true,
              },
              status: 'readable',
              relations: [],
              created_at: nowIso(),
              updated_at: nowIso(),
            };
            setLabels((prev) => [...prev, polyLabel]);
            setDirty(true);
            setSelectedId(polyLabel.id);
            const mid = pendingPolyline[Math.floor(pendingPolyline.length / 2)];
            setPostDrawChip({ labelId: polyLabel.id, kindFamily: 'view_opening', anchor: mid });
            setPendingPolyline([]);
            addToast('Polygon geschlossen ✓', 'success', 1200);
            return;
          }
        }
        setPendingPolyline((prev) => [...prev, pt]);
        return;
      }

      // ── 2-click tools (rectangle / circle) ──────────────────────────────
      if (tool === 'floorplan_opening' || tool === 'view_opening') {
        // N1: floorplan_opening MUST start on a wall. The wall is the
        // structural carrier — an opening that floats free in the floorplan
        // is meaningless. First click off any wall → reject with a hint.
        if (tool === 'floorplan_opening' && pendingStart == null) {
          if (snap?.kind !== 'wall_line' || !snap.source_label_id) {
            addToast('Klick auf eine Wand — Öffnung muss in einer Wand sitzen', 'info', 2000);
            return;
          }
          setPendingStart(snap.pt);
          setPendingAttachedWallId(snap.source_label_id);
          return;
        }
        if (pendingStart == null) {
          // view_opening: free-floating start, as before
          setPendingStart(pt);
          setPendingAttachedWallId(null);
          return;
        }
        pushUndo();
        // Circle: center = pendingStart, radius = distance to pt.
        if (tool === 'view_opening' && viewOpeningShape === 'circle') {
          const radius_px = Math.hypot(pt[0] - pendingStart[0], pt[1] - pendingStart[1]);
          const def = getDefaults(scope, key, sceneTag, 'view_opening');
          // K2: Alt → skip auto-infer (user explicitly opted out of helpers).
          const inferred = altHeld
            ? null
            : inferOpeningKind(pendingStart, labels, 'view_opening', imageSnapRadiusForView * 12);
          const label: ViewOpeningLabel = {
            id: uuid(),
            type: 'view_opening',
            geometry: { shape: 'circle', center: pendingStart, radius_px },
            attributes: {
              opening_kind: ((inferred as ViewOpeningLabel['attributes']['opening_kind'] | null)
                ?? (def.opening_kind as ViewOpeningLabel['attributes']['opening_kind'])) ?? 'window',
              frame_visible: (def.frame_visible as boolean) ?? true,
            },
            status: 'readable',
            relations: [],
            created_at: nowIso(),
            updated_at: nowIso(),
          };
          setLabels([...labels, label]);
          setDirty(true);
          setPendingStart(null);
          setSelectedId(label.id);
          // K2: Alt → skip the post-draw classifier chip too. The chip is
          // a helper that nudges the user to classify; if they've already
          // signaled "no helpers," respect that.
          if (!altHeld) {
            setPostDrawChip({ labelId: label.id, kindFamily: 'view_opening', anchor: pendingStart });
          }
          return;
        }
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

          // Auto-infer (M3.3): if 3+ nearby floorplan_openings share a kind,
          // default to that kind rather than blindly 'window'.
          // K2: Alt → skip neighbor inference entirely.
          const centroidQuad: Point = [(quad[0][0] + quad[2][0]) / 2, (quad[0][1] + quad[2][1]) / 2];
          const inferredFp = altHeld
            ? null
            : inferOpeningKind(centroidQuad, labels, 'floorplan_opening', imageSnapRadiusForView * 12);
          const finalKind: FloorplanOpeningLabel['attributes']['opening_kind'] =
            ((inferredFp as FloorplanOpeningLabel['attributes']['opening_kind'] | null)
              ?? (def.opening_kind as FloorplanOpeningLabel['attributes']['opening_kind'])) ?? 'window';
          // M4.2 width inheritance: median width of same-kind openings on
          // the same wall. K2: Alt skips.
          const inheritedW = altHeld
            ? null
            : inferOpeningWidthMm(attachWallId ?? null, finalKind ?? 'window', labels);
          label = {
            id: uuid(),
            type: 'floorplan_opening',
            geometry: { quad },
            attributes: {
              opening_kind: finalKind,
              width_mm: inheritedW ?? (def.width_mm as number | null) ?? derivedWidthMm,
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
          const centroidRect: Point = [(x0 + x1) / 2, (y0 + y1) / 2];
          const inferredV = altHeld
            ? null
            : inferOpeningKind(centroidRect, labels, 'view_opening', imageSnapRadiusForView * 12);
          label = {
            id: uuid(),
            type: 'view_opening',
            geometry: {
              top_edge: [[x0, y0], [x1, y0]],
              bottom_edge: [[x0, y1], [x1, y1]],
            },
            attributes: {
              opening_kind: ((inferredV as ViewOpeningLabel['attributes']['opening_kind'] | null)
                ?? (def.opening_kind as ViewOpeningLabel['attributes']['opening_kind'])) ?? 'window',
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
        // K2: post-draw classifier chip is a helper too — Alt suppresses it.
        if (!altHeld) {
          setPostDrawChip({
            labelId: label.id,
            kindFamily: tool === 'floorplan_opening' ? 'floorplan_opening' : 'view_opening',
            anchor: [(pendingStart[0] + pt[0]) / 2, (pendingStart[1] + pt[1]) / 2],
          });
        }
        return;
      }

      // ── Polyline tool (component_line) ────────────────────────────────────
      if (tool === 'component_line') {
        // P3 close-to-first-vertex: matches the visual close-polygon hint's
        // "near" radius (2× snap) so the green ring AND the close-commit
        // fire together. Click while the ring is visible = same as Enter.
        if (pendingPolyline.length >= 3) {
          const first = pendingPolyline[0];
          if (Math.hypot(pt[0] - first[0], pt[1] - first[1]) <= imageSnapRadiusForView * 2) {
            pushUndo();
            const def = getDefaults(scope, key, sceneTag, 'component_line');
            const closed = [...pendingPolyline, first];
            const inferredLine = altHeld ? null : inferLineKind(closed, imageSize[1]);
            const label: ComponentLineLabel = {
              id: uuid(),
              type: 'component_line',
              geometry: { polyline: closed },
              attributes: { line_kind: ((inferredLine as ComponentLineLabel['attributes']['line_kind'] | null)
                ?? (def.line_kind as ComponentLineLabel['attributes']['line_kind'])) ?? 'other' },
              status: 'readable',
              relations: [],
              created_at: nowIso(),
              updated_at: nowIso(),
            };
            setLabels((prev) => [...prev, label]);
            setDirty(true);
            setSelectedId(label.id);
            const mid = closed[Math.floor(closed.length / 2)];
            if (!altHeld) {
              setPostDrawChip({ labelId: label.id, kindFamily: 'component_line', anchor: mid });
            }
            setPendingPolyline([]);
            const rk = inferRegionKind(label, { imageHeight: imageSize[1] });
            addToast(
              rk === 'unknown'
                ? 'Polygon geschlossen ✓'
                : `Polygon geschlossen ✓ — ↻ ${regionKindLabel(rk)} erkannt`,
              'success', 1500,
            );
            return;
          }
        }
        // N2 anchored auto-commit: if THIS click lands on an existing
        // structural anchor (endpoint OR projection onto a wall/
        // component_line edge) AND pendingPolyline[0] was ALSO on a
        // structural anchor, the new polyline is "anchored at both ends" —
        // commit immediately without requiring Enter. Skipped on Alt
        // (per K2). The "on the edge" case is what lets a roof line that
        // sits on the gable wall mid-edge auto-close without Enter.
        if (!altHeld && pendingPolyline.length >= 1) {
          const endAnchor = nearestStructuralAnchor(pt, labels, imageSnapRadiusForView * 2);
          const startAnchor = nearestStructuralAnchor(pendingPolyline[0], labels, imageSnapRadiusForView * 2);
          if (endAnchor && startAnchor) {
            pushUndo();
            const def = getDefaults(scope, key, sceneTag, 'component_line');
            const finalPoly = [...pendingPolyline, endAnchor.point];
            const inferredLine = inferLineKind(finalPoly, imageSize[1]);
            const label: ComponentLineLabel = {
              id: uuid(),
              type: 'component_line',
              geometry: { polyline: finalPoly },
              attributes: { line_kind: ((inferredLine as ComponentLineLabel['attributes']['line_kind'] | null)
                ?? (def.line_kind as ComponentLineLabel['attributes']['line_kind'])) ?? 'other' },
              status: 'readable',
              relations: [],
              created_at: nowIso(),
              updated_at: nowIso(),
            };
            setLabels((prev) => [...prev, label]);
            setDirty(true);
            setSelectedId(label.id);
            const mid = finalPoly[Math.floor(finalPoly.length / 2)];
            setPostDrawChip({ labelId: label.id, kindFamily: 'component_line', anchor: mid });
            setPendingPolyline([]);
            const rk = inferRegionKind(label, { imageHeight: imageSize[1] });
            const baseToast = startAnchor.labelId === endAnchor.labelId
              ? '↩ verankert an gemeinsamem Label — Polylinie geschlossen'
              : '↩ verankert an zwei Labels';
            addToast(
              rk === 'unknown'
                ? baseToast
                : `${baseToast} — ↻ ${regionKindLabel(rk)} erkannt`,
              'success',
              2000,
            );
            return;
          }
        }
        setPendingPolyline((prev) => [...prev, pt]);
        return;
      }

      // ── 1-click tools ─────────────────────────────────────────────────────
      // Höhenkote workflow: the FIRST Höhenkote in a scene establishes the
      // Bezugsachse (vertical reference axis). Every subsequent Höhenkote
      // is hard-locked to that X-coordinate — only the Y varies. Alt
      // defeats the lock. This matches how Höhenkoten are drawn in real
      // construction plans: a stack on one vertical line, each labeling
      // a height at the same horizontal position.
      if (tool === 'height_mark') {
        pushUndo();
        const existingHKs = labels.filter((l) => l.type === 'height_mark');
        // M4.3 sibling-scene fallback: when this scene has no Höhenkote yet,
        // use the X-position remembered from another Ansicht of the same
        // house. X3: ONLY applies sceneTag-to-sceneTag (ansicht↔ansicht,
        // schnitt↔schnitt) — a Schnitt's column position is meaningful for
        // the next Schnitt, but not for an Ansicht (and vice versa).
        const siblingRatio = existingHKs.length === 0
          ? getHouseBezugXRatio(scope, key, sceneTag)
          : null;
        const bezugX = existingHKs.length > 0
          ? existingHKs[0].geometry.anchor[0]
          : (siblingRatio != null ? siblingRatio * imageSize[0] : null);
        const lockedX = bezugX != null && !e.altKey ? bezugX : pt[0];
        const anchor: Point = [lockedX, pt[1]];
        const def = getDefaults(scope, key, sceneTag, 'height_mark');
        const label: HeightMarkLabel = {
          id: uuid(),
          type: 'height_mark',
          geometry: { anchor },
          attributes: {
            value_mm: null,
            datum: (def.datum as HeightMarkLabel['attributes']['datum']) ?? null,
            reference_line_id: null,
          },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels([...labels, label]);
        setDirty(true);
        setSelectedIds(new Set([label.id]));
        // Position inline edit at the cursor — usually the actual click
        // (not the locked X) reads more naturally for the user.
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
        // Polyline close target: pass the first vertex when we have ≥3
        // points so the cursor visibly snaps to it (and clicking commits,
        // same as Enter).
        const isPolyTool =
          tool === 'component_line' ||
          (tool === 'view_opening' && viewOpeningShape === 'polygon');
        const pendingPolylineFirst =
          isPolyTool && pendingPolyline.length >= 3 ? pendingPolyline[0] : undefined;
        const target = findSnap({
          cursor: pt,
          pendingStart,
          tool: tool as SnapTool,
          labels,
          imageRadiusPx,
          modifiers: { shift: e.shiftKey, alt: e.altKey },
          referenceAngleDeg: effectiveAxisDeg,
          pendingPolylineFirst,
          // Q-disabled OR Alt-held → suppress every snap that isn't
          // structural (endpoint snap stays). That covers both the soft
          // axis-lock (no auto-straighten) AND the wall_line / line-edge
          // perpendicular projection (no auto-pull onto existing line
          // edges). Endpoint-snap is intentionally NOT gated: walls that
          // meet at a corner should still latch.
          disableSoftAxisSnap: !adaptiveAxisEnabled || e.altKey,
          disableLineSnap: !adaptiveAxisEnabled || e.altKey,
          softAxisToleranceDeg: axisConfident ? 10 : 3,
        });
        setSnap(target);

        // Length-quantize: only meaningful when we have a pendingStart and
        // the user has NOT signaled "no helpers" via Alt.
        if (!e.altKey && pendingStart && (tool === 'wall' || tool === 'dimensioned_distance')) {
          const effEnd = target?.pt ?? pt;
          const len = Math.hypot(effEnd[0] - pendingStart[0], effEnd[1] - pendingStart[1]);
          setLengthMatch(findLengthMatch(len, labels));
        } else if (lengthMatch) {
          setLengthMatch(null);
        }
      } else if (snap) {
        setSnap(null);
        if (lengthMatch) setLengthMatch(null);
      }
    },
    [tool, pendingStart, pendingPolyline, eventToSvgPoint, labels, view.w, snap, lengthMatch],
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
    // X5: any user-initiated edit clears the provenance badge — the value
    // is no longer purely "from another scene" once the user has touched it.
    setCrossSceneProvenance((m) => {
      if (!m.has(id)) return m;
      const next = new Map(m);
      next.delete(id);
      return next;
    });
    setLabels((prev) => {
      let touchedDim = false;
      const mapped = prev.map((l) => {
        if (l.id !== id) return l;
        const merged = { ...l, ...patch, updated_at: nowIso() } as Label;
        // M13: any attribute change is a signal — remember it as the default
        // for the next label of this type in this (scope, scene_tag).
        if (patch.attributes) {
          rememberDefaults(scope, key, sceneTag, merged.type, merged.attributes as Record<string, unknown>);
        }
        if (merged.type === 'dimensioned_distance' && patch.geometry) {
          touchedDim = true;
        }
        return merged;
      });
      // If a dim_distance's geometry changed (resize/move), its length may
      // have changed — re-pick the M1 references.
      return touchedDim ? recomputeM1References(mapped) : mapped;
    });
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
    setLabels((prev) => {
      const next = prev
        .filter((l) => !idsToDelete.has(l.id))
        // Also strip any relations pointing at the deleted label(s).
        .map((l) => ({
          ...l,
          relations: (l.relations ?? []).filter((r) => !idsToDelete.has(r.other_id)),
        }) as Label);
      // If a dim_distance was deleted, re-pick the M1 references — the
      // next-longest H/V might need to take over.
      const deletedAnyDim = prev.some(
        (l) => idsToDelete.has(l.id) && l.type === 'dimensioned_distance',
      );
      return deletedAnyDim ? recomputeM1References(next) : next;
    });
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
  // Forward-decl: a mutable ref points at save() so the autosave-on-leave
  // path in goToScene can call it even though save is defined below.
  const saveRef = useRef<(() => Promise<void>) | null>(null);
  const goToScene = useCallback(async (targetFile: string) => {
    if (dirty && autosave && saveRef.current) {
      try { await saveRef.current(); } catch { /* fall through to confirm */ }
    }
    if (dirty && !autosave) {
      const ok = window.confirm(
        'Ungespeicherte Änderungen.\n\n' +
        'OK = trotzdem weiter (Änderungen gehen verloren),\n' +
        'Abbrechen = hier bleiben (Cmd+S um zu speichern).',
      );
      if (!ok) return;
    }
    const path = `/${key}/scene/${encodeURIComponent(targetFile)}/annotate`;
    navigate(path);
  }, [dirty, autosave, key, navigate]);

  // ── save ──────────────────────────────────────────────────────────────────
  const save = useCallback(async () => {
    if (!data) return;
    setSaving(true);
    try {
      const anomalies = collectAnomalies(labels);
      // Defensive sanitation: drop labels whose geometry contains NaN /
      // non-finite numbers (those serialize to `null` and fail Point
      // schema validation). This guards against bad auto-applied state
      // (V0.1 with NaN calib, dragged labels gone off-screen, etc.).
      // Also coerce any enum-typed attribute (opening_kind, line_kind,
      // datum, status) back into the schema's allowed set so a stale
      // value from an old session can't blow up the save.
      const cleanLabels = labels
        .filter((l) => labelGeometryIsFinite(l))
        .map(sanitizeLabelForSave);
      const droppedCount = labels.length - cleanLabels.length;
      if (droppedCount > 0) {
        console.warn(`[save] dropping ${droppedCount} labels with non-finite geometry`);
      }
      // Schema allows ONLY a fixed top-level key set; spreading `data`
      // could carry deprecated keys. Build the payload from scratch.
      const payload: SceneLabels = {
        schema_version: '1.0',
        scope,
        scene_key: key,
        scene_file: decodedFile,
        scene_tag: sceneTag,
        scene_orientation: sceneOrientation,
        scene_level: sceneLevel,
        image_size_px: imageSize,
        annotated_by: data.annotated_by ?? 'jhoetter',
        annotated_at: nowIso(),
        labels: cleanLabels,
        anomalies: anomalies.length > 0 ? anomalies : undefined,
        // Preserve homography only when present + valid.
        ...(data.homography ? { homography: data.homography } : {}),
        // W7 — persist per-scene visibility filter so hides survive sessions.
        ...(hiddenLabelIds.size > 0
          ? { display: { hidden_label_ids: [...hiddenLabelIds] } }
          : {}),
      };
      await saveLabels(scope, key, decodedFile, payload);
      setDirty(false);
      // Refresh sibling-scene summary so the chip bar's label count +
      // readiness badge updates for this scene.
      setSceneSummaryRev((r) => r + 1);
      // Lift this scene's Höhenkote { datum: value_mm } into the
      // per-house cache so subsequent scenes can auto-fill values
      // when the user picks the same datum.
      // X6: Heights are an Ansicht/Schnitt construct. Grundriss has no
      // height_mark labels so this would be a no-op anyway, but be explicit
      // — never let a Grundriss save touch the house-heights cache.
      if (sceneTag === 'ansicht' || sceneTag === 'schnitt' || sceneTag === 'sonstiges') {
        rememberHouseHeights(scope, key, labels);
      }
      // X4: cache the building's horizontal + vertical reference dims so
      // sibling scenes of the same sceneTag can pre-fill them. Only writes
      // when value_mm is set — otherwise the cache learns nothing useful.
      for (const l of labels) {
        if (l.type !== 'dimensioned_distance' || !l.attributes.is_reference) continue;
        if (l.attributes.value_mm == null) continue;
        const o = dimOrientation(l.geometry.start, l.geometry.end);
        if (!o) continue;
        rememberBuildingDim(scope, key, sceneTag, o, l.attributes.value_mm, decodedFile);
      }
      // N4 — promote this scene's labels to house-wide facts. Idempotent:
      // saving the same scene twice produces the same facts. Drives N5
      // (height inference) and N7 (suggestions) on subsequent scenes.
      // W0 — snapshot facts before promote so we can detect phase advance.
      const prevFacts = loadHouseFacts(scope, key);
      promoteToFacts({
        scope,
        houseKey: key,
        sceneFile: decodedFile,
        sceneTag,
        sceneOrientation,
        sceneLevel,
        imageSize,
        labels,
      });
      // W0 — phase advancement detection. If a phase just completed, fire
      // a toast and write back the timestamp. SceneSummary uses the saved
      // house-list metadata; tag falls back to null for unsaved scenes.
      const nextFacts = loadHouseFacts(scope, key);
      const workflowScenes: WorkflowSceneSummary[] = (houseScenes ?? []).map((s) => ({
        file: s.file,
        tag: nextFacts.scene_metadata[s.file]?.kind ?? null,
      }));
      const { newFacts, advancedTo, nowOnPhase } = advanceWorkflow(
        prevFacts, nextFacts, workflowScenes, decodedFile, nowIso(),
      );
      if (advancedTo) {
        saveHouseFacts(scope, key, newFacts);
        addToast(
          `✓ ${PHASE_LABEL_DE[advancedTo]} erledigt — weiter mit ${PHASE_LABEL_DE[nowOnPhase]}`,
          'success', 4000,
        );
      }
      // M4.3: also remember the Bezugsachse X (as a ratio of image width)
      // so the next scene of the same house picks the same vertical
      // reference column for its first Höhenkote.
      rememberHouseBezugXRatio(scope, key, sceneTag, labels, imageSize[0]);
      addToast(`✓ Gespeichert (${labels.length} Labels)`, 'success');
    } catch (e) {
      const msg = (e as Error).message;
      console.error('[saveLabels] failed:', e);
      // Detail line(s) on a new line so the toast actually shows the
      // server's schema reason and the user can paste it back.
      addToast(
        `✗ Speichern fehlgeschlagen\n${msg}`,
        'error',
        20000,
      );
    } finally {
      setSaving(false);
    }
  }, [data, key, decodedFile, sceneTag, labels, imageSize, scope, addToast]);
  // Keep the goToScene autosave bridge pointed at the latest save closure.
  useEffect(() => { saveRef.current = save; }, [save]);

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
      // K10: don't steal keys from text inputs (typing "2" into a value
      // field shouldn't flip the status of a label).
      const t = e.target as HTMLElement;
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return;
      // K12: cheatsheet modality. While it's open, only Esc/? pass through
      // (the cheatsheet's own listener handles those). Background tool
      // shortcuts mustn't fire under the modal overlay.
      if (cheatsheetOpen) return;
      // K9: ignore key-repeat for action keys so holding Esc / Enter /
      // Delete / Backspace doesn't double-fire. Modifiers (Shift/Alt/etc.)
      // still register normally because they don't trigger actions here.
      if (e.repeat && (
        e.key === 'Escape' || e.key === 'Enter' ||
        e.key === 'Delete' || e.key === 'Backspace' ||
        e.key === '?' || e.key === ',' || e.key === '.'
      )) return;
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
        setLengthMatch(null);
        return;
      }
      if (e.key === 'Enter' && tool === 'component_line' && pendingPolyline.length >= 2) {
        e.preventDefault();
        pushUndo();
        const altOnEnter = e.altKey;
        const def = getDefaults(scope, key, sceneTag, 'component_line');
        // K2: Alt+Enter → no auto-infer.
        const inferredLine = altOnEnter ? null : inferLineKind(pendingPolyline, imageSize[1]);
        const label: ComponentLineLabel = {
          id: uuid(),
          type: 'component_line',
          geometry: { polyline: pendingPolyline },
          attributes: { line_kind: ((inferredLine as ComponentLineLabel['attributes']['line_kind'] | null)
            ?? (def.line_kind as ComponentLineLabel['attributes']['line_kind'])) ?? 'other' },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels((prev) => [...prev, label]);
        setDirty(true);
        setSelectedId(label.id);
        // K2: Alt → no post-draw classifier chip either.
        if (!altOnEnter) {
          const mid = pendingPolyline[Math.floor(pendingPolyline.length / 2)];
          setPostDrawChip({ labelId: label.id, kindFamily: 'component_line', anchor: mid });
        }
        setPendingPolyline([]);
        if (pendingPolyline.length >= 3) {
          const rk = inferRegionKind(label, { imageHeight: imageSize[1] });
          if (rk !== 'unknown') {
            addToast(`↻ ${regionKindLabel(rk)} erkannt`, 'info', 1800);
          }
        }
        return;
      }
      // Polygon view_opening: Enter commits the current polyline as a
      // polygon-shape opening. Needs ≥3 vertices for a real shape.
      if (
        e.key === 'Enter' &&
        tool === 'view_opening' &&
        viewOpeningShape === 'polygon' &&
        pendingPolyline.length >= 3
      ) {
        e.preventDefault();
        pushUndo();
        const def = getDefaults(scope, key, sceneTag, 'view_opening');
        const polyLabel: ViewOpeningLabel = {
          id: uuid(),
          type: 'view_opening',
          geometry: { shape: 'polygon', polygon: pendingPolyline },
          attributes: {
            opening_kind: (def.opening_kind as ViewOpeningLabel['attributes']['opening_kind']) ?? 'window',
            frame_visible: (def.frame_visible as boolean) ?? true,
          },
          status: 'readable',
          relations: [],
          created_at: nowIso(),
          updated_at: nowIso(),
        };
        setLabels((prev) => [...prev, polyLabel]);
        setDirty(true);
        setSelectedId(polyLabel.id);
        setPendingPolyline([]);
        return;
      }
      if (e.key === 'Delete' || e.key === 'Backspace') {
        // K4: Backspace inside a pending polyline removes the LAST placed
        // vertex (instead of deleting the selection). Lets the user
        // recover from a misclick mid-draw. Delete always deletes selection.
        if (e.key === 'Backspace' && pendingPolyline.length > 0) {
          e.preventDefault();
          setPendingPolyline((prev) => prev.slice(0, -1));
          return;
        }
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
      // K3 context-aware letter dispatch.
      // BEFORE the tool letter check below: if a single label is selected
      // and the typed letter is a reclassify hotkey for THAT label's type,
      // do the reclassify and return — never reach the tool switch. So
      // pressing D with a Bauteillinie selected reclassifies to Dach and
      // does NOT also switch to the Bemaßung tool. Letters with no
      // context action for the selected label type fall through to the
      // tool-switch block, which still fires.
      const openingHotkeys: Record<string, string> = {
        f: 'window',
        t: 'door',
        g: 'dormer',
        d: 'skylight',
        a: 'garage_door',
        z: 'other',
      };
      const lineHotkeys: Record<string, string> = {
        w: 'gebaeudekante',
        d: 'dachschraege',
        z: 'other',
      };
      if (
        selectedIds.size === 1 &&
        !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey
      ) {
        const sel = labels.find((l) => selectedIds.has(l.id));
        if (sel && (sel.type === 'floorplan_opening' || sel.type === 'view_opening') && openingHotkeys[e.key]) {
          let targetKind = openingHotkeys[e.key];
          // Floorplan openings don't have skylight/dormer — remap onto the
          // closest analog (D → passage, G → other).
          if (sel.type === 'floorplan_opening') {
            if (targetKind === 'skylight') targetKind = 'passage';
            if (targetKind === 'dormer') targetKind = 'other';
          }
          updateLabel(sel.id, {
            attributes: { ...sel.attributes, opening_kind: targetKind } as never,
          } as never);
          setPostDrawChip(null);
          e.preventDefault();
          return;
        }
        if (sel && sel.type === 'component_line' && lineHotkeys[e.key]) {
          updateLabel(sel.id, {
            attributes: { ...sel.attributes, line_kind: lineHotkeys[e.key] } as never,
          } as never);
          setPostDrawChip(null);
          e.preventDefault();
          return;
        }
      }

      // Tool hotkeys — only switch if the new tool is allowed under the
      // current scene_tag (or 'allTools' override is on). Reached only
      // when no context-reclassify above already handled the key.
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

      // M5.3 status hotkeys — flip the selected label's status. 1-4 are
      // unused by tool shortcuts; numeric keys read as "rank" which fits
      // a quality/triage axis (1 best, 4 worst).
      const statusHotkeys: Record<string, 'readable' | 'uncertain' | 'not_readable' | 'missing'> = {
        '1': 'readable',
        '2': 'uncertain',
        '3': 'not_readable',
        '4': 'missing',
      };
      if (
        selectedIds.size > 0 &&
        !e.metaKey && !e.ctrlKey && !e.altKey && !e.shiftKey &&
        statusHotkeys[e.key]
      ) {
        const next = statusHotkeys[e.key];
        const ids = Array.from(selectedIds);
        pushUndo();
        setLabels((prev) => prev.map((l) =>
          ids.includes(l.id) ? ({ ...l, status: next, updated_at: nowIso() } as Label) : l,
        ));
        setDirty(true);
        addToast(`${ids.length === 1 ? 'Status' : `${ids.length} Labels Status`} → ${next}`, 'info', 1400);
        e.preventDefault();
        return;
      }

      if (e.key === 'q' && !e.metaKey && !e.ctrlKey) {
        // Toggle adaptive building-axis snap. When off, snap falls back
        // to image-axis ortho — useful early in a session (no detected
        // axis yet) or for plans with no rectilinear axis.
        setAdaptiveAxisEnabled((v) => {
          const next = !v;
          try { window.localStorage.setItem('bim-db:annotate:adaptive-axis', String(next)); } catch { /* no-op */ }
          addToast(
            next
              ? (axisConfident && Math.abs(detectedAxisDeg) >= 0.5
                  ? `Ortho-Snap an · folgt Plan-Achse (${detectedAxisDeg.toFixed(1)}°)`
                  : 'Ortho-Snap an')
              : 'Ortho-Snap aus — frei zeichnen',
            'info',
            1800,
          );
          return next;
        });
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [save, undo, redo, selectedIds, selectedId, deleteLabel, resetView, zoomBy, tool, pendingPolyline, pushUndo, sceneTag, labels, updateLabel, prevScene, nextScene, goToScene, allTools, cheatsheetOpen]);

  const selectedLabel = labels.find((l) => l.id === selectedId) ?? null;
  const viewBox = `${view.x} ${view.y} ${view.w} ${view.h}`;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Datensatz', to: '/' },
            { label: key, to: `/${key}` },
            { label: decodedFile },
          ]}
        />
      }
      topbarTrailing={
        <div className="flex items-center gap-2">
          <Link
            to={`/${key}/scene/${encodeURIComponent(decodedFile)}/export`}
            className="text-[0.72rem] px-2.5 py-1 rounded-md border border-zinc-300 text-zinc-700 hover:bg-zinc-100"
            title="Set A / Set B Vorschau für diese Szene"
          >
            Export ▸
          </Link>
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
                  // For the active scene, prefer the live in-memory labels
                  // over the (stale-until-save) sibling summary.
                  const summary = isCurrent
                    ? (() => {
                        let hasH = false, hasV = false;
                        for (const l of labels) {
                          if (l.type !== 'dimensioned_distance' || !l.attributes.is_reference) continue;
                          const dx = l.geometry.end[0] - l.geometry.start[0];
                          const dy = l.geometry.end[1] - l.geometry.start[1];
                          const a = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
                          if (a < 15 || a > 165) hasH = true;
                          else if (a > 75 && a < 105) hasV = true;
                        }
                        return { count: labels.length, hasH, hasV };
                      })()
                    : sceneSummaries?.get(s.file);
                  const readinessColor = summary
                    ? (summary.hasH && summary.hasV
                        ? '#10b981'                       // emerald — ready
                        : (summary.hasH || summary.hasV ? '#f59e0b' : '#d4d4d8'))  // amber half / zinc none
                    : null;
                  const readinessTitle = !summary
                    ? ''
                    : summary.hasH && summary.hasV
                      ? ' · Bezug H+V gesetzt → Skalierung+Entzerrung bereit'
                      : summary.hasH
                        ? ' · nur horizontaler Bezug — vertikalen fehlt noch'
                        : summary.hasV
                          ? ' · nur vertikaler Bezug — horizontalen fehlt noch'
                          : ' · keine Bezugsmaße — Skalierung+Entzerrung noch nicht möglich';
                  return (
                    <button
                      key={s.file}
                      type="button"
                      onClick={() => goToScene(s.file)}
                      title={`${s.title}${summary ? ` · ${summary.count} Labels` : ''}${readinessTitle}`}
                      className={`px-1.5 py-0.5 rounded text-[0.65rem] font-medium tabular-nums whitespace-nowrap shrink-0 transition inline-flex items-center gap-1 ${
                        isCurrent
                          ? 'bg-accent text-white'
                          : 'text-zinc-600 hover:bg-zinc-100'
                      }`}
                    >
                      <span>{sceneShortLabel(s.file, s.title)}</span>
                      {readinessColor && (
                        <span
                          className="inline-block w-1.5 h-1.5 rounded-full"
                          style={{ backgroundColor: readinessColor }}
                        />
                      )}
                      {summary && summary.count > 0 && (
                        <span className={`text-[0.55rem] tabular-nums ${isCurrent ? 'text-white/80' : 'text-zinc-400'}`}>
                          {summary.count}
                        </span>
                      )}
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
          {/* K5 modifier-held indicator. Tiny chip in the topbar so the user
              can see at a glance that Alt/Shift is active. Alt is the
              "ignore all smart helpers for this gesture" key (K2); Shift is
              the strict ortho-lock. Reset to blank on window blur (K8). */}
          {(heldMods.alt || heldMods.shift) && (
            <span
              className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[0.7rem] font-medium tabular-nums whitespace-nowrap ${
                heldMods.alt
                  ? 'bg-amber-100 text-amber-900'
                  : 'bg-emerald-100 text-emerald-800'
              }`}
              title={heldMods.alt
                ? 'Alt gehalten — Helfer (Snap, Längen-Angleichung, Anschluss-Vererbung) für diese Geste aus.'
                : 'Shift gehalten — Ortho-Lock (0/45/90/…) erzwungen.'}
            >
              <kbd className="font-mono text-[0.65rem]">{heldMods.alt ? 'Alt' : 'Shift'}</kbd>
              <span>{heldMods.alt ? 'Helfer aus' : 'Ortho-Lock'}</span>
            </span>
          )}
          <BezugStatus labels={labels} />
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
          sceneOrientation={sceneOrientation}
          setSceneOrientation={(v) => { setSceneOrientation(v); setDirty(true); }}
          sceneLevel={sceneLevel}
          setSceneLevel={(v) => { setSceneLevel(v); setDirty(true); }}
          workflowFacts={workflowSnapshot.facts}
          workflowScenes={workflowSnapshot.scenes}
          currentSceneFile={decodedFile}
          onGoToScene={goToScene}
          onSetOrientation={(wallId, grundrissFile) => {
            const f = loadHouseFacts(scope, key);
            f.orientation = {
              north_edge_label_id: wallId,
              source_grundriss_file: grundrissFile,
              north_angle_deg: null,
            };
            saveHouseFacts(scope, key, f);
            setSceneSummaryRev((r) => r + 1);
            addToast('✓ Nordkante festgelegt', 'success', 2000);
          }}
          hiddenLabelIds={hiddenLabelIds}
          onToggleHiddenLabel={(id) => {
            setHiddenLabelIds((prev) => {
              const next = new Set(prev);
              if (next.has(id)) next.delete(id);
              else next.add(id);
              return next;
            });
            setDirty(true);
          }}
          onHideInherited={() => {
            setHiddenLabelIds((prev) => {
              const next = new Set(prev);
              for (const id of crossSceneProvenance.keys()) next.add(id);
              return next;
            });
            setDirty(true);
            addToast('↻ Geerbte Labels ausgeblendet', 'info', 2000);
          }}
          inheritedLabelIds={new Set(crossSceneProvenance.keys())}
          onMarkDetailDone={() => {
            const f = loadHouseFacts(scope, key);
            const wf = f.workflow ?? { ...{ schema_version: '1.0' as const, phase: 'detail' as const, phase_completed_at: { inventory: null, height_anchor: null, footprint: null, orientation: null, bezugsmasse: null, detail: null }, source_scene: { inventory: null, height_anchor: null, footprint: null, orientation: null, bezugsmasse: null, detail: null }, user_skipped: {} } };
            wf.phase_completed_at = { ...wf.phase_completed_at, detail: nowIso() };
            wf.phase = 'detail';
            f.workflow = wf;
            saveHouseFacts(scope, key, f);
            setSceneSummaryRev((r) => r + 1);
            addToast('✓ Haus fertig markiert', 'success', 3000);
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
          onResetLabels={async () => {
            const ok = window.confirm(
              `Alle Labels für „${decodedFile}" wirklich zurücksetzen?\n\n` +
              `Diese Aktion kann NICHT rückgängig gemacht werden.\n` +
              `Die Szene bleibt im Datensatz; nur die Annotationen verschwinden.`,
            );
            if (!ok) return;
            // Wipe local label state + the scene's metadata so the next
            // save() writes a clean payload. The save runs immediately so
            // the disk reflects the wipe even if the user closes the tab.
            pushUndo();
            setLabels([]);
            setSceneTag('nicht_klassifiziert');
            setSceneOrientation(null);
            setSceneLevel(null);
            setHiddenLabelIds(new Set());
            setCrossSceneProvenance(new Map());
            setSelectedIds(new Set());
            setDirty(true);
            // Defer the save by one tick so the React state updates
            // commit before save() reads them.
            setTimeout(() => { saveRef.current?.(); }, 0);
            addToast('Labels zurückgesetzt — speichere…', 'warn', 2200);
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
          viewOpeningShape={viewOpeningShape}
          onChangeViewOpeningShape={persistViewOpeningShape}
          onRefineAutoFix={(issue) => {
            const lbl = labels.find((l) => l.id === issue.labelId);
            if (!lbl) return;
            pushUndo();
            if (issue.autoFix?.type === 'snap_to_axis' && lbl.type === 'wall') {
              const start = lbl.geometry.start;
              const end = lbl.geometry.end;
              const len = Math.hypot(end[0] - start[0], end[1] - start[1]);
              const rad = (issue.autoFix.targetAngleDeg * Math.PI) / 180;
              const fixedEnd: Point = [
                start[0] + len * Math.cos(rad),
                start[1] - len * Math.sin(rad),
              ];
              setLabels((prev) => prev.map((x) =>
                x.id === lbl.id
                  ? ({ ...x, geometry: { start, end: fixedEnd }, updated_at: nowIso() } as Label)
                  : x,
              ));
              setDirty(true);
              addToast('✨ Wand begradigt', 'success', 1200);
            } else if (issue.autoFix?.type === 'set_status') {
              updateLabel(lbl.id, { status: 'readable' } as Partial<Label>);
              addToast(`Status → readable`, 'success', 1200);
            }
            setSelectedIds(new Set([lbl.id]));
          }}
          onRefineTidyAll={() => {
            // M5.2 one-shot tidy: apply tidyLineLabel to every line label
            // in the scene with the current effective building axis. Brief
            // toast summarizes how many changed.
            pushUndo();
            let changed = 0;
            const imageRadius = (SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1);
            const next = labels.map((l) => {
              if (l.type !== 'wall' && l.type !== 'dimensioned_distance' && l.type !== 'component_line') return l;
              if (l.type === 'component_line') return l;  // polyline tidy is a separate, harder problem
              const tidy = tidyLineLabel(l, labels, imageRadius, effectiveAxisDeg);
              if (tidy.orthoChanged || tidy.endpointFused) {
                changed++;
                return { ...tidy.label, updated_at: nowIso() } as Label;
              }
              return l;
            });
            if (changed === 0) {
              addToast('Nichts zu begradigen — alles ortho 👌', 'info', 1500);
              return;
            }
            setLabels(next);
            setDirty(true);
            addToast(`✨ ${changed} Linien aufgeräumt (⌘Z zum Rückgängig)`, 'success', 2200);
          }}
          onApplyHouseHeight={(datum, value_mm) => {
            pushUndo();
            // If a Höhenkote is selected, set its datum+value. Otherwise
            // drop a new one at the Bezugsachse X. Y comes from N5
            // calibration when known: bezug_y - value_mm × px_per_mm so
            // First lands at the real First height. Falls back to
            // viewport-Y center if no calibration yet.
            const sel = labels.find((l) => l.id === selectedId);
            if (sel?.type === 'height_mark') {
              updateLabel(sel.id, {
                attributes: { ...sel.attributes, datum: datum as never, value_mm } as never,
              } as never);
              setDirty(true);
              addToast(`✓ ${datum} = ${value_mm === 0 ? '±0,00' : (value_mm / 1000).toFixed(2).replace('.', ',') + ' m'}`, 'success', 1500);
              return;
            }
            const existingHKs = labels.filter((l) => l.type === 'height_mark') as HeightMarkLabel[];
            const bezugX = existingHKs.length > 0
              ? existingHKs[0].geometry.anchor[0]
              : view.x + view.w / 2;
            const facts = loadHouseFacts(scope, key);
            const calib = facts.calibration_per_scene[decodedFile];
            const bezugHM = existingHKs.find((h) => h.attributes.value_mm === 0);
            let y = view.y + view.h / 2;
            if (calib && bezugHM) {
              y = bezugHM.geometry.anchor[1] - value_mm * calib.px_per_mm;
            }
            const newLabel: HeightMarkLabel = {
              id: uuid(),
              type: 'height_mark',
              geometry: { anchor: [bezugX, y] },
              attributes: {
                value_mm,
                datum: datum as HeightMarkLabel['attributes']['datum'],
                reference_line_id: null,
              },
              status: 'readable',
              relations: [],
              created_at: nowIso(),
              updated_at: nowIso(),
            };
            setLabels((prev) => [...prev, newLabel]);
            setSelectedIds(new Set([newLabel.id]));
            setDirty(true);
            const yNote = calib && bezugHM ? ' (Y kalibriert)' : '';
            addToast(`+ ${datum} aus Haus-Höhen übernommen${yNote}`, 'success', 1800);
            // X5 provenance chip — this label's value came from cache
            setCrossSceneProvenance((m) => {
              const next = new Map(m);
              next.set(newLabel.id, calib && bezugHM
                ? `${datum} mit Y aus Bezug + Kalibrierung`
                : `${datum} aus anderer Szene`);
              return next;
            });
          }}
        />
      }
    >
      <div className="h-full bg-white relative overflow-hidden">
        {loading && <p className="absolute top-4 left-4 text-zinc-700 text-sm">Lade Labels…</p>}
        {error && <p className="absolute top-4 left-4 text-red-700 text-sm">Fehler: {error.message}</p>}
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
            {/* V0.2 / V1.2 — area hatch patterns. Each is named by region
                kind so renderers can pick by inferred type. Each pattern's
                strokes carry their own opacity; the consumer layers the
                pattern on top of a coloured fill via two stacked <path>s. */}
            {/* Generic area hatch — fallback for unknown closed regions. */}
            <pattern id="bim-area-hatch" patternUnits="userSpaceOnUse"
                     width="10" height="10" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="10" stroke="currentColor"
                    strokeWidth="0.6" opacity="0.45" />
            </pattern>
            {/* Roof hatch — crossed diagonals (architectural roof shading). */}
            <pattern id="bim-roof-hatch" patternUnits="userSpaceOnUse"
                     width="10" height="10" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="10" stroke="#ea580c"
                    strokeWidth="0.7" opacity="0.4" />
              <line x1="0" y1="0" x2="10" y2="0" stroke="#ea580c"
                    strokeWidth="0.4" opacity="0.25" />
            </pattern>
            {/* Gable hatch — sparse 60° diagonal. */}
            <pattern id="bim-gable-sparse" patternUnits="userSpaceOnUse"
                     width="14" height="14" patternTransform="rotate(60)">
              <line x1="0" y1="0" x2="0" y2="14" stroke="#fb923c"
                    strokeWidth="0.6" opacity="0.4" />
            </pattern>
            {/* Wall-body region hatch (dense 45° — matches wall hatch tone). */}
            <pattern id="bim-wall-region" patternUnits="userSpaceOnUse"
                     width="8" height="8" patternTransform="rotate(45)">
              <line x1="0" y1="0" x2="0" y2="8" stroke="#1f2937"
                    strokeWidth="0.7" opacity="0.5" />
            </pattern>
            {/* Ground / earth — alternating short ticks. */}
            <pattern id="bim-ground-hatch" patternUnits="userSpaceOnUse"
                     width="12" height="8">
              <line x1="0" y1="6" x2="3" y2="2" stroke="#166534"
                    strokeWidth="0.7" opacity="0.55" />
              <line x1="6" y1="6" x2="9" y2="2" stroke="#166534"
                    strokeWidth="0.7" opacity="0.55" />
            </pattern>
            {/* Mullion patterns — horizontal lines for windows, vertical for garage doors. */}
            <pattern id="bim-mullion-h" patternUnits="userSpaceOnUse"
                     width="10" height="6">
              <line x1="0" y1="3" x2="10" y2="3" stroke="#0284c7"
                    strokeWidth="0.6" opacity="0.55" />
            </pattern>
            <pattern id="bim-mullion-v" patternUnits="userSpaceOnUse"
                     width="6" height="10">
              <line x1="3" y1="0" x2="3" y2="10" stroke="#92400e"
                    strokeWidth="0.7" opacity="0.5" />
            </pattern>
            {/* Skylight — tilted 24° close-spaced. */}
            <pattern id="bim-skylight-tilt" patternUnits="userSpaceOnUse"
                     width="6" height="6" patternTransform="rotate(24)">
              <line x1="0" y1="0" x2="0" y2="6" stroke="#0891b2"
                    strokeWidth="0.6" opacity="0.55" />
            </pattern>
            {/* Stripe-30 — generic "unclassified" overlay. */}
            <pattern id="bim-stripe-30" patternUnits="userSpaceOnUse"
                     width="8" height="8" patternTransform="rotate(30)">
              <line x1="0" y1="0" x2="0" y2="8" stroke="#737373"
                    strokeWidth="0.5" opacity="0.4" />
            </pattern>
            {/* Interior wall cross-hatch (60° + 120°). */}
            <pattern id="bim-wall-cross" patternUnits="userSpaceOnUse"
                     width="12" height="12" patternTransform="rotate(60)">
              <line x1="0" y1="0" x2="0" y2="12" stroke="#475569"
                    strokeWidth="0.6" opacity="0.4" />
              <line x1="0" y1="0" x2="12" y2="0" stroke="#475569"
                    strokeWidth="0.6" opacity="0.4" />
            </pattern>
            {/* Partition wall stipple. */}
            <pattern id="bim-wall-stipple" patternUnits="userSpaceOnUse"
                     width="6" height="6">
              <circle cx="3" cy="3" r="0.5" fill="#94a3b8" opacity="0.5" />
            </pattern>
          </defs>
          <image
            href={imageUrl}
            x={0} y={0}
            width={imageSize[0]} height={imageSize[1]}
            opacity={imgOpacity}
            style={imgGrayscale ? { filter: 'grayscale(1)' } : undefined}
          />
          {/* Implied height-bezugslinien — for every Höhenkote, draw a
              thin dashed horizontal line across the canvas at its
              y-coordinate. The line is IMPLIED by the Höhenkote —
              labeling it with datum='first' IS labeling the First level.
              The user never has to draw the line itself.
              The Bezugshöhe (value=0) gets a much more prominent line:
              solid amber instead of dashed pink, so the ±0,00 anchor
              reads at a glance. */}
          {labels.map((l) => {
            if (l.type !== 'height_mark') return null;
            const datum = l.attributes.datum;
            const value = l.attributes.value_mm;
            // Only draw the implied line if there's SOMETHING to convey —
            // either a datum (which type of height) or a value of 0
            // (the Bezugshöhe).
            if (!datum && value !== 0) return null;
            const isBezug = value === 0;
            const [, yy] = l.geometry.anchor;
            const sw = (isBezug ? 1.8 : 1) / Math.max(0.1, view.w / imageSize[0]);
            const fontPx = (isBezug ? 13 : 11) * (view.w / imageSize[0]);
            const labelText = isBezug
              ? `±0,00${datum && DATUM_LABELS[datum] ? ` · ${DATUM_LABELS[datum]}` : ''}`
              : (datum && DATUM_LABELS[datum]) || '';
            const lineColor = isBezug ? '#f59e0b' : '#be185d';
            return (
              <g key={`implied-${l.id}`} pointerEvents="none">
                <line
                  x1={0} y1={yy} x2={imageSize[0]} y2={yy}
                  stroke={lineColor} strokeWidth={sw}
                  strokeDasharray={isBezug ? '0' : '3,5'}
                  opacity={isBezug ? 0.7 : 0.35}
                />
                {labelText && (
                  <text
                    x={6} y={yy - 4}
                    fill={lineColor}
                    fontFamily="ui-monospace, monospace"
                    fontSize={fontPx}
                    fontWeight={isBezug ? 800 : 600}
                    opacity={isBezug ? 0.95 : 0.7}
                    style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}
                  >
                    {labelText}
                  </text>
                )}
              </g>
            );
          })}
          {/* Linking visuals — dashed lines between number ↔ distance pairs */}
          <LinkVisuals labels={labels} selectedId={selectedId} />
          {/* Closed wall regions — translucent area fill behind the wall
              outlines so the user can read enclosed spaces (rooms,
              footprints) at a glance. Double-click inside one (in select
              mode) selects every wall that forms it. */}
          {closedRegions.length > 0 && (
            <g>
              {closedRegions.map((r, i) => {
                const d = r.polygon.map((p, j) => `${j === 0 ? 'M' : 'L'} ${p[0]} ${p[1]}`).join(' ') + ' Z';
                return (
                  <path
                    key={`region-${i}`}
                    d={d}
                    fill="rgba(14, 165, 233, 0.08)"
                    stroke="rgba(14, 165, 233, 0.30)"
                    strokeWidth={1 / Math.max(0.1, view.w / imageSize[0])}
                    strokeDasharray={`${4 / Math.max(0.1, view.w / imageSize[0])},${3 / Math.max(0.1, view.w / imageSize[0])}`}
                    pointerEvents={tool === 'select' ? 'fill' : 'none'}
                    style={{ cursor: tool === 'select' ? 'pointer' : 'default' }}
                    onDoubleClick={(e) => {
                      if (tool !== 'select') return;
                      e.stopPropagation();
                      setSelectedIds(new Set(r.wallIds));
                      addToast(`${r.wallIds.length} Wände der Fläche ausgewählt`, 'info', 1500);
                    }}
                  />
                );
              })}
            </g>
          )}
          {/* V3.3 — Grundriss room badges. Detected wall-cycles get a
              small "Raum" centroid pictogram; classification (kind +
              persistence) is deferred to V9. */}
          {sceneTag === 'grundriss' && rooms.map((r) => {
            const glyphPx = 18 * (view.w / Math.max(1, svgRef.current?.clientWidth ?? 1));
            return (
              <g key={r.id} pointerEvents="none">
                <circle
                  cx={r.centroid[0]} cy={r.centroid[1]}
                  r={glyphPx * 0.7}
                  fill="white" stroke="#475569" strokeWidth={1.2} opacity={0.85}
                />
                <text
                  x={r.centroid[0]} y={r.centroid[1] + glyphPx * 0.18}
                  textAnchor="middle"
                  fontSize={glyphPx * 0.55}
                  fontWeight={700}
                  fontFamily="ui-sans-serif, system-ui"
                  fill="#475569"
                >
                  Raum
                </text>
              </g>
            );
          })}
          {/* Existing labels — W7 hidden_label_ids skipped on canvas. */}
          {labels.filter((l) => !hiddenLabelIds.has(l.id)).map((l) => (
            <LabelGlyph
              key={l.id}
              label={l}
              selected={selectedIds.has(l.id)}
              tool={tool}
              allLabels={labels}
              inheritedFromOtherScene={crossSceneProvenance.has(l.id)}
              refineKind={refineKindByLabel.get(l.id) ?? null}
              imageHeight={imageSize[1]}
              screenScale={view.w / Math.max(1, svgRef.current?.clientWidth ?? 1)}
              imageSnapRadius={(SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1)}
              eventToSvgPoint={eventToSvgPoint}
              onDragStateChange={setIsDragging}
              onSnapChange={setSnap}
              onSelect={(modifiers) => {
                // M11 multi-select: Shift+click toggles individual; plain
                // click replaces selection.
                // M1.4 select-connected: Cmd/Ctrl+click selects every label
                // in the same connectivity component (transitively joined
                // via shared endpoints).
                if (modifiers?.meta) {
                  const compIdx = connectivity.componentOf.get(l.id);
                  if (compIdx != null) {
                    setSelectedIds(new Set(connectivity.components[compIdx]));
                  } else {
                    setSelectedIds(new Set([l.id]));
                  }
                } else if (modifiers?.shift) {
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
              onJointMove={(labelId, handleId, newPt, altKey) => {
                // Move this endpoint AND every other label endpoint sharing
                // the same joint, unless the user is holding Alt (escape
                // hatch — drag only this one even at a shared corner).
                const members = altKey
                  ? []
                  : jointMembersAt(connectivity, labelId, handleId);
                setLabels((prev) =>
                  prev.map((x) => {
                    if (x.id === labelId) {
                      return { ...x, geometry: moveHandle(x, handleId, newPt), updated_at: nowIso() } as Label;
                    }
                    const memberOnX = members.find((m) => m.labelId === x.id);
                    if (memberOnX) {
                      return { ...x, geometry: moveHandle(x, memberOnX.endpointId, newPt), updated_at: nowIso() } as Label;
                    }
                    return x;
                  }),
                );
                setDirty(true);
              }}
              jointSize={(handleId) => {
                const j = connectivity.jointOf.get(`${l.id}:${handleId}`);
                return j ? j.members.length : 1;
              }}
              onSplit={(labelId, pt) => {
                const target = labels.find((x) => x.id === labelId);
                if (!target) return;
                pushUndo();
                if (target.type === 'component_line') {
                  // Insert a vertex into the polyline at the segment closest
                  // to the click point.
                  const poly = target.geometry.polyline;
                  let bestI = 0;
                  let bestD = Infinity;
                  for (let i = 0; i + 1 < poly.length; i++) {
                    const proj = pointToSegment(pt, poly[i], poly[i + 1]);
                    if (proj.dist < bestD) { bestD = proj.dist; bestI = i + 1; }
                  }
                  const next = [...poly.slice(0, bestI), pt, ...poly.slice(bestI)];
                  setLabels((prev) =>
                    prev.map((x) =>
                      x.id === labelId
                        ? ({ ...x, geometry: { polyline: next }, updated_at: nowIso() } as Label)
                        : x,
                    ),
                  );
                  setDirty(true);
                  return;
                }
                // wall + dim_distance: split into two adjacent labels sharing
                // the new endpoint. Inherit all attributes; the split point
                // is projected onto the line so it lies exactly on it.
                const g = target.geometry as { start: Point; end: Point };
                const proj = pointToSegment(pt, g.start, g.end);
                const splitPt = proj.point;
                const left = {
                  ...target,
                  id: uuid(),
                  geometry: { start: g.start, end: splitPt },
                  created_at: nowIso(),
                  updated_at: nowIso(),
                } as Label;
                const right = {
                  ...target,
                  id: uuid(),
                  geometry: { start: splitPt, end: g.end },
                  created_at: nowIso(),
                  updated_at: nowIso(),
                } as Label;
                setLabels((prev) => [
                  ...prev.filter((x) => x.id !== labelId),
                  left, right,
                ]);
                setSelectedIds(new Set([right.id]));
                setDirty(true);
                addToast('✂ Aufgeteilt', 'success', 1200);
              }}
              onStartDrag={pushUndo}
            />
          ))}
          {/* Höhenkote placement guide (P2): horizontal line across the
              canvas at the cursor Y, so the user can align with horizontal
              features in the image. Vertical line at the locked Bezugsachse
              X too, so the snapping is visible. */}
          {tool === 'height_mark' && hoverPt && (() => {
            const existingHKs = labels.filter((l) => l.type === 'height_mark');
            const siblingRatio = existingHKs.length === 0
              ? getHouseBezugXRatio(scope, key, sceneTag)
              : null;
            const lockedX = existingHKs.length > 0
              ? existingHKs[0].geometry.anchor[0]
              : (siblingRatio != null ? siblingRatio * imageSize[0] : hoverPt[0]);
            const scale = view.w / imageSize[0];
            const sw = 1 / Math.max(0.1, scale);
            return (
              <g pointerEvents="none">
                <line
                  x1={0} y1={hoverPt[1]} x2={imageSize[0]} y2={hoverPt[1]}
                  stroke="#15803d" strokeWidth={sw}
                  strokeDasharray={`${5 / Math.max(0.1, scale)},${3 / Math.max(0.1, scale)}`}
                  opacity={0.6}
                />
                <line
                  x1={lockedX} y1={0} x2={lockedX} y2={imageSize[1]}
                  stroke="#15803d" strokeWidth={sw}
                  strokeDasharray={`${3 / Math.max(0.1, scale)},${4 / Math.max(0.1, scale)}`}
                  opacity={0.35}
                />
              </g>
            );
          })()}
          {/* N2 anchored polyline hint — when drawing a component_line with
              ≥1 placed vertex AND the cursor hovers near an existing label's
              endpoint AND pendingPolyline[0] is also on an existing endpoint,
              show a green ring + "↩ verankern" so the user knows clicking
              there auto-commits the polyline. */}
          {tool === 'component_line' && pendingPolyline.length >= 1 && hoverPt && (() => {
            const radiusImg = (SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1) * 2;
            const endAnchor = nearestEndpointOfAny(hoverPt, labels, radiusImg);
            const startAnchor = nearestEndpointOfAny(pendingPolyline[0], labels, radiusImg);
            if (!endAnchor || !startAnchor) return null;
            const scale = view.w / imageSize[0];
            return (
              <g pointerEvents="none">
                <circle
                  cx={endAnchor.endpoint[0]} cy={endAnchor.endpoint[1]} r={radiusImg}
                  fill="rgba(22, 163, 74, 0.18)"
                  stroke="#16a34a"
                  strokeWidth={2.5 * scale}
                />
                <text
                  x={endAnchor.endpoint[0] + radiusImg + 8} y={endAnchor.endpoint[1]}
                  fill="#16a34a"
                  fontSize={11 * scale}
                  fontFamily="ui-monospace, monospace"
                  style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 * scale }}
                >
                  Klick = verankern
                </text>
              </g>
            );
          })()}
          {/* P3 polyline close-to-first-vertex: when drawing component_line
              or polygon view_opening with ≥3 vertices, surface a green ring
              at the first vertex when the cursor is near it. */}
          {pendingPolyline.length >= 3 && hoverPt && (tool === 'component_line' || (tool === 'view_opening' && viewOpeningShape === 'polygon')) && (() => {
            const first = pendingPolyline[0];
            const d = Math.hypot(hoverPt[0] - first[0], hoverPt[1] - first[1]);
            const radius = (SNAP_SCREEN_RADIUS * view.w) / Math.max(1, svgRef.current?.clientWidth ?? 1) * 2;
            if (d > radius * 2) return null;
            const near = d < radius;
            const scale = view.w / imageSize[0];
            return (
              <g pointerEvents="none">
                <circle
                  cx={first[0]} cy={first[1]} r={radius}
                  fill={near ? 'rgba(22, 163, 74, 0.20)' : 'none'}
                  stroke={near ? '#16a34a' : '#94a3b8'}
                  strokeWidth={(near ? 2.5 : 1.5) * scale}
                  strokeDasharray={near ? '0' : '4,3'}
                />
                {near && (
                  <text
                    x={first[0] + radius + 8} y={first[1]}
                    fill="#16a34a"
                    fontSize={11 * scale}
                    fontFamily="ui-monospace, monospace"
                    style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 * scale }}
                  >
                    Klick = Polygon schließen
                  </text>
                )}
              </g>
            );
          })()}
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
          {/* Höhenkote Bezugsachse — persistent vertical line at the X of
              the first Höhenkote in the scene. Visible whenever the
              Höhenkote tool is active and at least one Höhenkote exists.
              All subsequent Höhenkoten lock to this X (Alt to defeat). */}
          {tool === 'height_mark' && (() => {
            const hks = labels.filter((l) => l.type === 'height_mark');
            if (hks.length === 0) return null;
            const bezugX = hks[0].geometry.anchor[0];
            const sw = 1.5 / Math.max(0.1, view.w / imageSize[0]);
            return (
              <g pointerEvents="none">
                <line
                  x1={bezugX} y1={0}
                  x2={bezugX} y2={imageSize[1]}
                  stroke="#0ea5e9"
                  strokeWidth={sw}
                  strokeDasharray="8,5"
                  opacity={0.7}
                />
                <text
                  x={bezugX + 6}
                  y={18 * (view.w / imageSize[0])}
                  fill="#0ea5e9"
                  fontFamily="ui-monospace, monospace"
                  fontSize={11 * (view.w / imageSize[0])}
                  fontWeight={600}
                  style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}
                >
                  Bezugsachse · Alt = freie Platzierung
                </text>
              </g>
            );
          })()}
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
              commit. The length/length-match/snap info now all live in the
              unified DrawHUD panel; no in-canvas badges here. */}
          {pendingStart && hoverPt && (tool === 'dimensioned_distance' || tool === 'wall') && (
            <line
              x1={pendingStart[0]} y1={pendingStart[1]}
              x2={snap?.pt[0] ?? hoverPt[0]} y2={snap?.pt[1] ?? hoverPt[1]}
              stroke="#f59e0b" strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
              strokeDasharray="6,4"
            />
          )}
          {/* N1: floorplan_opening preview — when attached to a wall, draw
              the actual along-wall rectangle (projected onto the wall axis,
              thickness from wall). Looks like a segment of the wall, not a
              free diagonal — matches what the commit will produce. */}
          {pendingStart && hoverPt && tool === 'floorplan_opening' && pendingAttachedWallId && (() => {
            const parent = labels.find((l) => l.id === pendingAttachedWallId && l.type === 'wall');
            if (!parent || parent.type !== 'wall') return null;
            const ws = parent.geometry.start;
            const we = parent.geometry.end;
            const wdx = we[0] - ws[0];
            const wdy = we[1] - ws[1];
            const wlen = Math.hypot(wdx, wdy);
            const ux = wlen ? wdx / wlen : 1;
            const uy = wlen ? wdy / wlen : 0;
            const endRaw = snap?.pt ?? hoverPt;
            const projA = (pendingStart[0] - ws[0]) * ux + (pendingStart[1] - ws[1]) * uy;
            const projB = (endRaw[0] - ws[0]) * ux + (endRaw[1] - ws[1]) * uy;
            const t0 = Math.min(projA, projB);
            const t1 = Math.max(projA, projB);
            const thicknessMm = parent.attributes.thickness_mm ?? 365;
            const depthHalfPx = (thicknessMm * WALL_PX_PER_MM) / 2;
            const px = -uy;
            const py = ux;
            const along = (t: number): Point => [ws[0] + ux * t, ws[1] + uy * t];
            const a = along(t0);
            const b = along(t1);
            const a1: Point = [a[0] + px * depthHalfPx, a[1] + py * depthHalfPx];
            const a2: Point = [a[0] - px * depthHalfPx, a[1] - py * depthHalfPx];
            const b1: Point = [b[0] + px * depthHalfPx, b[1] + py * depthHalfPx];
            const b2: Point = [b[0] - px * depthHalfPx, b[1] - py * depthHalfPx];
            const pts = [a1, b1, b2, a2].map((p) => p.join(',')).join(' ');
            return (
              <polygon
                points={pts}
                fill="rgba(245, 158, 11, 0.20)"
                stroke="#f59e0b"
                strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
                strokeDasharray="6,4"
              />
            );
          })()}
          {/* In-progress preview — 2-click rectangle tools (view_opening) */}
          {pendingStart && hoverPt && (
            (tool === 'view_opening' && viewOpeningShape === 'rectangle')
          ) && (
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
          {/* In-progress preview — circle (view_opening shape=circle) */}
          {pendingStart && hoverPt && tool === 'view_opening' && viewOpeningShape === 'circle' && (() => {
            const cx = pendingStart[0];
            const cy = pendingStart[1];
            const ex = snap?.pt[0] ?? hoverPt[0];
            const ey = snap?.pt[1] ?? hoverPt[1];
            const r = Math.hypot(ex - cx, ey - cy);
            return (
              <circle
                cx={cx} cy={cy} r={r}
                fill="rgba(245, 158, 11, 0.15)"
                stroke="#f59e0b" strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
                strokeDasharray="6,4"
              />
            );
          })()}
          {/* In-progress preview — polygon view_opening (polyline-stops) */}
          {tool === 'view_opening' && viewOpeningShape === 'polygon' && pendingPolyline.length > 0 && (
            <>
              <polyline
                points={[
                  ...pendingPolyline.map((p) => p.join(',')),
                  ...(hoverPt ? [hoverPt.join(',')] : []),
                ].join(' ')}
                fill="rgba(245, 158, 11, 0.10)"
                stroke="#f59e0b" strokeWidth={2 / Math.max(0.1, view.w / imageSize[0])}
                strokeDasharray="6,4"
              />
              {pendingPolyline.map((p, i) => (
                <circle key={i} cx={p[0]} cy={p[1]} r={4 / Math.max(0.1, view.w / imageSize[0])} fill="#f59e0b" />
              ))}
            </>
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
        {/* Removed: bottom-left hotkey legend. Hotkeys are visible on the
            sidebar tool buttons themselves; the full cheatsheet stays
            accessible via "?". */}
        {/* Canvas-display controls — opacity slider + color/gray toggle +
            zoom buttons in ONE horizontal palette at the bottom-right.
            Both image settings persist across images and houses
            (localStorage). Canvas background is white so lowering the
            opacity correctly fades the image to WHITE (the previous
            zinc-800 background made it fade to dark which read wrong). */}
        <div className="absolute bottom-3 right-3 flex items-center gap-1 bg-white/95 border border-zinc-300 rounded shadow-md px-2 py-1">
          <button
            type="button"
            onClick={() => setImgGrayscale((v) => !v)}
            className={`px-2 py-1 rounded text-[0.65rem] font-medium border transition ${
              imgGrayscale
                ? 'bg-zinc-800 text-white border-zinc-800'
                : 'bg-white text-zinc-700 border-border hover:bg-zinc-50'
            }`}
            title="Bild in Graustufen oder Farbe anzeigen"
          >
            {imgGrayscale ? 'Grau' : 'Farbe'}
          </button>
          <label className="flex items-center gap-1.5 ml-1">
            <span className="text-[0.6rem] text-zinc-500">Op</span>
            <input
              type="range"
              min={0.1} max={1} step={0.05}
              value={imgOpacity}
              onChange={(e) => setImgOpacity(Number(e.target.value))}
              className="w-24 accent-accent"
              title="Bilddeckkraft — Bild auf Weiß ausblenden, um Labels gegen den weißen Canvas zu prüfen"
            />
            <span className="text-[0.6rem] tabular-nums text-zinc-500 w-8 text-right">
              {Math.round(imgOpacity * 100)}%
            </span>
          </label>
          <div className="mx-1 w-px h-5 bg-zinc-300" aria-hidden="true" />
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
            onClick={() => zoomBy(0.7)}
            className="w-7 h-7 inline-flex items-center justify-center rounded hover:bg-zinc-100 text-zinc-800 text-lg leading-none"
            title="Hereinzoomen (+)"
          >
            +
          </button>
          <button
            type="button"
            onClick={resetView}
            className="w-9 h-7 inline-flex items-center justify-center rounded hover:bg-zinc-100 text-zinc-700 text-[0.6rem] font-semibold leading-none"
            title="Ansicht zurücksetzen (0/R)"
          >
            FIT
          </button>
        </div>
        <DrawHUD tool={tool} pendingStart={pendingStart} hoverPt={hoverPt} pendingPolyline={pendingPolyline} snap={snap} lengthMatch={lengthMatch} />
        {/* M12 inline edit. <input> floats at the cursor; Enter commits, Esc
            cancels (and deletes the freshly-created label so the user can
            click again). */}
        {pendingInlineEdit && (() => {
          // W5 — pre-fill with the dim's current value_mm if it was already
          // set (e.g. from Phase 4 derivation or cross-scene cache). User
          // can confirm with Enter or override.
          const target = labels.find((l) => l.id === pendingInlineEdit.labelId);
          let initialValue = '';
          if (target?.type === 'dimensioned_distance' && pendingInlineEdit.field === 'value_mm') {
            const v = target.attributes.value_mm;
            if (typeof v === 'number' && v > 0) {
              initialValue = (v / 1000).toFixed(2).replace('.', ',');
            }
          }
          return (
          <InlineEditInput
            key={pendingInlineEdit.labelId}
            screenPos={pendingInlineEdit.screenPos}
            placeholder={pendingInlineEdit.field === 'text' ? 'z. B. 1,75' : 'z. B. 1,75 oder 1750'}
            initialValue={initialValue}
            onCommit={(value) => {
              const trimmed = value.trim();
              if (trimmed === '') {
                // Empty commit (e.g. user clicks elsewhere without typing) —
                // JUST DISMISS the input. Don't delete the freshly-placed
                // label: the user can still see it and fill in the value
                // later via the inspector. Esc is the explicit "cancel"
                // gesture that does delete (see onCancel).
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
          );
        })()}
        {/* Building-axis badge — bottom-left corner. Shows the detected
            plan rotation so the user knows the snap is rotated to match
            the plan, not the image frame. Click to toggle adaptive snap
            (same as the Q hotkey). */}
        {/* Render NOTHING when adaptive snap is on AND no rotation detected
            (the common case). Render plain-language badge only when:
            (a) a non-trivial rotation is detected, or
            (b) the user disabled adaptive snap and we want to show that. */}
        {/* Ortho-snap state badge. Renders ONLY when there's something
            worth telling the user:
              • adaptive snap ON + plan rotation detected → emerald badge
                with the detected angle
              • adaptive snap OFF → amber "Ortho-Snap aus" so the user
                knows they're outside the default
            Silent otherwise. */}
        {(() => {
          const hasRotation = adaptiveAxisEnabled && axisConfident && Math.abs(detectedAxisDeg) >= 0.5;
          if (!hasRotation && adaptiveAxisEnabled) return null;
          const click = () => {
            setAdaptiveAxisEnabled((v) => {
              const next = !v;
              try { window.localStorage.setItem('bim-db:annotate:adaptive-axis', String(next)); } catch { /* no-op */ }
              return next;
            });
          };
          return (
            <button
              type="button"
              onClick={click}
              className={`absolute bottom-3 left-3 px-2.5 py-1.5 rounded-md text-[0.72rem] shadow-sm border transition ${
                hasRotation
                  ? 'bg-emerald-50 border-emerald-300 text-emerald-900 hover:bg-emerald-100'
                  : 'bg-amber-50 border-amber-300 text-amber-900 hover:bg-amber-100'
              }`}
              title="Q zum Umschalten"
            >
              {hasRotation
                ? <>Plan ist <span className="font-mono font-semibold">{detectedAxisDeg.toFixed(1)}°</span> gedreht — Snap folgt der Plan-Achse <span className="ml-1 text-emerald-700">[Q: Aus]</span></>
                : <>Ortho-Snap aus <span className="ml-1 text-amber-700">[Q: An]</span></>}
            </button>
          );
        })()}
        {/* Inline post-draw classifier chip (M3.2). Anchored at the
            just-drawn label's centroid; one-click or one-key sets the kind
            without scrolling to the inspector. Auto-dismisses after 3 s. */}
        {postDrawChip && (() => {
          const svg = svgRef.current;
          const ctm = svg?.getScreenCTM();
          if (!ctm) return null;
          const screenX = postDrawChip.anchor[0] * ctm.a + postDrawChip.anchor[1] * ctm.c + ctm.e;
          const screenY = postDrawChip.anchor[0] * ctm.b + postDrawChip.anchor[1] * ctm.d + ctm.f;
          const parentRect = svg?.parentElement?.getBoundingClientRect();
          const left = screenX - (parentRect?.left ?? 0);
          const top = screenY - (parentRect?.top ?? 0);
          return (
            <PostDrawChip
              kindFamily={postDrawChip.kindFamily}
              left={left}
              top={top}
              onPick={(kind) => {
                const lbl = labels.find((l) => l.id === postDrawChip.labelId);
                if (!lbl) { setPostDrawChip(null); return; }
                const attrKey = lbl.type === 'component_line' ? 'line_kind' : 'opening_kind';
                updateLabel(postDrawChip.labelId, {
                  attributes: { ...lbl.attributes, [attrKey]: kind } as never,
                } as never);
                setPostDrawChip(null);
              }}
              onDismiss={() => setPostDrawChip(null)}
              onHoverEnter={() => setPostDrawChipPaused(true)}
              onHoverLeave={() => setPostDrawChipPaused(false)}
            />
          );
        })()}
        {/* Color legend pip — bottom-right canvas corner */}
        <ColorLegendWidget />
        {/* K7 contextual actions bar — small text at bottom-center
            showing the keys that matter RIGHT NOW for current tool +
            selection + pending state. Always-visible, low-noise. */}
        <ActionsBar
          tool={tool}
          selectedIds={selectedIds}
          selectedLabel={selectedLabel}
          pendingStart={pendingStart}
          pendingPolyline={pendingPolyline}
          adaptiveAxisEnabled={adaptiveAxisEnabled}
        />
        {/* M12 toast stack — bottom-center over the canvas */}
        <ToastStack toasts={toasts} />
        {/* W4 — compass widget on Ansicht/Schnitt + north arrow on Grundriss.
            Renders only when Phase 3 orientation is set (so it actually has
            meaningful data to show). */}
        {workflowSnapshot.facts.orientation?.north_edge_label_id &&
         (sceneTag === 'ansicht' || sceneTag === 'schnitt' || sceneTag === 'grundriss') && (
          <SceneCompass
            sceneTag={sceneTag}
            sceneOrientation={sceneOrientation}
            facts={workflowSnapshot.facts}
            currentSceneFile={decodedFile}
            currentSceneLabels={labels}
          />
        )}
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
            // Hide the popover while a draw is in flight (pendingStart set) OR
            // while the user is dragging an existing label. Both states are
            // gestures where the popover would visually obstruct the canvas.
            opacity: (isDragging || pendingStart !== null) ? 0.05 : 1,
            pointerEvents: (isDragging || pendingStart !== null) ? 'none' : 'auto',
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
              anchorScreenPt={(() => {
                const anchorLabel = selectedLabel ?? labels.find((l) => selectedIds.has(l.id));
                if (!anchorLabel) return null;
                const c = labelCentroid(anchorLabel);
                const svg = svgRef.current;
                const ctm = svg?.getScreenCTM();
                if (!ctm) return null;
                return [
                  c[0] * ctm.a + c[1] * ctm.c + ctm.e,
                  c[0] * ctm.b + c[1] * ctm.d + ctm.f,
                ];
              })()}
              obstacleScreenPts={(() => {
                // P4: pass every visible label's centroid so the popover
                // picks the corner farthest from ALL of them, not just
                // opposite-quadrant of the selection.
                const svg = svgRef.current;
                const ctm = svg?.getScreenCTM();
                if (!ctm) return [];
                const out: Array<[number, number]> = [];
                for (const l of labels) {
                  const c = labelCentroid(l);
                  out.push([
                    c[0] * ctm.a + c[1] * ctm.c + ctm.e,
                    c[0] * ctm.b + c[1] * ctm.d + ctm.f,
                  ]);
                }
                return out;
              })()}
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
                  sceneFile={decodedFile}
                  provenance={crossSceneProvenance.get(selectedLabel.id) ?? null}
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
                  scope={scope}
                  houseKey={key}
                  onAutoFillToast={(m, labelId, note) => {
                    addToast(m, 'success', 2500);
                    if (labelId && note) {
                      setCrossSceneProvenance((mp) => {
                        const n = new Map(mp);
                        n.set(labelId, note);
                        return n;
                      });
                    }
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

// N6 — small pickers for scene_orientation (Ansicht/Schnitt) and scene_level
// (Grundriss). Rendered inline under the scene-tag block.
function SceneOrientationPicker({
  value, onChange,
}: { value: SceneOrientation | null; onChange: (v: SceneOrientation | null) => void }) {
  const opts: Array<{ id: SceneOrientation; label: string }> = [
    { id: 'north', label: 'N' }, { id: 'east', label: 'O' },
    { id: 'south', label: 'S' }, { id: 'west', label: 'W' },
  ];
  return (
    <div className="mt-2">
      <div className="text-[0.62rem] uppercase tracking-wider text-zinc-500 font-semibold mb-1">
        Himmelsrichtung
      </div>
      <div className="flex gap-1">
        {opts.map((o) => {
          const active = value === o.id;
          return (
            <button
              key={o.id}
              type="button"
              onClick={() => onChange(active ? null : o.id)}
              className={`flex-1 px-2 py-1 rounded text-[0.72rem] font-mono ${
                active ? 'bg-accent text-white font-semibold' : 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200'
              }`}
              title={`${o.label} — ${active ? 'abwählen' : 'als Himmelsrichtung setzen'}`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SceneLevelPicker({
  value, onChange,
}: { value: SceneLevel | null; onChange: (v: SceneLevel | null) => void }) {
  const opts: Array<{ id: SceneLevel; label: string; full: string }> = [
    { id: 'kg', label: 'KG', full: 'Kellergeschoss' },
    { id: 'ug', label: 'UG', full: 'Untergeschoss' },
    { id: 'eg', label: 'EG', full: 'Erdgeschoss' },
    { id: 'og', label: 'OG', full: 'Obergeschoss' },
    { id: 'dg', label: 'DG', full: 'Dachgeschoss' },
    { id: 'spitzboden', label: 'Spitzb.', full: 'Spitzboden' },
  ];
  return (
    <div className="mt-2">
      <div className="text-[0.62rem] uppercase tracking-wider text-zinc-500 font-semibold mb-1">
        Geschoss
      </div>
      <div className="grid grid-cols-3 gap-1">
        {opts.map((o) => {
          const active = value === o.id;
          return (
            <button
              key={o.id}
              type="button"
              onClick={() => onChange(active ? null : o.id)}
              className={`px-2 py-1 rounded text-[0.72rem] ${
                active ? 'bg-accent text-white font-semibold' : 'bg-zinc-100 text-zinc-700 hover:bg-zinc-200'
              }`}
              title={`${o.full} — ${active ? 'abwählen' : 'als Geschoss setzen'}`}
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// W1+ — house-first workflow guidance panel. Lives at the top of the rail
// above Szenen-Tag. Shows the current phase, what's blocking it, and a
// "go here" button to jump to the recommended scene. Auto-collapses once
// the user reaches Phase 5 (detail labeling).
function WorkflowGuide({
  facts,
  scenes,
  currentSceneFile,
  currentSceneTag,
  currentSceneLabels,
  onGoToScene,
  onSetOrientation,
  onMarkDetailDone,
}: {
  facts: HouseFacts;
  scenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  currentSceneTag: SceneTag;
  currentSceneLabels: Label[];
  onGoToScene: (file: string) => void;
  /** Phase 3 hook — caller writes facts.orientation, saves, and bumps
   *  sceneSummaryRev so the WorkflowGuide re-derives. */
  onSetOrientation: (wallId: string, grundrissFile: string) => void;
  /** Phase 5 hook — user clicks "fertig"; caller stamps
   *  workflow.phase_completed_at.detail = nowIso(). */
  onMarkDetailDone: () => void;
}) {
  const phase = workflowCurrentPhase(facts, scenes);
  const snap = workflowPhaseStatusSnapshot(facts, scenes);
  const [open, setOpen] = useState(phase !== 'detail');
  const phaseList: PhaseId[] = ['inventory', 'height_anchor', 'footprint', 'orientation', 'bezugsmasse', 'detail'];
  const phaseIdx = phaseList.indexOf(phase);

  // W1 — Phase 0 inventory body: list every scene that's missing a
  // tag/orientation/level. Per-scene "open" button → goToScene().
  const inventoryGaps = (() => {
    const gaps: { file: string; reason: string }[] = [];
    for (const s of scenes) {
      const meta = facts.scene_metadata[s.file];
      const tag = meta?.kind ?? s.tag;
      if (!tag || tag === 'nicht_klassifiziert') {
        gaps.push({ file: s.file, reason: 'Szenen-Tag fehlt' });
        continue;
      }
      if ((tag === 'ansicht' || tag === 'schnitt') && !meta?.orientation) {
        gaps.push({ file: s.file, reason: 'Himmelsrichtung fehlt' });
        continue;
      }
      if (tag === 'grundriss' && !meta?.level) {
        gaps.push({ file: s.file, reason: 'Geschoss fehlt' });
      }
    }
    return gaps;
  })();

  return (
    <section className="border border-zinc-200 rounded bg-zinc-50">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 px-2 py-1.5 text-[0.7rem] uppercase tracking-wider text-zinc-700 font-semibold"
      >
        <span className="w-3 text-center">{open ? '▾' : '▸'}</span>
        <span>Arbeitsablauf</span>
        <span className="ml-auto text-zinc-500 font-normal">
          Phase {phaseIdx + 1} / 6 — {workflowPhaseLabelDe(phase)}
        </span>
      </button>
      {open && (
        <div className="px-2 pb-2 space-y-1.5">
          {/* Six-step progress strip. Filled = complete (or user-skipped),
              empty = pending. The current step gets the accent ring. */}
          <div className="flex gap-1">
            {phaseList.map((p, i) => {
              const st = snap[p];
              const isCurrent = p === phase;
              const cls = st.complete
                ? 'bg-emerald-500 border-emerald-600'
                : isCurrent
                  ? 'bg-amber-200 border-amber-500'
                  : 'bg-zinc-100 border-zinc-300';
              return (
                <div
                  key={p}
                  className={`flex-1 h-1.5 rounded border ${cls}`}
                  title={`${i + 1}. ${workflowPhaseLabelDe(p)}${st.complete ? ' ✓' : ''}`}
                />
              );
            })}
          </div>

          {/* Phase 0 body. */}
          {phase === 'inventory' && (
            <div className="space-y-1.5">
              <p className="text-[0.72rem] text-zinc-700 leading-snug">
                <span className="font-semibold">Phase 1: Szenen-Inventar.</span> Jede
                Szene braucht einen Tag + (für Ansicht/Schnitt) eine Himmelsrichtung,
                (für Grundriss) ein Geschoss.
              </p>
              <p className="text-[0.7rem] text-zinc-500">
                {scenes.length - inventoryGaps.length} / {scenes.length} klassifiziert
                {inventoryGaps.length > 0 ? ` — ${inventoryGaps.length} offen:` : ' ✓'}
              </p>
              <ul className="space-y-0.5 ml-1 max-h-44 overflow-auto">
                {inventoryGaps.map((g) => {
                  const isCurrent = g.file === currentSceneFile;
                  return (
                    <li key={g.file}>
                      <button
                        type="button"
                        onClick={() => !isCurrent && onGoToScene(g.file)}
                        disabled={isCurrent}
                        className={`w-full text-left text-[0.7rem] px-1.5 py-0.5 rounded flex items-center gap-1.5 ${
                          isCurrent
                            ? 'bg-amber-100 text-amber-900 font-semibold cursor-default'
                            : 'hover:bg-zinc-100 text-zinc-800'
                        }`}
                        title={g.reason}
                      >
                        <span className="text-amber-600">⚠</span>
                        <span className="truncate flex-1">{g.file}</span>
                        <span className="text-[0.62rem] text-zinc-500">
                          {isCurrent ? '↳ hier' : g.reason}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {/* Phase 1 body — height anchor. */}
          {phase === 'height_anchor' && (
            <WorkflowGuideHeightAnchor
              facts={facts}
              scenes={scenes}
              currentSceneFile={currentSceneFile}
              onGoToScene={onGoToScene}
            />
          )}

          {/* Phase 2 body — footprint. */}
          {phase === 'footprint' && (
            <WorkflowGuideFootprint
              facts={facts}
              scenes={scenes}
              currentSceneFile={currentSceneFile}
              onGoToScene={onGoToScene}
            />
          )}

          {/* Phase 3 body — orientation. */}
          {phase === 'orientation' && (
            <WorkflowGuideOrientation
              facts={facts}
              scenes={scenes}
              currentSceneFile={currentSceneFile}
              currentSceneTag={currentSceneTag}
              currentSceneLabels={currentSceneLabels}
              onGoToScene={onGoToScene}
              onSetOrientation={onSetOrientation}
            />
          )}

          {/* Phase 4 body — bezugsmasse. */}
          {phase === 'bezugsmasse' && (
            <WorkflowGuideBezugsmasse
              facts={facts}
              scenes={scenes}
              currentSceneFile={currentSceneFile}
              currentSceneTag={currentSceneTag}
              currentSceneLabels={currentSceneLabels}
              onGoToScene={onGoToScene}
            />
          )}
          {phase === 'detail' && (
            <WorkflowGuideDetail
              facts={facts}
              scenes={scenes}
              currentSceneFile={currentSceneFile}
              onGoToScene={onGoToScene}
              onMarkDetailDone={onMarkDetailDone}
            />
          )}
        </div>
      )}
    </section>
  );
}

// W2 — Phase 1 sub-step UI. Lists the named height datums; each is ✓ if
// in house_facts.heights, ○ if missing. When the user is on the
// recommended Phase 1 scene, shows them as a checklist; otherwise shows
// the "go here" button.
function WorkflowGuideHeightAnchor({
  facts, scenes, currentSceneFile, onGoToScene,
}: {
  facts: HouseFacts;
  scenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  onGoToScene: (file: string) => void;
}) {
  const rec = workflowRecommendSceneFor('height_anchor', facts, scenes);
  const onRec = rec === currentSceneFile;
  const fmt = (mm: number | undefined) =>
    typeof mm !== 'number' ? '—'
    : mm === 0 ? '±0,00'
    : `${mm > 0 ? '+' : ''}${(mm / 1000).toFixed(2).replace('.', ',')} m`;
  const required: Array<{ key: keyof typeof facts.heights; label: string; required: boolean }> = [
    { key: 'bezug_mm', label: 'Bezugshöhe (±0,00)', required: true },
    { key: 'first_mm', label: 'First',              required: true },
    { key: 'traufe_mm', label: 'Traufe',            required: false },
    { key: 'gelaende_mm', label: 'Gelände',         required: false },
    { key: 'ok_ffb_eg_mm', label: 'OK FFB EG',      required: false },
    { key: 'ok_ffb_og_mm', label: 'OK FFB OG',      required: false },
    { key: 'ok_ffb_dg_mm', label: 'OK FFB DG',      required: false },
  ];
  return (
    <div className="space-y-1.5">
      <p className="text-[0.72rem] text-zinc-700 leading-snug">
        <span className="font-semibold">Phase 2: Höhenkoten ankern.</span>{' '}
        Bezugshöhe + First in einer Ansicht/Schnitt setzen. Weitere Höhen
        sind willkommen, aber optional.
      </p>
      {rec && !onRec && (
        <button
          type="button"
          onClick={() => onGoToScene(rec)}
          className="w-full text-left text-[0.7rem] px-2 py-1 rounded bg-amber-100 hover:bg-amber-200 text-amber-900 font-medium"
        >
          → Empfohlene Szene: {rec}
        </button>
      )}
      {onRec && (
        <p className="text-[0.7rem] text-emerald-700">↳ du bist auf der empfohlenen Szene.</p>
      )}
      <ul className="space-y-0.5 ml-1">
        {required.map(({ key, label, required: req }) => {
          const v = facts.heights[key] as number | undefined;
          const set = typeof v === 'number';
          return (
            <li
              key={key}
              className="flex items-center gap-1.5 text-[0.7rem]"
              title={set ? `${label} = ${fmt(v)}` : (req ? 'Pflicht für Phase 2' : 'optional')}
            >
              <span className={set ? 'text-emerald-600' : (req ? 'text-amber-600' : 'text-zinc-400')}>
                {set ? '✓' : (req ? '⚠' : '○')}
              </span>
              <span className={set ? 'text-zinc-600' : 'text-zinc-800'}>{label}</span>
              <span className="ml-auto font-mono text-zinc-500">{set ? fmt(v) : '—'}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// W3 — Phase 2 sub-step UI. Checks footprint extent + outer wall
// thickness; pulls every fact from house_facts. New outer walls drawn
// in any scene of this house inherit wall_thickness.outer_mm by default.
function WorkflowGuideFootprint({
  facts, scenes, currentSceneFile, onGoToScene,
}: {
  facts: HouseFacts;
  scenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  onGoToScene: (file: string) => void;
}) {
  const rec = workflowRecommendSceneFor('footprint', facts, scenes);
  const onRec = rec === currentSceneFile;
  const fmt = (mm: number | undefined) =>
    typeof mm !== 'number' ? '—' : `${(mm / 1000).toFixed(2).replace('.', ',')} m`;
  const fmtTh = (mm: number | undefined) =>
    typeof mm !== 'number' ? '—' : `${mm} mm`;
  const checklist: Array<{ key: string; label: string; set: boolean; value: string }> = [
    {
      key: 'width', label: 'Breite (horizontaler Bezugsmaß)',
      set: typeof facts.extent.width_mm === 'number',
      value: fmt(facts.extent.width_mm),
    },
    {
      key: 'depth', label: 'Tiefe (vertikaler Bezugsmaß)',
      set: typeof facts.extent.depth_mm === 'number',
      value: fmt(facts.extent.depth_mm),
    },
    {
      key: 'outer', label: 'Außenwand-Dicke',
      set: typeof facts.wall_thickness.outer_mm === 'number',
      value: fmtTh(facts.wall_thickness.outer_mm),
    },
  ];
  return (
    <div className="space-y-1.5">
      <p className="text-[0.72rem] text-zinc-700 leading-snug">
        <span className="font-semibold">Phase 3: Hausgrundriss vermessen.</span>{' '}
        Außenwände zeichnen, Wandstärke setzen, horizontalen + vertikalen
        Bezugsmaß über die volle Gebäudebreite/-tiefe legen.
      </p>
      {rec && !onRec && (
        <button
          type="button"
          onClick={() => onGoToScene(rec)}
          className="w-full text-left text-[0.7rem] px-2 py-1 rounded bg-amber-100 hover:bg-amber-200 text-amber-900 font-medium"
        >
          → Empfohlene Szene: {rec}
        </button>
      )}
      {onRec && (
        <p className="text-[0.7rem] text-emerald-700">↳ du bist auf dem EG-Grundriss.</p>
      )}
      <ul className="space-y-0.5 ml-1">
        {checklist.map(({ key, label, set, value }) => (
          <li key={key} className="flex items-center gap-1.5 text-[0.7rem]">
            <span className={set ? 'text-emerald-600' : 'text-amber-600'}>{set ? '✓' : '⚠'}</span>
            <span className={set ? 'text-zinc-600' : 'text-zinc-800'}>{label}</span>
            <span className="ml-auto font-mono text-zinc-500">{value}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// W4 — compass widget. On Grundriss: shows the picked north arrow.
// On Ansicht/Schnitt: shows the cardinal direction the scene faces.
// Always positioned at bottom-right, screen-pinned, doesn't intercept
// pointer events on the canvas.
function SceneCompass({
  sceneTag, sceneOrientation, facts, currentSceneFile, currentSceneLabels,
}: {
  sceneTag: SceneTag;
  sceneOrientation: SceneOrientation | null;
  facts: HouseFacts;
  currentSceneFile: string;
  currentSceneLabels: Label[];
}) {
  // Determine the arrow direction.
  let label = '?';
  let arrowDeg = 0;        // 0 = up
  if (sceneTag === 'ansicht' || sceneTag === 'schnitt') {
    if (sceneOrientation) {
      label = ({ north: 'N', south: 'S', east: 'O', west: 'W' } as const)[sceneOrientation];
      arrowDeg = ({ north: 0, east: 90, south: 180, west: 270 } as const)[sceneOrientation];
    }
  } else if (sceneTag === 'grundriss') {
    // On the source Grundriss, render the picked wall's normal vector as
    // the north arrow. Otherwise just show "N" pointing up.
    label = 'N';
    if (currentSceneFile === facts.orientation?.source_grundriss_file) {
      const wall = currentSceneLabels.find(
        (l) => l.id === facts.orientation?.north_edge_label_id && l.type === 'wall',
      ) as WallLabel | undefined;
      if (wall) {
        const dx = wall.geometry.end[0] - wall.geometry.start[0];
        const dy = wall.geometry.end[1] - wall.geometry.start[1];
        // The wall RUNS along its length; north is the outward normal of
        // the building, which is perpendicular. The convention: the
        // outward normal points away from the building's centroid. We
        // don't have the centroid here, so use the wall's perpendicular
        // direction (the *image* perpendicular — left-hand normal).
        const ang = (Math.atan2(-dx, dy) * 180) / Math.PI;
        arrowDeg = ((ang % 360) + 360) % 360;
      }
    }
  }
  return (
    <div className="absolute bottom-3 right-3 z-10 pointer-events-none">
      <div className="w-12 h-12 rounded-full bg-white/95 border border-zinc-300 shadow-md flex items-center justify-center relative">
        <span
          className="absolute text-amber-600 font-bold text-base leading-none"
          style={{
            transform: `rotate(${arrowDeg}deg)`,
            transformOrigin: '50% 50%',
            top: '6px',
          }}
        >
          ▲
        </span>
        <span className="absolute bottom-0.5 text-[0.62rem] font-bold text-zinc-700">
          {label}
        </span>
      </div>
    </div>
  );
}

// W6 — Phase 5 detail UI. Hausgerüst steht; user goes scene-by-scene.
// Coverage heuristic per scene: count expected label types (walls for
// Grundriss, openings + heights for Ansicht/Schnitt) vs. observed. Bar
// shows the running average; click any scene to navigate. "Fertig"
// button stamps phase_completed_at.detail.
function WorkflowGuideDetail({
  facts, scenes, currentSceneFile, onGoToScene, onMarkDetailDone,
}: {
  facts: HouseFacts;
  scenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  onGoToScene: (file: string) => void;
  onMarkDetailDone: () => void;
}) {
  // For each scene, we don't have its full label list here (only the
  // current scene's). Fall back to scene_metadata. Coverage is "is the
  // scene calibrated AND has metadata" for Ansicht/Schnitt, "has level"
  // for Grundriss. Real per-scene label-count coverage would need a
  // server endpoint; this is the cheap version.
  const done = facts.workflow?.phase_completed_at.detail != null;
  return (
    <div className="space-y-1.5">
      {!done && (
        <p className="text-[0.72rem] text-zinc-700 leading-snug">
          <span className="font-semibold">Phase 6: Detail-Beschriftung.</span>{' '}
          Hausgerüst steht — geh die Szenen frei durch und ergänze
          Öffnungen, Wände, weitere Höhen, Maße. Wenn du fertig bist,
          klick „Haus fertig" unten.
        </p>
      )}
      {done && (
        <p className="text-[0.72rem] text-emerald-700 leading-snug">
          ✓ Du hast dieses Haus als fertig markiert. Du kannst trotzdem
          weiter beschriften.
        </p>
      )}
      <ul className="space-y-0.5 ml-1 max-h-44 overflow-auto">
        {scenes.map((s) => {
          const meta = facts.scene_metadata[s.file];
          const tag = meta?.kind ?? s.tag ?? '?';
          const hasCalib = !!facts.calibration_per_scene[s.file];
          const isHeightScene = tag === 'ansicht' || tag === 'schnitt';
          const ok = isHeightScene ? hasCalib : tag === 'grundriss' ? !!meta?.level : !!meta?.kind;
          const isCurrent = s.file === currentSceneFile;
          return (
            <li key={s.file}>
              <button
                type="button"
                onClick={() => !isCurrent && onGoToScene(s.file)}
                disabled={isCurrent}
                className={`w-full text-left text-[0.7rem] px-1.5 py-0.5 rounded flex items-center gap-1.5 ${
                  isCurrent
                    ? 'bg-amber-100 text-amber-900 font-semibold cursor-default'
                    : 'hover:bg-zinc-100 text-zinc-800'
                }`}
                title={`${tag}${meta?.orientation ? ` · ${meta.orientation}` : ''}${meta?.level ? ` · ${meta.level}` : ''}${hasCalib ? ' · kalibriert' : ''}`}
              >
                <span className={ok ? 'text-emerald-600' : 'text-zinc-400'}>{ok ? '✓' : '○'}</span>
                <span className="flex-1 truncate">{s.file}</span>
                <span className="text-[0.62rem] text-zinc-500 font-mono">{tag.slice(0, 3)}</span>
              </button>
            </li>
          );
        })}
      </ul>
      {!done && (
        <button
          type="button"
          onClick={onMarkDetailDone}
          className="w-full text-[0.7rem] px-2 py-1 rounded bg-emerald-600 hover:bg-emerald-700 text-white font-semibold"
        >
          ✓ Haus fertig markieren
        </button>
      )}
    </div>
  );
}

// W5 — Phase 4 sub-step UI. Per current Ansicht/Schnitt: tracks 4.a
// (Bezugshöhe in scene), 4.b (horizontal is_reference dim), 4.c (vertical
// is_reference dim). The three together unlock per-scene calibration.
// When the user isn't on an Ansicht/Schnitt, shows the next-uncalibrated
// scene as the "go here" recommendation.
function WorkflowGuideBezugsmasse({
  facts, scenes, currentSceneFile, currentSceneTag, currentSceneLabels, onGoToScene,
}: {
  facts: HouseFacts;
  scenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  currentSceneTag: SceneTag;
  currentSceneLabels: Label[];
  onGoToScene: (file: string) => void;
}) {
  const rec = workflowRecommendSceneFor('bezugsmasse', facts, scenes);
  const onRec = rec === currentSceneFile;
  const isHeightScene = currentSceneTag === 'ansicht' || currentSceneTag === 'schnitt';
  // 4.a — Bezugshöhe (value_mm === 0) exists in current scene.
  const hasBezug = currentSceneLabels.some(
    (l) => l.type === 'height_mark' && l.attributes.value_mm === 0,
  );
  // 4.b / 4.c — is_reference dims with value_mm set, in horizontal vs vertical.
  let hasHRef = false; let hasVRef = false;
  for (const l of currentSceneLabels) {
    if (l.type !== 'dimensioned_distance' || !l.attributes.is_reference) continue;
    if (l.attributes.value_mm == null) continue;
    const dx = l.geometry.end[0] - l.geometry.start[0];
    const dy = l.geometry.end[1] - l.geometry.start[1];
    const ang = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
    if (ang < 15 || ang > 165) hasHRef = true;
    else if (ang > 75 && ang < 105) hasVRef = true;
  }
  // Coverage of all Ansicht/Schnitt scenes — how far the phase is overall.
  const heightScenes = scenes.filter((s) => {
    const t = facts.scene_metadata[s.file]?.kind ?? s.tag;
    return (t === 'ansicht' || t === 'schnitt') && !s.detail_only;
  });
  const done = heightScenes.filter((s) => facts.calibration_per_scene[s.file]).length;
  return (
    <div className="space-y-1.5">
      <p className="text-[0.72rem] text-zinc-700 leading-snug">
        <span className="font-semibold">Phase 5: Bezugsmaße pro Szene.</span>{' '}
        In jeder Ansicht/Schnitt eine Bezugshöhe (±0,00) setzen, dann
        einen horizontalen + vertikalen Bezugsmaß. Werte werden aus
        Haus-Maßen vorgeschlagen.
      </p>
      <p className="text-[0.7rem] text-zinc-500">
        {done} / {heightScenes.length} Szenen kalibriert
      </p>
      {rec && !onRec && (
        <button
          type="button"
          onClick={() => onGoToScene(rec)}
          className="w-full text-left text-[0.7rem] px-2 py-1 rounded bg-amber-100 hover:bg-amber-200 text-amber-900 font-medium"
        >
          → Nächste unkalibrierte Szene: {rec}
        </button>
      )}
      {isHeightScene && (
        <ul className="space-y-0.5 ml-1">
          <li className="flex items-center gap-1.5 text-[0.7rem]">
            <span className={hasBezug ? 'text-emerald-600' : 'text-amber-600'}>
              {hasBezug ? '✓' : '⚠'}
            </span>
            <span className={hasBezug ? 'text-zinc-600' : 'text-zinc-800'}>4.a — Bezugshöhe ±0,00 setzen</span>
          </li>
          <li className="flex items-center gap-1.5 text-[0.7rem]">
            <span className={hasHRef ? 'text-emerald-600' : 'text-amber-600'}>
              {hasHRef ? '✓' : '⚠'}
            </span>
            <span className={hasHRef ? 'text-zinc-600' : 'text-zinc-800'}>4.b — Horizontaler Bezugsmaß</span>
          </li>
          <li className="flex items-center gap-1.5 text-[0.7rem]">
            <span className={hasVRef ? 'text-emerald-600' : 'text-amber-600'}>
              {hasVRef ? '✓' : '⚠'}
            </span>
            <span className={hasVRef ? 'text-zinc-600' : 'text-zinc-800'}>4.c — Vertikaler Bezugsmaß</span>
          </li>
        </ul>
      )}
      {isHeightScene && !hasBezug && (
        <p className="text-[0.65rem] text-zinc-500 italic">
          Tipp: 4.a ist Pflicht zuerst. Ohne Bezugshöhe ist Y für geerbte Höhenkoten nicht definiert.
        </p>
      )}
    </div>
  );
}

// W4 — Phase 3 sub-step UI. On the EG Grundriss, lists every wall with
// its direction (horizontal / vertical) and length. Click a wall →
// records it as the north-facing edge via onSetOrientation. The
// geometric formula in faceLengthAlong() then drives Phase 4's
// horizontal Bezugsmaß auto-suggestions.
function WorkflowGuideOrientation({
  facts, scenes, currentSceneFile, currentSceneTag, currentSceneLabels,
  onGoToScene, onSetOrientation,
}: {
  facts: HouseFacts;
  scenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  currentSceneTag: SceneTag;
  currentSceneLabels: Label[];
  onGoToScene: (file: string) => void;
  onSetOrientation: (wallId: string, grundrissFile: string) => void;
}) {
  const rec = workflowRecommendSceneFor('orientation', facts, scenes);
  const onRec = rec === currentSceneFile;
  const onGrundriss = currentSceneTag === 'grundriss';
  const set = facts.orientation?.north_edge_label_id != null;
  // Outer walls on this Grundriss — heuristic: pick the 6 longest walls,
  // they're the most likely outer perimeter members.
  const outerWalls = (() => {
    if (!onGrundriss) return [];
    const walls = currentSceneLabels.filter((l) => l.type === 'wall');
    const sorted = walls
      .map((w) => {
        const wall = w as WallLabel;
        const dx = wall.geometry.end[0] - wall.geometry.start[0];
        const dy = wall.geometry.end[1] - wall.geometry.start[1];
        const len = Math.hypot(dx, dy);
        const angDeg = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
        const isHoriz = angDeg < 15 || angDeg > 165;
        const isVert = angDeg > 75 && angDeg < 105;
        return { id: wall.id, length: len, isHoriz, isVert };
      })
      .sort((a, b) => b.length - a.length)
      .slice(0, 8);
    return sorted;
  })();
  const px_per_mm = facts.calibration_per_scene[currentSceneFile]?.px_per_mm;
  return (
    <div className="space-y-1.5">
      <p className="text-[0.72rem] text-zinc-700 leading-snug">
        <span className="font-semibold">Phase 4: Himmelsrichtung festlegen.</span>{' '}
        Auf dem Grundriss eine Außenwand als Nordkante markieren. Damit
        weiß jede Ansicht/Schnitt, welche Hausbreite sie zeigt.
      </p>
      {set && (
        <p className="text-[0.7rem] text-emerald-700">
          ✓ Nordkante gewählt — du kannst neu wählen, wenn nötig.
        </p>
      )}
      {rec && !onRec && (
        <button
          type="button"
          onClick={() => onGoToScene(rec)}
          className="w-full text-left text-[0.7rem] px-2 py-1 rounded bg-amber-100 hover:bg-amber-200 text-amber-900 font-medium"
        >
          → Empfohlene Szene: {rec}
        </button>
      )}
      {onRec && !onGrundriss && (
        <p className="text-[0.7rem] text-amber-700">
          ⚠ Diese Szene ist nicht als Grundriss klassifiziert.
        </p>
      )}
      {onGrundriss && outerWalls.length === 0 && (
        <p className="text-[0.7rem] text-amber-700">
          Noch keine Wände. Phase 3 abschließen, dann hier weitermachen.
        </p>
      )}
      {onGrundriss && outerWalls.length > 0 && (
        <ul className="space-y-0.5 ml-1 max-h-44 overflow-auto">
          {outerWalls.map((w) => {
            const isPicked = facts.orientation?.north_edge_label_id === w.id;
            const dir = w.isHoriz ? '↔' : w.isVert ? '↕' : '↗';
            const mm = px_per_mm ? (w.length / px_per_mm).toFixed(0) : '—';
            return (
              <li key={w.id}>
                <button
                  type="button"
                  onClick={() => onSetOrientation(w.id, currentSceneFile)}
                  className={`w-full text-left text-[0.7rem] px-1.5 py-0.5 rounded flex items-center gap-1.5 ${
                    isPicked
                      ? 'bg-emerald-100 text-emerald-900 font-semibold'
                      : 'hover:bg-amber-50 text-zinc-800'
                  }`}
                  title={`Wall ${w.id.slice(0, 6)} — ${dir} ${mm} mm`}
                >
                  <span className="text-zinc-400 w-4 text-center">{dir}</span>
                  <span className="flex-1 truncate font-mono text-[0.65rem]">{w.id.slice(0, 8)}</span>
                  <span className="font-mono text-zinc-500">{mm} mm</span>
                  {isPicked && <span className="text-emerald-600 ml-1">✓ N</span>}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

function ToolPalette({
  tool,
  setTool,
  sceneTag,
  setSceneTag,
  sceneOrientation,
  setSceneOrientation,
  sceneLevel,
  setSceneLevel,
  workflowFacts,
  workflowScenes,
  currentSceneFile,
  onGoToScene,
  onSetOrientation,
  onMarkDetailDone,
  hiddenLabelIds,
  onToggleHiddenLabel,
  onHideInherited,
  inheritedLabelIds,
  labels,
  selectedId,
  onSelectLabel,
  onUndo,
  undoDepth,
  onResetView,
  autosave,
  onToggleAutosave,
  onResetDefaults,
  onResetLabels,
  allTools,
  onToggleAllTools,
  scope,
  houseKey,
  defaultsRev,
  onDefaultsChange,
  viewOpeningShape,
  onChangeViewOpeningShape,
  onApplyHouseHeight,
  onRefineAutoFix,
  onRefineTidyAll,
}: {
  tool: Tool;
  setTool: (t: Tool) => void;
  sceneTag: SceneTag;
  setSceneTag: (t: SceneTag) => void;
  sceneOrientation: SceneOrientation | null;
  setSceneOrientation: (v: SceneOrientation | null) => void;
  sceneLevel: SceneLevel | null;
  setSceneLevel: (v: SceneLevel | null) => void;
  workflowFacts: HouseFacts;
  workflowScenes: WorkflowSceneSummary[];
  currentSceneFile: string;
  onGoToScene: (file: string) => void;
  onSetOrientation: (wallId: string, grundrissFile: string) => void;
  onMarkDetailDone: () => void;
  hiddenLabelIds: Set<string>;
  onToggleHiddenLabel: (id: string) => void;
  onHideInherited: () => void;
  inheritedLabelIds: Set<string>;
  labels: Label[];
  selectedId: string | null;
  onSelectLabel: (id: string | null) => void;
  onUndo: () => void;
  undoDepth: number;
  onResetView: () => void;
  autosave: boolean;
  onToggleAutosave: () => void;
  onResetDefaults: () => void;
  onResetLabels: () => void;
  allTools: boolean;
  onToggleAllTools: () => void;
  scope: LabelScope;
  houseKey: string;
  defaultsRev: number;
  onDefaultsChange: () => void;
  viewOpeningShape: 'rectangle' | 'circle' | 'polygon';
  onChangeViewOpeningShape: (s: 'rectangle' | 'circle' | 'polygon') => void;
  onApplyHouseHeight: (datum: string, value_mm: number) => void;
  onRefineAutoFix: (issue: RefineIssue) => void;
  onRefineTidyAll: () => void;
}) {
  void defaultsRev;
  // Avoid unused-prop warnings; scope and houseKey are read by the inline
  // Phase 3 picker that writes facts.orientation.
  void scope; void houseKey;
  return (
    <div className="px-3 py-3 space-y-4">
      <WorkflowGuide
        facts={workflowFacts}
        scenes={workflowScenes}
        currentSceneFile={currentSceneFile}
        currentSceneTag={sceneTag}
        currentSceneLabels={labels}
        onGoToScene={onGoToScene}
        onSetOrientation={onSetOrientation}
        onMarkDetailDone={onMarkDetailDone}
      />
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
        {/* N6: scene-specific metadata. For Ansicht/Schnitt: which face
            (N/S/E/W). For Grundriss: which floor (KG/UG/EG/OG/DG/Spitzboden).
            Drives cross-scene cache scoping — Nordansicht's M1 references
            only pre-fill future Nordansichten, EG's wall thicknesses only
            pre-fill EG, etc. */}
        {(sceneTag === 'ansicht' || sceneTag === 'schnitt') && (
          <div className={sceneOrientation == null ? 'ring-2 ring-amber-400 rounded animate-pulse' : ''}>
            <SceneOrientationPicker value={sceneOrientation} onChange={setSceneOrientation} />
          </div>
        )}
        {sceneTag === 'grundriss' && (
          <div className={sceneLevel == null ? 'ring-2 ring-amber-400 rounded animate-pulse' : ''}>
            <SceneLevelPicker value={sceneLevel} onChange={setSceneLevel} />
          </div>
        )}
      </section>

      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
          Werkzeuge
        </h3>
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
              <Fragment key={t}>
                <ToolBtn
                  current={tool}
                  onSet={setTool}
                  value={t}
                  hotkey={meta.hotkey}
                  Icon={meta.Icon}
                >
                  {meta.label}
                </ToolBtn>
                {/* Shape submenu — only meaningful when view_opening is the
                    active tool. Sits where the old kind submenu used to be
                    (visually anchored to its parent ToolBtn). */}
                {t === 'view_opening' && tool === 'view_opening' && (
                  <ShapeSubmenu
                    value={viewOpeningShape}
                    onChange={onChangeViewOpeningShape}
                  />
                )}
              </Fragment>
            );
          })}
        </div>
        {sceneTag === 'nicht_klassifiziert' && !allTools && (
          <p className="text-[0.7rem] text-muted mt-2 leading-snug">
            Setze einen Szenen-Tag oben oder aktiviere „alle anzeigen", damit Werkzeuge verfügbar werden.
          </p>
        )}
      </section>

      <SceneChecklist sceneTag={sceneTag} labels={labels} />

      <RefineQueue
        labels={labels}
        scope={scope}
        houseKey={houseKey}
        sceneLevel={sceneLevel}
        sceneTag={sceneTag}
        sceneOrientation={sceneOrientation}
        onJump={(labelId) => onSelectLabel(labelId)}
        onAutoFix={onRefineAutoFix}
        onTidyAll={onRefineTidyAll}
      />

      {/* House heights panel — only relevant for scenes that contain
          Höhenkote (ansicht / schnitt / sonstiges). Shows house-wide known
          datums + one-click [Anwenden] to apply them to the current scene
          (either the selected Höhenkote, or a new one). */}
      {(sceneTag === 'ansicht' || sceneTag === 'schnitt' || sceneTag === 'sonstiges') && (
        <HouseHeightsPanel
          scope={scope}
          houseKey={houseKey}
          labels={labels}
          selectedId={selectedId}
          onApply={onApplyHouseHeight}
        />
      )}

      {/* Shape submenu lives inline under the view_opening ToolBtn above.
          Kind classification (Fenster/Tür/Gaube/…) happens post-draw in the
          inspector or via hotkeys — never as a pre-draw modal switch. */}

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
          <LabelsByType
            labels={labels}
            selectedId={selectedId}
            onSelect={onSelectLabel}
            hiddenIds={hiddenLabelIds}
            onToggleHidden={onToggleHiddenLabel}
            onHideInherited={onHideInherited}
            inheritedIds={inheritedLabelIds}
          />
        )}
      </section>
      {/* Settings live in a gear popover at the very bottom — out of the
          primary path. Auto-save, all-tools toggle, default-reset all here. */}
      <SettingsMenu
        autosave={autosave}
        onToggleAutosave={onToggleAutosave}
        allTools={allTools}
        onToggleAllTools={onToggleAllTools}
        onResetDefaults={onResetDefaults}
        onResetLabels={onResetLabels}
        sceneTag={sceneTag}
      />
      {/* Legende moved to canvas corner widget; not in the sidebar primary path. */}
    </div>
  );
}

// M5.1 Refine queue: sidebar section listing every label with an issue —
// missing classification, missing value/datum, off-axis wall, non-readable
// status. Click a row to jump to (and select) the label; click "Fix" for
// the one-click autoFix. "Alle aufräumen" runs the M5.2 batch tidy.
function RefineQueue({
  labels, scope, houseKey, sceneLevel, sceneTag, sceneOrientation, onJump, onAutoFix, onTidyAll,
}: {
  labels: Label[];
  scope: LabelScope;
  houseKey: string;
  sceneLevel: SceneLevel | null;
  sceneTag: SceneTag;
  sceneOrientation: SceneOrientation | null;
  onJump: (labelId: string) => void;
  onAutoFix: (issue: RefineIssue) => void;
  onTidyAll: () => void;
}) {
  const issues = useMemo(
    () => collectRefineIssues(labels, { scope, houseKey, sceneLevel, sceneTag, sceneOrientation }),
    [labels, scope, houseKey, sceneLevel, sceneTag, sceneOrientation],
  );
  const [open, setOpen] = useState(issues.length > 0);
  if (issues.length === 0) return null;
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 text-[0.7rem] uppercase tracking-wider text-amber-700 font-semibold mb-1"
      >
        <span className="w-3 text-center">{open ? '▾' : '▸'}</span>
        <span>Auffälligkeiten</span>
        <span className="ml-1 inline-flex items-center justify-center min-w-[1.25rem] h-4 px-1 rounded-full bg-amber-100 text-amber-800 text-[0.65rem] font-mono">
          {issues.length}
        </span>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onTidyAll(); }}
          className="ml-auto text-[0.62rem] px-1.5 py-0.5 rounded bg-zinc-100 hover:bg-zinc-200 text-zinc-700"
          title="Alle near-ortho Linien an die Bau-Achse begradigen (M5.2)"
        >
          Alle aufräumen
        </button>
      </button>
      {open && (
        <ul className="space-y-px ml-3">
          {issues.map((iss, i) => (
            <li
              key={`${iss.labelId}-${iss.kind}-${i}`}
              className="flex items-center gap-1.5 text-[0.7rem] py-0.5"
            >
              <button
                type="button"
                onClick={() => onJump(iss.labelId)}
                className="flex-1 text-left truncate hover:text-accent"
                title="Anspringen + markieren"
              >
                {iss.description}
              </button>
              {iss.autoFix && (
                <>
                  {iss.fixHint && (
                    <span className="text-[0.6rem] font-mono text-zinc-500" title="Was [Fix] anwenden würde">
                      {iss.fixHint}
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => onAutoFix(iss)}
                    className="text-[0.62rem] px-1.5 py-0.5 rounded bg-accent text-white hover:opacity-90"
                    title="Vorschlag anwenden"
                  >
                    Fix
                  </button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// M4.1 House heights panel: surfaces every house-wide known datum (First /
// Traufe / OK FFB / …) so the user can apply it to the current scene with
// one click. Bidirectional: edits in this scene get written back into the
// localStorage cache via rememberHouseHeights() in save(), so the next
// scene of the same house already sees them.
function HouseHeightsPanel({
  scope, houseKey, labels, selectedId, onApply,
}: {
  scope: LabelScope;
  houseKey: string;
  labels: Label[];
  selectedId: string | null;
  onApply: (datum: string, value_mm: number) => void;
}) {
  const [open, setOpen] = useState(true);
  const heights = getHouseHeights(scope, houseKey);
  const entries = Object.entries(heights);
  // Suppress entries that are ALREADY present in the current scene with a
  // value set. No point in offering "Anwenden First" if First is already
  // labeled here.
  const heightsHere = new Set<string>();
  for (const l of labels) {
    if (l.type !== 'height_mark') continue;
    const d = l.attributes.datum;
    if (d && l.attributes.value_mm != null) heightsHere.add(d);
  }
  const DATUM_NAMES: Record<string, string> = {
    first: 'First', traufe: 'Traufe', gelaende: 'Gelände',
    ok_ffb: 'OK FFB', geschoss: 'Geschoss', sockel: 'Sockel',
    kniestock: 'Kniestock',
  };
  const fmt = (mm: number) =>
    mm === 0 ? '±0,00'
    : `${mm > 0 ? '+' : ''}${(mm / 1000).toFixed(2).replace('.', ',')} m`;
  return (
    <section>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1"
      >
        <span className="w-3 text-center">{open ? '▾' : '▸'}</span>
        <span>Haus-Höhen</span>
        <span className="ml-auto text-zinc-400 font-normal">({entries.length})</span>
      </button>
      {open && (entries.length === 0 ? (
        <p className="text-[0.7rem] text-muted italic leading-snug ml-3">
          Noch keine bekannt — sobald eine Höhenkote in einer Szene Wert + Datum hat, erscheint sie hier auf allen Szenen dieses Hauses.
        </p>
      ) : (
        <div className="ml-3 space-y-1">
          {/* N7 — "alle anwenden": batch-drop every still-missing datum
              with its known value (and Y-position when calibration is
              known). One click bootstraps a fresh scene from house facts. */}
          {entries.some(([d]) => !heightsHere.has(d)) && (
            <button
              type="button"
              onClick={() => {
                for (const [datum, mm] of entries) {
                  if (heightsHere.has(datum)) continue;
                  onApply(datum, mm);
                }
              }}
              className="w-full text-[0.65rem] px-1.5 py-1 rounded bg-emerald-50 border border-emerald-300 text-emerald-900 hover:bg-emerald-100 font-medium"
              title="Alle noch fehlenden Datumshöhen in dieser Szene anlegen"
            >
              ↻ Alle bekannten Höhen einsetzen ({entries.filter(([d]) => !heightsHere.has(d)).length})
            </button>
          )}
          <ul className="space-y-px">
          {entries.map(([datum, mm]) => {
            const have = heightsHere.has(datum);
            const sel = selectedId ? labels.find((l) => l.id === selectedId) : null;
            const willTarget = sel?.type === 'height_mark' ? 'die markierte Kote' : 'eine neue Kote';
            return (
              <li key={datum} className="flex items-center gap-1.5 text-[0.72rem] py-0.5">
                <span className={`flex-1 truncate ${have ? 'text-zinc-400' : 'text-zinc-700'}`}>
                  <span className="font-medium">{DATUM_NAMES[datum] ?? datum}</span>
                  <span className="ml-1.5 font-mono text-zinc-500">{fmt(mm)}</span>
                </span>
                {have ? (
                  <span className="text-[0.62rem] text-emerald-600">✓ hier</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => onApply(datum, mm)}
                    className="text-[0.65rem] px-1.5 py-0.5 rounded bg-accent text-white hover:opacity-90"
                    title={`In dieser Szene auf ${willTarget} anwenden`}
                  >
                    Anwenden
                  </button>
                )}
              </li>
            );
          })}
          </ul>
        </div>
      ))}
    </section>
  );
}

// Settings popover anchored at the bottom of the sidebar. Out of the primary
// path so the main column stays focused on Szenen-Tag → Werkzeuge → Labels →
// Checklist. Auto-save, all-tools, reset-defaults all live here.
function SettingsMenu({
  autosave, onToggleAutosave,
  allTools, onToggleAllTools,
  onResetDefaults, onResetLabels, sceneTag,
}: {
  autosave: boolean;
  onToggleAutosave: () => void;
  allTools: boolean;
  onToggleAllTools: () => void;
  onResetDefaults: () => void;
  onResetLabels: () => void;
  sceneTag: SceneTag;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="border-t border-border pt-2 mt-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-1.5 text-[0.65rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-800 font-semibold"
      >
        <span className="w-3 text-center">{open ? '▾' : '▸'}</span>
        <span>Einstellungen</span>
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 pl-3 text-[0.72rem]">
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={autosave} onChange={onToggleAutosave} className="accent-accent" />
            <span>Auto-Save (30 s)</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input type="checkbox" checked={allTools} onChange={onToggleAllTools} className="accent-accent" />
            <span>Alle Werkzeuge zeigen</span>
          </label>
          <button
            type="button"
            onClick={onResetDefaults}
            className="text-zinc-500 hover:text-accent hover:underline text-left"
            title={`Defaults für scope+tag '${sceneTag}' zurücksetzen`}
          >
            Defaults für „{sceneTag}" zurücksetzen
          </button>
          <button
            type="button"
            onClick={onResetLabels}
            className="text-red-700 hover:underline text-left font-medium"
            title="Alle Labels dieser Szene löschen und Anmerkungen von vorn anfangen"
          >
            ⚠ Labels dieser Szene zurücksetzen…
          </button>
        </div>
      )}
    </section>
  );
}

// Inline post-draw classifier chip (M3.2). Floats next to the just-drawn
// label's centroid; one click sets the kind. Auto-dismisses by parent timer.
function PostDrawChip({
  kindFamily,
  left, top,
  onPick,
  onDismiss,
  onHoverEnter,
  onHoverLeave,
}: {
  kindFamily: 'floorplan_opening' | 'view_opening' | 'component_line';
  left: number;
  top: number;
  onPick: (kind: string) => void;
  onDismiss: () => void;
  onHoverEnter: () => void;
  onHoverLeave: () => void;
}) {
  const opts: Array<{ id: string; label: string; key: string }> = (() => {
    if (kindFamily === 'floorplan_opening') return [
      { id: 'window',      label: 'Fenster',   key: 'f' },
      { id: 'door',        label: 'Tür',       key: 't' },
      { id: 'passage',     label: 'Durchgang', key: 'd' },
      { id: 'garage_door', label: 'Tor',       key: 'a' },
      { id: 'other',       label: 'Sonstige',  key: 'z' },
    ];
    if (kindFamily === 'view_opening') return [
      { id: 'window',      label: 'Fenster',     key: 'f' },
      { id: 'door',        label: 'Tür',         key: 't' },
      { id: 'skylight',    label: 'Dachfenster', key: 'd' },
      { id: 'dormer',      label: 'Gaube',       key: 'g' },
      { id: 'garage_door', label: 'Tor',         key: 'a' },
      { id: 'other',       label: 'Sonstige',    key: 'z' },
    ];
    // component_line
    return [
      { id: 'gebaeudekante', label: 'Wand',     key: 'w' },
      { id: 'dachschraege',  label: 'Dach',     key: 'd' },
      { id: 'other',         label: 'Sonstige', key: 'z' },
    ];
  })();
  return (
    <div
      className="absolute z-30 bg-white border border-zinc-300 rounded-md shadow-lg px-1.5 py-1 flex gap-1 text-[0.7rem]"
      // Clamp so the chip never escapes the canvas. Best-effort — the parent
      // is the canvas container, so left/top are already in its coords.
      style={{
        left: Math.max(8, left + 12),
        top: Math.max(8, top - 16),
        pointerEvents: 'auto',
      }}
      onPointerDown={(e) => e.stopPropagation()}
      onPointerEnter={onHoverEnter}
      onPointerLeave={onHoverLeave}
    >
      {opts.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onPick(opt.id)}
          className="px-1.5 py-0.5 rounded hover:bg-accent hover:text-white text-zinc-700 inline-flex items-center gap-1"
          title={`${opt.label} (${opt.key.toUpperCase()})`}
        >
          <span>{opt.label}</span>
          <kbd className="text-[0.58rem] font-mono text-zinc-400">{opt.key.toUpperCase()}</kbd>
        </button>
      ))}
      <button
        type="button"
        onClick={onDismiss}
        className="px-1 text-zinc-400 hover:text-zinc-700"
        title="Schließen"
      >
        ×
      </button>
    </div>
  );
}

// Compact color legend on the canvas (bottom-right corner). Just an "i" pip
// by default; click to expand the full color → kind list. Moved here from
// the sidebar (where it was always-visible noise) to keep the primary
// sidebar path focused on Szenen-Tag → Werkzeuge → Labels → Checklist.
// K7 contextual actions bar. Renders a tiny bottom-center text line with the
// 3-5 most relevant shortcuts for the current state — so the user always
// sees "what can I do right now from the keyboard." Replaces the hidden
// cheatsheet for in-flow guidance; the full cheatsheet still exists via "?".
function ActionsBar({
  tool, selectedIds, selectedLabel, pendingStart, pendingPolyline, adaptiveAxisEnabled,
}: {
  tool: Tool;
  selectedIds: Set<string>;
  selectedLabel: Label | null;
  pendingStart: Point | null;
  pendingPolyline: Point[];
  adaptiveAxisEnabled: boolean;
}) {
  const hint = (k: string, label: string) => (
    <span className="inline-flex items-center gap-1">
      <kbd className="font-mono text-[0.6rem] bg-white/15 px-1 rounded">{k}</kbd>
      <span className="text-white/80">{label}</span>
    </span>
  );
  // Compose the action list per state. Highest-priority context wins.
  let actions: React.ReactNode[] = [];
  // 1) Pending polyline (component_line OR polygon view_opening)
  if (pendingPolyline.length > 0) {
    actions = [
      hint('Klick', 'Punkt setzen'),
      pendingPolyline.length >= 3 ? hint('Klick auf 1.', 'Polygon schließen') : null,
      hint('Enter', 'Beenden'),
      hint('Backspace', 'letzten Punkt zurück'),
      hint('Esc', 'Abbruch'),
      hint('Alt', 'Helfer aus'),
    ].filter(Boolean);
  }
  // 2) Pending 2-click (wall/dim/opening rectangle/circle)
  else if (pendingStart) {
    actions = [
      hint('Klick', tool === 'wall' ? 'Wand-Ende setzen' : tool === 'dimensioned_distance' ? 'Bemaßungs-Ende' : 'zweiter Punkt'),
      hint('Alt', 'Helfer aus'),
      hint('Shift', 'Achsen-Lock'),
      hint('Esc', 'Abbruch'),
    ];
  }
  // 3) A label is selected — show reclassify + status + edit hotkeys for its type
  else if (selectedIds.size === 1 && selectedLabel) {
    const t = selectedLabel.type;
    if (t === 'floorplan_opening') {
      actions = [
        hint('F/T', 'Fenster/Tür'),
        hint('D/A/Z', 'Durchgang/Tor/Sonst.'),
        hint('1-4', 'Status'),
        hint('Del', 'löschen'),
      ];
    } else if (t === 'view_opening') {
      actions = [
        hint('F/T/D/G', 'Fenster/Tür/Dachfst./Gaube'),
        hint('A/Z', 'Tor/Sonst.'),
        hint('1-4', 'Status'),
        hint('Del', 'löschen'),
      ];
    } else if (t === 'component_line') {
      actions = [
        hint('W', 'Wand'),
        hint('D', 'Dach'),
        hint('Z', 'Sonst.'),
        hint('1-4', 'Status'),
        hint('Del', 'löschen'),
      ];
    } else if (t === 'wall') {
      actions = [
        hint('←/→', '±10mm Stärke'),
        hint('Shift+←/→', '±50mm'),
        hint('Doppelklick', 'Wand teilen'),
        hint('1-4', 'Status'),
        hint('Del', 'löschen'),
      ];
    } else if (t === 'height_mark') {
      actions = [
        hint('1-4', 'Status'),
        hint('Del', 'löschen'),
      ];
    } else {
      actions = [hint('1-4', 'Status'), hint('Del', 'löschen')];
    }
  }
  // 4) Multi-selection
  else if (selectedIds.size > 1) {
    actions = [
      <span key="n" className="text-white/70 mr-1">{selectedIds.size} Labels</span>,
      hint('1-4', 'Status'),
      hint('Del', 'löschen'),
      hint('Esc', 'abwählen'),
    ];
  }
  // 5) Drawing tool with nothing pending
  else if (tool !== 'select') {
    actions = [
      hint('Klick', tool === 'height_mark' ? 'Höhenkote setzen' : 'Start'),
      hint('Alt', 'Helfer aus'),
      hint('Shift', 'Achsen-Lock'),
      hint('S', 'Auswahl'),
    ];
  }
  // 6) Idle in select mode, nothing selected
  else {
    actions = [
      hint('W/D/O/L/H', 'Werkzeuge'),
      hint('?', 'Tastenkürzel'),
      hint('Q', adaptiveAxisEnabled ? 'Snap aus' : 'Snap an'),
    ];
  }
  return (
    <div className="absolute bottom-3 left-1/2 -translate-x-1/2 z-10 bg-zinc-900/85 text-white text-[0.7rem] rounded-full px-3 py-1 shadow-md max-w-[90vw] overflow-hidden">
      <div className="flex items-center gap-3 whitespace-nowrap">
        {actions.map((a, i) => (
          <Fragment key={i}>{a}</Fragment>
        ))}
      </div>
    </div>
  );
}

function ColorLegendWidget() {
  const [open, setOpen] = useState(false);
  return (
    <div className="absolute bottom-3 right-3 z-10">
      {open ? (
        <div className="bg-white/95 border border-zinc-200 rounded-md shadow-sm p-2 text-[0.7rem]">
          <div className="flex items-center justify-between mb-1">
            <span className="font-semibold text-zinc-600 uppercase tracking-wider text-[0.62rem]">Legende</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-zinc-400 hover:text-zinc-700 w-4 h-4 inline-flex items-center justify-center"
              title="Schließen"
            >
              ×
            </button>
          </div>
          <ul className="space-y-px">
            {LEGEND.map((e) => (
              <li key={e.kindKey} className="flex items-center gap-1.5 text-zinc-700">
                <span
                  className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                  style={{ backgroundColor: e.swatch }}
                />
                <span>{e.label}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="w-6 h-6 rounded-full bg-white/95 border border-zinc-300 shadow-sm text-[0.7rem] text-zinc-500 hover:text-zinc-800"
          title="Legende anzeigen"
        >
          i
        </button>
      )}
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

// Shape submenu — sits inline under the view_opening tool button when
// active, mirroring the visual treatment of FamilyToolButton's sub-options.
// Pre-draw, the ONLY decision is gesture (rectangle / circle / polygon).
// Kind (Fenster/Tür/Gaube) is set after the fact via the inspector.
function ShapeSubmenu({
  value,
  onChange,
}: {
  value: 'rectangle' | 'circle' | 'polygon';
  onChange: (s: 'rectangle' | 'circle' | 'polygon') => void;
}) {
  const opts: Array<{ id: 'rectangle' | 'circle' | 'polygon'; label: string; hint: string }> = [
    { id: 'rectangle', label: 'Rechteck', hint: 'Zwei Klicks für die Diagonale.' },
    { id: 'circle',    label: 'Kreis',    hint: 'Klick 1 = Mittelpunkt, Klick 2 = Radius.' },
    { id: 'polygon',   label: 'Polygon',  hint: 'Klicke jede Ecke; Enter zum Abschließen.' },
  ];
  const current = opts.find((o) => o.id === value);
  return (
    <div className="ml-5 pl-2 border-l border-zinc-200 space-y-px py-0.5">
      {opts.map((opt) => {
        const isCurrent = opt.id === value;
        return (
          <button
            key={opt.id}
            type="button"
            title={opt.hint}
            onClick={() => onChange(opt.id)}
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
      {current && (
        <p className="text-[0.62rem] text-muted leading-snug px-2 pt-1 pb-0.5 italic">{current.hint}</p>
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

const LABEL_TYPE_LABEL: Record<Label['type'], string> = {
  wall: 'Wände',
  floorplan_opening: 'Öffnungen (GR)',
  view_opening: 'Öffnungen (Ans.)',
  component_line: 'Linien',
  height_mark: 'Höhenkoten',
  dimensioned_distance: 'Bemaßungen',
  dimension_number: 'Maßzahlen',
};

const LABEL_TYPE_ORDER: Label['type'][] = [
  'wall',
  'floorplan_opening',
  'view_opening',
  'component_line',
  'height_mark',
  'dimensioned_distance',
  'dimension_number',
];

// Short identifying line per label — used in the sidebar Labels list so a
// scene with five height_marks doesn't render as five lines of identical
// "▲ height_mark" text. Each type pulls its own most-recognizable
// attribute(s).
function labelSummary(l: Label): string {
  switch (l.type) {
    case 'wall': {
      const t = l.attributes.thickness_mm;
      return t != null ? `Wand · ${t} mm` : 'Wand';
    }
    case 'floorplan_opening': {
      const a = l.attributes;
      const kindLabels: Record<string, string> = {
        window: 'Fenster', door: 'Tür', passage: 'Durchgang',
        garage_door: 'Tor', other: 'Öffnung',
      };
      const k = kindLabels[a.opening_kind ?? 'other'] ?? 'Öffnung';
      const w = a.width_mm != null ? ` · ${a.width_mm} mm` : '';
      const swing = a.opening_kind === 'door' && a.swing_side && a.swing_side !== 'none'
        ? ` (${a.swing_side === 'left' ? 'L' : 'R'})`
        : '';
      return `${k}${w}${swing}`;
    }
    case 'view_opening': {
      const kindLabels: Record<string, string> = {
        window: 'Fenster', door: 'Tür', skylight: 'Dachfenster',
        dormer: 'Gaube', garage_door: 'Tor', other: 'Öffnung',
      };
      return kindLabels[l.attributes.opening_kind ?? 'other'] ?? 'Öffnung';
    }
    case 'component_line': {
      const kindLabels: Record<string, string> = {
        gebaeudekante: 'Wand',
        dachschraege: 'Dach',
        other: 'Sonstige',
        // Legacy values still rendered as-is so old labels stay readable.
        first: 'First (legacy)', traufe: 'Traufe (legacy)',
        gelaende: 'Gelände (legacy)', geschoss: 'Geschoss (legacy)',
        ok_ffb: 'OK FFB (legacy)', sockel: 'Sockel (legacy)',
        kniestock: 'Kniestock (legacy)', firstkante: 'Firstkante (legacy)',
      };
      const k = l.attributes.line_kind ?? 'other';
      const pts = l.geometry.polyline.length;
      return `${kindLabels[k] ?? k} · ${pts} Pkt`;
    }
    case 'height_mark': {
      const datum = l.attributes.datum;
      const v = l.attributes.value_mm;
      const datumLbl = datum && DATUM_LABELS[datum] ? DATUM_LABELS[datum] : '';
      const valueLbl = v == null
        ? '(noch ohne Wert)'
        : v === 0
        ? '±0,00'
        : `${v > 0 ? '+' : ''}${(v / 1000).toFixed(2).replace('.', ',')} m`;
      return datumLbl ? `${datumLbl} · ${valueLbl}` : valueLbl;
    }
    case 'dimensioned_distance': {
      const a = l.attributes;
      const dx = l.geometry.end[0] - l.geometry.start[0];
      const dy = l.geometry.end[1] - l.geometry.start[1];
      const ang = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
      const orient = ang < 15 || ang > 165 ? '↔' : ang > 75 && ang < 105 ? '↕' : '↗';
      const val = a.value_mm != null
        ? (a.value_mm >= 1000
            ? `${(a.value_mm / 1000).toFixed(2).replace('.', ',')} m`
            : `${a.value_mm} mm`)
        : '(noch ohne Wert)';
      const ref = a.is_reference ? ' M1' : '';
      return `${orient} ${val}${ref}`;
    }
    case 'dimension_number':
      return l.attributes.text || '(noch ohne Text)';
  }
}

// Sidebar Labels list — grouped by type, each section collapsible, with
// per-label summary text instead of just "type". Replaces a flat list
// that was useless when several labels of the same type existed.
function LabelsByType({
  labels, selectedId, onSelect, hiddenIds, onToggleHidden, onHideInherited, inheritedIds,
}: {
  labels: Label[];
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  hiddenIds: Set<string>;
  onToggleHidden: (id: string) => void;
  onHideInherited: () => void;
  inheritedIds: Set<string>;
}) {
  const [collapsed, setCollapsed] = useState<Set<Label['type']>>(() => new Set());
  const groups = useMemo(() => {
    const byType = new Map<Label['type'], Label[]>();
    for (const l of labels) {
      const list = byType.get(l.type) ?? [];
      list.push(l);
      byType.set(l.type, list);
    }
    return LABEL_TYPE_ORDER
      .map((t) => [t, byType.get(t) ?? []] as const)
      .filter(([, g]) => g.length > 0);
  }, [labels]);

  const toggle = (t: Label['type']) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(t)) next.delete(t);
      else next.add(t);
      return next;
    });
  };

  // W7 — when this scene has any inherited labels not yet hidden, show
  // the batch "ausblenden" button.
  const inheritedVisible = labels.some((l) => inheritedIds.has(l.id) && !hiddenIds.has(l.id));
  return (
    <div className="space-y-2">
      {inheritedVisible && (
        <button
          type="button"
          onClick={onHideInherited}
          className="w-full text-[0.66rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-900 py-0.5"
        >
          ↻ Geerbte ausblenden
        </button>
      )}
      {groups.map(([type, group]) => {
        const isCollapsed = collapsed.has(type);
        return (
          <div key={type}>
            <button
              type="button"
              onClick={() => toggle(type)}
              className="w-full flex items-center gap-1.5 text-[0.65rem] uppercase tracking-wider text-zinc-500 hover:text-zinc-800 font-semibold"
            >
              <span className="w-3 text-center">{isCollapsed ? '▸' : '▾'}</span>
              <span>{LABEL_TYPE_LABEL[type]}</span>
              <span className="text-zinc-400 font-normal">({group.length})</span>
            </button>
            {!isCollapsed && (
              <ul className="space-y-px mt-1 ml-3">
                {group.map((l) => {
                  const isHidden = hiddenIds.has(l.id);
                  return (
                  <li key={l.id} className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => onSelect(l.id)}
                      className={`flex-1 text-left px-2 py-1 rounded text-[0.72rem] truncate flex items-center gap-1.5 ${
                        selectedId === l.id
                          ? 'bg-accent/10 text-accent font-semibold'
                          : 'hover:bg-zinc-100 text-zinc-800'
                      } ${isHidden ? 'opacity-50 italic' : ''}`}
                      title={l.id}
                    >
                      <span
                        className="inline-block w-2 h-2 rounded-sm shrink-0"
                        style={{ backgroundColor: labelColor(l) }}
                      />
                      <span className="font-mono">{labelGlyph(l)}</span>{' '}
                      <span className="truncate">{labelSummary(l)}</span>
                    </button>
                    {/* W7 — eye-icon toggle. Hidden = label exists in JSON
                        but doesn't render on canvas. */}
                    <button
                      type="button"
                      onClick={(e) => { e.stopPropagation(); onToggleHidden(l.id); }}
                      title={isHidden ? 'Auf Canvas wieder einblenden' : 'Auf Canvas ausblenden (bleibt gespeichert)'}
                      className="px-1 py-0.5 text-zinc-400 hover:text-zinc-900"
                    >
                      {isHidden ? '◌' : '●'}
                    </button>
                  </li>
                  );
                })}
              </ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

// "Was muss ich labeln?" — per scene-tag checklist with live ✓/○ status.
// Tells the user, at a glance, what each scene actually NEEDS for the
// downstream ML pipeline to work. Empty checks are not failures — they're
// reminders. Two genuine required items per scene-tag (driven by the
// homography needing 1H + 1V Bezug), the rest are recommended.
function SceneChecklist({
  sceneTag, labels,
}: { sceneTag: SceneTag; labels: Label[] }) {
  if (sceneTag === 'nicht_klassifiziert') return null;

  // Count what's there.
  let walls = 0;
  let fpOpenings = 0;
  let viewOpenings = 0;
  let lineForm = 0;             // gebaeudekante | dachschraege (NEW form-only)
  let heights = 0;
  let bezugshoehe = false;
  let refH = false;
  let refV = false;
  for (const l of labels) {
    if (l.type === 'wall') walls++;
    else if (l.type === 'floorplan_opening') fpOpenings++;
    else if (l.type === 'view_opening') viewOpenings++;
    else if (l.type === 'component_line') {
      const k = l.attributes.line_kind;
      if (k === 'gebaeudekante' || k === 'dachschraege') lineForm++;
    }
    else if (l.type === 'height_mark') {
      heights++;
      if (l.attributes.value_mm === 0) bezugshoehe = true;
    }
    else if (l.type === 'dimensioned_distance' && l.attributes.is_reference) {
      const dx = l.geometry.end[0] - l.geometry.start[0];
      const dy = l.geometry.end[1] - l.geometry.start[1];
      const a = Math.abs((Math.atan2(dy, dx) * 180) / Math.PI);
      if (a < 15 || a > 165) refH = true;
      else if (a > 75 && a < 105) refV = true;
    }
  }

  type Item = { ok: boolean; label: string; hint: string; required?: boolean };
  const items: Item[] = [];

  if (sceneTag === 'grundriss' || sceneTag === 'sonstiges') {
    items.push(
      { ok: walls >= 3, label: `Wände (${walls})`, hint: 'Außenwand-Polygon mit Stärke. Mindestens 3 Wände erwartet.', required: true },
      { ok: fpOpenings >= 1, label: `Öffnungen (${fpOpenings})`, hint: 'Fenster + Türen. Schnappt an Wandachsen, Drehung folgt automatisch.' },
    );
  }
  if (sceneTag === 'ansicht' || sceneTag === 'schnitt' || sceneTag === 'sonstiges') {
    items.push(
      { ok: lineForm >= 3, label: `Gebäudeform (${lineForm} Linien)`, hint: 'Wand + Dach: vertikale Außenkanten und Dachschrägen. Mindestens 3 Linien beschreiben eine Silhouette.', required: true },
      { ok: viewOpenings >= 1, label: `Öffnungen (${viewOpenings})`, hint: 'Fenster, Türen, Dachfenster, Gauben.' },
      { ok: bezugshoehe, label: 'Bezugshöhe ±0,00', hint: 'Eine Höhenkote mit Wert = 0 setzt den Nullpunkt. Alle anderen Höhen werden relativ dazu gelesen.', required: true },
      { ok: heights >= 2, label: `Höhenkoten (${heights})`, hint: 'First, Traufe, Gelände — mit Datum gesetzt. Aus den Y-Positionen folgen alle Höhenbezugslinien automatisch (rosa gestrichelt).' },
    );
  }
  // Homography references — only useful if some dim exists at all.
  const totalDims = labels.filter((l) => l.type === 'dimensioned_distance').length;
  items.push(
    { ok: refH, label: 'Bezug horizontal (M1)', hint: 'Längste horizontale Bemaßung wird automatisch markiert. Ohne H-Bezug keine X-Entzerrung.', required: true },
    { ok: refV, label: 'Bezug vertikal (M1)',   hint: 'Längste vertikale Bemaßung wird automatisch markiert. Ohne V-Bezug keine Y-Entzerrung.', required: true },
  );
  if (totalDims === 0) {
    // Demote refs to recommended when there's nothing to base them on yet.
    items[items.length - 2].required = false;
    items[items.length - 1].required = false;
  }

  const requiredOk = items.filter((i) => i.required && i.ok).length;
  const requiredTotal = items.filter((i) => i.required).length;

  return (
    <section>
      <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        Was muss ich labeln?{' '}
        <span className={`font-normal ${
          requiredOk === requiredTotal ? 'text-emerald-600' : 'text-amber-600'
        }`}>
          ({requiredOk}/{requiredTotal})
        </span>
      </h3>
      <ul className="space-y-px">
        {items.map((i, idx) => (
          <li key={idx} title={i.hint}
              className={`flex items-baseline gap-2 px-2 py-1 rounded text-[0.72rem] ${
                i.ok ? 'text-emerald-700' : i.required ? 'text-amber-700' : 'text-zinc-500'
              }`}>
            <span className="w-3 text-center font-semibold">{i.ok ? '✓' : '○'}</span>
            <span className="flex-1">{i.label}</span>
            {!i.required && (
              <span className="text-[0.6rem] text-zinc-400 shrink-0">opt.</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

// ── canvas glyph rendering ─────────────────────────────────────────────────

// V1 — map each label subtype to a hatch pattern id (rendered as a
// pattern overlay on top of the solid fill) and a centroid glyph. Each
// helper returns null when no decoration applies.

function floorplanOpeningHatch(kind: string | undefined | null): string | null {
  switch (kind) {
    case 'window':       return 'bim-mullion-h';
    case 'garage_door':  return 'bim-mullion-v';
    case 'other':        return 'bim-stripe-30';
    case 'door':         return null;
    case 'passage':      return null;
    default:             return null;
  }
}

function viewOpeningHatch(kind: string | undefined | null): string | null {
  switch (kind) {
    case 'window':       return 'bim-mullion-h';
    case 'skylight':     return 'bim-skylight-tilt';
    case 'garage_door':  return 'bim-mullion-v';
    case 'dormer':       return 'bim-gable-sparse';
    case 'other':        return 'bim-stripe-30';
    case 'door':         return null;
    default:             return null;
  }
}

type GlyphComponent = (props: { cx: number; cy: number; size?: number; strokeWidth?: number }) => React.ReactElement;

function openingGlyphFor(kind: string | undefined | null): GlyphComponent | null {
  switch (kind) {
    case 'door':         return DoorSwingGlyph;
    case 'garage_door':  return GarageDoorGlyph;
    case 'passage':      return PassageGlyph;
    case 'other':        return QuestionGlyph;
    case 'window':       return null;
    default:             return null;
  }
}

function viewOpeningGlyphFor(kind: string | undefined | null, shape: string | undefined): GlyphComponent | null {
  if (kind === 'window') {
    if (shape === 'circle')  return WindowCircleGlyph;
    if (shape === 'polygon') return WindowArchedGlyph;
    return WindowPaneGlyph;
  }
  switch (kind) {
    case 'door':         return DoorHandleGlyph;
    case 'skylight':     return SkylightGlyph;
    case 'dormer':       return DormerGlyph;
    case 'garage_door':  return GarageDoorGlyph;
    case 'other':        return QuestionGlyph;
    default:             return null;
  }
}

function openLineGlyphFor(kind: string | undefined | null): GlyphComponent | null {
  switch (kind) {
    case 'first':         return RidgeGlyph;
    case 'traufe':        return EaveGlyph;
    case 'gelaende':      return GroundGlyph;
    case 'dachschraege':  return RoofSlopeGlyph;
    case 'geschoss':
    case 'ok_ffb':
    case 'sockel':
    case 'kniestock':     return LevelTickGlyph;
    case 'firstkante':    return RidgeGlyph;
    case 'gebaeudekante': return null;
    case 'other':         return QuestionGlyph;
    default:              return null;
  }
}

function regionHatchFor(kind: RegionKind): string | null {
  switch (kind) {
    case 'roof':       return 'bim-roof-hatch';
    case 'gable':      return 'bim-gable-sparse';
    case 'wall_body':  return 'bim-wall-region';
    case 'ground':     return 'bim-ground-hatch';
    case 'unknown':    return 'bim-area-hatch';
    default:           return 'bim-area-hatch';
  }
}

function regionGlyphFor(kind: RegionKind): GlyphComponent | null {
  switch (kind) {
    case 'roof':       return RoofSlopeGlyph;
    case 'gable':      return GableGlyph;
    case 'wall_body':  return WallBodyGlyph;
    case 'ground':     return GroundGlyph;
    case 'unknown':    return QuestionGlyph;
    default:           return null;
  }
}

// Axis-aligned bounding box for any label. Returns [minX, minY, maxX, maxY]
// in image-pixel coords. Used to anchor V3 cross-cutting decorations
// (corner badges, issue rings) without each branch reimplementing.
function labelBBox(label: Label): [number, number, number, number] | null {
  const acc: Point[] = [];
  const g = label.geometry as Record<string, unknown>;
  if (label.type === 'wall' || label.type === 'dimensioned_distance') {
    acc.push((g.start as Point), (g.end as Point));
  } else if (label.type === 'floorplan_opening') {
    acc.push(...(g.quad as Point[]));
  } else if (label.type === 'view_opening') {
    if (g.shape === 'circle') {
      const c = g.center as Point;
      const r = g.radius_px as number;
      acc.push([c[0] - r, c[1] - r], [c[0] + r, c[1] + r]);
    } else if (g.shape === 'polygon') {
      acc.push(...(g.polygon as Point[]));
    } else {
      acc.push(...(g.top_edge as Point[]), ...(g.bottom_edge as Point[]));
    }
  } else if (label.type === 'component_line') {
    acc.push(...((g.polyline as Point[])));
  } else if (label.type === 'height_mark') {
    const a = g.anchor as Point;
    acc.push([a[0] - 14, a[1] - 19], [a[0] + 14, a[1] + 4]);
  } else if (label.type === 'dimension_number') {
    const a = g.anchor as Point | undefined;
    if (a) acc.push(a);
  }
  if (acc.length === 0) return null;
  const xs = acc.map((p) => p[0]);
  const ys = acc.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

// German labels per opening_kind (for the readable pill).
function openingKindLabel(kind: string | undefined | null): string {
  switch (kind) {
    case 'window':       return 'Fenster';
    case 'door':         return 'Tür';
    case 'garage_door':  return 'Tor';
    case 'passage':      return 'Durchgang';
    case 'skylight':     return 'Dachfenster';
    case 'dormer':       return 'Gaube';
    case 'other':        return '?';
    default:             return 'Öffnung';
  }
}
function lineKindLabel(kind: string | undefined | null): string {
  switch (kind) {
    case 'first':         return 'First';
    case 'traufe':        return 'Traufe';
    case 'gelaende':      return 'Gelände';
    case 'dachschraege':  return 'Dachkante';
    case 'gebaeudekante': return 'Gebäudekante';
    case 'firstkante':    return 'Firstkante';
    case 'geschoss':      return 'Geschoss';
    case 'ok_ffb':        return 'OK FFB';
    case 'sockel':        return 'Sockel';
    case 'kniestock':     return 'Kniestock';
    case 'other':         return '?';
    default:              return 'Linie';
  }
}

// Floating label pill — glyph + German text — used at the centroid of any
// region/opening/line so its semantic kind reads at a glance. Sized in
// screen-pinned units via the caller's `screenScale`.
function LabelKindPill({
  cx, cy, color, Glyph, label, screenScale,
}: {
  cx: number;
  cy: number;
  color: string;
  Glyph: GlyphComponent | null;
  label: string;
  screenScale: number;
}) {
  const fontPx = 11 * screenScale;
  const padPx = 6 * screenScale;
  const glyphSize = 14 * screenScale;
  const hasGlyph = Glyph != null;
  const textW = label.length * fontPx * 0.6;
  const pillW = (hasGlyph ? glyphSize + padPx : 0) + textW + padPx * 1.6;
  const pillH = Math.max(glyphSize, fontPx) + padPx;
  const px = cx - pillW / 2;
  const py = cy - pillH / 2;
  return (
    <g pointerEvents="none" style={{ color }}>
      <rect
        x={px} y={py}
        width={pillW} height={pillH}
        rx={pillH / 2}
        fill="white" stroke={color} strokeWidth={1} opacity={0.92}
      />
      {hasGlyph && Glyph && (
        <Glyph
          cx={px + padPx * 0.8 + glyphSize / 2}
          cy={py + pillH / 2}
          size={glyphSize}
        />
      )}
      <text
        x={px + (hasGlyph ? padPx * 0.8 + glyphSize + padPx : padPx * 0.8)}
        y={py + pillH / 2 + fontPx * 0.32}
        fill={color}
        fontFamily="ui-sans-serif, system-ui"
        fontSize={fontPx}
        fontWeight={700}
      >
        {label}
      </text>
    </g>
  );
}

function heightMarkGlyphFor(datum: string | null | undefined, isBezug: boolean): GlyphComponent | null {
  if (isBezug) return BezugGlyph;
  switch (datum) {
    case 'first':       return RidgeGlyph;
    case 'traufe':      return EaveGlyph;
    case 'gelaende':    return GroundGlyph;
    case 'geschoss':
    case 'ok_ffb':
    case 'sockel':
    case 'kniestock':   return LevelTickGlyph;
    case 'other':       return QuestionGlyph;
    default:            return null;
  }
}

function LabelGlyph({
  label,
  selected,
  tool,
  allLabels,
  inheritedFromOtherScene,
  refineKind,
  imageHeight,
  screenScale,
  imageSnapRadius,
  eventToSvgPoint,
  onSelect,
  onMutateGeometry,
  onMutateAttributes,
  onJointMove,
  jointSize,
  onSplit,
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
  /** V3 — when true, label was promoted from another scene's facts. */
  inheritedFromOtherScene: boolean;
  /** V3 — most critical RefineIssue kind for this label, or null. */
  refineKind: RefineIssue['kind'] | null;
  /** Image height in px — used by region-kind inference (V2). */
  imageHeight: number;
  /** Image-pixels per screen-pixel — used to size on-canvas glyphs in
   *  screen-pinned units (multiply a desired screen px by this factor). */
  screenScale: number;
  /** Per-image-pixel snap radius. Caller computes it from the screen radius
   *  divided by the current view-to-screen scale. */
  imageSnapRadius: number;
  eventToSvgPoint: (e: ReactPointerEvent<SVGSVGElement> | PointerEvent) => Point | null;
  onSelect: (modifiers?: { shift?: boolean; meta?: boolean }) => void;
  onMutateGeometry: (newGeom: Label['geometry']) => void;
  onMutateAttributes: (newAttrs: Record<string, unknown>) => void;
  /** M1.2 joint-aware drag: parent applies the move to every label sharing
   *  the joint that (label.id, handleId) lives in. If the joint is unique
   *  (1 member) this collapses to a single-label moveHandle. Alt key on the
   *  pointer event opts out of joint behavior (caller passes altKey state). */
  onJointMove: (labelId: string, handleId: string, newPt: Point, altKey: boolean) => void;
  /** How many labels participate in the joint at handle `handleId`. Used to
   *  render the multi-joint ring + count chip. Map<handleId, memberCount>. */
  jointSize: (handleId: string) => number;
  /** M1.3 wall split: double-click on a wall/dim/line body inserts a vertex
   *  at the click point. Parent splits walls + dims into two labels sharing
   *  the new endpoint; for component_line inserts a polyline vertex. */
  onSplit: (labelId: string, pt: Point) => void;
  onStartDrag: () => void;
  onDragStateChange: (dragging: boolean) => void;
  onSnapChange: (s: SnapTarget | null) => void;
}) {
  // Color per (type, subtype) — see lib/colors.ts. Selected always takes
  // precedence (bright red) so the active label is unambiguous.
  const baseColor = labelColor(label);
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
      if (!dragged) onSelect({ shift: mv.shiftKey, meta: mv.metaKey || mv.ctrlKey });
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
      const finalPt: Point = snapped?.pt ?? raw;
      // Joint-aware drag — parent applies the move to every label sharing
      // the joint. Alt held → only this endpoint moves (escape hatch).
      onJointMove(label.id, handleId, finalPt, mv.altKey);
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
    onSelect({ shift: e.shiftKey, meta: e.metaKey || e.ctrlKey });
  };

  // Drawing tools (everything except select) must let clicks pass
  // straight through to the canvas — otherwise clicking ON an existing
  // wall while drawing a new wall would be intercepted by the wall's own
  // pointer handlers and the new wall never gets committed. The snap
  // engine still picks up the wall's endpoints because snap math runs in
  // the canvas-level pointermove handler, independent of pointer events.
  const isDrawingTool = tool !== 'select';
  // M1.3 wall split: double-clicking a wall/dim/line body inserts a vertex
  // at the click point. Walls and dim_distances get split into two new
  // labels sharing the new endpoint; component_lines get a new polyline
  // vertex inserted mid-segment. Only relevant in select mode.
  const onBodyDoubleClick = (e: React.MouseEvent) => {
    if (tool !== 'select') return;
    if (
      label.type !== 'wall' &&
      label.type !== 'dimensioned_distance' &&
      label.type !== 'component_line'
    ) return;
    e.stopPropagation();
    const pt = eventToSvgPoint(e as unknown as ReactPointerEvent<SVGSVGElement>);
    if (!pt) return;
    onSplit(label.id, pt);
  };
  const bodyProps = {
    onClick,
    onDoubleClick: onBodyDoubleClick,
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
              {/* V1 — gold star above the chip: this dim drives the homography. */}
              <g style={{ color: '#f59e0b' }}>
                <DimRefStarGlyph cx={0} cy={-18} size={12 * screenScale} />
              </g>
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
      const kind = label.attributes.opening_kind ?? 'window';
      const hatchId = floorplanOpeningHatch(kind);
      const cx = (a[0] + b[0] + c[0] + d[0]) / 4;
      const cy = (a[1] + b[1] + c[1] + d[1]) / 4;
      const quadW = Math.hypot(b[0] - a[0], b[1] - a[1]);
      const quadH = Math.hypot(d[0] - a[0], d[1] - a[1]);
      const minDim = Math.min(quadW, quadH);
      const glyphPx = 16 * screenScale;
      // Don't paint a glyph when it would dwarf the opening itself.
      const showGlyph = kind !== 'window' && minDim > glyphPx * 1.8;
      const Glyph = openingGlyphFor(kind);
      const pts = `${a[0]},${a[1]} ${b[0]},${b[1]} ${c[0]},${c[1]} ${d[0]},${d[1]}`;
      body = (
        <g {...bodyProps}>
          {attached && (
            <polygon
              points={pts}
              fill="white" stroke="#a21caf" strokeWidth={sw + 1} strokeDasharray="4,3"
            />
          )}
          <polygon points={pts} fill={fill} stroke={stroke} strokeWidth={sw} />
          {hatchId && (
            <polygon points={pts} fill={`url(#${hatchId})`} stroke="none" opacity={0.7} />
          )}
          {inner}
          {showGlyph && (
            <LabelKindPill
              cx={cx}
              cy={cy}
              color={stroke}
              Glyph={Glyph}
              label={openingKindLabel(kind)}
              screenScale={screenScale}
            />
          )}
        </g>
      );
      break;
    }
    case 'view_opening': {
      const g = label.geometry as Record<string, unknown>;
      const kind = label.attributes.opening_kind ?? 'window';
      const hatchId = viewOpeningHatch(kind);
      const glyphPx = 16 * screenScale;
      const ViewGlyph = viewOpeningGlyphFor(kind, g.shape as string | undefined);
      if (g.shape === 'circle') {
        const center = g.center as Point;
        const radius_px = g.radius_px as number;
        body = (
          <g {...bodyProps}>
            <circle
              cx={center[0]} cy={center[1]} r={radius_px}
              fill={fill} stroke={stroke} strokeWidth={sw}
            />
            {hatchId && (
              <circle
                cx={center[0]} cy={center[1]} r={radius_px}
                fill={`url(#${hatchId})`} stroke="none" opacity={0.65}
              />
            )}
            {radius_px > glyphPx * 1.6 && (
              <LabelKindPill
                cx={center[0]}
                cy={center[1]}
                color={stroke}
                Glyph={ViewGlyph}
                label={openingKindLabel(kind)}
                screenScale={screenScale}
              />
            )}
          </g>
        );
      } else if (g.shape === 'polygon') {
        const polygon = g.polygon as Point[];
        const path = `M ${polygon.map(p => p.join(',')).join(' L ')} Z`;
        const cx = polygon.reduce((s, p) => s + p[0], 0) / polygon.length;
        const cy = polygon.reduce((s, p) => s + p[1], 0) / polygon.length;
        const bx = Math.max(...polygon.map(p => p[0])) - Math.min(...polygon.map(p => p[0]));
        const by = Math.max(...polygon.map(p => p[1])) - Math.min(...polygon.map(p => p[1]));
        const small = Math.min(bx, by);
        body = (
          <g {...bodyProps}>
            <path d={path} fill={fill} stroke={stroke} strokeWidth={sw} />
            {hatchId && (
              <path d={path} fill={`url(#${hatchId})`} stroke="none" opacity={0.65} />
            )}
            {small > glyphPx * 1.6 && (
              <LabelKindPill
                cx={cx}
                cy={cy}
                color={stroke}
                Glyph={ViewGlyph}
                label={openingKindLabel(kind)}
                screenScale={screenScale}
              />
            )}
          </g>
        );
      } else {
        const { top_edge, bottom_edge } = label.geometry as { top_edge: Point[]; bottom_edge: Point[] };
        const path = `M ${top_edge.map(p => p.join(',')).join(' L ')}` +
                     ` L ${[...bottom_edge].reverse().map(p => p.join(',')).join(' L ')} Z`;
        const all = [...top_edge, ...bottom_edge];
        const cx = all.reduce((s, p) => s + p[0], 0) / all.length;
        const cy = all.reduce((s, p) => s + p[1], 0) / all.length;
        const bx = Math.max(...all.map(p => p[0])) - Math.min(...all.map(p => p[0]));
        const by = Math.max(...all.map(p => p[1])) - Math.min(...all.map(p => p[1]));
        const small = Math.min(bx, by);
        body = (
          <g {...bodyProps}>
            <path d={path} fill={fill} stroke={stroke} strokeWidth={sw} />
            {hatchId && (
              <path d={path} fill={`url(#${hatchId})`} stroke="none" opacity={0.65} />
            )}
            {small > glyphPx * 1.6 && (
              <LabelKindPill
                cx={cx}
                cy={cy}
                color={stroke}
                Glyph={ViewGlyph}
                label={openingKindLabel(kind)}
                screenScale={screenScale}
              />
            )}
          </g>
        );
      }
      break;
    }
    case 'component_line': {
      const pts = label.geometry.polyline;
      // V0.2: a component_line with ≥3 vertices is conceptually an AREA
      // (the polygon closed via the implicit first→last edge), even when
      // the user didn't loop back to the start — anchoring both ends to
      // existing walls (N2 auto-commit) is a closed region too. Two-vertex
      // polylines stay as lines. Render with a hatched fill so the user
      // sees the enclosed region as a thing, not just an outline.
      const isArea = pts.length >= 3;
      const closedPath = isArea
        ? `M ${pts.map((p) => p.join(',')).join(' L ')} Z`
        : null;
      // Pump fill alpha up so closed regions are actually visible (was
      // 0x1a → effectively invisible on pale-paper scans). 0x33 ≈ 20%.
      const areaFill = selected ? '#dc262655' : `${baseColor}33`;
      const lk = label.attributes.line_kind ?? 'other';
      const LineGlyph = openLineGlyphFor(lk);
      const glyphPx = 16 * screenScale;
      // V2 — when this polyline is a closed area, run region-kind inference
      // and pick the matching hatch + centroid glyph. Falls back to the
      // generic bim-area-hatch when inference returns 'unknown'.
      const regionKind: RegionKind | null = isArea
        ? inferRegionKind(label, { imageHeight })
        : null;
      const regionHatchId = regionKind ? regionHatchFor(regionKind) : null;
      const RegionGlyph = regionKind ? regionGlyphFor(regionKind) : null;
      const regionCentroid = isArea ? polygonCentroid(pts) : null;
      // Place glyph at midpoint of the longest segment so it doesn't crowd
      // joints. Skip for closed areas — they get a centroid glyph below.
      let lineGlyphAt: Point | null = null;
      if (!isArea && pts.length >= 2 && LineGlyph) {
        let bestLen = 0;
        let bestMid: Point = pts[0];
        for (let i = 0; i < pts.length - 1; i++) {
          const dx = pts[i + 1][0] - pts[i][0];
          const dy = pts[i + 1][1] - pts[i][1];
          const len = Math.hypot(dx, dy);
          if (len > bestLen && len > glyphPx * 2) {
            bestLen = len;
            bestMid = [(pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2];
          }
        }
        if (bestLen > 0) lineGlyphAt = bestMid;
      }
      body = (
        <g {...bodyProps}>
          {closedPath && (
            <>
              <path d={closedPath} fill={areaFill} stroke="none" />
              <path
                d={closedPath}
                fill={`url(#${regionHatchId ?? 'bim-area-hatch'})`}
                stroke="none"
                opacity={0.6}
              />
            </>
          )}
          <polyline points={pts.map(p => p.join(',')).join(' ')}
                    fill="none" stroke={stroke} strokeWidth={sw + 1} />
          {pts.map((p, i) => <circle key={i} cx={p[0]} cy={p[1]} r={3} fill={stroke} />)}
          {/* Per-kind line pill at midpoint of OPEN polylines. */}
          {lineGlyphAt && (
            <LabelKindPill
              cx={lineGlyphAt[0]}
              cy={lineGlyphAt[1]}
              color={stroke}
              Glyph={LineGlyph}
              label={lineKindLabel(lk)}
              screenScale={screenScale}
            />
          )}
          {/* V2 — region centroid pill: text-first ("Dachfläche") so two
              regions of different kinds are unmistakable, with the
              pictogram as a glyph next to it. */}
          {regionCentroid && regionKind && (
            <LabelKindPill
              cx={regionCentroid[0]}
              cy={regionCentroid[1]}
              color={stroke}
              Glyph={RegionGlyph}
              label={regionKindLabel(regionKind)}
              screenScale={screenScale}
            />
          )}
        </g>
      );
      break;
    }
    case 'height_mark': {
      const [x, y] = label.geometry.anchor;
      const datum = label.attributes.datum ?? null;
      const value = label.attributes.value_mm;
      // A Höhenkote with value=0 is the Bezugshöhe (±0,00) — the anchor
      // that every other height is read relative to. Render it with a
      // prominent visual: filled amber triangle + "±0,00" label.
      const isBezug = value === 0;
      const triFill = isBezug ? '#f59e0b' : fill;
      const triStroke = isBezug ? '#b45309' : stroke;
      const triSw = isBezug ? sw + 1 : sw;
      const valueText = value != null
        ? (value === 0
            ? '±0,00'
            : `${value > 0 ? '+' : ''}${(value / 1000).toFixed(2).replace('.', ',')} m`)
        : null;
      const fullLabel = [datum ? DATUM_LABELS[datum] ?? datum : '', valueText]
        .filter(Boolean).join(' ');
      const DatumGlyph = heightMarkGlyphFor(datum, isBezug);
      const glyphPx = 14 * screenScale;
      // Glyph sits to the right of the text. Approximate text width via
      // character count × monospace cell (~7px × font size / 13).
      const textPx = (fullLabel?.length ?? 0) * 7.5 * (isBezug ? 15 : 13) / 13;
      const glyphX = x + 14 + textPx + glyphPx * 0.7;
      const glyphY = y - 12;
      body = (
        <g {...bodyProps}>
          {isBezug && (
            // Outer ring for the Bezugshöhe — reads as "this one is special".
            <polygon
              points={`${x},${y + 3} ${x - 14},${y - 19} ${x + 14},${y - 19}`}
              fill="none" stroke="#f59e0b" strokeWidth={triSw + 1}
              strokeDasharray="3,2" opacity={0.7}
            />
          )}
          <polygon points={`${x},${y} ${x - 10},${y - 16} ${x + 10},${y - 16}`}
                   fill={triFill} stroke={triStroke} strokeWidth={triSw} />
          {fullLabel && (
            <text x={x + 14} y={y - 6} fill={isBezug ? '#b45309' : stroke}
                  fontFamily="ui-monospace, monospace"
                  fontSize={isBezug ? 15 : 13}
                  fontWeight={isBezug ? 800 : 600}
                  style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}>
              {fullLabel}
            </text>
          )}
          {DatumGlyph && fullLabel && (
            <g pointerEvents="none" style={{ color: isBezug ? '#b45309' : stroke }} opacity={0.85}>
              <DatumGlyph cx={glyphX} cy={glyphY} size={glyphPx} />
            </g>
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

  // V3 — cross-cutting decorations (provenance + refine).
  const v3Bbox = (inheritedFromOtherScene || refineKind) ? labelBBox(label) : null;
  const v3GlyphPx = 14 * screenScale;
  const v3Pad = 4 * screenScale;
  // refineKind → display config.
  // height_conflict is the loudest: red ring around the bbox + corner badge.
  // off_axis / classify / no_value / no_datum / not_readable share an amber
  // ? corner-badge (less alarming).
  const isLoudIssue = refineKind === 'height_conflict';
  const showQBadge = refineKind != null && refineKind !== 'height_conflict';

  return (
    <g>
      {body}
      {/* V3 — provenance + refine overlay. Drawn over the body but under
          selection handles so issue rings sit on top of the geometry. */}
      {v3Bbox && (
        <g pointerEvents="none">
          {/* Inherited-from-other-scene dashed outline. */}
          {inheritedFromOtherScene && (
            <rect
              x={v3Bbox[0] - v3Pad}
              y={v3Bbox[1] - v3Pad}
              width={v3Bbox[2] - v3Bbox[0] + v3Pad * 2}
              height={v3Bbox[3] - v3Bbox[1] + v3Pad * 2}
              fill="none"
              stroke="#7c3aed"
              strokeWidth={1.4}
              strokeDasharray="4,3"
              opacity={0.6}
              rx={3}
            />
          )}
          {/* Loud issue ring (height_conflict). */}
          {isLoudIssue && (
            <rect
              x={v3Bbox[0] - v3Pad * 1.5}
              y={v3Bbox[1] - v3Pad * 1.5}
              width={v3Bbox[2] - v3Bbox[0] + v3Pad * 3}
              height={v3Bbox[3] - v3Bbox[1] + v3Pad * 3}
              fill="none"
              stroke="#dc2626"
              strokeWidth={2}
              strokeDasharray="3,2"
              opacity={0.75}
              rx={3}
            />
          )}
          {/* Inherited corner badge — ↻ glyph at upper-right. */}
          {inheritedFromOtherScene && (
            <g style={{ color: '#7c3aed' }}>
              <circle
                cx={v3Bbox[2] + v3Pad}
                cy={v3Bbox[1] - v3Pad}
                r={v3GlyphPx * 0.55}
                fill="white"
                stroke="#7c3aed"
                strokeWidth={1}
              />
              <text
                x={v3Bbox[2] + v3Pad}
                y={v3Bbox[1] - v3Pad + v3GlyphPx * 0.22}
                textAnchor="middle"
                fontSize={v3GlyphPx * 0.7}
                fontFamily="ui-monospace, monospace"
                fill="#7c3aed"
                fontWeight={700}
              >
                ↻
              </text>
            </g>
          )}
          {/* "?" / "!" corner badge for refine issues. */}
          {(isLoudIssue || showQBadge) && (
            <g style={{ color: isLoudIssue ? '#dc2626' : '#d97706' }}>
              <circle
                cx={v3Bbox[0] - v3Pad}
                cy={v3Bbox[1] - v3Pad}
                r={v3GlyphPx * 0.55}
                fill="white"
                stroke={isLoudIssue ? '#dc2626' : '#d97706'}
                strokeWidth={1}
              />
              <text
                x={v3Bbox[0] - v3Pad}
                y={v3Bbox[1] - v3Pad + v3GlyphPx * 0.25}
                textAnchor="middle"
                fontSize={v3GlyphPx * 0.8}
                fontWeight={800}
                fontFamily="ui-monospace, monospace"
                fill={isLoudIssue ? '#dc2626' : '#d97706'}
              >
                {isLoudIssue ? '!' : '?'}
              </text>
            </g>
          )}
        </g>
      )}
      {handles.map((h) => {
        const sharedCount = jointSize(h.id);
        // Multi-joint visualization: a thicker green ring around the
        // handle when ≥2 labels share this endpoint, plus a tiny chip
        // showing the count. Means the user knows that a drag here will
        // move the whole joint, not just this one wall.
        const isJoint = sharedCount >= 2;
        const ringColor = isJoint ? '#10b981' : '#dc2626';
        const dotColor = ringColor;
        return (
          <g
            key={h.id}
            style={{ cursor: h.cursor ?? 'move' }}
            onPointerDown={onHandlePointerDown(h.id)}
          >
            {/* outer ring (hit area) */}
            <circle cx={h.pt[0]} cy={h.pt[1]} r={9} fill="white" stroke={ringColor} strokeWidth={isJoint ? 3 : 2.5} />
            {/* inner dot */}
            <circle cx={h.pt[0]} cy={h.pt[1]} r={3} fill={dotColor} />
            {isJoint && (
              <g pointerEvents="none">
                <circle
                  cx={h.pt[0] + 11} cy={h.pt[1] - 11}
                  r={7} fill={ringColor} stroke="white" strokeWidth={1.2}
                />
                <text
                  x={h.pt[0] + 11} y={h.pt[1] - 8}
                  textAnchor="middle" fill="white"
                  fontSize={9} fontWeight={800} fontFamily="ui-monospace, monospace"
                >
                  {sharedCount}
                </text>
              </g>
            )}
          </g>
        );
      })}
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

  // Source of truth for this layout: spec/keyboard.md. Keep them in sync.
  const sections: Array<[string, Array<[string, string]>]> = [
    ['Modifikatoren (global)', [
      ['Alt (halten)', 'Alle Helfer für diese Geste aus (Snap, Längen-Angleichung, Wandstärke-Vererbung, Auto-Klassifizierung, …)'],
      ['Shift (halten)', 'Ortho-Lock (0/45/90/135°) erzwingen · oder zur Mehrfachauswahl hinzufügen'],
      ['Cmd/Ctrl (halten)', 'Verbundene Komponente auswählen · Cmd+A alles · Cmd+S speichern · Cmd+Z undo'],
      ['Q', 'Ortho-Snap an/aus (persistent)'],
    ]],
    ['Werkzeuge', [
      ['S', 'Auswählen'],
      ['D', 'Bemaßte Strecke'],
      ['N', 'Maßzahl'],
      ['W', 'Wand'],
      ['O', 'Öffnung (tag-abhängig)'],
      ['L', 'Bauteillinie'],
      ['H', 'Höhenkote'],
    ]],
    ['Beim Zeichnen', [
      ['Klick', 'Punkt setzen'],
      ['Enter', 'Polylinie beenden (≥2 Punkte für Linie, ≥3 für Polygon)'],
      ['Klick nahe Start', 'Polygon schließen (≥3 Punkte) — gleich wie Enter'],
      ['Backspace', 'Letzten Polylinien-Punkt zurücknehmen'],
      ['Esc', 'Geste abbrechen (pending wegwerfen)'],
    ]],
    ['Auswahl', [
      ['Klick', 'Auswahl ersetzen'],
      ['Shift+Klick', 'Auswahl umschalten'],
      ['Cmd/Ctrl+Klick', 'Verbundene Komponente auswählen'],
      ['Drag auf leerer Fläche', 'Rubber-band Multi-Select'],
      ['Doppelklick auf Wand/Linie', 'Teilen (Vertex einfügen)'],
      ['Doppelklick in geschlossener Fläche', 'Alle Wände der Fläche'],
      ['Cmd/Ctrl + A', 'Alles auswählen'],
      ['Del', 'Auswahl löschen'],
      ['Backspace', 'Auswahl löschen — außer wenn Polylinie läuft'],
    ]],
    ['Öffnung reklassifizieren (1 ausgewählt)', [
      ['F', 'Fenster'],
      ['T', 'Tür'],
      ['G', 'Gaube (Ansicht) / Sonstige (Grundriss)'],
      ['D', 'Dachfenster (Ansicht) / Durchgang (Grundriss)'],
      ['A', 'Tor'],
      ['Z', 'Sonstige'],
    ]],
    ['Bauteillinie reklassifizieren (1 ausgewählt)', [
      ['W', 'Wand (vertikale Gebäudekante)'],
      ['D', 'Dach (Dachschräge)'],
      ['Z', 'Sonstige'],
    ]],
    ['Wand-Attribute (1 ausgewählt)', [
      ['← / →', '±10 mm Wandstärke'],
      ['Shift+← / →', '±50 mm Wandstärke'],
      ['Lila Handle ziehen', 'Wandstärke direkt zeichnen'],
    ]],
    ['Status (Auswahl)', [
      ['1', 'readable'],
      ['2', 'uncertain'],
      ['3', 'not_readable'],
      ['4', 'missing'],
    ]],
    ['Ansicht', [
      ['R oder 0', 'Ansicht zurücksetzen'],
      ['+ / =', 'Zoom in'],
      ['- / _', 'Zoom out'],
      ['Mausrad / Trackpad', 'Pan'],
      ['Shift+Drag / Right-Drag', 'Pan'],
    ]],
    ['Szenen-Navigation', [
      [',', 'Vorige Szene des Hauses'],
      ['.', 'Nächste Szene des Hauses'],
    ]],
    ['Speichern + Verlauf', [
      ['Cmd/Ctrl + S', 'Speichern'],
      ['Cmd/Ctrl + Z', 'Rückgängig'],
      ['Cmd/Ctrl + Shift + Z', 'Wiederherstellen'],
    ]],
    ['Hilfe', [
      ['?', 'Dieses Fenster ein/aus'],
      ['Esc', 'Dieses Fenster schließen'],
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
  initialValue,
  onCommit,
  onCancel,
}: {
  screenPos: [number, number];
  placeholder: string;
  initialValue?: string;
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
      defaultValue={initialValue ?? ''}
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
            className={`px-3 py-1.5 rounded shadow-lg text-[0.78rem] leading-snug max-w-[80vw] whitespace-pre-line break-words ${cls}`}
          >
            {t.message}
          </div>
        );
      })}
    </div>
  );
}

// Live in-canvas HUD shown while drawing a stroke or polyline. ONE compact
// panel — length + (length-match) + (snap target). No competing badges/
// circles/text scattered on the canvas. Quiet by default.
function DrawHUD({
  tool,
  pendingStart,
  hoverPt,
  pendingPolyline,
  snap,
  lengthMatch,
}: {
  tool: Tool;
  pendingStart: Point | null;
  hoverPt: Point | null;
  pendingPolyline: Point[];
  snap: SnapTarget | null;
  lengthMatch: LengthMatch | null;
}) {
  // 2-click line tools: angle from pendingStart → hoverPt
  if ((tool === 'dimensioned_distance' || tool === 'wall') && pendingStart && hoverPt) {
    const dx = (snap?.pt[0] ?? hoverPt[0]) - pendingStart[0];
    const dy = (snap?.pt[1] ?? hoverPt[1]) - pendingStart[1];
    const length = Math.hypot(dx, dy);
    return (
      <div className="absolute top-3 right-3 bg-black/80 text-white px-3 py-1.5 rounded font-mono text-[0.78rem] leading-snug pointer-events-none">
        <div className="flex items-center gap-3">
          <span className="text-amber-300 tabular-nums">L {length.toFixed(0)} px</span>
          {snap && snap.kind !== 'angle_lock' && (
            <span className="text-emerald-300">↦ {snap.hint}</span>
          )}
          {snap?.kind === 'angle_lock' && (
            <span className="text-emerald-300">⊥ {snap.hint}</span>
          )}
          {lengthMatch && (
            <span className={lengthMatch.withinSnapTolerance ? 'text-emerald-300' : 'text-amber-300'}>
              {lengthMatch.withinSnapTolerance ? '= ' : '≈ '}
              {lengthMatch.matchedLength.toFixed(0)} px
            </span>
          )}
        </div>
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
      const g = l.geometry as Record<string, unknown>;
      if (g.shape === 'circle') return g.center as Point;
      if (g.shape === 'polygon') {
        const poly = g.polygon as Point[];
        let sx = 0; let sy = 0;
        for (const p of poly) { sx += p[0]; sy += p[1]; }
        return [sx / poly.length, sy / poly.length];
      }
      const t = (l.geometry as { top_edge: Point[] }).top_edge;
      const b = (l.geometry as { bottom_edge: Point[] }).bottom_edge;
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


// Display labels for Höhenkote datums — used by the glyph and the implied
// horizontal-line layer. A Höhenkote with datum='first' renders as
// "First +12,5 m" on the canvas; that single label encodes both "this is
// the Firsthöhe" and "this is the value" — no separate component_line
// needed.
const DATUM_LABELS: Record<string, string> = {
  first:     'First',
  traufe:    'Traufe',
  gelaende:  'Gelände',
  geschoss:  'Geschoss',
  ok_ffb:    'OK FFB',
  sockel:    'Sockel',
  kniestock: 'Kniestock',
  other:     '',
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
  sceneFile,
  provenance,
  onChange,
  onDelete,
  onUnlink,
  onSelectId,
  onLinkTo,
  scope,
  houseKey,
  onAutoFillToast,
}: {
  label: Label;
  allLabels: Label[];
  sceneFile: string;
  provenance: string | null;
  onChange: (patch: Partial<Label>) => void;
  onDelete: () => void;
  onUnlink: (otherId: string) => void;
  onSelectId: (id: string) => void;
  onLinkTo: (otherId: string) => void;
  scope: LabelScope;
  houseKey: string;
  onAutoFillToast: (message: string, provenanceForLabelId?: string, provenanceNote?: string) => void;
}) {
  return (
    <div className="p-4 space-y-4">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Label</div>
        <div className="text-[0.95rem] font-semibold">{label.type}</div>
        <div className="text-[0.65rem] text-zinc-400 font-mono break-all">{label.id}</div>
        {provenance && (
          <div
            className="mt-2 text-[0.7rem] px-2 py-1 rounded bg-sky-50 border border-sky-200 text-sky-900 inline-flex items-center gap-1.5"
            title="Aus einer anderen Szene desselben Hauses übernommen. Sobald du den Wert änderst, verschwindet dieser Hinweis."
          >
            <span aria-hidden>↻</span>
            <span>{provenance}</span>
          </div>
        )}
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
      {label.type === 'height_mark' && (
        <HeightMarkFields
          label={label}
          allLabels={allLabels}
          sceneFile={sceneFile}
          onChange={onChange}
          scope={scope}
          houseKey={houseKey}
          onAutoFillToast={onAutoFillToast}
        />
      )}

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

// Pill-row kind picker. Replaces the old <select> dropdown so the kind
// classification is one tap, not a dropdown interaction. Colors come from
// labelColor() so each kind reads as its visual identity even before drawing.
// Hotkeys: pressing F/T/D/G/Z while a label is selected updates this same
// attribute — see the keydown handler at the top of AnnotatePage.
function KindPills({
  current,
  onSet,
  kinds,
  swatchFor,
}: {
  current: string;
  onSet: (k: string) => void;
  kinds: Array<{ id: string; label: string; key?: string }>;
  swatchFor: (id: string) => string;
}) {
  return (
    <div>
      <span className="text-[0.7rem] text-muted">Art</span>
      <div className="flex flex-wrap gap-1 mt-0.5">
        {kinds.map((k) => {
          const active = current === k.id;
          return (
            <button
              key={k.id}
              type="button"
              onClick={() => onSet(k.id)}
              className={`px-2 py-1 rounded text-[0.72rem] font-medium border transition flex items-center gap-1.5 ${
                active
                  ? 'border-transparent text-white shadow-sm'
                  : 'bg-white border-border text-zinc-700 hover:border-zinc-400'
              }`}
              style={active ? { backgroundColor: swatchFor(k.id) } : undefined}
              title={k.key ? `${k.label} (${k.key.toUpperCase()})` : k.label}
            >
              <span
                className="inline-block w-1.5 h-1.5 rounded-sm"
                style={{ backgroundColor: active ? 'rgba(255,255,255,0.85)' : swatchFor(k.id) }}
              />
              {k.label}
              {k.key && (
                <kbd className={`text-[0.6rem] font-mono ${active ? 'opacity-70' : 'text-zinc-400'}`}>
                  {k.key.toUpperCase()}
                </kbd>
              )}
            </button>
          );
        })}
      </div>
    </div>
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
      <KindPills
        current={a.opening_kind ?? 'window'}
        onSet={(k) => onChange({ attributes: { ...a, opening_kind: k as FloorplanOpeningLabel['attributes']['opening_kind'] } } as Partial<Label>)}
        kinds={[
          { id: 'window',      label: 'Fenster',    key: 'f' },
          { id: 'door',        label: 'Tür',        key: 't' },
          { id: 'passage',     label: 'Durchgang',  key: 'd' },
          { id: 'garage_door', label: 'Tor',        key: 'g' },
          { id: 'other',       label: 'Sonstige',   key: 'z' },
        ]}
        swatchFor={(id) => labelColor({ ...label, attributes: { ...a, opening_kind: id as FloorplanOpeningLabel['attributes']['opening_kind'] } })}
      />
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
      <KindPills
        current={a.opening_kind ?? 'window'}
        onSet={(k) => onChange({ attributes: { ...a, opening_kind: k as ViewOpeningLabel['attributes']['opening_kind'] } } as Partial<Label>)}
        kinds={[
          { id: 'window',      label: 'Fenster',     key: 'f' },
          { id: 'door',        label: 'Tür',         key: 't' },
          { id: 'skylight',    label: 'Dachfenster', key: 'd' },
          { id: 'dormer',      label: 'Gaube',       key: 'g' },
          { id: 'garage_door', label: 'Tor',         key: 'a' },
          { id: 'other',       label: 'Sonstige',    key: 'z' },
        ]}
        swatchFor={(id) => labelColor({ ...label, attributes: { ...a, opening_kind: id as ViewOpeningLabel['attributes']['opening_kind'] } })}
      />
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
  const current = label.attributes.line_kind ?? 'other';
  // Legacy line_kinds (first/traufe/…) get a 'legacy' marker — they exist
  // in the schema for backwards compat but the user shouldn't reach for
  // them on new labels. Höhenkote.datum replaces them.
  const LEGACY = new Set([
    'first', 'traufe', 'gelaende', 'geschoss',
    'ok_ffb', 'sockel', 'kniestock', 'firstkante',
  ]);
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Linie</h4>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Art</span>
        <select
          value={current}
          onChange={(e) => onChange({ attributes: { line_kind: e.target.value as ComponentLineLabel['attributes']['line_kind'] } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="gebaeudekante">Wand</option>
          <option value="dachschraege">Dach</option>
          <option value="other">Sonstige</option>
          {LEGACY.has(current) && (
            <option value={current}>
              {current === 'firstkante' ? 'Firstkante' :
               current === 'first' ? 'First' :
               current === 'traufe' ? 'Traufe' :
               current === 'gelaende' ? 'Gelände' :
               current === 'geschoss' ? 'Geschoss' :
               current === 'ok_ffb' ? 'OK FFB' :
               current === 'sockel' ? 'Sockel' :
               'Kniestock'} (legacy)
            </option>
          )}
        </select>
      </label>
      {LEGACY.has(current) && (
        <p className="text-[0.65rem] text-amber-700 bg-amber-50 border border-amber-200 rounded px-2 py-1 leading-snug">
          Diese Linie hat einen Höhenbezug als Typ — neuere Modellierung
          erfasst Höhen ausschließlich über Höhenkote.datum.
          Empfehlung: löschen + Höhenkote setzen.
        </p>
      )}
      <p className="text-[0.65rem] text-muted">
        Polylinie mit {label.geometry.polyline.length} Punkten.
      </p>
    </section>
  );
}

function HeightMarkFields({
  label, allLabels, sceneFile, onChange, scope, houseKey, onAutoFillToast,
}: {
  label: HeightMarkLabel;
  allLabels: Label[];
  sceneFile: string;
  onChange: (p: Partial<Label>) => void;
  scope: LabelScope;
  houseKey: string;
  onAutoFillToast: (message: string, provenanceForLabelId?: string, provenanceNote?: string) => void;
}) {
  const isBezug = label.attributes.value_mm === 0;
  // House-wide datum → value lookup: shows which datums are already known
  // from other scenes of this house. Used to auto-fill when the user
  // picks a known datum on a Höhenkote that has no value yet.
  const houseHeights = getHouseHeights(scope, houseKey);
  const DATUM_NAMES: Record<string, string> = {
    first: 'First', traufe: 'Traufe', gelaende: 'Gelände',
    ok_ffb: 'OK FFB', geschoss: 'Geschoss', sockel: 'Sockel',
    kniestock: 'Kniestock', other: 'Sonstige',
  };
  return (
    <section className="space-y-2">
      <h4 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">Höhenkote</h4>
      <button
        type="button"
        onClick={() => onChange({
          attributes: { ...label.attributes, value_mm: isBezug ? null : 0 },
        } as Partial<Label>)}
        className={`w-full px-2 py-1.5 rounded text-[0.78rem] font-medium border transition ${
          isBezug
            ? 'bg-amber-50 border-amber-300 text-amber-900 hover:bg-amber-100'
            : 'bg-white border-border text-zinc-700 hover:bg-zinc-50'
        }`}
        title="Eine Höhenkote pro Szene sollte die Bezugshöhe (±0,00) sein — alle anderen werden relativ dazu gelesen."
      >
        {isBezug ? '✓ Bezugshöhe (±0,00)' : 'Als ±0,00 markieren (Bezugshöhe)'}
      </button>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Datum (welche Höhe?)</span>
        <select
          value={label.attributes.datum ?? ''}
          onChange={(e) => {
            const newDatum = (e.target.value || null) as HeightMarkLabel['attributes']['datum'];
            const patch: HeightMarkLabel['attributes'] = { ...label.attributes, datum: newDatum };
            let nextGeom: HeightMarkLabel['geometry'] | undefined;
            // Cross-scene propagation: if this datum has a known value
            // from another scene of the same house AND the current
            // value is unset, pre-fill it. Heights are house-wide —
            // First in the south elevation = First in the EG-Grundriss.
            if (newDatum && newDatum !== 'other' && label.attributes.value_mm == null) {
              const known = houseHeights[newDatum];
              if (typeof known === 'number') {
                patch.value_mm = known;
                // N5: when the scene is calibrated (M1 references with
                // value_mm exist) AND Bezugshöhe Y is known in this scene,
                // also AUTO-POSITION the Y so the label snaps to where
                // this datum actually sits. Caveat: only useful when the
                // user just picked the datum without already placing the
                // mark at a custom position.
                const facts = loadHouseFacts(scope, houseKey);
                const calib = facts.calibration_per_scene[sceneFile];
                const bezugLabel = allLabels.find(
                  (l) => l.type === 'height_mark' && l.attributes.value_mm === 0,
                ) as HeightMarkLabel | undefined;
                if (calib && bezugLabel && bezugLabel.id !== label.id) {
                  // Image Y grows down; building height grows up. So a
                  // datum that's +12500 mm above Bezug sits 12500 * px_per_mm
                  // pixels HIGHER (smaller Y) than Bezug.
                  const deltaPx = known * calib.px_per_mm;
                  const newY = bezugLabel.geometry.anchor[1] - deltaPx;
                  nextGeom = { anchor: [label.geometry.anchor[0], newY] };
                  onAutoFillToast(
                    `↑ ${DATUM_NAMES[newDatum] ?? newDatum} = ${known === 0 ? '±0,00' : `${(known / 1000).toFixed(2).replace('.', ',')} m`} — Wert + Y-Position übernommen`,
                    label.id,
                    `${DATUM_NAMES[newDatum] ?? newDatum} mit Y aus Bezug + Kalibrierung`,
                  );
                } else {
                  onAutoFillToast(
                    `↑ ${DATUM_NAMES[newDatum] ?? newDatum} = ${known === 0 ? '±0,00' : `${(known / 1000).toFixed(2).replace('.', ',')} m`} aus anderer Szene übernommen`,
                    label.id,
                    `${DATUM_NAMES[newDatum] ?? newDatum} aus anderer Szene`,
                  );
                }
              }
            }
            onChange({ attributes: patch, ...(nextGeom ? { geometry: nextGeom } : {}) } as Partial<Label>);
          }}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem] bg-white"
        >
          <option value="">(nicht gesetzt)</option>
          {(['first','traufe','gelaende','ok_ffb','geschoss','sockel','kniestock','other'] as const).map((d) => {
            const knownMm = houseHeights[d];
            const known = typeof knownMm === 'number'
              ? ` · ${knownMm === 0 ? '±0,00' : (knownMm / 1000).toFixed(2).replace('.', ',') + ' m'}`
              : '';
            return (
              <option key={d} value={d}>{DATUM_NAMES[d]}{known}</option>
            );
          })}
        </select>
      </label>
      <label className="block">
        <span className="text-[0.7rem] text-muted">Wert (mm)</span>
        <input
          type="number"
          value={label.attributes.value_mm ?? ''}
          onChange={(e) => onChange({ attributes: { ...label.attributes, value_mm: e.target.value === '' ? null : Number(e.target.value) } } as Partial<Label>)}
          className="w-full px-2 py-1 rounded border border-border text-[0.8rem]"
          placeholder="z. B. 12500 (= 12,5 m). 0 = Bezugshöhe."
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
  anchorScreenPt,
  obstacleScreenPts,
  children,
}: {
  title: string;
  onClose?: () => void;
  storageKey: string;
  hidden?: boolean;
  /** Screen-coord centroid of the currently-selected label, used as the
   *  signal that selection changed and we should re-place. Null → default. */
  anchorScreenPt?: [number, number] | null;
  /** Screen-coord centroids of EVERY visible label (including the selected
   *  one). The popover picks the canvas corner farthest from all of them
   *  so it doesn't land on top of any geometry. */
  obstacleScreenPts?: Array<[number, number]>;
  children: React.ReactNode;
}) {
  const STORAGE = `bim-db:annotate:popover:${storageKey}`;
  // M2.3 smart placement: position is derived from the selection's quadrant,
  // not from localStorage. Manual drag overrides for THIS selection only;
  // a new selection re-picks the opposite quadrant. The user never has to
  // re-position the popover after it lands on top of something.
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const [userPinned, setUserPinned] = useState(false);
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return window.localStorage.getItem(`${STORAGE}:collapsed`) === '1'; } catch { return false; }
  });
  // Idle-collapse: when the pointer has been away from the popover for
  // IDLE_MS, shrink to a chip. Hovering the chip / popover restores it.
  // Click-through inside the popover counts as activity.
  const IDLE_MS = 800;
  const [idle, setIdle] = useState(false);
  const idleTimer = useRef<number | null>(null);
  const popRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ dx: number; dy: number } | null>(null);

  // Auto-position based on anchorScreenPt — opposite-quadrant placement.
  // Re-runs every time the anchor changes (i.e. user selects a different
  // label), wiping any manual drag from the previous selection.
  useEffect(() => {
    if (hidden) return;
    setUserPinned(false);
    const el = popRef.current;
    if (!el) return;
    const parent = el.offsetParent as HTMLElement | null;
    if (!parent) return;
    const pr = parent.getBoundingClientRect();
    const w = el.offsetWidth || 280;
    const h = el.offsetHeight || 200;
    if (anchorScreenPt) {
      // P4 smarter placement: score each of the 4 canvas corners by the
      // MINIMUM distance from the corner's popover-bbox to any visible
      // label's centroid (the obstacle set). Pick the corner with the
      // largest min-distance — i.e. the corner with the most empty canvas
      // around it. Falls back to opposite-quadrant when no obstacles given.
      const corners: Array<{ x: number; y: number }> = [
        { x: 16,                           y: 16 },
        { x: Math.max(8, pr.width - w - 16), y: 16 },
        { x: 16,                           y: Math.max(8, pr.height - h - 16) },
        { x: Math.max(8, pr.width - w - 16), y: Math.max(8, pr.height - h - 16) },
      ];
      const obstaclesLocal = (obstacleScreenPts ?? []).map(([sx, sy]) => [sx - pr.left, sy - pr.top] as [number, number]);
      const bboxToPoint = (cx: number, cy: number, px: number, py: number) => {
        // Distance from the popover's bbox to obstacle point.
        const dx = Math.max(cx - px, 0, px - (cx + w));
        const dy = Math.max(cy - py, 0, py - (cy + h));
        return Math.hypot(dx, dy);
      };
      const score = (c: { x: number; y: number }) => {
        if (obstaclesLocal.length === 0) return -Infinity;
        let minD = Infinity;
        for (const [ox, oy] of obstaclesLocal) {
          const d = bboxToPoint(c.x, c.y, ox, oy);
          if (d < minD) minD = d;
        }
        return minD;
      };
      let best = corners[0];
      let bestScore = -Infinity;
      for (const c of corners) {
        const s = score(c);
        if (s > bestScore) { bestScore = s; best = c; }
      }
      // When obstaclesLocal is empty, fall back to the opposite-quadrant
      // heuristic against anchorScreenPt.
      if (obstaclesLocal.length === 0) {
        const [ax, ay] = anchorScreenPt;
        const localX = ax - pr.left;
        const localY = ay - pr.top;
        const onLeft = localX > pr.width / 2;
        const onTop = localY > pr.height / 2;
        best = {
          x: onLeft ? 16 : Math.max(8, pr.width - w - 16),
          y: onTop ? 16 : Math.max(8, pr.height - h - 16),
        };
      }
      setPos(best);
    } else {
      setPos({ x: Math.max(8, pr.width - w - 16), y: 16 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorScreenPt?.[0], anchorScreenPt?.[1], hidden, obstacleScreenPts?.length]);

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
    setUserPinned(true);     // user took over for THIS selection
    try { (e.target as Element).releasePointerCapture(e.pointerId); } catch { /* no-op */ }
  }, []);

  const toggleCollapsed = useCallback(() => {
    setCollapsed((v) => {
      const next = !v;
      try { window.localStorage.setItem(`${STORAGE}:collapsed`, next ? '1' : '0'); } catch { /* no-op */ }
      return next;
    });
  }, [STORAGE]);

  // Idle-collapse: pointer-leave → start timer; pointer-enter → cancel timer + restore.
  const resetIdleTimer = useCallback(() => {
    if (idleTimer.current != null) window.clearTimeout(idleTimer.current);
    idleTimer.current = window.setTimeout(() => setIdle(true), IDLE_MS);
  }, []);
  const cancelIdle = useCallback(() => {
    if (idleTimer.current != null) {
      window.clearTimeout(idleTimer.current);
      idleTimer.current = null;
    }
    setIdle(false);
  }, []);
  useEffect(() => () => {
    if (idleTimer.current != null) window.clearTimeout(idleTimer.current);
  }, []);
  // When the anchor changes (new selection), reset idle state so the user
  // can see the new label's inspector.
  useEffect(() => {
    cancelIdle();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchorScreenPt?.[0], anchorScreenPt?.[1]]);
  void userPinned;     // referenced indirectly through setUserPinned; kept for future "preserve on next selection" extension

  if (hidden) return null;
  // Idle-chip: when the pointer has been away for IDLE_MS, render a small
  // chip showing just the title. Click the chip to restore the full panel.
  if (idle && !collapsed) {
    return (
      <button
        ref={popRef as unknown as React.RefObject<HTMLButtonElement>}
        type="button"
        onClick={cancelIdle}
        onPointerEnter={cancelIdle}
        className="absolute z-30 bg-white/95 border border-zinc-300 rounded-md shadow text-[0.72rem] font-medium px-2.5 py-1 text-zinc-800 hover:bg-white"
        style={{
          left: pos?.x ?? 16,
          top: pos?.y ?? 16,
        }}
        title="Klick zum Aufklappen"
      >
        {title}
      </button>
    );
  }
  return (
    <div
      ref={popRef}
      onPointerEnter={cancelIdle}
      onPointerLeave={resetIdleTimer}
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
