import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import JSZip from 'jszip';
import { fetchLabels, useResource } from '../api/client';
import type { Label, LabelScope, SceneLabels } from '../api/types';
import { Shell } from '../components/layout/Shell';
import { Breadcrumb } from '../components/layout/Breadcrumb';
import { computeRectification, rectifyLabel, type Affine } from '../lib/homography';

// M6 — Compilation preview.
//
// Two-pane view of the labeled scene:
//   LEFT  = raw scene image + only reference dimensioned_distance strokes
//           (the inputs to Model 1)
//   RIGHT = the same image transformed by the affine derived from those
//           references + all labels also transformed (the inputs to Model 2)
//
// "Download ZIP" packages both panes + the homography matrix into a single
// archive the user can hand off to the training pipeline.

export function PreviewPage() {
  const location = useLocation();
  const { key = '', file = '' } = useParams();
  const decodedFile = decodeURIComponent(file);
  const scope: LabelScope = location.pathname.startsWith('/dataset/') ? 'dataset' : 'house';
  const imageUrl =
    scope === 'dataset'
      ? `/static/dataset/${key}/${decodedFile}`
      : `/scene/${key}/${encodeURIComponent(decodedFile)}`;

  const { data, error, loading } = useResource(() => fetchLabels(scope, key, decodedFile), [scope, key, decodedFile]);

  const rect = useMemo(() => {
    if (!data) return null;
    return computeRectification(data.labels, { imageSize: data.image_size_px });
  }, [data]);

  return (
    <Shell
      breadcrumb={
        <Breadcrumb
          items={[
            { label: scope === 'dataset' ? 'Datensatz' : 'Alle Häuser', to: scope === 'dataset' ? '/dataset' : '/' },
            { label: key, to: scope === 'dataset' ? `/dataset/${key}` : `/house/${key}` },
            { label: `Vorschau & Export: ${decodedFile}` },
          ]}
        />
      }
      leftSidebar={
        <div className="px-3 py-3 space-y-4">
          <Link
            to={`${location.pathname.replace('/preview', '/annotate')}`}
            className="inline-block px-3 py-1.5 rounded-md text-[0.78rem] font-medium bg-accent text-white hover:opacity-90"
          >
            ← Zurück zum Editor
          </Link>

          <section>
            <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
              Status
            </h3>
            {loading && <p className="text-[0.78rem] text-muted">Lade…</p>}
            {error && <p className="text-[0.78rem] text-red-700">Fehler: {error.message}</p>}
            {rect && <StatusBlock rect={rect} labelsCount={data?.labels.length ?? 0} />}
          </section>

          {rect && data && (
            <ExportSection
              data={data}
              rect={rect}
              imageUrl={imageUrl}
              scope={scope}
              houseKey={key}
              sceneFile={decodedFile}
            />
          )}
        </div>
      }
    >
      <div className="grid grid-cols-2 gap-3 p-4 h-full bg-zinc-100">
        <Pane title="Original + Referenzen (Model 1 Ground Truth)" tone="left">
          {data && (
            <RawPane
              data={data}
              imageUrl={imageUrl}
              referencesOnly
            />
          )}
        </Pane>
        <Pane title="Entzerrt + Alle Labels (Model 2 Ground Truth)" tone="right">
          {data && rect && rect.status === 'ok' && (
            <RectifiedPane data={data} imageUrl={imageUrl} rect={rect} />
          )}
          {data && rect && rect.status !== 'ok' && (
            <InsufficientReferences rect={rect} />
          )}
        </Pane>
      </div>
    </Shell>
  );
}

// ── status block in sidebar ────────────────────────────────────────────────

