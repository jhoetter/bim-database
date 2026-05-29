// N4 — house-wide facts derived from labels across scenes of the same house.
//
// Every label is evidence about a physical building. Some labels promote to
// "facts" — properties of the building that any scene witnesses identically:
// building width, First height, EG slab elevation, outer wall thickness,
// the per-scene px-per-mm calibration etc. The cache here is the single
// source of truth for those facts.
//
// Storage: localStorage for v1 (single-user app, no concurrent writes).
// Key: `bim-db:annotate:house-facts:<scope>:<houseKey>`. The structure
// mirrors what spec/data/dataset/<house>/house_facts.json would hold if
// we promoted to filesystem; the migration would be a single read pass.
//
// Reads are cheap (synchronous). Writes happen on save() — promotion is
// idempotent (same label saved twice produces the same fact).

import type { Label, LabelScope, SceneLevel, SceneOrientation, SceneTag } from '../api/types';
import { dimOrientation } from './building_dims';

export interface SceneCalibration {
  /** Pixels per mm — average of H and V if both M1 references are present. */
  px_per_mm: number;
  computed_from: 'M1-H-Bezug' | 'M1-V-Bezug' | 'M1-both';
}

export interface SceneMetadataEntry {
  kind: SceneTag;
  orientation?: SceneOrientation | null;
  level?: SceneLevel | null;
  image_size_px?: [number, number];
  /** Y coordinate (in image px) of the Bezugshöhe (±0,00) Höhenkote, if
   *  one exists in this scene. Used to derive Y for known datums on
   *  Höhenkote auto-position (N5). */
  bezug_y_px?: number;
}

// W4 — orientation graph (Phase 3). The user picks one EG-Grundriss wall
// as the north-facing edge; everything else is derived from that wall's
// pixel geometry × the Grundriss calibration. The cardinal name is the
// user's label; the geometry decides which extent dimension a given
// Ansicht/Schnitt spans (per spec §4.3 — gable-facing-N case must
// resolve to depth, not width).
export interface OrientationGraph {
  /** Wall label id (in the source Grundriss file) the user picked as
   *  "the north-facing edge". Null until Phase 3 completes. */
  north_edge_label_id: string | null;
  /** Optional manual rotation for scanned plans not orthogonal to the
   *  page. Defaults to 0; applied as a counter-clockwise rotation of
   *  the derived basis. */
  north_angle_deg?: number | null;
  /** Which scene file the picked edge belongs to — needed for
   *  resolving the wall's geometry on load. */
  source_grundriss_file: string;
}

// W0.1 — workflow state machine. Persisted alongside house_facts so it
// survives sessions. The phase pointer is the *first phase whose
// completion predicate fails*; once it advances, the phase_completed_at
// timestamp records when. user_skipped lets a user permanently dismiss
// a phase ("I've got that upstream — stop telling me").
export const PHASE_IDS = [
  'inventory', 'height_anchor', 'footprint',
  'orientation', 'bezugsmasse', 'detail',
] as const;
export type PhaseId = typeof PHASE_IDS[number];

export interface WorkflowState {
  schema_version: '1.0';
  phase: PhaseId;
  phase_completed_at: Record<PhaseId, string | null>;
  source_scene: Record<PhaseId, string | null>;
  user_skipped: Partial<Record<PhaseId, boolean>>;
}

// W0.2 — generic provenance-tagged fact bag for future extensions
// (roof pitch, gable count, chimney count, …). Lives outside the typed
// fields so new derived facts append without schema churn.
export interface FactEntry {
  value: unknown;
  sources: string[];
  computed_at: string;
  algorithm?: string;
}

