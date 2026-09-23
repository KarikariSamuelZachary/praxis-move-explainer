'use client';

import {
  EndgameGradeResult,
  EndgamePlayoutStatus,
  endgameResolutionLabel,
} from '@/lib/endgames';
import { EndgamePosition } from '@/types';

/**
 * The drill's three-state panel: quiet in progress, detailed resolved.
 *
 * Shared verbatim by all three Endgame Trainer surfaces -- rated trainer,
 * Woodpecker review and category-picker practice -- because the interaction
 * contract is identical (every user move is graded server-side; a drill ends
 * only on real checkmate or a legitimate board draw). The only difference is
 * what a resolved drill MEANS, and that is the `context` prop:
 *
 *   * rated    -- a solved/failed drill writes users.endgame_trainer_rating.
 *   * review   -- a Woodpecker replay never touches the rating; FSRS is the
 *                 outcome.
 *   * practice -- nothing is written at all: no rating, no capture, so the
 *                 copy stays deliberately low-stakes ("Solved." / "Not
 *                 quite.") instead of reusing rated/review language.
 *
 * Everything else -- labels, colors, layout, the failure copy -- is one
 * system, not three.
 *
 * The resolved card leads with its verdict and the authored explanation; the
 * per-drill stat row (moves, time, rating) is deliberately not rendered.
 *
 * The in-progress state follows the drill-screen mockup: a state header, a
 * display-serif headline, then the assists under a hairline -- "Hint" as the
 * gold primary (a Puzzles-style piece-only highlight) and "Show move" as the
 * secondary that plays the immediate best move. The mockup's move cells and
 * coach note are deliberately not built.
 */

// The exact walnut card language of the Puzzles page.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

// The display serif (Cinzel) carries every panel headline, the same face the
// section headings use.
const HEADING_CLASS =
  'font-display text-[26px] font-semibold leading-[1.15] text-[#f7e5c6]';

// The mockup's gold primary action: centered icon + label, chevron parked at
// the right edge.
const GOLD_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

// The secondary action beside the gold primary ("Show move"): same height and
// shape so the pair reads as one control group.
const SECONDARY_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl border border-white/15 bg-white/5 px-4 py-3.5 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50';

export interface DrillStatusPanelProps {
  position: EndgamePosition;
  /** rated = trainer loop (rating); review = Woodpecker (FSRS); practice =
   * category picker (nothing written). */
  context: 'rated' | 'review' | 'practice';
  result: EndgameGradeResult | null;
  isThinking: boolean;
  /**
   * "Hint": highlight just the piece to move for the CURRENT position, like
   * the Puzzles board's hint. Nothing is played and nothing is printed; the
   * user still plays the move through the normal grading path.
   */
  onRequestHint?: () => void;
  /**
   * "Show move": play the immediate best move for the CURRENT position as
   * the user's move. Revealing the answer counts as hint-assisted, so the
   * drill's rating/FSRS consequence matches a hint-assisted solve.
   */
  onRequestSolution?: () => void;
  /**
   * "Play it out": the settled FAILED drill's continuation. Available in
   * every context (the verdict is final in all three), rendered inside the
   * failed block only. `onPlayItOut` starts it; `playout` reports whether it
   * is running or has ended.
   */
  onPlayItOut?: () => void;
  /**
   * "Retry": restart the same drill from its initial position as a fresh
   * attempt. Rendered beside "Play it out" in the failed block.
   */
  onRetry?: () => void;
  playout?: EndgamePlayoutStatus | null;
}

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

/** The "Play it out" mark, styled like the other button glyphs. */
function PlayIcon() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M7 5.5v13l11-6.5L7 5.5Z" />
    </svg>
  );
}

/** The Hint button's mark. */
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

/** The "Show move" mark, matching the Puzzles page's Show Solution glyph. */
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

/** A retry mark, matching the Puzzles page's "Play Again" refresh glyph. */
function RetryIcon() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
    </svg>
  );
}

