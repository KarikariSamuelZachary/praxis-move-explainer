'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Image from 'next/image';
import Link from 'next/link';
import type { BoardApi } from '@/components/board/ChessBoard';
import type { EndgameBoardProps } from '@/components/board/EndgameBoard';
import DrillStatusPanel from '@/components/endgames/DrillStatusPanel';
import {
  EndgamePlayoutStatus,
  fetchEndgameWoodpeckerQueue,
  submitEndgameWoodpeckerMove,
  type EndgameMoveSubmission,
  type EndgameSubmitMove,
} from '@/lib/endgames';
import type {
  EndgameWoodpeckerAttemptResponse,
  EndgameWoodpeckerQueueEntry,
  Puzzle,
} from '@/types';

const ChessBoard = dynamic(() => import('@/components/board/ChessBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full max-w-[700px] animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

// The same full-resolution board the rated Endgame Trainer uses. Typed for
// the review queue's grade response so the resolved panel gets `attempt` /
// `scheduling` with their real shape.
const EndgameBoard = dynamic<
  EndgameBoardProps<EndgameWoodpeckerAttemptResponse>
>(() => import('@/components/board/EndgameBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full max-w-[700px] animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// The Puzzle tab and the Endgame tab are two genuinely separate queues, so
// they keep separate page state; only the review chrome (tabs, bird,
// auto-advance setting, exit affordance) is shared.
type ReviewTab = 'puzzles' | 'endgames';

type WoodpeckerEntry = {
  id: string;
  puzzle_id: string;
  theme: string;
  source_reason: string | null;
  due: string;
  state: number;
  reps: number;
  lapses: number;
  is_mastered: boolean;
};

type Feedback = 'idle' | 'correct' | 'mistake';

function ExitIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" viewBox="0 0 24 24">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

function CheckCircleIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="currentColor" opacity="0.18" />
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" />
      <path d="m7.5 12.5 3 3 6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function XCircleIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="currentColor" opacity="0.18" />
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" />
      <path d="m8.5 8.5 7 7M15.5 8.5l-7 7" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
    </svg>
  );
}

function NextIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}

function SettingsIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" viewBox="0 0 24 24">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0L6.2 6.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.09a2 2 0 0 1 1 1.74v.5a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.38a2 2 0 0 0-.73-2.73l-.15-.09a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function ProgressCells({ completed, total }: { completed: number; total: number }) {
  return (
    <div className="flex w-full gap-1.5">
      {Array.from({ length: total }).map((_, i) => {
        const filled = i < completed;
        return (
          <div
            key={i}
            className={`h-1.5 flex-1 rounded-full transition-colors ${
              filled ? 'bg-emerald-500' : 'bg-white/10'
            }`}
          />
        );
      })}
    </div>
  );
}

// The queue switcher's handle hangs off the navbar in the page's top-left
// corner: it costs no layout row (the old segmented row was 56px of
// permanent chrome), it always says which queue you are in (name + due
// badge), and it drops the two-queue menu on hover, focus or tap. On every
// load it peeks open for a few seconds so the other queue's badge is never
// a secret, then collapses to the handle.
const QUEUE_PEEK_MS = 6000;
const QUEUE_HOVER_OPEN_MS = 180;
const QUEUE_HOVER_CLOSE_MS = 300;
const QUEUE_TAB_STORAGE_KEY = 'woodpecker.activeQueue';
// The handle slides along the navbar's bottom edge; its parked position is
// kept in localStorage. QUEUE_MENU_WIDTH mirrors the menu's w-56 so the
// menu can flip sides before it runs off the right edge.
const QUEUE_X_STORAGE_KEY = 'woodpecker.switcherX';
const QUEUE_MENU_WIDTH = 224;
const QUEUE_MENU_MARGIN = 8;
const QUEUE_DRAG_THRESHOLD = 4;
const QUEUE_NUDGE_PX = 16;

/** The queue the user was last on, or null on the server / no storage. */
function readStoredQueueTab(): ReviewTab | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.localStorage.getItem(QUEUE_TAB_STORAGE_KEY);
    return stored === 'puzzles' || stored === 'endgames' ? stored : null;
  } catch {
    // Storage can be unavailable (private mode): start on Puzzles as before.
    return null;
  }
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

/** The x the handle was last dragged to, or null for the default corner. */
function readStoredSwitcherX(): number | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = window.localStorage.getItem(QUEUE_X_STORAGE_KEY);
    if (stored === null) return null;
    const parsed = Number.parseInt(stored, 10);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

function storeSwitcherX(x: number) {
  try {
    window.localStorage.setItem(QUEUE_X_STORAGE_KEY, String(Math.round(x)));
  } catch {
    // Non-fatal: the handle stays put for this visit.
  }
}

function clearStoredSwitcherX() {
  try {
    window.localStorage.removeItem(QUEUE_X_STORAGE_KEY);
  } catch {
    // Non-fatal.
  }
}

function storeQueueTab(tab: ReviewTab) {
  try {
    window.localStorage.setItem(QUEUE_TAB_STORAGE_KEY, tab);
  } catch {
    // Non-fatal: the switch still happens for this visit.
  }
}

/** The queue due badge: the same emerald signal as before, shared by the
 * handle and both menu rows. */
function DueBadge({ count }: { count: number | null }) {
  if (count === null || count <= 0) return null;
  return (
    <span className="inline-flex h-5 min-w-[1.25rem] items-center justify-center rounded-full bg-[#10b981] px-1 text-[11px] font-bold leading-none text-white">
      {count > 99 ? '99+' : count}
    </span>
  );
}

function ChevronDownIcon({ open }: { open: boolean }) {
  return (
    <svg
      className={`h-3.5 w-3.5 transition-transform duration-150 ${open ? 'rotate-180' : ''}`}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2.2"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      className="h-4 w-4"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2.4"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="m5 13 4 4L19 7" />
    </svg>
  );
}

/** One queue row inside the dropped menu (role=tab, like the old pills). */
function QueueOption({
  label,
  count,
  active,
  onSelect,
}: {
  label: string;
  count: number | null;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onSelect}
      className={`flex w-full items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm font-semibold transition ${
        active
          ? 'bg-[#f7e5c6] text-[#241206]'
          : 'text-[#f7e5c6]/80 hover:bg-white/5 hover:text-[#f7e5c6]'
      }`}
    >
      <span className="flex items-center gap-2">
        <span>{label}</span>
        <DueBadge count={count} />
      </span>
      {active && <CheckIcon />}
    </button>
  );
}

