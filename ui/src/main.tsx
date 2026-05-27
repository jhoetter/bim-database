import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { router } from './router';
import './index.css';

// One-time localStorage migration: bim-db keys with the old `:synthetic:`
// scope segment move to `:dataset:`. Idempotent — once migrated, the source
// key is removed. Safe to leave in place forever; cost is one O(n) scan
// of localStorage per page load (typically <100 keys).
(() => {
  try {
    const renames: Array<[string, string]> = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (!k) continue;
      if (k.startsWith('bim-db:') && k.includes(':synthetic:')) {
        renames.push([k, k.replace(':synthetic:', ':dataset:')]);
      }
    }
    for (const [oldKey, newKey] of renames) {
      // If the new key already exists, prefer it (user has used the new
      // UI since the rename) — but still remove the stale `:synthetic:` key.
      if (window.localStorage.getItem(newKey) === null) {
        const v = window.localStorage.getItem(oldKey);
        if (v !== null) window.localStorage.setItem(newKey, v);
      }
      window.localStorage.removeItem(oldKey);
    }
  } catch { /* localStorage unavailable; nothing to migrate */ }
})();

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root');

createRoot(root).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>
);