export default function DrillStatusPanel({
  position,
  context,
  result,
  isThinking,
  onRequestHint,
  onRequestSolution,
  onPlayItOut,
  onRetry,
  playout = null,
}: DrillStatusPanelProps) {
  const hintUsed = (result?.hints_used ?? 0) > 0;
  const isWinDrill = position.is_winning;
  const isReview = context === 'review';
  const isPractice = context === 'practice';

  // The live header only: a resolved drill leads with its own verdict
  // headline instead, so a "Resolved"/"Reviewed" row would only repeat it.
  const statusHeader = result
    ? null
    : {
        // Live: the header names the drill itself (the mockup's "CONVERT
        // THE WIN") and the pulsing dot carries the "still on" signal.
        dot: 'bg-amber-300 animate-pulse',
        label: isWinDrill ? 'Convert the win' : 'Hold the draw',
        labelClass: 'text-amber-200/80',
      };

  return (
    <div className={`${CARD_CLASS} p-6 shadow-2xl shadow-black/25`}>
      <p className="sr-only" role="status" aria-live="polite">
        {result?.status === 'solved'
          ? hintUsed
            ? 'Solved with a hint.'
            : isPractice
              ? 'Practice position solved.'
              : isReview
                ? 'Review resolved.'
                : 'Drill solved.'
          : result?.status === 'failed'
            ? isPractice
              ? 'Practice attempt failed.'
              : isReview
                ? 'Review failed.'
                : 'Drill failed.'
            : ''}
      </p>

      {/* The live state header. */}
      {statusHeader && (
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className={`inline-block h-2.5 w-2.5 rounded-full ${statusHeader.dot}`} />
            <span
              className={`text-[11px] font-bold uppercase tracking-[0.3em] ${statusHeader.labelClass}`}
            >
              {statusHeader.label}
            </span>
          </div>
        </div>
      )}

      {/* ---------- IN PROGRESS ---------- */}
      {!result && (
        <>
          <h2 className={`mt-3 ${HEADING_CLASS}`}>
            {isWinDrill
              ? 'Find the fastest route to checkmate.'
              : 'Hold the draw to the end.'}
          </h2>
          {!isWinDrill && (
            <p className="mt-3 text-[13px] leading-6 text-white/55">
              The position is already drawn. Play it out.
            </p>
          )}

          {/* The two assists. "Hint" highlights only the piece to move (the
              board owns the fade); "Show move" plays the immediate best move
              through the normal grading path, so both keep the drill honest:
              either reveal makes a solve hint-assisted. */}
          {onRequestHint && (
            <div className="mt-5 border-t border-white/10 pt-4">
              <div
                className={`grid gap-3 ${
                  onRequestSolution ? 'grid-cols-2' : 'grid-cols-1'
                }`}
              >
                <button
                  type="button"
                  onClick={onRequestHint}
                  disabled={isThinking}
                  className={GOLD_BUTTON_CLASS}
                >
                  <BulbIcon />
                  <span>Hint</span>
                </button>
                {onRequestSolution && (
                  <button
                    type="button"
                    onClick={onRequestSolution}
                    disabled={isThinking}
                    className={SECONDARY_BUTTON_CLASS}
                  >
                    <EyeIcon />
                    <span>Show move</span>
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* ---------- SOLVED ---------- */}
      {result?.status === 'solved' && (
        <>
          {/* Centered and led by a larger tick: the solved card is a single
              celebratory beat, unlike the live and failed rows. */}
          <div className="mt-3 flex flex-col items-center gap-2.5 text-center">
            <span className="text-[#10b981]">
              <CheckCircleIcon className="h-10 w-10" />
            </span>
            <h2 className={HEADING_CLASS}>
              {isPractice
                ? 'Solved'
                : isWinDrill
                  ? 'Conversion complete'
                  : 'Draw held'}
              {hintUsed ? ' (hint used)' : ''}.
            </h2>
          </div>
        </>
      )}

      {/* ---------- FAILED ---------- */}
      {result?.status === 'failed' && (
        <>
          {/* Leads the card: the failure headline replaces the state header.
              One verdict for every surface -- the practice "Not quite." with
              its gold mark; the authored "Why this failed" box below carries
              the specifics. */}
          <div className="flex items-center gap-2">
            <span className="text-[#d9b87c]">
              <BreakIcon />
            </span>
            <h2 className={HEADING_CLASS}>Not quite.</h2>
          </div>

          {/* The authored, position-specific explanation: it answers "why did
              THIS move fail" in a way the generic headline cannot. */}
          {result.common_mistake && (
            <div className="mt-4 rounded-xl border border-[#d9b87c]/40 bg-[#d9b87c]/10 px-4 py-3.5">
              <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-[#e8cfa0]/90">
                Why this failed
              </div>
              <p className="mt-1.5 text-sm leading-5 text-[#f0e0c0]/95">
                {result.common_mistake}
              </p>
            </div>
          )}

          {result.resolution && (
            <div className="mt-3 text-[11px] uppercase tracking-[0.2em] text-white/40">
              Ended: {endgameResolutionLabel(result.resolution)}
            </div>
          )}

          {/* The settled-drill choices, styled as one control pair like
              Hint / Show move: a no-stakes continuation and a fresh attempt.
              The continuation runs with no status chrome of its own -- the
              board's red ring is the only signal that the drill is open. */}
          {(onPlayItOut || onRetry) && (
            <div className="mt-5 border-t border-white/10 pt-4">
              <div
                className={`grid gap-3 ${
                  onPlayItOut && onRetry ? 'grid-cols-2' : 'grid-cols-1'
                }`}
              >
                {onPlayItOut && (
                  <button
                    type="button"
                    onClick={onPlayItOut}
                    disabled={playout !== null}
                    className={GOLD_BUTTON_CLASS}
                  >
                    <PlayIcon />
                    <span>Play it out</span>
                  </button>
                )}
                {onRetry && (
                  <button
                    type="button"
                    onClick={onRetry}
                    className={SECONDARY_BUTTON_CLASS}
                  >
                    <RetryIcon />
                    <span>Retry</span>
                  </button>
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