export interface HouseFacts {
  schema_version: '1.0';
  extent: {
    width_mm?: number;
    depth_mm?: number;
    height_mm?: number;
    sources: Record<string, string[]>;
  };
  heights: {
    bezug_mm?: number;
    first_mm?: number;
    traufe_mm?: number;
    gelaende_mm?: number;
    ok_ffb_eg_mm?: number;
    ok_ffb_og_mm?: number;
    ok_ffb_dg_mm?: number;
    geschoss_mm?: number;
    sockel_mm?: number;
    kniestock_mm?: number;
    firstkante_mm?: number;
    sources: Record<string, string[]>;
  };
  wall_thickness: {
    outer_mm?: number;
    inner_mm?: number;
  };
  openings_catalog: Array<{
    kind: string;
    width_mm: number;
    height_mm?: number;
    instances: number;
  }>;
  calibration_per_scene: Record<string, SceneCalibration>;
  scene_metadata: Record<string, SceneMetadataEntry>;
  // W0 — optional so older caches load forward without migration.
  orientation?: OrientationGraph | null;
  workflow?: WorkflowState | null;
  derived_facts?: Record<string, FactEntry>;
}

export function defaultWorkflowState(): WorkflowState {
  const blank: Record<PhaseId, string | null> = {
    inventory: null, height_anchor: null, footprint: null,
    orientation: null, bezugsmasse: null, detail: null,
  };
  return {
    schema_version: '1.0',
    phase: 'inventory',
    phase_completed_at: blank,
    source_scene: { ...blank },
    user_skipped: {},
  };
}

function defaultFacts(): HouseFacts {
  return {
    schema_version: '1.0',
    extent: { sources: {} },
    heights: { sources: {} },
    wall_thickness: {},
    openings_catalog: [],
    calibration_per_scene: {},
    scene_metadata: {},
    orientation: null,
    workflow: defaultWorkflowState(),
    derived_facts: {},
  };
}

function storageKey(scope: LabelScope, houseKey: string): string {
  return `bim-db:annotate:house-facts:${scope}:${houseKey}`;
}

export function loadHouseFacts(scope: LabelScope, houseKey: string): HouseFacts {
  try {
    const raw = window.localStorage.getItem(storageKey(scope, houseKey));
    if (!raw) return defaultFacts();
    const parsed = JSON.parse(raw);
    if (parsed?.schema_version !== '1.0') return defaultFacts();
    // W0 forward-compat: old caches may lack orientation/workflow/derived_facts.
    const facts = parsed as HouseFacts;
    if (facts.orientation === undefined) facts.orientation = null;
    if (!facts.workflow) facts.workflow = defaultWorkflowState();
    if (!facts.derived_facts) facts.derived_facts = {};
    return facts;
  } catch {
    return defaultFacts();
  }
}

export function saveHouseFacts(scope: LabelScope, houseKey: string, facts: HouseFacts): void {
  try {
    window.localStorage.setItem(storageKey(scope, houseKey), JSON.stringify(facts));
  } catch { /* no-op */ }
  // U13 — also schedule a debounced push to the server so the file at
  // data/dataset/<key>/house_facts.json stays in sync. localStorage is
  // the synchronous read cache; the server is canonical.
  scheduleServerPush(scope, houseKey, facts);
}

// U13 server sync. Both sides are best-effort — a network blip MUST NOT
// drop a user's local edit.
const PUSH_DEBOUNCE_MS = 800;
const pushTimers = new Map<string, ReturnType<typeof setTimeout>>();
function scheduleServerPush(scope: LabelScope, houseKey: string, facts: HouseFacts): void {
  if (scope !== 'dataset') return;
  const k = `${scope}:${houseKey}`;
  const prev = pushTimers.get(k);
  if (prev) clearTimeout(prev);
  const t = setTimeout(() => {
    pushTimers.delete(k);
    void import('../api/client').then(({ putHouseFactsRaw }) =>
      putHouseFactsRaw(houseKey, facts).catch((e) => {
        // Don't surface — the user's local edit is still safe in
        // localStorage; we'll retry on the next save.
        // eslint-disable-next-line no-console
        console.warn('house_facts server push failed', e);
      }),
    );
  }, PUSH_DEBOUNCE_MS);
  pushTimers.set(k, t);
}

