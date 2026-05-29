// R5 — 3D annotation preview.
//
// Reads house_facts from localStorage + fetches every scene's labels
// for this house, runs lib/scene_3d.ts to assemble the geometry, and
// renders the result with react-three-fiber. Read-only — clicking is
// the editor's job.

import { Suspense, useEffect, useMemo, useState } from 'react';
import { Link, useParams } from 'react-router';
import { Canvas } from '@react-three/fiber';
import { OrbitControls } from '@react-three/drei';
import * as THREE from 'three';

import { fetchDataset, fetchLabels } from '../api/client';
import type { DatasetHouse, SceneLabels } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { loadHouseFacts } from '../lib/house_facts';
import { buildScene3D, type BuiltScene3D } from '../lib/scene_3d';
import { labelColor } from '../lib/colors';

// Scale-down factor: 1 unit in Three.js = SCALE mm in world. Keeps camera
// numbers in a sane range (a typical house is ~15 m = 15 units instead of
// 15 000 — orbit controls + near/far planes work much better).
const SCALE = 1 / 1000;

export function Preview3DPage() {
  const { key = '' } = useParams();
  const [dataset, setDataset] = useState<DatasetHouse | null>(null);
  const [labelsByFile, setLabelsByFile] = useState<Record<string, SceneLabels>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const ds = await fetchDataset(key);
        if (cancelled) return;
        setDataset(ds);
        const labels: Record<string, SceneLabels> = {};
        await Promise.all(
          (ds.drawings ?? []).map(async (d) => {
            try {
              const lab = await fetchLabels('dataset', key, d.file);
              labels[d.file] = lab;
            } catch { /* per-scene errors are non-fatal */ }
          }),
        );
        if (!cancelled) setLabelsByFile(labels);
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [key]);

  const facts = useMemo(() => loadHouseFacts('dataset', key), [key, labelsByFile]);
  const scene3d = useMemo(() => {
    return buildScene3D({ facts, scenes: labelsByFile });
  }, [facts, labelsByFile]);

  void dataset;

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: 'Datensatz', to: '/' },
            { label: key, to: `/${key}` },
            { label: '3D' },
          ]}
        />
      }
      leftSidebar={<SidePanel scene={scene3d} />}
    >
      <div className="flex flex-col h-full">
        <div className="flex-1 min-h-0 bg-zinc-200 relative">
          {loading && <p className="absolute top-3 left-3 text-[0.78rem] text-zinc-700">Lade…</p>}
          {error && <p className="absolute top-3 left-3 text-[0.78rem] text-red-700">{error}</p>}
          <Canvas
            camera={{ position: [12, 10, 14], fov: 45 }}
            shadows={false}
          >
            <Suspense fallback={null}>
              <Scene3D scene={scene3d} />
            </Suspense>
            <OrbitControls makeDefault target={[0, 1, 0]} />
            <ambientLight intensity={0.65} />
            <directionalLight position={[10, 12, 8]} intensity={0.5} />
            <hemisphereLight args={['#ffffff', '#1f2937', 0.35]} />
            <gridHelper args={[60, 30, '#52525b', '#a1a1aa']} position={[0, scene3d.ground_y * SCALE + 0.001, 0]} />
            <axesHelper args={[2]} />
          </Canvas>
        </div>
      </div>
    </Shell>
  );
}

// ── 3D scene composition ──────────────────────────────────────────────

function Scene3D({ scene }: { scene: BuiltScene3D }) {
  const width = scene.building.width_mm * SCALE;
  const depth = scene.building.depth_mm * SCALE;
  const wallTop = scene.wall_top_y * SCALE;
  const ground = scene.ground_y * SCALE;
  const thickness = scene.wall_thickness_mm * SCALE;
  const ridge = (scene.ridge_y ?? scene.wall_top_y) * SCALE;
  const hasRoof = scene.ridge_y != null;

  return (
    <group>
      {/* Ground plane — large square at y=ground. */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, ground, 0]} receiveShadow>
        <planeGeometry args={[60, 60]} />
        <meshStandardMaterial color="#f5e9d4" />
      </mesh>

      {/* Building footprint as a thin floor at y=0 (±0,00). */}
      <mesh position={[0, 0.001, 0]} rotation={[-Math.PI / 2, 0, 0]}>
        <planeGeometry args={[width, depth]} />
        <meshStandardMaterial color="#ede5d6" transparent opacity={0.65} />
      </mesh>

      {/* Walls: four extruded slabs along the perimeter. */}
      <Walls
        width={width}
        depth={depth}
        bottomY={ground}
        topY={wallTop}
        thickness={thickness}
        confidence={scene.building.confidence.walls}
      />

      {/* Floor slabs at named OK FFB heights. */}
      {scene.floor_slabs.map((s) => (
        <mesh key={s.name} position={[0, s.y_mm * SCALE, 0]} rotation={[-Math.PI / 2, 0, 0]}>
          <planeGeometry args={[width * 0.98, depth * 0.98]} />
          <meshStandardMaterial color="#bcd4f5" transparent opacity={0.35} />
        </mesh>
      ))}

      {/* Roof: simple gable when first_mm is known + a clear "long axis". */}
      {hasRoof && (
        <Roof
          width={width}
          depth={depth}
          eaveY={wallTop}
          ridgeY={ridge}
          confidence={scene.building.confidence.roof}
        />
      )}

      {/* Openings projected onto each face. */}
      <Openings scene={scene} width={width} depth={depth} ground={ground} wallTop={wallTop} />

      {/* Height marks. */}
      {scene.height_marks.map((h) => (
        <HeightLine key={h.label} y={h.y_mm * SCALE} label={h.label} extent={Math.max(width, depth) * 1.4} />
      ))}

      {/* Compass — rotated so +Z points to the labeled north. */}
      <Compass north_angle={scene.north_arrow_angle} y={ground + 0.05} extent={Math.max(width, depth) * 0.55} />
    </group>
  );
}

