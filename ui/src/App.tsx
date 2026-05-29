import { Outlet } from 'react-router';

// R0 — Ontology context removed with the catalog. The annotation editor
// uses no app-wide context yet; this just renders the router outlet.
export function App() {
  return <Outlet />;
}