function StatusBlock({ rect, labelsCount }: { rect: ReturnType<typeof computeRectification>; labelsCount: number }) {
  const colorMap = {
    ok: 'bg-green-100 text-green-900 border-green-200',
    insufficient_references: 'bg-amber-100 text-amber-900 border-amber-200',
    degenerate: 'bg-red-100 text-red-900 border-red-200',
  } as const;
  return (
    <div className={`px-2.5 py-2 rounded-md border text-[0.78rem] leading-snug ${colorMap[rect.status]}`}>
      <div className="font-semibold mb-1">
        {rect.status === 'ok' && '✓ Homographie berechnet'}
        {rect.status === 'insufficient_references' && '⚠ Noch keine Entzerrung möglich'}
        {rect.status === 'degenerate' && '✗ Bezüge degeneriert'}
      </div>
      {rect.reason && <p className="text-[0.72rem] mb-1">{rect.reason}</p>}
      {rect.status === 'ok' && (
        <ul className="text-[0.7rem] font-mono leading-snug">
          <li>Bezüge: {rect.computed_from.length}</li>
          <li>Rect-Size: {rect.rectified_size_px.join(' × ')}</li>
          <li>Skalierung: {rect.display_scale.toExponential(2)} px/mm</li>
          <li>RMS-Residuum: {rect.rms_residual_px.toFixed(2)} px</li>
          <li>Labels gesamt: {labelsCount}</li>
        </ul>
      )}
    </div>
  );
}

function InsufficientReferences({ rect }: { rect: ReturnType<typeof computeRectification> }) {
  return (
    <div className="h-full flex items-center justify-center px-6 text-center">
      <div className="max-w-md">
        <p className="text-zinc-700 mb-3 text-[0.85rem]">
          {rect.reason ?? 'Mehr Bezugsstrecken nötig.'}
        </p>
        <p className="text-[0.72rem] text-muted leading-relaxed">
          Im Editor mindestens 1 horizontale + 1 vertikale Bemaßte Strecke setzen,
          beide mit <code className="font-mono">is_reference</code>,{' '}
          <code className="font-mono">value_mm</code> und{' '}
          <code className="font-mono">target_orientation</code>.
        </p>
      </div>
    </div>
  );
}

// ── panes ───────────────────────────────────────────────────────────────────

function Pane({
  title,
  tone,
  children,
}: {
  title: string;
  tone: 'left' | 'right';
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col bg-white border border-border rounded-lg overflow-hidden">
      <header
        className={`flex-shrink-0 px-3 py-2 text-[0.72rem] font-semibold border-b border-border ${
          tone === 'left' ? 'bg-blue-50 text-blue-900' : 'bg-emerald-50 text-emerald-900'
        }`}
      >
        {title}
      </header>
      <div className="flex-1 overflow-auto bg-zinc-50 relative">{children}</div>
    </div>
  );
}

function RawPane({
  data,
  imageUrl,
  referencesOnly,
}: {
  data: SceneLabels;
  imageUrl: string;
  referencesOnly: boolean;
}) {
  const [w, h] = data.image_size_px;
  const refs = referencesOnly
    ? data.labels.filter((l) => l.type === 'dimensioned_distance' && l.attributes.is_reference)
    : data.labels;
  return (
    <div
      className="relative w-full h-full flex items-center justify-center"
    >
      <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="xMidYMid meet" className="w-full h-full">
        <image href={imageUrl} x={0} y={0} width={w} height={h} />
        {refs.map((l) => (
          <RawLabelGlyph key={l.id} label={l} />
        ))}
      </svg>
    </div>
  );
}

function RectifiedPane({
  data,
  imageUrl,
  rect,
}: {
  data: SceneLabels;
  imageUrl: string;
  rect: NonNullable<ReturnType<typeof computeRectification>>;
}) {
  const [rw, rh] = rect.rectified_size_px;
  const [iw, ih] = data.image_size_px;
  const A = rect.affine;

  // Transform every label through the affine.
  const transformed = useMemo(() => data.labels.map((l) => rectifyLabel(A, l)), [data.labels, A]);

  return (
    <div className="relative w-full h-full flex items-center justify-center">
      <svg viewBox={`0 0 ${rw} ${rh}`} preserveAspectRatio="xMidYMid meet" className="w-full h-full">
        {/* Image transformed via a 4x4 SVG transform that maps raw image coords
            into rectified output coords. Using two-step: place the image at
            its natural size, then transform it through the affine. */}
        <g transform={`matrix(${A.a} ${A.b} ${A.c} ${A.d} ${A.tx} ${A.ty})`}>
          <image href={imageUrl} x={0} y={0} width={iw} height={ih} />
        </g>
        {/* Labels are pre-transformed so they live in rectified coords directly. */}
        {transformed.map((l) => (
          <RawLabelGlyph key={l.id} label={l} />
        ))}
      </svg>
    </div>
  );
}

// ── lightweight glyph renderer (read-only; reuses logic from AnnotatePage) ──