function Walls({
  width, depth, bottomY, topY, thickness, confidence,
}: {
  width: number; depth: number; bottomY: number; topY: number; thickness: number;
  confidence: BuiltScene3D['building']['confidence']['walls'];
}) {
  const h = topY - bottomY;
  const y = (topY + bottomY) / 2;
  const colour = confidence === 'solid' ? '#c0a98e' : confidence === 'approximate' ? '#caaf8b' : '#d6c39f';
  const opacity = confidence === 'guessed' ? 0.55 : 0.9;
  // North wall (+Z): runs along X axis.
  // South (-Z): mirror.
  // East (+X): runs along Z.
  // West (-X): mirror.
  return (
    <>
      <mesh position={[0, y,  depth / 2]}>
        <boxGeometry args={[width, h, thickness]} />
        <meshStandardMaterial color={colour} transparent opacity={opacity} />
      </mesh>
      <mesh position={[0, y, -depth / 2]}>
        <boxGeometry args={[width, h, thickness]} />
        <meshStandardMaterial color={colour} transparent opacity={opacity} />
      </mesh>
      <mesh position={[ width / 2, y, 0]}>
        <boxGeometry args={[thickness, h, depth]} />
        <meshStandardMaterial color={colour} transparent opacity={opacity} />
      </mesh>
      <mesh position={[-width / 2, y, 0]}>
        <boxGeometry args={[thickness, h, depth]} />
        <meshStandardMaterial color={colour} transparent opacity={opacity} />
      </mesh>
    </>
  );
}

function Roof({
  width, depth, eaveY, ridgeY, confidence,
}: {
  width: number; depth: number; eaveY: number; ridgeY: number;
  confidence: BuiltScene3D['building']['confidence']['roof'];
}) {
  // Two-piece gable. Ridge runs along the LONG axis. If width >= depth, ridge is parallel to X.
  const ridgeAlongX = width >= depth;
  void (ridgeY - eaveY);
  const opacity = confidence === 'approximate' ? 0.7 : 0.85;
  const colour = '#a85a3c';
  if (ridgeAlongX) {
    // Two slopes meeting along X at y=ridgeY, descending to ±Z eaves at y=eaveY.
    // Each slope is a parallelogram; build via BufferGeometry.
    const half = depth / 2;
    return (
      <group>
        <Triangle3 points={[
          [-width / 2, eaveY,  half], [ width / 2, eaveY,  half], [ width / 2, ridgeY, 0],
        ]} color={colour} opacity={opacity} />
        <Triangle3 points={[
          [-width / 2, eaveY,  half], [ width / 2, ridgeY, 0], [-width / 2, ridgeY, 0],
        ]} color={colour} opacity={opacity} />
        <Triangle3 points={[
          [-width / 2, eaveY, -half], [-width / 2, ridgeY, 0], [ width / 2, ridgeY, 0],
        ]} color={colour} opacity={opacity} />
        <Triangle3 points={[
          [-width / 2, eaveY, -half], [ width / 2, ridgeY, 0], [ width / 2, eaveY, -half],
        ]} color={colour} opacity={opacity} />
        {/* Gable triangles at ±X end. */}
        <Triangle3 points={[
          [ width / 2, eaveY,  half], [ width / 2, eaveY, -half], [ width / 2, ridgeY, 0],
        ]} color="#c8b29a" opacity={opacity} />
        <Triangle3 points={[
          [-width / 2, eaveY,  half], [-width / 2, ridgeY, 0], [-width / 2, eaveY, -half],
        ]} color="#c8b29a" opacity={opacity} />
      </group>
    );
  }
  // Ridge parallel to Z: mirror.
  const halfW = width / 2;
  return (
    <group>
      <Triangle3 points={[
        [ halfW, eaveY, -depth / 2], [ halfW, eaveY,  depth / 2], [0, ridgeY,  depth / 2],
      ]} color={colour} opacity={opacity} />
      <Triangle3 points={[
        [ halfW, eaveY, -depth / 2], [0, ridgeY,  depth / 2], [0, ridgeY, -depth / 2],
      ]} color={colour} opacity={opacity} />
      <Triangle3 points={[
        [-halfW, eaveY, -depth / 2], [0, ridgeY, -depth / 2], [0, ridgeY,  depth / 2],
      ]} color={colour} opacity={opacity} />
      <Triangle3 points={[
        [-halfW, eaveY, -depth / 2], [0, ridgeY,  depth / 2], [-halfW, eaveY,  depth / 2],
      ]} color={colour} opacity={opacity} />
      <Triangle3 points={[
        [ halfW, eaveY,  depth / 2], [-halfW, eaveY,  depth / 2], [0, ridgeY,  depth / 2],
      ]} color="#c8b29a" opacity={opacity} />
      <Triangle3 points={[
        [ halfW, eaveY, -depth / 2], [0, ridgeY, -depth / 2], [-halfW, eaveY, -depth / 2],
      ]} color="#c8b29a" opacity={opacity} />
      void h;
    </group>
  );
}