function QueueSwitcher({
  active,
  puzzleDue,
  endgameDue,
  onSelect,
}: {
  active: ReviewTab;
  puzzleDue: number | null;
  endgameDue: number | null;
  onSelect: (tab: ReviewTab) => void;
}) {
  // The peek starts dropped open (state initialized true), so the first
  // paint already shows both queues; this effect only schedules the
  // collapse. If the pointer is on it (or it holds focus), the interaction
  // owns the switcher and the peek never yanks it closed.
  const [open, setOpen] = useState(true);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pointerInsideRef = useRef(false);
  const focusInsideRef = useRef(false);
  // Any real interaction (hover, tap, focus) hands the switcher over to the
  // user: the auto-peek must never close a menu they just opened.
  const interactedRef = useRef(false);
  // --- slide-along-the-navbar state -------------------------------------
  // x === null means the CSS default corner; a number is a px offset inside
  // the page, applied inline after a drag / on restore.
  const [x, setX] = useState<number | null>(null);
  const [trackWidth, setTrackWidth] = useState<number | null>(null);
  const handleRef = useRef<HTMLButtonElement | null>(null);
  const xRef = useRef<number | null>(null);
  const dragRef = useRef<{
    pointerId: number;
    startPointerX: number;
    startLeft: number;
    min: number;
    max: number;
    moved: boolean;
  } | null>(null);
  // Suppresses the click a drag would otherwise deliver to the handle.
  const draggedRef = useRef(false);

  const clearOpenTimer = useCallback(() => {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);

  const clearCloseTimer = useCallback(() => {
    if (closeTimerRef.current) {
      clearTimeout(closeTimerRef.current);
      closeTimerRef.current = null;
    }
  }, []);

  const scheduleClose = useCallback(() => {
    clearOpenTimer();
    clearCloseTimer();
    closeTimerRef.current = setTimeout(() => {
      closeTimerRef.current = null;
      // The pointer/focus owning the switcher beats any pending close.
      if (pointerInsideRef.current || focusInsideRef.current) return;
      setOpen(false);
    }, QUEUE_HOVER_CLOSE_MS);
  }, [clearCloseTimer, clearOpenTimer]);

  const applyX = useCallback((next: number | null, persist: boolean) => {
    xRef.current = next;
    setX(next);
    if (persist && next !== null) storeSwitcherX(next);
  }, []);

  /** The slide track: the box the handle's `left` actually resolves against.
   *  That is its offsetParent (the positioned page root), NOT the padded
   *  max-width container it happens to sit inside -- measuring that made the
   *  right bound stop short by the page padding (and, on wide screens, by the
   *  whole centered max-width gutter). Flush bounds: the user asked to reach
   *  both ends of the navbar. */
  const measureBounds = useCallback(() => {
    const root = containerRef.current?.offsetParent ?? null;
    const width =
      root instanceof HTMLElement
        ? root.clientWidth
        : typeof window === 'undefined'
          ? 0
          : window.innerWidth;
    const handleWidth = handleRef.current?.offsetWidth ?? 0;
    return {
      width,
      min: 0,
      max: Math.max(0, width - handleWidth),
    };
  }, []);

  /** The handle's current left offset inside that same track (default corner
   *  included), used as the drag/keyboard baseline. */
  const measureLeft = useCallback(() => {
    const handle = handleRef.current;
    const root = containerRef.current?.offsetParent ?? null;
    if (!handle || !(root instanceof HTMLElement)) return 0;
    return (
      handle.getBoundingClientRect().left - root.getBoundingClientRect().left
    );
  }, []);

  // Restore the parked position. Post-hydration and post-paint on purpose:
  // SSR paints the default corner, then the handle slides to the spot the
  // user chose (and the rAF keeps the read/apply out of the effect body,
  // same as the measurement pass below).
  useEffect(() => {
    const raf = requestAnimationFrame(() => {
      const stored = readStoredSwitcherX();
      if (stored === null) return;
      const { min, max } = measureBounds();
      applyX(clamp(stored, min, max), false);
    });
    return () => cancelAnimationFrame(raf);
  }, [applyX, measureBounds]);

  // Keep the bounds current: a resized window must neither strand the
  // handle off-screen nor leave its menu overflowing the right edge.
  useEffect(() => {
    const measure = () => {
      const { min, max, width } = measureBounds();
      setTrackWidth(width);
      const current = xRef.current;
      if (current === null) return;
      const clamped = clamp(current, min, max);
      if (clamped !== current) applyX(clamped, true);
    };
    // Async first pass: a post-paint measurement, not an effect-body write.
    const raf = requestAnimationFrame(measure);
    window.addEventListener('resize', measure);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('resize', measure);
    };
  }, [applyX, measureBounds]);

  // Peek once per mount: dropped open for a few seconds, then collapsed.
  // The open state starts true; only the collapse needs scheduling.
  useEffect(() => {
    const timer = setTimeout(() => {
      if (
        interactedRef.current ||
        pointerInsideRef.current ||
        focusInsideRef.current
      ) {
        return;
      }
      setOpen(false);
    }, QUEUE_PEEK_MS);
    return () => clearTimeout(timer);
  }, []);

  // Touch / pointer-away: a press outside the switcher closes the menu.
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    return () => document.removeEventListener('pointerdown', onPointerDown);
  }, [open]);

  // Unmount cleanup for the hover timers (the peek timer cleans up above).
  useEffect(
    () => () => {
      if (openTimerRef.current) clearTimeout(openTimerRef.current);
      if (closeTimerRef.current) clearTimeout(closeTimerRef.current);
    },
    []
  );

  const onHandlePointerDown = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      if (event.pointerType === 'mouse' && event.button !== 0) return;
      clearOpenTimer();
      clearCloseTimer();
      draggedRef.current = false;
      const { min, max } = measureBounds();
      dragRef.current = {
        pointerId: event.pointerId,
        startPointerX: event.clientX,
        startLeft: xRef.current ?? measureLeft(),
        min,
        max,
        moved: false,
      };
      event.currentTarget.setPointerCapture(event.pointerId);
    },
    [clearCloseTimer, clearOpenTimer, measureBounds, measureLeft]
  );

  const onHandlePointerMove = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      const dx = event.clientX - drag.startPointerX;
      if (!drag.moved && Math.abs(dx) < QUEUE_DRAG_THRESHOLD) return;
      drag.moved = true;
      draggedRef.current = true;
      // A drag is not a hover: the menu gets out of the way.
      setOpen(false);
      applyX(clamp(drag.startLeft + dx, drag.min, drag.max), false);
    },
    [applyX]
  );

  const onHandlePointerUp = useCallback(
    (event: React.PointerEvent<HTMLButtonElement>) => {
      const drag = dragRef.current;
      if (!drag || drag.pointerId !== event.pointerId) return;
      dragRef.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      if (drag.moved && xRef.current !== null) {
        storeSwitcherX(xRef.current);
      }
    },
    []
  );

  const onHandleClick = useCallback(() => {
    if (draggedRef.current) {
      // The click every drag delivers: consume it instead of toggling.
      draggedRef.current = false;
      return;
    }
    interactedRef.current = true;
    setOpen((current) => !current);
  }, []);

  const onHandleDoubleClick = useCallback(() => {
    // Back to the default corner, with no lingering menu.
    clearStoredSwitcherX();
    xRef.current = null;
    setX(null);
    setOpen(false);
  }, []);

  const handleSelect = useCallback(
    (tab: ReviewTab) => {
      onSelect(tab);
      setOpen(false);
    },
    [onSelect]
  );

  const activeLabel = active === 'puzzles' ? 'Puzzles' : 'Endgames';
  const activeDue = active === 'puzzles' ? puzzleDue : endgameDue;
  // Flip the menu to the handle's right edge before it would run off the
  // page: a handle parked near the right corner must still open fully.
  const menuRight =
    x !== null &&
    trackWidth !== null &&
    x + QUEUE_MENU_WIDTH + QUEUE_MENU_MARGIN > trackWidth;

  return (
    // z-[35]: the handle must slide OVER the board's settings gear (z-30,
    // which ties on z and wins on DOM order) while staying UNDER the fixed
    // nav and its dropdowns (z-40/50), so a parked handle can never cover
    // the profile menu or the bell.
    <div
      ref={containerRef}
      className={`absolute top-2 z-[35] ${x === null ? 'left-6 lg:left-10' : ''}`}
      style={x === null ? undefined : { left: `${x}px` }}
      onPointerEnter={() => {
        // A drag owns the handle; hover must not fight it.
        if (dragRef.current) return;
        pointerInsideRef.current = true;
        interactedRef.current = true;
        clearCloseTimer();
        if (openTimerRef.current) return;
        openTimerRef.current = setTimeout(() => {
          openTimerRef.current = null;
          setOpen(true);
        }, QUEUE_HOVER_OPEN_MS);
      }}
      onPointerLeave={(event) => {
        pointerInsideRef.current = false;
        // A touch pointer leaves the instant its tap ends; those menus stay
        // up until an outside tap (handled below), Escape, or a selection.
        if (event.pointerType === 'touch') return;
        scheduleClose();
      }}
      onFocus={() => {
        focusInsideRef.current = true;
        interactedRef.current = true;
        clearOpenTimer();
        clearCloseTimer();
        setOpen(true);
      }}
      onBlur={(event) => {
        if (containerRef.current?.contains(event.relatedTarget as Node)) return;
        focusInsideRef.current = false;
        scheduleClose();
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          setOpen(false);
          return;
        }
        // Arrow keys slide the parked handle while the handle itself holds
        // focus -- the keyboard equivalent of dragging.
        if (
          event.target !== handleRef.current ||
          (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')
        ) {
          return;
        }
        event.preventDefault();
        interactedRef.current = true;
        const { min, max } = measureBounds();
        const current = xRef.current ?? measureLeft();
        applyX(
          clamp(
            current +
              (event.key === 'ArrowRight' ? QUEUE_NUDGE_PX : -QUEUE_NUDGE_PX),
            min,
            max
          ),
          true
        );
      }}
    >
      <button
        ref={handleRef}
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        aria-controls="woodpecker-queue-menu"
        onPointerDown={onHandlePointerDown}
        onPointerMove={onHandlePointerMove}
        onPointerUp={onHandlePointerUp}
        onPointerCancel={onHandlePointerUp}
        onClick={onHandleClick}
        onDoubleClick={onHandleDoubleClick}
        className="flex h-8 cursor-grab touch-none select-none items-center gap-2 rounded-b-xl border border-t-0 border-black/50 bg-[linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] bg-cover bg-center px-3 text-xs font-semibold text-[#f7e5c6]/90 shadow-[0_6px_18px_rgba(0,0,0,0.35)] transition hover:text-[#f7e5c6] active:cursor-grabbing"
      >
        <span>{activeLabel}</span>
        <DueBadge count={activeDue} />
        <ChevronDownIcon open={open} />
      </button>

      {open && (
        <div
          id="woodpecker-queue-menu"
          role="tablist"
          aria-label="Woodpecker review queues"
          className={`absolute ${menuRight ? 'right-0' : 'left-0'} top-full mt-1 w-56 overflow-hidden rounded-xl border border-black/50 bg-[linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] bg-cover bg-center p-1.5 shadow-[0_16px_38px_rgba(0,0,0,0.48),inset_0_1px_0_rgba(255,255,255,0.08)] [animation:profile-menu-in_160ms_ease-out] motion-reduce:animate-none`}
        >
          <QueueOption
            label="Puzzles"
            count={puzzleDue}
            active={active === 'puzzles'}
            onSelect={() => handleSelect('puzzles')}
          />
          <QueueOption
            label="Endgames"
            count={endgameDue}
            active={active === 'endgames'}
            onSelect={() => handleSelect('endgames')}
          />
        </div>
      )}
    </div>
  );
}

