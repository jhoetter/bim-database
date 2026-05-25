import {
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { Link } from 'react-router';

// Resizable left sidebar + topbar with breadcrumb slot + main content + optional
// right rail (slides in when `rightRail` is provided). Mirrors the AppShell
// pattern from ~/repos/bim-ai and ~/repos/bim-book — width persisted in
// localStorage, '[' toggles, narrow drag collapses to 0.

const LEFT_MIN = 200;
const LEFT_MAX = 480;
const LEFT_HIDE_THRESHOLD = 32;
const LEFT_DEFAULT = 280;
const LEFT_WIDTH_KEY = 'bim-db:left-sidebar:width';
const LEFT_OPEN_KEY = 'bim-db:left-sidebar:open';

const RIGHT_MIN = 320;
const RIGHT_MAX = 720;
const RIGHT_DEFAULT = 460;
const RIGHT_WIDTH_KEY = 'bim-db:right-sidebar:width';

interface ShellProps {
  /** Content for the resizable left sidebar (filters, facts, etc.). */
  leftSidebar: ReactNode;
  /** Topbar trailing slot — usually the breadcrumb / page title. */
  breadcrumb?: ReactNode;
  /** Topbar trailing right slot — extra controls (filter chips, actions). */
  topbarTrailing?: ReactNode;
  /** Main content. */
  children: ReactNode;
  /** Optional slide-in right rail (scene detail). When null/undefined, hidden. */
  rightRail?: ReactNode | null;
  /** Label for the rail toggle / aria. */
  rightRailLabel?: string;
  /** Callback when the user closes the right rail (e.g., click X). */
  onCloseRightRail?: () => void;
}

function readNumber(key: string, fallback: number, min: number, max: number): number {
  if (typeof window === 'undefined') return fallback;
  const raw = window.localStorage.getItem(key);
  const parsed = raw == null ? NaN : parseInt(raw, 10);
  if (Number.isNaN(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function readBool(key: string, fallback: boolean): boolean {
  if (typeof window === 'undefined') return fallback;
  const raw = window.localStorage.getItem(key);
  if (raw == null) return fallback;
  return raw === 'true';
}

export function Shell({
  leftSidebar,
  breadcrumb,
  topbarTrailing,
  children,
  rightRail,
  rightRailLabel = 'Detailpanel',
  onCloseRightRail,
}: ShellProps) {
  const [leftOpen, setLeftOpen] = useState(() => readBool(LEFT_OPEN_KEY, true));
  const [leftWidth, setLeftWidth] = useState(() =>
    readNumber(LEFT_WIDTH_KEY, LEFT_DEFAULT, LEFT_MIN, LEFT_MAX),
  );
  const [rightWidth, setRightWidth] = useState(() =>
    readNumber(RIGHT_WIDTH_KEY, RIGHT_DEFAULT, RIGHT_MIN, RIGHT_MAX),
  );
  const leftResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const rightResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    window.localStorage.setItem(LEFT_OPEN_KEY, String(leftOpen));
  }, [leftOpen]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable) return;
      if (e.key === '[') {
        e.preventDefault();
        setLeftOpen((v) => !v);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  const handleLeftResizeStart = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      leftResizeRef.current = { startX: event.clientX, startWidth: leftWidth };

      const doc = event.currentTarget.ownerDocument;
      doc.body.style.cursor = 'col-resize';
      doc.body.style.userSelect = 'none';

      let lastWidth = leftWidth;
      const onMove = (ev: globalThis.PointerEvent) => {
        const state = leftResizeRef.current;
        if (!state) return;
        const next = state.startWidth + ev.clientX - state.startX;
        if (next <= LEFT_HIDE_THRESHOLD) {
          setLeftOpen(false);
        } else {
          const clamped = Math.max(LEFT_MIN, Math.min(LEFT_MAX, next));
          lastWidth = clamped;
          setLeftWidth(clamped);
          setLeftOpen(true);
        }
      };
      const onUp = () => {
        leftResizeRef.current = null;
        doc.body.style.cursor = '';
        doc.body.style.userSelect = '';
        window.localStorage.setItem(LEFT_WIDTH_KEY, String(lastWidth));
        doc.removeEventListener('pointermove', onMove);
        doc.removeEventListener('pointerup', onUp);
      };
      doc.addEventListener('pointermove', onMove);
      doc.addEventListener('pointerup', onUp);
    },
    [leftWidth],
  );

  const handleRightResizeStart = useCallback(
    (event: PointerEvent<HTMLDivElement>) => {
      event.preventDefault();
      rightResizeRef.current = { startX: event.clientX, startWidth: rightWidth };

      const doc = event.currentTarget.ownerDocument;
      doc.body.style.cursor = 'col-resize';
      doc.body.style.userSelect = 'none';

      let lastWidth = rightWidth;
      const onMove = (ev: globalThis.PointerEvent) => {
        const state = rightResizeRef.current;
        if (!state) return;
        // Right rail: dragging leftward grows width.
        const next = state.startWidth - (ev.clientX - state.startX);
        const clamped = Math.max(RIGHT_MIN, Math.min(RIGHT_MAX, next));
        lastWidth = clamped;
        setRightWidth(clamped);
      };
      const onUp = () => {
        rightResizeRef.current = null;
        doc.body.style.cursor = '';
        doc.body.style.userSelect = '';
        window.localStorage.setItem(RIGHT_WIDTH_KEY, String(lastWidth));
        doc.removeEventListener('pointermove', onMove);
        doc.removeEventListener('pointerup', onUp);
      };
      doc.addEventListener('pointermove', onMove);
      doc.addEventListener('pointerup', onUp);
    },
    [rightWidth],
  );

  const hasRightRail = rightRail != null;
  const layoutStyle: CSSProperties = {
    // CSS variables let global.css consumers reference these widths if needed.
    ['--left-sidebar-w' as string]: `${leftWidth}px`,
    ['--right-rail-w' as string]: `${rightWidth}px`,
  };

  return (
    <div className="flex h-screen overflow-hidden" style={layoutStyle}>
      {/* Left sidebar */}
      <aside
        className={`flex-shrink-0 bg-zinc-50 border-r border-border overflow-hidden transition-[width,min-width,border-right-width] ${
          leftOpen ? '' : 'w-0 min-w-0 border-r-0'
        }`}
        style={{
          width: leftOpen ? leftWidth : 0,
          minWidth: leftOpen ? leftWidth : 0,
        }}
        aria-hidden={!leftOpen}
      >
        <div className="h-full flex flex-col">
          <div className="px-4 py-3 border-b border-border flex items-center gap-2">
            <Link to="/" className="text-[0.95rem] font-semibold tracking-tight">
              BIM House DB
            </Link>
          </div>
          <div className="flex-1 overflow-y-auto">{leftSidebar}</div>
        </div>
      </aside>

      {/* Left resize handle */}
      {leftOpen && (
        <div
          role="separator"
          aria-label="Sidebar-Breite ändern"
          aria-orientation="vertical"
          onPointerDown={handleLeftResizeStart}
          className="w-1.5 -mx-[3px] flex-shrink-0 cursor-col-resize relative z-10 hover:bg-accent/20"
        />
      )}

      {/* Main column */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
        <header className="h-11 flex-shrink-0 flex items-center gap-2 px-3 border-b border-border bg-white">
          <button
            type="button"
            onClick={() => setLeftOpen((v) => !v)}
            aria-label="Sidebar umschalten"
            title="Sidebar ([)"
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-muted hover:bg-zinc-100 hover:text-zinc-900"
          >
            <SidebarIcon />
          </button>
          <div className="flex-1 min-w-0">{breadcrumb}</div>
          {topbarTrailing}
        </header>
        <main className="flex-1 overflow-y-auto bg-white min-w-0">{children}</main>
      </div>

      {/* Right rail */}
      {hasRightRail && (
        <>
          <div
            role="separator"
            aria-label="Detailpanel-Breite ändern"
            aria-orientation="vertical"
            onPointerDown={handleRightResizeStart}
            className="w-1.5 -mx-[3px] flex-shrink-0 cursor-col-resize relative z-10 hover:bg-accent/20"
          />
          <aside
            className="flex-shrink-0 bg-white border-l border-border overflow-hidden flex flex-col"
            style={{ width: rightWidth, minWidth: rightWidth }}
            aria-label={rightRailLabel}
          >
            <div className="h-11 flex-shrink-0 flex items-center justify-between px-3 border-b border-border">
              <span className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">
                {rightRailLabel}
              </span>
              {onCloseRightRail && (
                <button
                  type="button"
                  onClick={onCloseRightRail}
                  aria-label="Detailpanel schließen"
                  className="w-7 h-7 inline-flex items-center justify-center rounded-md text-muted hover:bg-zinc-100 hover:text-zinc-900"
                >
                  ×
                </button>
              )}
            </div>
            <div className="flex-1 overflow-y-auto">{rightRail}</div>
          </aside>
        </>
      )}
    </div>
  );
}

function SidebarIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <rect x="1" y="1.5" width="4.5" height="13" rx="1" opacity="0.35" />
      <rect x="7" y="1.5" width="8" height="3" rx="0.75" />
      <rect x="7" y="6.5" width="8" height="3" rx="0.75" />
      <rect x="7" y="11.5" width="8" height="3" rx="0.75" />
    </svg>
  );
}
