import { Outlet } from 'react-router';
import { OntologyProvider } from './api/ontology';

// The actual layout chrome lives in components/layout/Shell.tsx and is rendered
// per-route by each page. This shell only provides app-wide context.
export function App() {
  return (
    <OntologyProvider>
      <Outlet />
    </OntologyProvider>
  );
}
