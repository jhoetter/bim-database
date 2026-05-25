import { Link, Outlet } from 'react-router';

export function App() {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-border bg-white">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-4">
          <Link to="/" className="text-lg font-semibold tracking-tight">
            BIM House Database
          </Link>
          <span className="text-xs text-muted">
            data/houses/house-N/ — validated by schema/house.schema.json
          </span>
        </div>
      </header>
      <main className="flex-1">
        <Outlet />
      </main>
    </div>
  );
}
