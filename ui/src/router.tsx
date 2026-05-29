import { lazy, Suspense } from 'react';
import { Navigate, createBrowserRouter, useParams } from 'react-router';

// Legacy /:key/extract or /:key/scene/:file → bounce to the new
// canonical URLs. Saves bookmarks from breaking.
function BackToHouse() {
  const { key = '' } = useParams();
  return <Navigate to={`/${key}`} replace />;
}
function RenameScenePreview() {
  const { key = '', file = '' } = useParams();
  return <Navigate to={`/${key}/scene/${encodeURIComponent(file)}/export`} replace />;
}

import { App } from './App';
import { DatasetPage } from './pages/DatasetPage';
import { AnnotatePage } from './pages/AnnotatePage';
import { IntakePage } from './pages/IntakePage';
import { ExtractPage } from './pages/ExtractPage';
import { ExportPreviewPage } from './pages/ExportPreviewPage';
import { ExportPage } from './pages/ExportPage';
import { SubmitPage } from './pages/SubmitPage';

// R5 — the 3D preview drags in three + react-three-fiber + drei (~950 KB).
// Split it so the rest of the app stays well under the bundle budget.
const Preview3DPage = lazy(
  () => import('./pages/Preview3DPage').then((m) => ({ default: m.Preview3DPage })),
);
const Preview3DSuspense = () => (
  <Suspense fallback={<p className="p-6 text-zinc-500">Lade 3D-Renderer…</p>}>
    <Preview3DPage />
  </Suspense>
);

// R0 — the catalog ("houses") side of the app has been stripped. Only the
// dataset path remains: PDF intake → bbox scene extraction → annotation →
// export.

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      // Root = the dataset list itself. The `dataset/` URL prefix was
      // dropped since this app only does one thing now.
      { index: true, Component: DatasetPage },
      { path: 'intake', Component: IntakePage },
      { path: 'submit', Component: SubmitPage },
      // The house IS the PDF + scene extraction. /:key opens the
      // extract view directly; the deeper level is per-scene annotation.
      { path: ':key', Component: ExtractPage },
      { path: ':key/export', Component: ExportPage },
      { path: ':key/scene/:file/annotate', Component: AnnotatePage },
      { path: ':key/scene/:file/export', Component: ExportPreviewPage },
      { path: ':key/3d', Component: Preview3DSuspense },
      // Back-compat: legacy URL prefixes from earlier iterations.
      { path: ':key/extract', element: <BackToHouse /> },
      { path: ':key/scene/:file/export-preview', element: <RenameScenePreview /> },
      { path: ':key/scene/:file', element: <BackToHouse /> },
      { path: 'dataset', element: <Navigate to="/" replace /> },
    ],
  },
]);
