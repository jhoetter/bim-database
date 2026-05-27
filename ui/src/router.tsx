import { createBrowserRouter } from 'react-router';
import { App } from './App';
import { HousesPage } from './pages/HousesPage';
import { HousePage } from './pages/HousePage';
import { SyntheticPage } from './pages/SyntheticPage';
import { SyntheticHousePage } from './pages/SyntheticHousePage';
import { AnnotatePage } from './pages/AnnotatePage';

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      { index: true, Component: HousesPage },
      // House detail: same component renders with or without the scene right-rail.
      { path: 'house/:key', Component: HousePage },
      { path: 'house/:key/scene/:file', Component: HousePage },
      // Synthetic drawings — separate section, generated via
      // scripts/generate_synthetic_drawings.py.
      { path: 'synthetic', Component: SyntheticPage },
      { path: 'synthetic/:key', Component: SyntheticHousePage },
      { path: 'synthetic/:key/scene/:file', Component: SyntheticHousePage },
      // Annotation editor — same component handles both scopes; the URL
      // prefix (/synthetic/... vs /house/...) decides which folder the
      // labels live in.
      { path: 'synthetic/:key/scene/:file/annotate', Component: AnnotatePage },
      { path: 'house/:key/scene/:file/annotate', Component: AnnotatePage },
    ],
  },
]);
