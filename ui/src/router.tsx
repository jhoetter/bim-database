import { createBrowserRouter } from 'react-router';
import { App } from './App';
import { HousesPage } from './pages/HousesPage';
import { HousePage } from './pages/HousePage';
import { ScenePage } from './pages/ScenePage';

export const router = createBrowserRouter([
  {
    path: '/',
    Component: App,
    children: [
      { index: true, Component: HousesPage },
      { path: 'house/:key', Component: HousePage },
      { path: 'house/:key/scene/:file', Component: ScenePage },
    ],
  },
]);
