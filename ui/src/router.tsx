import { createBrowserRouter } from 'react-router';
import { App } from './App';
import { HousesPage } from './pages/HousesPage';
import { HousePage } from './pages/HousePage';
import { DatasetPage } from './pages/DatasetPage';
import { DatasetHousePage } from './pages/DatasetHousePage';
import { AnnotatePage } from './pages/AnnotatePage';
import { PreviewPage } from './pages/PreviewPage';

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      { index: true, Component: HousesPage },
      // House detail: same component renders with or without the scene right-rail.
      { path: 'house/:key', Component: HousePage },
      { path: 'house/:key/scene/:file', Component: HousePage },
      // Dataset (supervised-learning corpus). Drawings come in two flavors —
      // AI-generated (gpt-image-*) and real (scanned plans copied via
      // scripts/include_real_plans.py). The UI treats them uniformly.
      { path: 'dataset', Component: DatasetPage },
      { path: 'dataset/:key', Component: DatasetHousePage },
      { path: 'dataset/:key/scene/:file', Component: DatasetHousePage },
      // Annotation editor — same component handles both scopes; the URL
      // prefix (/dataset/... vs /house/...) decides which folder the
      // labels live in.
      { path: 'dataset/:key/scene/:file/annotate', Component: AnnotatePage },
      { path: 'house/:key/scene/:file/annotate', Component: AnnotatePage },
      // Compilation preview — side-by-side raw vs rectified ground truths
      // + zip download. M6 of spec/annotation-tool.md.
      { path: 'dataset/:key/scene/:file/preview', Component: PreviewPage },
      { path: 'house/:key/scene/:file/preview', Component: PreviewPage },
    ],
  },
]);