function getSideToMove(puzzle: Puzzle | null) {
  if (!puzzle) return 'White';
  return (puzzle.fen || '').split(/\s+/)[1] === 'b' ? 'Black' : 'White';
}

function formatTheme(theme: string): string {
  // Convert Lichess camelCase theme keys ('mateIn2', 'xRayAttack') to
  // readable labels ('Mate in 2', 'X ray attack').
  const spaced = theme
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([a-zA-Z])([0-9])/g, '$1 $2')
    .replace(/([0-9])([a-zA-Z])/g, '$1 $2')
    .trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

function formatSourceReason(reason: string): string {
  switch (reason) {
    case 'wrong_answer':
      return 'You missed this one';
    case 'slow_solution':
      return 'You solved it slowly';
    default:
      return formatTheme(reason);
  }
}

export default function WoodpeckerPage() {
  const [activeTab, setActiveTab] = useState<ReviewTab>('puzzles');

  const boardApi = useRef<BoardApi | null>(null);
  const startTimeRef = useRef<number>(0);
  const advanceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [queue, setQueue] = useState<WoodpeckerEntry[] | null>(null);
  const [puzzles, setPuzzles] = useState<Record<string, Puzzle>>({});
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Feedback>('idle');
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [completedCount, setCompletedCount] = useState(0);
  const settingsRef = useRef<HTMLDivElement>(null);

  // --- Endgame review queue (separate table, separate endpoints) ---------
  const [endgameQueue, setEndgameQueue] = useState<
    EndgameWoodpeckerQueueEntry[] | null
  >(null);
  const [endgameLoading, setEndgameLoading] = useState(true);
  const [endgameError, setEndgameError] = useState<string | null>(null);
  const [endgameIndex, setEndgameIndex] = useState(0);
  const [endgameResult, setEndgameResult] =
    useState<EndgameWoodpeckerAttemptResponse | null>(null);
  const [endgamePlayout, setEndgamePlayout] =
    useState<EndgamePlayoutStatus | null>(null);
  // Bumped by "Retry": remounts the board at the entry's initial position
  // while the entry itself stays current.
  const [endgameRetryKey, setEndgameRetryKey] = useState(0);
  // True while the current replay is a "Retry": its graded moves still
  // resolve the card for the verdict, but the backend writes no FSRS
  // transition and no attempt row.
  const [endgameIsRetry, setEndgameIsRetry] = useState(false);
  // The endgame review tab's two assists: the running hint/reveal count (sent
  // with every review move; a hinted pass is scheduled as not-clean on
  // resolution) plus one request counter per button.
  const [endgameHintsUsed, setEndgameHintsUsed] = useState(0);
  const [endgameHintRequest, setEndgameHintRequest] = useState(0);
  const [endgameSolutionRequest, setEndgameSolutionRequest] = useState(0);
  const [endgameIsThinking, setEndgameIsThinking] = useState(false);
  const [endgameCompletedCount, setEndgameCompletedCount] = useState(0);
  const endgameDrillStartedAtRef = useRef(0);
  const endgameAdvanceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );

  const currentEntry = queue?.[currentIndex] ?? null;
  const currentPuzzle = currentEntry ? puzzles[currentEntry.puzzle_id] : null;
  const sideToMove = getSideToMove(currentPuzzle ?? null);

  const total = queue?.length ?? 0;
  const remaining = Math.max(total - completedCount, 0);
  const estimatedMinutes = Math.max(1, Math.ceil(remaining * 0.75));
  const cellTotal = Math.max(total, 1);

  const currentEndgameEntry = endgameQueue?.[endgameIndex] ?? null;
  const endgamePosition = currentEndgameEntry?.position ?? null;
  const endgameTotal = endgameQueue?.length ?? 0;
  const endgameRemaining = Math.max(endgameTotal - endgameCompletedCount, 0);
  const endgameEstimatedMinutes = Math.max(1, Math.ceil(endgameRemaining * 1.5));
  const endgameCellTotal = Math.max(endgameTotal, 1);

  // Tab badges: what is still left in each due queue, derived from the same
  // loaded queue the tab renders -- never a separate count of its own.
  const puzzleDue = queue ? remaining : null;
  const endgameDue = endgameQueue ? endgameRemaining : null;

  const clearAdvanceTimeout = useCallback(() => {
    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
      advanceTimeoutRef.current = null;
    }
  }, []);

  const clearEndgameAdvanceTimeout = useCallback(() => {
    if (endgameAdvanceTimeoutRef.current) {
      clearTimeout(endgameAdvanceTimeoutRef.current);
      endgameAdvanceTimeoutRef.current = null;
    }
  }, []);

  const loadQueue = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const queueRequestStartedAt = performance.now();
      console.info('[WOODPECKER_PROFILE] queue_request_start', {
        timestamp: new Date().toISOString(),
      });
      const res = await fetch('/api/woodpecker/queue', { cache: 'no-store' });
      console.info('[WOODPECKER_PROFILE] queue_request_resolved', {
        timestamp: new Date().toISOString(),
        elapsed_ms: Number((performance.now() - queueRequestStartedAt).toFixed(2)),
        status: res.status,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || body.error || `Failed to load queue (${res.status})`);
      }
      const entries: WoodpeckerEntry[] = await res.json();
      setQueue(entries);
      setCurrentIndex(0);
      setCompletedCount(0);
      setFeedback('idle');

      const uniqueIds = Array.from(new Set(entries.map((e) => e.puzzle_id)));
      const fetched: Record<string, Puzzle> = {};
      const fanoutStartedAt = performance.now();
      console.info('[WOODPECKER_PROFILE] detail_fanout_start', {
        timestamp: new Date().toISOString(),
        request_count: uniqueIds.length,
      });
      await Promise.all(
        uniqueIds.map(async (id) => {
          const requestStartedAt = performance.now();
          const r = await fetch(`/api/puzzles/${encodeURIComponent(id)}`, { cache: 'no-store' });
          console.info('[WOODPECKER_PROFILE] detail_request_resolved', {
            timestamp: new Date().toISOString(),
            puzzle_id: id,
            elapsed_ms: Number((performance.now() - requestStartedAt).toFixed(2)),
            fanout_elapsed_ms: Number((performance.now() - fanoutStartedAt).toFixed(2)),
            status: r.status,
          });
          if (!r.ok) return;
          const data = await r.json();
          const setupMove = data.moves?.[0];
          let normalizedFen = data.fen;
          let solutionMoves: string[] = data.moves ?? [];
          let previousMove: string | undefined;
          if (setupMove) {
            try {
              const { Chess } = await import('chess.js');
              const chess = new Chess(data.fen);
              chess.move({
                from: setupMove.slice(0, 2),
                to: setupMove.slice(2, 4),
                promotion: setupMove[4] || undefined,
              });
              normalizedFen = chess.fen();
              solutionMoves = (data.moves as string[]).slice(1);
              previousMove = setupMove;
            } catch {
              normalizedFen = data.fen;
              solutionMoves = data.moves ?? [];
            }
          }
          fetched[id] = {
            id: data.id,
            fen: normalizedFen,
            initialFen: data.fen,
            moves: solutionMoves,
            rating: data.rating,
            themes: data.themes,
            gameUrl: data.gameUrl,
            previousMove,
          };
        })
      );
      console.info('[WOODPECKER_PROFILE] detail_fanout_complete', {
        timestamp: new Date().toISOString(),
        elapsed_ms: Number((performance.now() - fanoutStartedAt).toFixed(2)),
        request_count: uniqueIds.length,
      });
      setPuzzles(fetched);
    } catch (e) {
      console.error('Woodpecker queue load failed:', e);
      setError(e instanceof Error ? e.message : 'Failed to load queue');
    } finally {
      setIsLoading(false);
    }
  }, []);

  // The endgame queue is loaded alongside the puzzle queue so both tab
  // badges are exact from the first paint; a due card's drill payload is
  // nested in the entry, so there is no per-position detail fanout.
  const loadEndgameQueue = useCallback(async () => {
    setEndgameLoading(true);
    setEndgameError(null);
    try {
      const entries = await fetchEndgameWoodpeckerQueue();
      setEndgameQueue(entries);
      setEndgameIndex(0);
      setEndgameCompletedCount(0);
      setEndgameResult(null);
      setEndgamePlayout(null);
      setEndgameHintsUsed(0);
      setEndgameHintRequest(0);
      setEndgameSolutionRequest(0);
      setEndgameIsRetry(false);
      setEndgameIsThinking(false);
      endgameDrillStartedAtRef.current = Date.now();
    } catch (e) {
      console.error('Endgame review queue load failed:', e);
      setEndgameError(
        e instanceof Error ? e.message : 'Failed to load endgame reviews'
      );
    } finally {
      setEndgameLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(() => {
    loadEndgameQueue();
  }, [loadEndgameQueue]);

  // Restore the queue the user was last on. Deliberately an effect rather
  // than a lazy initializer: localStorage does not exist during SSR, and
  // reading it in the first render would mismatch the server's HTML.
  useEffect(() => {
    const stored = readStoredQueueTab();
    if (stored) setActiveTab(stored);
  }, []);

  useEffect(() => {
    if (!isSettingsOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (settingsRef.current && !settingsRef.current.contains(event.target as Node)) {
        setIsSettingsOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setIsSettingsOpen(false);
    };

    document.addEventListener('pointerdown', handlePointerDown);
    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isSettingsOpen]);

  useEffect(() => {
    startTimeRef.current = Date.now();
    return () => clearAdvanceTimeout();
  }, [currentIndex, clearAdvanceTimeout]);

  useEffect(() => {
    endgameDrillStartedAtRef.current = Date.now();
    return () => clearEndgameAdvanceTimeout();
  }, [endgameIndex, clearEndgameAdvanceTimeout]);

  useEffect(() => () => clearEndgameAdvanceTimeout(), [clearEndgameAdvanceTimeout]);

  const advanceToNext = useCallback(() => {
    clearAdvanceTimeout();
    if (!queue) return;
    if (currentIndex + 1 >= queue.length) {
      setFeedback('idle');
      setCompletedCount(queue.length);
      setCurrentIndex(queue.length);
      return;
    }
    setCompletedCount((c) => c + 1);
    setCurrentIndex((i) => i + 1);
    setFeedback('idle');
    boardApi.current?.resetPuzzle();
  }, [clearAdvanceTimeout, currentIndex, queue]);

  const recordAttempt = useCallback(
    async (solved: boolean) => {
      if (!currentEntry) return;
      try {
        await fetch('/api/woodpecker/attempts', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            entry_id: currentEntry.id,
            solved_correctly: solved,
            time_taken_ms: Date.now() - startTimeRef.current,
          }),
        });
      } catch (err) {
        console.error('Failed to record attempt:', err);
      }
    },
    [currentEntry]
  );

  const handlePuzzleSolved = useCallback(() => {
    if (feedback !== 'idle') return;
    setFeedback('correct');
    recordAttempt(true);
    if (autoAdvance) {
      clearAdvanceTimeout();
      advanceTimeoutRef.current = setTimeout(advanceToNext, 1500);
    }
  }, [advanceToNext, autoAdvance, clearAdvanceTimeout, feedback, recordAttempt]);

  const handlePuzzleFailed = useCallback(() => {
    if (feedback !== 'idle') return;
    setFeedback('mistake');
    recordAttempt(false);
    if (autoAdvance) {
      clearAdvanceTimeout();
      advanceTimeoutRef.current = setTimeout(advanceToNext, 1500);
    }
  }, [advanceToNext, autoAdvance, clearAdvanceTimeout, feedback, recordAttempt]);

  const handlePuzzleEnd = useCallback(() => {
    // No-op: state transitions are driven by solved/failed handlers.
  }, []);

  const handleNextClick = useCallback(() => {
    clearAdvanceTimeout();
    if (feedback === 'idle') {
      setCompletedCount((c) => c + 1);
      setCurrentIndex((i) => i + 1);
    } else {
      advanceToNext();
    }
  }, [advanceToNext, clearAdvanceTimeout, feedback]);

  const handleExit = useCallback(() => {
    clearAdvanceTimeout();
  }, [clearAdvanceTimeout]);

  // --- Endgame review handlers -------------------------------------------

  const advanceEndgame = useCallback(() => {
    clearEndgameAdvanceTimeout();
    if (!endgameQueue) return;
    setEndgameResult(null);
    setEndgamePlayout(null);
    setEndgameHintsUsed(0);
    setEndgameHintRequest(0);
    setEndgameSolutionRequest(0);
    setEndgameIsRetry(false);
    if (endgameIndex + 1 >= endgameQueue.length) {
      setEndgameCompletedCount(endgameQueue.length);
      setEndgameIndex(endgameQueue.length);
      return;
    }
    setEndgameCompletedCount((c) => c + 1);
    setEndgameIndex((i) => i + 1);
  }, [clearEndgameAdvanceTimeout, endgameIndex, endgameQueue]);

  const handleEndgameResolved = useCallback(
    (resolved: EndgameWoodpeckerAttemptResponse) => {
      setEndgameResult(resolved);
      // Auto-advance a SOLVED review only. A failed review must hold on
      // screen: its resolved panel is where "Play it out" and "Retry"
      // live, and the old blanket 1500ms timer took them away before the
      // reviewer could act. A solved card has no actions, so continuing
      // there keeps the puzzle tab's cadence.
      if (autoAdvance && resolved.status === 'solved') {
        clearEndgameAdvanceTimeout();
        endgameAdvanceTimeoutRef.current = setTimeout(advanceEndgame, 1500);
      }
    },
    [advanceEndgame, autoAdvance, clearEndgameAdvanceTimeout]
  );

  /** "Retry": replay the current review card from its initial position as an
   *  UNRATED attempt -- the first resolution already scheduled the card. */
  const handleEndgameRetry = useCallback(() => {
    // A retry supersedes any pending auto-advance.
    clearEndgameAdvanceTimeout();
    setEndgameResult(null);
    setEndgamePlayout(null);
    setEndgameHintsUsed(0);
    setEndgameHintRequest(0);
    setEndgameSolutionRequest(0);
    setEndgameIsRetry(true);
    setEndgameIsThinking(false);
    endgameDrillStartedAtRef.current = Date.now();
    setEndgameRetryKey((key) => key + 1);
  }, [clearEndgameAdvanceTimeout]);

  const handleEndgameHintRevealed = useCallback(() => {
    // Counted on successful reveals only, from either assist ("Hint" or
    // "Show move"). Every review move from here carries the running count,
    // and the attempts route schedules a hint-assisted pass as not-clean
    // (FSRS Again) on resolution.
    setEndgameHintsUsed((count) => count + 1);
  }, []);

  const handleEndgameThinkingChange = useCallback((thinking: boolean) => {
    setEndgameIsThinking(thinking);
  }, []);

  const handleEndgameExit = useCallback(() => {
    clearEndgameAdvanceTimeout();
  }, [clearEndgameAdvanceTimeout]);

  // Leaving the endgames tab abandons whatever the board was showing. The
  // board remounts on return, so keeping a resolved result (or an active
  // playout) would leave the panel describing a position the board no longer
  // holds -- and a settled replay could be graded a second time. Advancing
  // is what auto-advance would have done anyway.
  const handleTabChange = useCallback(
    (tab: ReviewTab) => {
      setActiveTab(tab);
      // Remembered for the next visit, so the collapsed handle starts on the
      // queue the user actually uses.
      storeQueueTab(tab);
      setIsSettingsOpen(false);
      if (tab === 'puzzles' && endgameResult) {
        advanceEndgame();
      }
    },
    [advanceEndgame, endgameResult]
  );

  // The board is route-agnostic: the review tab injects the per-entry
  // attempts endpoint, and the accumulated replay time rides along on every
  // move (the server reads it only on the resolving move).
  const submitEndgameReviewMove = useCallback<
    EndgameSubmitMove<EndgameWoodpeckerAttemptResponse>
  >(
    (submission: EndgameMoveSubmission) => {
      if (!currentEndgameEntry) {
        return Promise.reject(new Error('No endgame review is loaded.'));
      }
      return submitEndgameWoodpeckerMove({
        entry_id: currentEndgameEntry.id,
        time_taken_ms: Math.max(0, Date.now() - endgameDrillStartedAtRef.current),
        ...submission,
      });
    },
    [currentEndgameEntry]
  );

  const queueIsEmpty = !isLoading && queue !== null && queue.length === 0;
  const queueFinished = !isLoading && queue !== null && currentIndex >= queue.length;

  const showResult = feedback !== 'idle';
  const hasQueueRemaining = !isLoading && queue !== null && !queueFinished && !queueIsEmpty;
  const showingBoard = !!currentPuzzle && hasQueueRemaining;
  const puzzleUnavailable = hasQueueRemaining && !currentPuzzle;

  const endgameQueueIsEmpty =
    !endgameLoading && endgameQueue !== null && endgameQueue.length === 0;
  const endgameQueueFinished =
    !endgameLoading && endgameQueue !== null && endgameIndex >= endgameQueue.length;

  const renderPuzzleReview = () => {
    if (isLoading) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
            <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
            <p className="text-white/60">Loading your reviews...</p>
          </div>
        </div>
      );
    }
    if (error) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} px-8 py-6 text-center text-white/80 shadow-2xl shadow-black/30`}>
            <p className="mb-4">{error}</p>
            <button
              onClick={loadQueue}
              className="rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold transition hover:bg-white/10"
            >
              Retry
            </button>
          </div>
        </div>
      );
    }
    if (queueIsEmpty || queueFinished) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} w-full max-w-md p-8 text-center shadow-2xl shadow-black/30`}>
            <Image
              src="/woodpecker-bird-v2.webp"
              alt=""
              width={80}
              height={80}
              className="mx-auto mb-4 h-20 w-20 object-contain"
            />
            <h2 className="mb-2 text-2xl font-bold text-[#f7e5c6]">
              {queueFinished ? 'Session complete!' : 'No reviews due'}
            </h2>
            <p className="mb-6 text-white/60">
              {queueFinished
                ? 'You cleared everything due today. Come back later for the next batch.'
                : 'You\'re all caught up. Add a puzzle from the Puzzles page to start a new cycle.'}
            </p>
            <div className="grid grid-cols-2 gap-3">
              <Link
                href="/puzzles"
                className="rounded-xl border border-white/20 bg-white/5 px-4 py-3 text-sm font-semibold transition hover:bg-white/10"
              >
                Go to Puzzles
              </Link>
              <button
                onClick={loadQueue}
                className="rounded-xl bg-[#10b981] px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition-colors hover:bg-emerald-400"
              >
                Refresh
              </button>
            </div>
          </div>
        </div>
      );
    }
    if (puzzleUnavailable) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} w-full max-w-md p-8 text-center shadow-2xl shadow-black/30`}>
            <h2 className="mb-2 text-2xl font-bold text-[#f7e5c6]">Puzzle unavailable</h2>
            <p className="mb-6 text-white/60">
              This review&apos;s puzzle couldn&apos;t be loaded. Skip it to continue your session.
            </p>
            <div className="flex justify-center gap-3">
              <button
                onClick={() => {
                  setCompletedCount((c) => c + 1);
                  setCurrentIndex((i) => i + 1);
                }}
                className="rounded-xl bg-[#10b981] px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition-colors hover:bg-emerald-400"
              >
                Skip &middot; {Math.min(currentIndex + 1, cellTotal)} / {cellTotal}
              </button>
            </div>
          </div>
        </div>
      );
    }
    if (!showingBoard) return null;

    return (
      <div className="grid h-full grid-cols-1 gap-6 lg:grid-cols-[18rem_minmax(0,1fr)_22rem] xl:grid-cols-[20rem_minmax(0,1fr)_22rem]">
        {/* ============== LEFT CARD ============== */}
        <section className="hidden min-h-0 min-w-0 lg:block">
          <div className={`${CARD_CLASS} flex h-full w-full flex-col justify-between p-6 shadow-2xl shadow-black/25`}>
            {/* Top: identity & progress */}
            <div className="flex flex-col items-center">
              <Image
                src="/woodpecker-bird-v2.webp"
                alt=""
                width={112}
                height={112}
                className="mb-5 h-[112px] w-[112px] object-contain"
              />
              <div className="text-[12px] font-bold uppercase tracking-[0.3em] text-[#f7e5c6]/60">
                Woodpecker
              </div>
              <div className="mt-2 text-[11px] uppercase tracking-wider text-white/40">Puzzle</div>
              <div className="mt-2 text-5xl font-bold leading-none text-[#f7e5c6]">
                {Math.min(currentIndex + 1, cellTotal)} <span className="text-white/40">/ {cellTotal}</span>
              </div>
              <div className="mt-6 w-full">
                <ProgressCells completed={currentIndex} total={cellTotal} />
              </div>
            </div>

            {/* Middle: remaining stats (centered) */}
            <div className="flex flex-col items-center text-center">
              <div className="text-[11px] uppercase tracking-[0.2em] text-white/50">Remaining</div>
              <div className="mt-2 text-6xl font-bold leading-none text-[#f7e5c6]">{remaining}</div>
              <div className="mt-3 text-sm text-white/50">
                Estimated time: {estimatedMinutes} min
              </div>
            </div>

            {/* Bottom: exit */}
            <div>
              <Link
                href="/puzzles"
                onClick={handleExit}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-[#f7e5c6]/30 bg-transparent px-4 py-3 text-sm font-semibold text-[#f7e5c6] transition hover:border-[#f7e5c6]/60 hover:bg-[#f7e5c6]/5"
              >
                <ExitIcon />
                Exit Review
              </Link>
            </div>
          </div>
        </section>

        {/* ============== CENTER: CHESSBOARD ============== */}
        <section className="min-h-0 min-w-0">
          <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
            <div className="w-full">
              <ChessBoard
                puzzle={currentPuzzle}
                onPuzzleSolved={handlePuzzleSolved}
                onPuzzleFailed={handlePuzzleFailed}
                onPuzzleEnd={handlePuzzleEnd}
                apiRef={boardApi}
              />
            </div>

            <div ref={settingsRef} className="absolute right-2 top-2 z-30 xl:left-full xl:right-auto xl:top-0 xl:ml-[2px]">
              <button
                type="button"
                onClick={() => setIsSettingsOpen((open) => !open)}
                className={`relative flex h-7 w-7 items-center justify-center rounded-md border text-[#f0e0c0] transition hover:scale-105 active:scale-95 ${
                  isSettingsOpen
                    ? 'border-[#d9b87c]/70 text-[#f7e5c6]'
                    : 'border-black/60 hover:border-[#d9b87c]/45'
                }`}
                style={{
                  borderRadius: '4px',
                  background:
                    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                  boxShadow:
                    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
                }}
                aria-label="Woodpecker settings"
                aria-haspopup="dialog"
                aria-expanded={isSettingsOpen}
                title="Woodpecker settings"
              >
                <SettingsIcon />
              </button>

              {isSettingsOpen && (
                <div
                  role="dialog"
                  aria-label="Woodpecker settings"
                  className="absolute right-0 top-full mt-2 w-60 rounded-xl border border-[#d9b87c]/30 bg-[#1b120d]/95 p-3 text-white shadow-[0_18px_42px_rgba(0,0,0,0.52),inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-xl"
                >
                  <div className="flex items-center justify-between border-b border-white/10 pb-2">
                    <span className="text-[10px] font-bold uppercase tracking-[0.24em] text-[#f7e5c6]/65">
                      Review settings
                    </span>
                    <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[#37be7e]">
                      {autoAdvance ? 'Auto' : 'Manual'}
                    </span>
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-4">
                    <div>
                      <div className="text-sm font-semibold text-[#f7e5c6]">Auto-advance</div>
                      <div className="mt-1 text-[11px] leading-4 text-white/45">
                        Continue after the review ends
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setAutoAdvance((enabled) => !enabled)}
                      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                        autoAdvance ? 'bg-[#10b981]' : 'bg-white/15'
                      }`}
                      aria-pressed={autoAdvance}
                      aria-label="Toggle auto-advance"
                    >
                      <span
                        className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${
                          autoAdvance ? 'translate-x-5' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>

                  {!autoAdvance && feedback !== 'idle' && (
                    <button
                      type="button"
                      onClick={handleNextClick}
                      className="mt-3 w-full rounded-lg bg-emerald-500 py-2.5 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition hover:bg-emerald-400"
                    >
                      Next Review
                    </button>
                  )}

                  <div className="mt-3 border-t border-white/10 pt-2 text-[10px] text-white/40">
                    {autoAdvance
                      ? 'Next review loads after a short pause.'
                      : 'Use Next Review when you are ready.'}
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ============== RIGHT CARD ============== */}
        <section className="hidden min-h-0 min-w-0 xl:block">
          <div className={`${CARD_CLASS} flex h-fit w-full flex-col gap-5 p-5 shadow-2xl shadow-black/25`}>
            {/* Top: result banner (after move) OR "your move" header */}
            {showResult ? (
              <div
                className={`flex flex-col gap-3 rounded-xl border p-4 ${
                  feedback === 'correct'
                    ? 'border-emerald-400/30 bg-emerald-500/10'
                    : 'border-rose-400/30 bg-rose-500/10'
                }`}
              >
                <div className="flex items-center gap-2.5">
                  {feedback === 'correct' ? (
                    <CheckCircleIcon className="h-5 w-5 shrink-0 text-emerald-400" />
                  ) : (
                    <XCircleIcon className="h-5 w-5 shrink-0 text-rose-400" />
                  )}
                  <span
                    className={`text-[11px] font-bold uppercase tracking-[0.25em] ${
                      feedback === 'correct' ? 'text-emerald-400' : 'text-rose-400'
                    }`}
                  >
                    Result
                  </span>
                </div>
                <h2
                  className={`text-2xl font-semibold leading-snug ${
                    feedback === 'correct' ? 'text-emerald-200' : 'text-rose-200'
                  }`}
                >
                  {feedback === 'correct' ? 'Nice - keep going' : 'Logged for review'}
                </h2>
              </div>
            ) : (
              <div className="flex flex-col gap-3">
                <div className="flex items-center gap-2.5">
                  <span
                    className={`inline-block h-2.5 w-2.5 rounded-full ${
                      sideToMove === 'White' ? 'bg-white' : 'bg-zinc-800 ring-1 ring-white/40'
                    }`}
                  />
                  <span className="text-[11px] font-bold uppercase tracking-[0.3em] text-[#f7e5c6]/60">
                    Your Move
                  </span>
                </div>
                <h2 className="text-3xl font-semibold leading-snug text-[#f7e5c6]">
                  Find the best move for {sideToMove}.
                </h2>
              </div>
            )}

            {/* Puzzle details */}
            <div className="flex flex-col gap-5">
              <div>
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                  This Puzzle
                </div>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {(currentPuzzle?.themes ?? []).map((t) => (
                    <span
                      key={t}
                      className="rounded-full border border-[#f7e5c6]/20 bg-[#f7e5c6]/5 px-2.5 py-1 text-xs font-medium text-[#f7e5c6]/80"
                    >
                      {formatTheme(t)}
                    </span>
                  ))}
                </div>
              </div>
              <div className="flex flex-col gap-2.5 border-t border-white/5 pt-4 text-sm">
                <div className="flex items-baseline justify-between">
                  <span className="text-white/40">Rating</span>
                  <span className="font-semibold text-[#f7e5c6]">
                    {currentPuzzle?.rating ?? '-'}
                  </span>
                </div>
                {currentEntry?.source_reason && (
                  <div className="flex items-baseline justify-between">
                    <span className="text-white/40">Added because</span>
                    <span className="text-xs font-medium text-white/70">
                      {formatSourceReason(currentEntry.source_reason)}
                    </span>
                  </div>
                )}
                </div>
            </div>

          </div>
        </section>
      </div>
    );
  };

  const renderEndgameReview = () => {
    if (endgameLoading) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
            <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
            <p className="text-white/60">Loading endgame reviews...</p>
          </div>
        </div>
      );
    }
    if (endgameError) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} px-8 py-6 text-center text-white/80 shadow-2xl shadow-black/30`}>
            <p className="mb-4">{endgameError}</p>
            <button
              onClick={loadEndgameQueue}
              className="rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold transition hover:bg-white/10"
            >
              Retry
            </button>
          </div>
        </div>
      );
    }
    if (endgameQueueIsEmpty || endgameQueueFinished) {
      return (
        <div className="flex h-[70vh] items-center justify-center">
          <div className={`${CARD_CLASS} w-full max-w-md p-8 text-center shadow-2xl shadow-black/30`}>
            <Image
              src="/woodpecker-bird-v2.webp"
              alt=""
              width={80}
              height={80}
              className="mx-auto mb-4 h-20 w-20 object-contain"
            />
            <h2 className="mb-2 text-2xl font-bold text-[#f7e5c6]">
              {endgameQueueFinished ? 'Session complete!' : 'No endgame reviews due'}
            </h2>
            <p className="mb-6 text-white/60">
              {endgameQueueFinished
                ? 'You cleared every endgame due today. Come back later for the next batch.'
                : 'You\'re all caught up. Fail a rated endgame drill to send it here for review.'}
            </p>
            <div className="grid grid-cols-2 gap-3">
              <Link
                href="/train/endgametrainer"
                className="rounded-xl border border-white/20 bg-white/5 px-4 py-3 text-sm font-semibold transition hover:bg-white/10"
              >
                Endgame Trainer
              </Link>
              <button
                onClick={loadEndgameQueue}
                className="rounded-xl bg-[#10b981] px-4 py-3 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition-colors hover:bg-emerald-400"
              >
                Refresh
              </button>
            </div>
          </div>
        </div>
      );
    }
    if (!endgamePosition || !currentEndgameEntry) return null;

    return (
      <div className="grid h-full grid-cols-1 gap-6 lg:grid-cols-[18rem_minmax(0,1fr)_22rem] xl:grid-cols-[20rem_minmax(0,1fr)_22rem]">
        {/* ============== LEFT CARD ============== */}
        <section className="hidden min-h-0 min-w-0 lg:block">
          <div className={`${CARD_CLASS} flex h-full w-full flex-col justify-between p-6 shadow-2xl shadow-black/25`}>
            {/* Top: identity & progress */}
            <div className="flex flex-col items-center">
              <Image
                src="/woodpecker-bird-v2.webp"
                alt=""
                width={112}
                height={112}
                className="mb-5 h-[112px] w-[112px] object-contain"
              />
              <div className="text-[12px] font-bold uppercase tracking-[0.3em] text-[#f7e5c6]/60">
                Woodpecker
              </div>
              <div className="mt-2 text-[11px] uppercase tracking-wider text-white/40">Endgame</div>
              <div className="mt-2 text-5xl font-bold leading-none text-[#f7e5c6]">
                {Math.min(endgameIndex + 1, endgameCellTotal)} <span className="text-white/40">/ {endgameCellTotal}</span>
              </div>
              <div className="mt-6 w-full">
                <ProgressCells completed={endgameIndex} total={endgameCellTotal} />
              </div>
            </div>

            {/* Middle: remaining stats (centered) */}
            <div className="flex flex-col items-center text-center">
              <div className="text-[11px] uppercase tracking-[0.2em] text-white/50">Remaining</div>
              <div className="mt-2 text-6xl font-bold leading-none text-[#f7e5c6]">{endgameRemaining}</div>
              <div className="mt-3 text-sm text-white/50">
                Estimated time: {endgameEstimatedMinutes} min
              </div>
            </div>

            {/* Bottom: exit */}
            <div>
              <Link
                href="/train/endgametrainer"
                onClick={handleEndgameExit}
                className="flex w-full items-center justify-center gap-2 rounded-lg border border-[#f7e5c6]/30 bg-transparent px-4 py-3 text-sm font-semibold text-[#f7e5c6] transition hover:border-[#f7e5c6]/60 hover:bg-[#f7e5c6]/5"
              >
                <ExitIcon />
                Exit Review
              </Link>
            </div>
          </div>
        </section>

        {/* ============== CENTER: FULL-RESOLUTION ENDGAME BOARD ============== */}
        <section className="min-h-0 min-w-0">
          <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
            <div className="w-full">
              <EndgameBoard
                key={`${currentEndgameEntry.id}:${endgameRetryKey}`}
                position={endgamePosition}
                submitMove={submitEndgameReviewMove}
                playout={endgamePlayout?.state === 'active'}
                onPlayoutResolved={(ending) =>
                  setEndgamePlayout({ state: 'done', ending })
                }
                hintsUsed={endgameHintsUsed}
                retry={endgameIsRetry}
                hintRequest={endgameHintRequest}
                solutionRequest={endgameSolutionRequest}
                onHintRevealed={handleEndgameHintRevealed}
                onThinkingChange={handleEndgameThinkingChange}
                onDrillResolved={handleEndgameResolved}
              />
            </div>

            <div ref={settingsRef} className="absolute right-2 top-2 z-30 xl:left-full xl:right-auto xl:top-0 xl:ml-[2px]">
              <button
                type="button"
                onClick={() => setIsSettingsOpen((open) => !open)}
                className={`relative flex h-7 w-7 items-center justify-center rounded-md border text-[#f0e0c0] transition hover:scale-105 active:scale-95 ${
                  isSettingsOpen
                    ? 'border-[#d9b87c]/70 text-[#f7e5c6]'
                    : 'border-black/60 hover:border-[#d9b87c]/45'
                }`}
                style={{
                  borderRadius: '4px',
                  background:
                    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
                  backgroundSize: 'cover',
                  backgroundPosition: 'center',
                  boxShadow:
                    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
                }}
                aria-label="Woodpecker settings"
                aria-haspopup="dialog"
                aria-expanded={isSettingsOpen}
                title="Woodpecker settings"
              >
                <SettingsIcon />
              </button>

              {isSettingsOpen && (
                <div
                  role="dialog"
                  aria-label="Woodpecker settings"
                  className="absolute right-0 top-full mt-2 w-60 rounded-xl border border-[#d9b87c]/30 bg-[#1b120d]/95 p-3 text-white shadow-[0_18px_42px_rgba(0,0,0,0.52),inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-xl"
                >
                  <div className="flex items-center justify-between border-b border-white/10 pb-2">
                    <span className="text-[10px] font-bold uppercase tracking-[0.24em] text-[#f7e5c6]/65">
                      Review settings
                    </span>
                    <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[#37be7e]">
                      {autoAdvance ? 'Auto' : 'Manual'}
                    </span>
                  </div>

                  <div className="mt-3 flex items-center justify-between gap-4">
                    <div>
                      <div className="text-sm font-semibold text-[#f7e5c6]">Auto-advance</div>
                      <div className="mt-1 text-[11px] leading-4 text-white/45">
                        Continue after a solved review
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setAutoAdvance((enabled) => !enabled)}
                      className={`relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors ${
                        autoAdvance ? 'bg-[#10b981]' : 'bg-white/15'
                      }`}
                      aria-pressed={autoAdvance}
                      aria-label="Toggle auto-advance"
                    >
                      <span
                        className={`inline-block h-5 w-5 rounded-full bg-white shadow transition-transform ${
                          autoAdvance ? 'translate-x-5' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                  </div>

                  {endgameResult !== null &&
                    (!autoAdvance || endgameResult.status === 'failed') && (
                      <button
                        type="button"
                        onClick={advanceEndgame}
                        className="mt-3 w-full rounded-lg bg-emerald-500 py-2.5 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition hover:bg-emerald-400"
                      >
                        Next Review
                      </button>
                    )}

                  <div className="mt-3 border-t border-white/10 pt-2 text-[10px] text-white/40">
                    {autoAdvance
                      ? 'Solved reviews advance; failed ones wait for you.'
                      : 'Use Next Review when you are ready.'}
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

        {/* ============== RIGHT CARD: SHARED THREE-STATE PANEL ============== */}
        <section className="hidden min-h-0 min-w-0 xl:block">
          <div className="flex h-full flex-col gap-4 overflow-y-auto pr-1">
            <DrillStatusPanel
              key={currentEndgameEntry.id}
              context="review"
              position={endgamePosition}
              result={endgameResult}
              isThinking={endgameIsThinking}
              onPlayItOut={() => setEndgamePlayout({ state: 'active' })}
              onRetry={handleEndgameRetry}
              playout={endgamePlayout}
              onRequestHint={() => setEndgameHintRequest((n) => n + 1)}
              onRequestSolution={() => setEndgameSolutionRequest((n) => n + 1)}
            />

            {endgameResult && (
              <button
                type="button"
                onClick={advanceEndgame}
                className={`${CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
              >
                <NextIcon />
                Next Review
              </button>
            )}
          </div>
        </section>
      </div>
    );
  };

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-hidden px-6 pb-[10px] pt-6 text-white lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto flex h-full w-full max-w-[1760px] flex-col">
        {/* Two queues, one page: the seeds are separate on the backend, so
            the switcher only ever swaps which queue is being reviewed. Its
            handle floats in the top-left corner -- no layout row, so the
            board gets the ~56px the old segmented control held permanently
            -- and drops the menu on hover / focus / tap, peeking open once
            per load. */}
        <QueueSwitcher
          active={activeTab}
          puzzleDue={puzzleDue}
          endgameDue={endgameDue}
          onSelect={handleTabChange}
        />

        <div
          role="tabpanel"
          aria-label={activeTab === 'puzzles' ? 'Puzzle reviews' : 'Endgame reviews'}
          className="min-h-0 flex-1"
        >
          {activeTab === 'puzzles' ? renderPuzzleReview() : renderEndgameReview()}
        </div>
      </div>
    </div>
  );
}