function RawLabelGlyph({ label }: { label: Label }) {
  const color = LABEL_COLOR[label.type] ?? '#16a34a';
  const sw = 2;
  switch (label.type) {
    case 'dimensioned_distance': {
      const { start, end } = label.geometry;
      const isRef = label.attributes.is_reference;
      const c = isRef ? '#f59e0b' : color;
      return (
        <g>
          <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke={c} strokeWidth={isRef ? sw + 1 : sw} />
          <circle cx={start[0]} cy={start[1]} r={isRef ? 6 : 4} fill={c} />
          <circle cx={end[0]} cy={end[1]} r={isRef ? 6 : 4} fill={c} />
        </g>
      );
    }
    case 'dimension_number': {
      const a = label.geometry.anchor;
      if (!a) return null;
      return (
        <g>
          <circle cx={a[0]} cy={a[1]} r={6} fill={color + '33'} stroke={color} strokeWidth={sw} />
          <text x={a[0] + 10} y={a[1] - 6} fill={color} fontFamily="ui-monospace, monospace" fontSize={14}
                style={{ paintOrder: 'stroke', stroke: 'white', strokeWidth: 3 }}>
            {label.attributes.text}
          </text>
        </g>
      );
    }
    case 'wall': {
      const { start, end } = label.geometry;
      return <line x1={start[0]} y1={start[1]} x2={end[0]} y2={end[1]} stroke={color} strokeWidth={3} />;
    }
    case 'floorplan_opening': {
      const [aa, b, c, d] = label.geometry.quad;
      return <polygon points={`${aa.join(',')} ${b.join(',')} ${c.join(',')} ${d.join(',')}`} fill={color + '22'} stroke={color} strokeWidth={sw} />;
    }
    case 'view_opening': {
      const g = label.geometry as Record<string, unknown>;
      if (g.shape === 'circle') {
        const center = g.center as [number, number];
        const r = g.radius_px as number;
        return <circle cx={center[0]} cy={center[1]} r={r} fill={color + '22'} stroke={color} strokeWidth={sw} />;
      }
      if (g.shape === 'polygon') {
        const polygon = g.polygon as Array<[number, number]>;
        const path = `M ${polygon.map((p) => p.join(',')).join(' L ')} Z`;
        return <path d={path} fill={color + '22'} stroke={color} strokeWidth={sw} />;
      }
      const top_edge = g.top_edge as Array<[number, number]>;
      const bottom_edge = g.bottom_edge as Array<[number, number]>;
      const path = `M ${top_edge.map((p) => p.join(',')).join(' L ')}` +
                   ` L ${[...bottom_edge].reverse().map((p) => p.join(',')).join(' L ')} Z`;
      return <path d={path} fill={color + '22'} stroke={color} strokeWidth={sw} />;
    }
    case 'component_line': {
      const pts = label.geometry.polyline;
      return <polyline points={pts.map(p => p.join(',')).join(' ')} fill="none" stroke={color} strokeWidth={sw + 1} />;
    }
    case 'height_mark': {
      const [x, y] = label.geometry.anchor;
      return <polygon points={`${x},${y} ${x - 10},${y - 16} ${x + 10},${y - 16}`} fill={color + '33'} stroke={color} strokeWidth={sw} />;
    }
  }
}

const LABEL_COLOR: Record<Label['type'], string> = {
  dimensioned_distance: '#16a34a',
  dimension_number: '#0ea5e9',
  wall: '#7c3aed',
  floorplan_opening: '#ea580c',
  view_opening: '#ea580c',
  component_line: '#0891b2',
  height_mark: '#be185d',
};

// ── export ──────────────────────────────────────────────────────────────────

