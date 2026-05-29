import { createContext, useCallback, useContext, useState } from 'react';
import type { ReactNode } from 'react';

// U15 — shared toast surface, lifted out of AnnotatePage / ExtractPage.
// One provider mounted at the app root; pages call useToast() to push.
// The visual is the AnnotatePage variant (tone-aware bottom-right
// stack with auto-dismiss); ExtractPage's silent pip subsumes here.

export type ToastTone = 'info' | 'success' | 'warn' | 'error';

interface Toast {
  id: string;
  message: string;
  tone: ToastTone;
}

interface ToastContextValue {
  addToast: (message: string, tone?: ToastTone, ttlMs?: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Fallback — pages outside the provider get a no-op so a forgotten
    // mount doesn't crash. Logs once so we notice in dev.
    if (typeof window !== 'undefined' && !(window as { __toastWarned?: boolean }).__toastWarned) {
      (window as { __toastWarned?: boolean }).__toastWarned = true;
      // eslint-disable-next-line no-console
      console.warn('useToast() called outside ToastProvider');
    }
    return { addToast: () => undefined };
  }
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const addToast = useCallback<ToastContextValue['addToast']>((message, tone = 'info', ttlMs = 2500) => {
    const id = Math.random().toString(36).slice(2, 8);
    setToasts((prev) => [...prev, { id, message, tone }]);
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), ttlMs);
  }, []);
  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {toasts.length > 0 && (
        <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-1.5 pointer-events-none">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`text-[0.78rem] px-3 py-1.5 rounded-md shadow-lg ${
                t.tone === 'success' ? 'bg-emerald-700 text-white' :
                t.tone === 'warn'    ? 'bg-amber-700 text-white' :
                t.tone === 'error'   ? 'bg-red-700 text-white' :
                                       'bg-zinc-900 text-white'
              }`}
            >
              {t.message}
            </div>
          ))}
        </div>
      )}
    </ToastContext.Provider>
  );
}
