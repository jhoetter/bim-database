import { createBrowserRouter } from 'react-router';
import { App } from './App';
import { HousesPage } from './pages/HousesPage';
import { HousePage } from './pages/HousePage';

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      { index: true, Component: HousesPage },
      // House detail: same component renders with or without the scene right-rail.
      { path: 'house/:key', Component: HousePage },
      { path: 'house/:key/scene/:file', Component: HousePage },
    ],
  },
]);