function ExportSection({
  data,
  rect,
  imageUrl,
  scope,
  houseKey,
  sceneFile,
}: {
  data: SceneLabels;
  rect: NonNullable<ReturnType<typeof computeRectification>>;
  imageUrl: string;
  scope: LabelScope;
  houseKey: string;
  sceneFile: string;
}) {
  const [building, setBuilding] = useState(false);
  const rawImgRef = useRef<HTMLImageElement | null>(null);

  // Preload the raw image so the export can read its pixels.
  useEffect(() => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.src = imageUrl;
    rawImgRef.current = img;
  }, [imageUrl]);

  const buildZip = async () => {
    setBuilding(true);
    try {
      const zip = new JSZip();
      const stem = sceneFile.replace(/\.[^.]+$/, '');

      // raw PNG (just fetch the original — already a PNG/AVIF/JPG on the server)
      const rawBlob = await (await fetch(imageUrl)).blob();
      const rawExt = rawBlob.type.includes('avif') ? 'avif' :
                     rawBlob.type.includes('png') ? 'png' :
                     rawBlob.type.includes('jpeg') ? 'jpg' : 'bin';
      zip.file(`raw/${stem}.${rawExt}`, rawBlob);

      // raw labels = only reference strokes (Model 1 target set)
      const rawLabels: SceneLabels = {
        ...data,
        labels: data.labels.filter((l) => l.type === 'dimensioned_distance' && l.attributes.is_reference),
      };
      zip.file(`raw/${stem}.labels.json`, JSON.stringify(rawLabels, null, 2));

      // rectified PNG — warp the raw through the affine onto a canvas
      const rectifiedPng = await renderRectifiedPng(imageUrl, rect.affine, rect.rectified_size_px);
      zip.file(`rectified/${stem}.png`, rectifiedPng);

      // rectified labels = ALL labels transformed (Model 2 target set)
      const rectifiedLabels: SceneLabels = {
        ...data,
        image_size_px: rect.rectified_size_px,
        labels: data.labels.map((l) => rectifyLabel(rect.affine, l)),
        homography: {
          matrix: rect.matrix,
          computed_from: rect.computed_from,
          rectified_size_px: rect.rectified_size_px,
          rms_residual_px: rect.rms_residual_px,
          status: rect.status,
        },
      };
      zip.file(`rectified/${stem}.labels.json`, JSON.stringify(rectifiedLabels, null, 2));

      // homography (separate file too)
      zip.file('homography.json', JSON.stringify({
        scope,
        scene_key: houseKey,
        scene_file: sceneFile,
        matrix: rect.matrix,
        computed_from: rect.computed_from,
        image_size_px: data.image_size_px,
        rectified_size_px: rect.rectified_size_px,
        rms_residual_px: rect.rms_residual_px,
        status: rect.status,
        generated_at: new Date().toISOString(),
      }, null, 2));

      const blob = await zip.generateAsync({ type: 'blob' });
      downloadBlob(blob, `${stem}.ground-truth.zip`);
    } finally {
      setBuilding(false);
    }
  };

  return (
    <section>
      <h3 className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold mb-1.5">
        Export
      </h3>
      <button
        type="button"
        onClick={buildZip}
        disabled={building || rect.status !== 'ok'}
        className={`w-full px-3 py-1.5 rounded-md text-[0.78rem] font-medium ${
          building || rect.status !== 'ok'
            ? 'bg-zinc-200 text-zinc-500 cursor-not-allowed'
            : 'bg-accent text-white hover:opacity-90'
        }`}
      >
        {building ? 'ZIP wird gebaut…' : 'Beide Ground Truths als ZIP'}
      </button>
      <p className="text-[0.65rem] text-muted mt-1.5 leading-snug">
        Enthält: <code className="font-mono">raw/&lt;stem&gt;.png + .labels.json</code>{' '}
        (Referenzen für Model 1), <code className="font-mono">rectified/&lt;stem&gt;.png + .labels.json</code>{' '}
        (alle Labels für Model 2), <code className="font-mono">homography.json</code>.
      </p>
    </section>
  );
}

async function renderRectifiedPng(
  imageUrl: string,
  A: Affine,
  size: [number, number],
): Promise<Blob> {
  const img = new Image();
  img.crossOrigin = 'anonymous';
  await new Promise<void>((res, rej) => {
    img.onload = () => res();
    img.onerror = () => rej(new Error(`failed to load ${imageUrl}`));
    img.src = imageUrl;
  });
  const c = document.createElement('canvas');
  c.width = size[0];
  c.height = size[1];
  const ctx = c.getContext('2d');
  if (!ctx) throw new Error('canvas 2d unavailable');
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(0, 0, c.width, c.height);
  // setTransform(a, b, c, d, e, f) → matrix [a c e; b d f; 0 0 1]
  ctx.setTransform(A.a, A.b, A.c, A.d, A.tx, A.ty);
  ctx.drawImage(img, 0, 0);
  return await new Promise<Blob>((res, rej) =>
    c.toBlob((b) => (b ? res(b) : rej(new Error('toBlob failed'))), 'image/png'),
  );
}

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
