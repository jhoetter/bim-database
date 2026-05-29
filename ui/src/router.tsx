import { lazy, Suspense } from 'react';
import { Navigate, createBrowserRouter } from 'react-router';
import { App } from './App';
import { DatasetPage } from './pages/DatasetPage';
import { DatasetHousePage } from './pages/DatasetHousePage';
import { AnnotatePage } from './pages/AnnotatePage';
import { IntakePage } from './pages/IntakePage';
import { ExtractPage } from './pages/ExtractPage';
import { ExportPreviewPage } from './pages/ExportPreviewPage';
import { ExportPage } from './pages/ExportPage';

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
// export. See spec/end-to-end-readiness.md.

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      // Root = the dataset list itself. The `dataset/` URL prefix was
      // dropped since this app only does one thing now.
      { index: true, Component: DatasetPage },
      { path: 'intake', Component: IntakePage },
      { path: ':key/extract', Component: ExtractPage },
      { path: ':key/export', Component: ExportPage },
      { path: ':key/scene/:file/export-preview', Component: ExportPreviewPage },
      { path: ':key/3d', Component: Preview3DSuspense },
      { path: ':key', Component: DatasetHousePage },
      { path: ':key/scene/:file', Component: DatasetHousePage },
      { path: ':key/scene/:file/annotate', Component: AnnotatePage },
      // Back-compat: /dataset/* URLs from before the rename still resolve.
      { path: 'dataset', element: <Navigate to="/" replace /> },
    ],
  },
]);