// Pull the server's house_facts into localStorage. Used by AnnotatePage
// on mount so cross-machine continuity works. If the server returns 404,
// push the current localStorage to the server (first-ever migration).
export async function syncHouseFactsFromServer(scope: LabelScope, houseKey: string): Promise<HouseFacts> {
  if (scope !== 'dataset') return loadHouseFacts(scope, houseKey);
  try {
    const { fetchHouseFactsRaw, putHouseFactsRaw } = await import('../api/client');
    const remote = await fetchHouseFactsRaw(houseKey);
    if (remote && typeof remote === 'object' && (remote as HouseFacts).schema_version === '1.0') {
      // Server wins.
      window.localStorage.setItem(storageKey(scope, houseKey), JSON.stringify(remote));
      return remote as HouseFacts;
    }
    // First-time migration: push our local copy if it has anything.
    const local = loadHouseFacts(scope, houseKey);
    await putHouseFactsRaw(houseKey, local).catch(() => undefined);
    return local;
  } catch {
    return loadHouseFacts(scope, houseKey);
  }
}

function addSource(map: Record<string, string[]>, fact: string, source: string): void {
  const existing = map[fact] ?? [];
  if (!existing.includes(source)) {
    map[fact] = [...existing, source];
  }
}

/** Compute pxPerMm for a scene from its labels. Looks at M1-H and M1-V
 *  references with value_mm set; returns the average if both exist,
 *  otherwise whichever is available, or null. */
export function computeSceneCalibration(labels: Label[]): SceneCalibration | null {
  let hCalib: number | null = null;
  let vCalib: number | null = null;
  for (const l of labels) {
    if (l.type !== 'dimensioned_distance') continue;
    if (!l.attributes.is_reference) continue;
    if (l.attributes.value_mm == null || l.attributes.value_mm <= 0) continue;
    const orient = dimOrientation(l.geometry.start, l.geometry.end);
    if (!orient) continue;
    const lenPx = Math.hypot(
      l.geometry.end[0] - l.geometry.start[0],
      l.geometry.end[1] - l.geometry.start[1],
    );
    if (lenPx < 1) continue;
    const pxPerMm = lenPx / l.attributes.value_mm;
    if (orient === 'horizontal') hCalib = pxPerMm;
    else if (orient === 'vertical') vCalib = pxPerMm;
  }
  if (hCalib != null && vCalib != null) {
    return { px_per_mm: (hCalib + vCalib) / 2, computed_from: 'M1-both' };
  }
  if (hCalib != null) return { px_per_mm: hCalib, computed_from: 'M1-H-Bezug' };
  if (vCalib != null) return { px_per_mm: vCalib, computed_from: 'M1-V-Bezug' };
  return null;
}

