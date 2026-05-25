import { Link } from 'react-router';
import type { ReactNode } from 'react';

interface Crumb {
  label: ReactNode;
  to?: string;
}

export function Breadcrumb({ items }: { items: Crumb[] }) {
  return (
    <nav className="flex items-center gap-1.5 text-[0.8125rem] min-w-0" aria-label="Seitenposition">
      {items.map((c, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={i} className="flex items-center gap-1.5 min-w-0">
            {c.to && !isLast ? (
              <Link
                to={c.to}
                className="text-muted hover:text-zinc-900 truncate"
              >
                {c.label}
              </Link>
            ) : (
              <span
                className={`truncate ${isLast ? 'text-zinc-900 font-medium' : 'text-muted'}`}
              >
                {c.label}
              </span>
            )}
            {!isLast && <span className="text-zinc-300" aria-hidden="true">/</span>}
          </span>
        );
      })}
    </nav>
  );
}
