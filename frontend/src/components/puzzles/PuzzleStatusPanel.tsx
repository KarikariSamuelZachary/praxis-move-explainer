'use client';

import { PUZZLE_CARD_CLASS } from './puzzleStyles';

/**
 * The puzzles right-column panel, sharing the Endgame Trainer's panel
 * language (DrillStatusPanel): a live state header, a display-serif headline,
 * then the assists under a hairline -- "Hint" as the gold primary and
 * "Solution" as the secondary. The label stays one word so the pair keeps the
 * trainer's half-panel button size (a longer label wraps and doubles the
 * row height). Once the puzzle resolves, the panel leads with
 * its verdict and "Next Puzzle" moves to the page-level button, exactly like
 * the trainer's resolved state.
 *
 * This panel writes nothing: themed practice is unrated by design, and the
 * rated loop's rating update happens in the page, not here.
 */

const HEADING_CLASS =
  'font-display text-[26px] font-semibold leading-[1.15] text-[#f7e5c6]';

const GOLD_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

const SECONDARY_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl border border-white/15 bg-white/5 px-4 py-3.5 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50';

function CheckCircleIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12 2.5 2.5 4.5-5" />
    </svg>
  );
}

function BreakIcon() {
  return (
    <svg
      className="h-5 w-5"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path d="M12 3 2.5 20h19L12 3Z" />
      <path d="M12 9v5" />
      <path d="M12 17.5h.01" />
    </svg>
  );
}

/** The Hint button's mark, matching the trainer's. */
function BulbIcon() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.9"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M9 18h6" />
      <path d="M10 21h4" />
      <path d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.8 1 .9 1.6l.1.6h5.2l.1-.6c.1-.6.4-1.2.9-1.6A6 6 0 0 0 12 3Z" />
    </svg>
  );
}

/** The "Show Solution" mark, matching the trainer's "Show move" glyph. */
function EyeIcon() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.9"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

export interface PuzzleStatusPanelProps {
  sideToMoveLabel: 'White' | 'Black';
  /** Theme being practiced, or null for the rated "All Tactics" loop. */
  themeLabel: string | null;
  /** Set once the puzzle resolves; null while the solver is thinking. */
  result: 'solved' | 'failed' | null;
  onHint: () => void;
  onShowSolution: () => void;
}

export default function PuzzleStatusPanel({
  sideToMoveLabel,
  themeLabel,
  result,
  onHint,
  onShowSolution,
}: PuzzleStatusPanelProps) {
  const live = result === null;

  return (
    <div className={`${PUZZLE_CARD_CLASS} p-6 shadow-2xl shadow-black/25`}>
      <p className="sr-only" role="status" aria-live="polite">
        {result === 'solved'
          ? 'Puzzle solved.'
          : result === 'failed'
            ? 'Puzzle not solved.'
            : ''}
      </p>

      {/* ---------- IN PROGRESS ---------- */}
      {live && (
        <>
          {themeLabel && (
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-2.5">
                <span
                  className={`inline-block h-2.5 w-2.5 rounded-full ${
                    sideToMoveLabel === 'White'
                      ? 'bg-white'
                      : 'bg-zinc-800 ring-1 ring-white/40'
                  }`}
                />
                <span className="text-[11px] font-bold uppercase tracking-[0.3em] text-amber-200/80">
                  Theme · {themeLabel}
                </span>
              </div>
            </div>
          )}

          <h2 className={`mt-3 ${HEADING_CLASS}`}>
            Find the best move for {sideToMoveLabel}.
          </h2>

          <div className="mt-5 border-t border-white/10 pt-4">
            <div className="grid grid-cols-2 gap-3">
              <button type="button" onClick={onHint} className={GOLD_BUTTON_CLASS}>
                <BulbIcon />
                <span>Hint</span>
              </button>
              <button
                type="button"
                onClick={onShowSolution}
                className={SECONDARY_BUTTON_CLASS}
              >
                <EyeIcon />
                <span>Solution</span>
              </button>
            </div>
          </div>
        </>
      )}

      {/* ---------- SOLVED ---------- */}
      {result === 'solved' && (
        <div className="mt-3 flex flex-col items-center gap-2.5 text-center">
          <span className="text-[#10b981]">
            <CheckCircleIcon className="h-10 w-10" />
          </span>
          <h2 className={HEADING_CLASS}>Solved.</h2>
        </div>
      )}

      {/* ---------- FAILED ---------- */}
      {result === 'failed' && (
        <div className="flex items-center gap-2">
          <span className="text-[#d9b87c]">
            <BreakIcon />
          </span>
          <h2 className={HEADING_CLASS}>Not quite.</h2>
        </div>
      )}
    </div>
  );
}
