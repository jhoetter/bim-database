import { Navigate, createBrowserRouter } from 'react-router';
import { App } from './App';
import { DatasetPage } from './pages/DatasetPage';
import { DatasetHousePage } from './pages/DatasetHousePage';
import { AnnotatePage } from './pages/AnnotatePage';
import { IntakePage } from './pages/IntakePage';
import { ExtractPage } from './pages/ExtractPage';

// R0 — the catalog ("houses") side of the app has been stripped. Only the
// dataset path remains: PDF intake → bbox scene extraction → annotation →
// export. See spec/end-to-end-readiness.md.

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      // R0.1 — root redirects to the dataset list.
      { index: true, element: <Navigate to="/dataset" replace /> },
      { path: 'dataset', Component: DatasetPage },
      { path: 'dataset/intake', Component: IntakePage },
      { path: 'dataset/:key/extract', Component: ExtractPage },
      { path: 'dataset/:key', Component: DatasetHousePage },
      { path: 'dataset/:key/scene/:file', Component: DatasetHousePage },
      { path: 'dataset/:key/scene/:file/annotate', Component: AnnotatePage },
    ],
  },
]);
