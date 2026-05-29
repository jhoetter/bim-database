import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { router } from './router';
import { ToastProvider } from './lib/toast';
import { ExtractUndoProvider } from './lib/extract_undo';
import './index.css';

// One-time localStorage migrations.
//
// 1. Legacy `:synthetic:` → `:dataset:` rename (pre-R tracker).
// 2. R0.13 — sweep every `bim-db:*:house:*` entry. After R0 there's only
//    a dataset scope; house-scope state is orphaned and would point at
//    deleted on-disk files. Gated by a sentinel key so the sweep runs
//    once per browser and the toast (set on `__bim_house_sweep_count`)
//    is surfaced by the UI on first paint.
(() => {
  try {
    const renames: Array<[string, string]> = [];
    const houseKeys: string[] = [];
    for (let i = 0; i < window.localStorage.length; i++) {
      const k = window.localStorage.key(i);
      if (!k) continue;
      if (k.startsWith('bim-db:') && k.includes(':synthetic:')) {
        renames.push([k, k.replace(':synthetic:', ':dataset:')]);
      }
      if (k.startsWith('bim-db:') && k.includes(':house:')) {
        houseKeys.push(k);
      }
    }
    for (const [oldKey, newKey] of renames) {
      if (window.localStorage.getItem(newKey) === null) {
        const v = window.localStorage.getItem(oldKey);
        if (v !== null) window.localStorage.setItem(newKey, v);
      }
      window.localStorage.removeItem(oldKey);
    }
    const sentinel = 'bim-db:houses-removed:v1';
    if (!window.localStorage.getItem(sentinel) && houseKeys.length > 0) {
      for (const k of houseKeys) window.localStorage.removeItem(k);
      // Expose the count so the UI can surface a one-time toast on first
      // paint (read + cleared by the DatasetPage on mount).
      (window as unknown as { __bimHouseSweepCount?: number }).__bimHouseSweepCount = houseKeys.length;
    }
    window.localStorage.setItem(sentinel, '1');
  } catch { /* localStorage unavailable; nothing to migrate */ }
})();

const root = document.getElementById('root');
if (!root) throw new Error('Missing #root');

createRoot(root).render(
  <StrictMode>
    <ToastProvider>
      <ExtractUndoProvider>
        <RouterProvider router={router} />
      </ExtractUndoProvider>
    </ToastProvider>
  </StrictMode>
);
