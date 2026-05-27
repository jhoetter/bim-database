import {
  type CSSProperties,
  type PointerEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useRef,
  useState,
} from 'react';
import { Link, NavLink } from 'react-router';

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
  /**
   * 'reserved'         — right rail is a flex sibling; canvas shrinks when shown (default)
   * 'overlay-pinnable' — right rail floats over the canvas; canvas never reflows.
   *                      User can pin it back to reserved mode via header icon
   *                      (persisted in localStorage). Used by AnnotatePage so
   *                      selection never causes the drawing to reflow.
   */
  rightRailMode?: 'reserved' | 'overlay-pinnable';
}

const RAIL_PINNED_KEY = 'bim-db:annotate:rail-pinned';

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
  rightRailMode = 'reserved',
}: ShellProps) {
  const [leftOpen, setLeftOpen] = useState(() => readBool(LEFT_OPEN_KEY, true));
  const [leftWidth, setLeftWidth] = useState(() =>
    readNumber(LEFT_WIDTH_KEY, LEFT_DEFAULT, LEFT_MIN, LEFT_MAX),
  );
  const [rightWidth, setRightWidth] = useState(() =>
    readNumber(RIGHT_WIDTH_KEY, RIGHT_DEFAULT, RIGHT_MIN, RIGHT_MAX),
  );
  // overlay-pinnable: starts unpinned (overlay) by default; user pins via the
  // header icon. Persisted so the choice survives reloads.
  const [pinned, setPinned] = useState(() => readBool(RAIL_PINNED_KEY, false));
  const leftResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const rightResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);

  const hasRightRail = rightRail != null;
  const isOverlay = rightRailMode === 'overlay-pinnable' && !pinned;
  const railOccupiesLayout = hasRightRail && !isOverlay;

  const togglePinned = () => {
    const next = !pinned;
    setPinned(next);
    window.localStorage.setItem(RAIL_PINNED_KEY, String(next));
  };

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
          <div className="h-11 flex-shrink-0 px-4 border-b border-border flex items-center gap-2">
            <Link to="/" className="text-[0.95rem] font-semibold tracking-tight">
              BIM House DB
            </Link>
          </div>
          <SectionTabs />
          <div className="flex-1 overflow-y-auto overflow-x-hidden min-w-0">{leftSidebar}</div>
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

      {/* Main column. Wrapped in `relative` so the overlay rail can position
          against it; the main flex flow is unaffected by the overlay. */}
      <div className="flex-1 flex flex-col overflow-hidden min-w-0 relative">
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

        {/* Overlay variant of the right rail. Lives inside the main column so
            its `position: absolute` snaps to the main area, not the whole
            shell. */}
        {hasRightRail && isOverlay && (
          <aside
            className="absolute top-11 right-0 bottom-0 bg-white border-l border-border shadow-xl flex flex-col z-20 pointer-events-auto"
            style={{ width: rightWidth, minWidth: rightWidth }}
            aria-label={rightRailLabel}
          >
            <RightRailHeader
              label={rightRailLabel}
              pinned={pinned}
              onTogglePinned={togglePinned}
              onClose={onCloseRightRail}
              canPin
            />
            <div className="flex-1 overflow-y-auto">{rightRail}</div>
            {/* Resize handle on the LEFT edge of the overlay */}
            <div
              role="separator"
              aria-label="Detailpanel-Breite ändern"
              aria-orientation="vertical"
              onPointerDown={handleRightResizeStart}
              className="absolute top-0 bottom-0 left-0 w-1.5 -ml-[3px] cursor-col-resize hover:bg-accent/20"
            />
          </aside>
        )}
      </div>

      {/* Reserved-space variant of the right rail (default + pinned overlay).
          A flex sibling — its presence shrinks the main column. */}
      {railOccupiesLayout && (
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
            <RightRailHeader
              label={rightRailLabel}
              pinned={pinned}
              onTogglePinned={rightRailMode === 'overlay-pinnable' ? togglePinned : undefined}
              onClose={onCloseRightRail}
              canPin={rightRailMode === 'overlay-pinnable'}
            />
            <div className="flex-1 overflow-y-auto">{rightRail}</div>
          </aside>
        </>
      )}
    </div>
  );
}

// Shared header for both rail variants: label + pin toggle (only present in
// overlay-pinnable mode) + close button.
function RightRailHeader({
  label,
  pinned,
  onTogglePinned,
  onClose,
  canPin,
}: {
  label: string;
  pinned: boolean;
  onTogglePinned?: () => void;
  onClose?: () => void;
  canPin: boolean;
}) {
  return (
    <div className="h-11 flex-shrink-0 flex items-center justify-between px-3 border-b border-border">
      <span className="text-[0.7rem] uppercase tracking-wider text-muted font-semibold">{label}</span>
      <div className="flex items-center gap-1">
        {canPin && onTogglePinned && (
          <button
            type="button"
            onClick={onTogglePinned}
            aria-label={pinned ? 'Layout lösen' : 'Layout fest anheften'}
            title={pinned ? 'Layout lösen (Overlay)' : 'Layout fest anheften'}
            className={`w-7 h-7 inline-flex items-center justify-center rounded-md ${
              pinned ? 'bg-zinc-200 text-zinc-900' : 'text-muted hover:bg-zinc-100 hover:text-zinc-900'
            }`}
          >
            <PinIcon filled={pinned} />
          </button>
        )}
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Detailpanel schließen"
            className="w-7 h-7 inline-flex items-center justify-center rounded-md text-muted hover:bg-zinc-100 hover:text-zinc-900"
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}

function PinIcon({ filled }: { filled: boolean }) {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill={filled ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="1.5" aria-hidden="true">
      <path d="M8 1.5l1.8 4.5h3.7l-3.2 2.4 1.2 4.6L8 10.8 4.5 13l1.2-4.6L2.5 6h3.7L8 1.5z" />
    </svg>
  );
}

// Two top-level sections: the house catalog (default '/', source records and
// real architect plans) and the supervised-learning dataset (under '/dataset',
// a mix of AI-generated gpt-image-* drawings and real plans copied in from
// starred houses via scripts/include_real_plans.py).
function SectionTabs() {
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    `flex-1 text-center text-[0.72rem] font-medium px-2 py-1.5 rounded ${
      isActive
        ? 'bg-white text-zinc-900 shadow-sm'
        : 'text-muted hover:text-zinc-900'
    }`;
  return (
    <div className="mx-3 mt-3 inline-flex bg-zinc-100 rounded-md p-0.5 gap-0.5">
      <NavLink to="/" end className={linkCls}>
        Häuser
      </NavLink>
      <NavLink to="/dataset" className={linkCls}>
        Datensatz
      </NavLink>
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