function Triangle3({
  points, color, opacity,
}: {
  points: [[number, number, number], [number, number, number], [number, number, number]];
  color: string;
  opacity: number;
}) {
  const geom = useMemo(() => {
    const g = new THREE.BufferGeometry();
    const verts = new Float32Array([
      points[0][0], points[0][1], points[0][2],
      points[1][0], points[1][1], points[1][2],
      points[2][0], points[2][1], points[2][2],
    ]);
    g.setAttribute('position', new THREE.BufferAttribute(verts, 3));
    g.computeVertexNormals();
    return g;
  }, [points]);
  return (
    <mesh geometry={geom}>
      <meshStandardMaterial color={color} transparent opacity={opacity} side={THREE.DoubleSide} />
    </mesh>
  );
}

function Openings({
  scene, width, depth, ground, wallTop,
}: {
  scene: BuiltScene3D; width: number; depth: number; ground: number; wallTop: number;
}) {
  // For each face, project each opening to its 3D position on the wall.
  // The face's centre is at the wall's centre line; we offset by half-
  // thickness so the rectangle sits ON the wall.
  const out: React.ReactElement[] = [];
  const _ = wallTop; void _;  // for future use (door swing on floor slab)
  void ground;
  for (const face of scene.openings) {
    if (face.items.length === 0) continue;
    let normal: [number, number, number];
    let along: [number, number, number];
    let centreOffset: [number, number, number];
    let faceWidth = width;
    if (face.face === 'north') {
      normal = [0, 0, 1]; along = [1, 0, 0]; centreOffset = [0, 0, depth / 2]; faceWidth = width;
    } else if (face.face === 'south') {
      normal = [0, 0, -1]; along = [-1, 0, 0]; centreOffset = [0, 0, -depth / 2]; faceWidth = width;
    } else if (face.face === 'east') {
      normal = [1, 0, 0]; along = [0, 0, -1]; centreOffset = [width / 2, 0, 0]; faceWidth = depth;
    } else {
      normal = [-1, 0, 0]; along = [0, 0, 1]; centreOffset = [-width / 2, 0, 0]; faceWidth = depth;
    }
    for (const item of face.items) {
      // The face has a leftmost wall corner at -faceWidth/2 along `along`,
      // rightmost at +faceWidth/2. The label's cx_along_face_mm is measured
      // from the LEFT of the face's image — which we (v1) take as the left
      // wall edge. Map to a position in [-faceWidth/2, +faceWidth/2].
      const cxMm = (item.cx_along_face_mm) * SCALE;
      const wMm = item.width_mm * SCALE;
      const hMm = item.height_mm * SCALE;
      const px = centreOffset[0] + along[0] * (cxMm - faceWidth / 2);
      const py = item.cy_world_mm * SCALE;
      const pz = centreOffset[2] + along[2] * (cxMm - faceWidth / 2);
      const epsX = normal[0] * 0.005;
      const epsZ = normal[2] * 0.005;
      const colour = labelColor({
        type: 'view_opening', attributes: { opening_kind: item.kind },
        geometry: { top_edge: [], bottom_edge: [] },
        status: 'readable', id: 'tmp',
      } as never);
      out.push(
        <mesh
          key={`${face.face}-${item.sources.join('-')}-${px.toFixed(3)}-${py.toFixed(3)}`}
          position={[px + epsX, py, pz + epsZ]}
          rotation={[0, Math.atan2(normal[0], normal[2]), 0]}
        >
          <planeGeometry args={[wMm, hMm]} />
          <meshStandardMaterial color={colour} side={THREE.DoubleSide} />
        </mesh>,
      );
    }
  }
  return <>{out}</>;
}