/** Promote qualifying labels of one scene to house facts. Idempotent. */
export function promoteToFacts(args: {
  scope: LabelScope;
  houseKey: string;
  sceneFile: string;
  sceneTag: SceneTag;
  sceneOrientation: SceneOrientation | null;
  sceneLevel: SceneLevel | null;
  imageSize: [number, number];
  labels: Label[];
}): HouseFacts {
  const facts = loadHouseFacts(args.scope, args.houseKey);
  const srcPrefix = `${args.sceneFile}#`;

  // Scene metadata always promoted (cheap).
  facts.scene_metadata[args.sceneFile] = {
    kind: args.sceneTag,
    orientation: args.sceneOrientation,
    level: args.sceneLevel,
    image_size_px: args.imageSize,
  };

  // Calibration from M1 references.
  const calib = computeSceneCalibration(args.labels);
  if (calib) {
    facts.calibration_per_scene[args.sceneFile] = calib;
  }

  // Extent from is_reference dim_distances with value_mm set.
  for (const l of args.labels) {
    if (l.type !== 'dimensioned_distance' || !l.attributes.is_reference) continue;
    if (l.attributes.value_mm == null) continue;
    const orient = dimOrientation(l.geometry.start, l.geometry.end);
    if (!orient) continue;
    const src = `${srcPrefix}dim:${l.id}`;
    if (orient === 'horizontal') {
      // For Ansicht: building width. For Schnitt: building depth. For
      // Grundriss: either, depending on orientation of the building axis
      // in that floor view — pragmatic: write to width_mm (it's the
      // largest H extent we've seen), let conflict detection (N8) flag
      // if Ansicht and Grundriss disagree.
      if (args.sceneTag === 'schnitt') {
        if (facts.extent.depth_mm == null || l.attributes.value_mm > facts.extent.depth_mm) {
          facts.extent.depth_mm = l.attributes.value_mm;
        }
        addSource(facts.extent.sources, 'depth_mm', src);
      } else {
        if (facts.extent.width_mm == null || l.attributes.value_mm > facts.extent.width_mm) {
          facts.extent.width_mm = l.attributes.value_mm;
        }
        addSource(facts.extent.sources, 'width_mm', src);
      }
    } else {
      // Vertical is building height regardless of scene tag.
      if (facts.extent.height_mm == null || l.attributes.value_mm > facts.extent.height_mm) {
        facts.extent.height_mm = l.attributes.value_mm;
      }
      addSource(facts.extent.sources, 'height_mm', src);
    }
  }

  // Heights from height_marks with datum + value_mm set.
  let bezugY: number | undefined;
  for (const l of args.labels) {
    if (l.type !== 'height_mark') continue;
    const v = l.attributes.value_mm;
    if (v == null) continue;
    if (v === 0) bezugY = l.geometry.anchor[1];
    const d = l.attributes.datum;
    if (!d || d === 'other') {
      // Bezugshöhe (value=0) has no datum but is still meaningful.
      if (v === 0) {
        facts.heights.bezug_mm = 0;
        addSource(facts.heights.sources, 'bezug_mm', `${srcPrefix}hm:${l.id}`);
      }
      continue;
    }
    const datumKey: string | null = (() => {
      switch (d) {
        case 'first': return 'first_mm';
        case 'traufe': return 'traufe_mm';
        case 'gelaende': return 'gelaende_mm';
        case 'sockel': return 'sockel_mm';
        case 'kniestock': return 'kniestock_mm';
        case 'geschoss': return 'geschoss_mm';
        case 'ok_ffb': {
          if (args.sceneLevel === 'og') return 'ok_ffb_og_mm';
          if (args.sceneLevel === 'dg') return 'ok_ffb_dg_mm';
          return 'ok_ffb_eg_mm';
        }
        default: return null;
      }
    })();
    if (!datumKey) continue;
    (facts.heights as unknown as Record<string, number | undefined>)[datumKey] = v;
    addSource(facts.heights.sources, datumKey, `${srcPrefix}hm:${l.id}`);
  }
  if (bezugY != null) {
    facts.scene_metadata[args.sceneFile] = {
      ...facts.scene_metadata[args.sceneFile],
      bezug_y_px: bezugY,
    };
  }

  // Openings catalog — accumulate distinct (kind, width) pairs with counts.
  const counter = new Map<string, { kind: string; width_mm: number; instances: number }>();
  for (const l of args.labels) {
    if (l.type !== 'floorplan_opening' && l.type !== 'view_opening') continue;
    const k = (l.attributes as { opening_kind?: string }).opening_kind ?? 'window';
    const w = (l.attributes as { width_mm?: number | null }).width_mm;
    if (typeof w !== 'number' || w <= 0) continue;
    const bucket = Math.round(w / 50) * 50;     // 50 mm buckets to fold near-duplicates
    const ck = `${k}-${bucket}`;
    const prev = counter.get(ck) ?? { kind: k, width_mm: bucket, instances: 0 };
    prev.instances += 1;
    counter.set(ck, prev);
  }
  // Merge into existing catalog (overwrite this scene's contribution).
  // Simple approach: blank the existing catalog scoped to this scene and
  // recount. Acceptable for v1 since we don't track per-label sources.
  if (counter.size > 0) {
    const existing = new Map<string, typeof facts.openings_catalog[number]>();
    for (const e of facts.openings_catalog) {
      existing.set(`${e.kind}-${e.width_mm}`, e);
    }
    for (const [ck, entry] of counter) {
      const prev = existing.get(ck);
      existing.set(ck, prev ? { ...prev, instances: Math.max(prev.instances, entry.instances) } : entry);
    }
    facts.openings_catalog = [...existing.values()].sort(
      (a, b) => (a.kind === b.kind ? a.width_mm - b.width_mm : a.kind.localeCompare(b.kind)),
    );
  }

  saveHouseFacts(args.scope, args.houseKey, facts);
  return facts;
}