function HeightLine({ y, label, extent }: { y: number; label: string; extent: number }) {
  // Thin horizontal line; the label is omitted in v1 (drei.Text would
  // bloat the bundle).
  void label;
  return (
    <mesh position={[0, y, 0]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[extent, 0.01]} />
      <meshStandardMaterial color="#a16207" transparent opacity={0.5} />
    </mesh>
  );
}

function Compass({ north_angle, y, extent }: { north_angle: number; y: number; extent: number }) {
  const halfL = extent / 2;
  // North arrow: a thin red triangle from origin pointing toward +Z,
  // then rotated by north_angle around the Y axis.
  return (
    <group position={[0, y, 0]} rotation={[0, north_angle, 0]}>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, halfL / 2]}>
        <planeGeometry args={[0.05, halfL]} />
        <meshStandardMaterial color="#dc2626" />
      </mesh>
      <Triangle3 points={[
        [-0.15, 0, halfL - 0.3], [0.15, 0, halfL - 0.3], [0, 0, halfL + 0.15],
      ]} color="#dc2626" opacity={1} />
    </group>
  );
}

function SidePanel({ scene }: { scene: BuiltScene3D }) {
  return (
    <div className="px-3 py-3 space-y-3 text-[0.78rem]">
      <header>
        <div className="text-[0.65rem] uppercase tracking-wider text-muted font-medium">Preview</div>
        <h2 className="text-[0.95rem] font-semibold leading-snug">3D-Modell</h2>
        <p className="text-[0.7rem] text-muted">Read-only · Orbit-Kamera · y in mm</p>
      </header>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1">
          Geometrie
        </h3>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[0.72rem]">
          <dt className="text-muted">Breite</dt>
          <dd className="font-mono tabular-nums">{Math.round(scene.building.width_mm)} mm</dd>
          <dt className="text-muted">Tiefe</dt>
          <dd className="font-mono tabular-nums">{Math.round(scene.building.depth_mm)} mm</dd>
          <dt className="text-muted">Traufe</dt>
          <dd className="font-mono tabular-nums">{Math.round(scene.wall_top_y)} mm</dd>
          <dt className="text-muted">First</dt>
          <dd className="font-mono tabular-nums">{scene.ridge_y != null ? `${Math.round(scene.ridge_y)} mm` : '–'}</dd>
          <dt className="text-muted">Gelände</dt>
          <dd className="font-mono tabular-nums">{Math.round(scene.ground_y)} mm</dd>
          <dt className="text-muted">Wandstärke</dt>
          <dd className="font-mono tabular-nums">{scene.wall_thickness_mm} mm</dd>
        </dl>
      </section>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1">
          Konfidenz
        </h3>
        <ul className="text-[0.7rem] space-y-0.5">
          {(['footprint', 'walls', 'slabs', 'roof'] as const).map((k) => (
            <li key={k} className="flex items-center gap-1.5">
              <ConfidenceDot c={scene.building.confidence[k]} />
              <span className="capitalize">{k}</span>
              <span className="ml-auto text-muted">{scene.building.confidence[k]}</span>
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1">
          Öffnungen
        </h3>
        <ul className="text-[0.7rem] space-y-0.5">
          {scene.openings.map((f) => (
            <li key={f.face} className="flex justify-between">
              <span className="capitalize">{f.face}</span>
              <span className="font-mono">{f.items.length}</span>
            </li>
          ))}
        </ul>
      </section>
      {scene.missing.length > 0 && (
        <section>
          <h3 className="text-[0.7rem] uppercase tracking-wider text-amber-700 font-semibold mb-1">
            Was fehlt
          </h3>
          <ul className="text-[0.7rem] text-amber-800 space-y-0.5">
            {scene.missing.map((m) => <li key={m}>⚠ {m}</li>)}
          </ul>
        </section>
      )}
      <section>
        <Link to=".." relative="path" className="block text-[0.72rem] text-accent hover:underline">
          ← Zurück zum Haus
        </Link>
      </section>
    </div>
  );
}

function ConfidenceDot({ c }: { c: 'solid' | 'approximate' | 'guessed' | 'missing' }) {
  const cls = c === 'solid' ? 'bg-emerald-500'
    : c === 'approximate' ? 'bg-amber-400'
    : c === 'guessed' ? 'bg-zinc-400'
    : 'bg-red-400';
  return <span className={`inline-block w-2 h-2 rounded-full ${cls}`} />;
}
