'use client';

import { useState } from 'react';

import {
  ENDGAME_FAILURE_COPY,
  EndgameGradeResult,
  EndgamePlayoutResolution,
  EndgamePlayoutStatus,
  endgameDueLabel,
  endgameResolutionLabel,
  playoutEndingLabel,
} from '@/lib/endgames';
import {
  EndgameHintResponse,
  EndgamePosition,
  EndgameRatingUpdate,
  EndgameReviewCapture,
  EndgameScheduling,
} from '@/types';

/**
 * The drill's three-state panel: quiet in-progress, rich resolved.
 *
 * Shared verbatim by all three Endgame Trainer surfaces -- rated trainer,
 * Woodpecker review and category-picker practice -- because the interaction
 * contract is identical (every user move is graded server-side; a drill ends
 * only on real checkmate or a legitimate board draw). The only difference is
 * what a resolved drill MEANS, and that is the `context` prop:
 *
 *   * rated    -- a solved/failed drill writes users.endgame_trainer_rating,
 *                 so the resolved stat cell reports the rating delta.
 *   * review   -- a Woodpecker replay never touches the rating; FSRS is the
 *                 outcome, so the same cell reports when the card comes back.
 *   * practice -- nothing is written at all: no rating, no capture. There is
 *                 no third stat to report, so the cell is dropped and the
 *                 copy stays deliberately low-stakes ("Solved." / "Not
 *                 quite.") instead of reusing rated/review language.
 *
 * Everything else -- labels, colors, layout, the move history, the failure
 * copy -- is one system, not three.
 *
 * The in-progress state follows the drill-screen mockup: a state header, a
 * display-serif headline, the two move cells in one divided box, then the
 * actions under a hairline -- the panel's single hint as a gold primary
 * button, with no copy beneath it. Two secondary actions from the mockup
 * (a move reveal separate from the solution, and the coach note) are
 * deliberately not built yet.
 */

// The exact walnut card language of the Puzzles page.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

const CELL_LABEL_CLASS =
  'text-[10px] font-bold uppercase tracking-[0.16em] text-white/40';

// The display serif (Cinzel) carries every panel headline, the same face the
// section headings and the rating numeral use.
const HEADING_CLASS =
  'font-display text-[26px] font-semibold leading-[1.15] text-[#f7e5c6]';

// The mockup's gold primary action: centered icon + label, chevron parked at
// the right edge.
const GOLD_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

/** Both wire responses structurally satisfy this: the review response only
 * lacks `rating`/`review_capture`, and those are read in rated context only. */
export type DrillPanelResult = EndgameGradeResult & {
  rating?: EndgameRatingUpdate | null;
  review_capture?: EndgameReviewCapture | null;
};

export interface DrillStatusPanelProps {
  position: EndgamePosition;
  /** rated = trainer loop (rating); review = Woodpecker (FSRS); practice =
   * category picker (nothing written). */
  context: 'rated' | 'review' | 'practice';
  result: DrillPanelResult | null;
  moveCount: number;
  history: string[];
  elapsedSeconds: number;
  lastUserSan: string | null;
  lastOpponentSan: string | null;
  isThinking: boolean;
  /** Review context only: the FSRS block written on the resolving move. */
  scheduling?: EndgameScheduling | null;
  /**
   * "Show Hint": reveal the single best move for the CURRENT position
   * (never a plan). The board fetches and highlights it; the user still
   * plays it through the normal grading path, which is why the consequence
   * copy below is shown before the tap rather than after.
   */
  onRequestHint?: () => void;
  /** The revealed hint for the position on the board, or null. The caller
   * clears it on every move, so the button returns for the next position. */
  revealedHint?: EndgameHintResponse | null;
  /**
   * "Play it out": the settled FAILED drill's continuation. Available in
   * every context (the verdict is final in all three), rendered inside the
   * failed block only. `onPlayItOut` starts it; `playout` reports whether it
   * is running or has ended.
   */
  onPlayItOut?: () => void;
  /** Fast-forward the current position to its final result. During a line
   *  step-through this same action is the "Skip to end" escape hatch. */
  onSkipToResult?: () => void;
  /** Advance one ply of a fast-forwarded line (the stepper's "Next move"). */
  onStepPlayout?: () => void;
  /** Plies played in the continuation so far, for the soft exit prompt. */
  playoutPlies?: number;
  playout?: EndgamePlayoutStatus | null;
}

// The soft exit prompt fires after 8 full moves. Decisive conversions in
// these drills measured 13-25 plies and drawn shuffles 18-20, so 16 plies is
// late enough to let a quick mate finish and early enough to matter on the
// long shuffles this prompt exists for.
const SOFT_PROMPT_PLIES = 16;

/** The solved paragraph, in each context's vocabulary -- and hint-aware. */
function solvedConsequenceCopy(
  context: 'rated' | 'review' | 'practice',
  hintUsed: boolean
): string {
  if (context === 'practice') {
    return hintUsed
      ? 'Solved with a hint. Nothing here is rated or saved either way.'
      : 'Played out to a real finish. Nothing here is rated or saved.';
  }
  if (context === 'review') {
    return hintUsed
      ? 'Hint-assisted \u2014 the card is not advanced and comes back in the queue.'
      : 'You carried the technique to the board\u2019s real ending. Reviewed \u2014 no rating change.';
  }
  return hintUsed
    ? 'Hint-assisted \u2014 no rating change, up or down.'
    : 'You carried the technique to the board\u2019s real ending.';
}

/** The done-state copy: a reached ending, or a tablebase verdict. */
function playoutResolutionCopy(
  resolution: EndgamePlayoutResolution,
  isPractice: boolean
): string {
  const stands = isPractice ? '' : ' The recorded result stands.';
  if (resolution.kind === 'verdict') {
    if (resolution.outcome === 'draw') {
      return `Tablebase verdict: a dead draw.${stands}`;
    }
    return resolution.outcome === 'win'
      ? `Tablebase verdict: mate is forced.${stands}`
      : `Tablebase verdict: mate is forced against you.${stands}`;
  }
  if (resolution.ending) {
    return `The game reached ${playoutEndingLabel(resolution.ending)}.${stands}`;
  }
  return `The continuation stopped.${stands}`;
}

function CheckCircleIcon() {
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

function PlayIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M8 5.5v13l11-6.5-11-6.5Z" />
    </svg>
  );
}

/** The hint button's mark, and the "Show Hint" glyph of the mockup. */
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

function ChevronRightIcon() {
  return (
    <svg
      className="h-[17px] w-[17px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2.2"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

function formatDuration(totalSeconds: number): string {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

/** Compact two-column move list. A drill can start with either color to
 * move, so a black-to-move start renders as "1... move" like a real score. */
function MoveHistory({
  history,
  startsAsBlack,
}: {
  history: string[];
  startsAsBlack: boolean;
}) {
  if (history.length === 0) return null;

  const rows: { number: number; white?: string; black?: string }[] = [];
  let index = 0;
  if (startsAsBlack) {
    rows.push({ number: 1, black: history[0] });
    index = 1;
  }
  let number = startsAsBlack ? 2 : 1;
  while (index < history.length) {
    rows.push({ number, white: history[index], black: history[index + 1] });
    index += 2;
    number += 1;
  }

  return (
    <div className="mt-4 max-h-28 overflow-y-auto rounded-xl border border-white/10 bg-black/20 px-3 py-2 font-mono text-xs leading-6 text-white/70">
      {rows.map((row) => (
        <span key={row.number} className="mr-3 whitespace-nowrap">
          <span className="text-white/35">{row.number}.</span>{' '}
          {row.white ?? '...'} {row.black ?? ''}
        </span>
      ))}
    </div>
  );
}

/** The mockup's single divided box: the user's last move beside the
 * defender's answer. Deliberately the mono face, not the display serif: SAN
 * is case-sensitive (Kxh3 is a capture with a knight, KXH3 reads as a king
 * move), and Cinzel renders lowercase as small capitals. */
function MoveCells({
  lastUserSan,
  lastOpponentSan,
  isThinking,
}: {
  lastUserSan: string | null;
  lastOpponentSan: string | null;
  isThinking: boolean;
}) {
  return (
    <div className="mt-5 grid grid-cols-2 divide-x divide-white/10 overflow-hidden rounded-xl border border-white/10 bg-black/20">
      <div className="px-4 py-3">
        <div className={CELL_LABEL_CLASS}>Last move</div>
        <div className="mt-1 font-mono text-lg text-[#f7e5c6]">
          {lastUserSan ?? '—'}
        </div>
      </div>
      <div className="px-4 py-3">
        <div className={CELL_LABEL_CLASS}>Defender</div>
        <div
          className={`mt-1 font-mono text-lg ${
            isThinking ? 'animate-pulse text-amber-200/80' : 'text-[#f7e5c6]'
          }`}
        >
          {isThinking ? 'replying…' : lastOpponentSan ?? '—'}
        </div>
      </div>
    </div>
  );
}

export default function DrillStatusPanel({
  position,
  context,
  result,
  moveCount,
  history,
  elapsedSeconds,
  lastUserSan,
  lastOpponentSan,
  isThinking,
  scheduling = null,
  onRequestHint,
  revealedHint = null,
  onPlayItOut,
  onSkipToResult,
  onStepPlayout,
  playoutPlies = 0,
  playout = null,
}: DrillStatusPanelProps) {
  const [promptDismissed, setPromptDismissed] = useState(false);
  const showPlayoutPrompt =
    playout?.state === 'active' &&
    playoutPlies >= SOFT_PROMPT_PLIES &&
    !promptDismissed;
  const hintUsed = (result?.hints_used ?? 0) > 0;
  const isWinDrill = position.is_winning;
  const startsAsBlack = position.fen.split(/\s+/)[1] === 'b';
  const isReview = context === 'review';
  const isPractice = context === 'practice';

  // The shared state header. Live and solved only: a failed drill leads with
  // its failure headline instead, so a "Technique broke" row above it would
  // only repeat the verdict.
  const statusHeader =
    result?.status === 'failed'
      ? null
      : result?.status === 'solved'
        ? {
            dot: 'bg-[#10b981]',
            label: isPractice ? 'Solved' : isReview ? 'Reviewed' : 'Resolved',
            labelClass: 'text-[#10b981]',
          }
        : {
            // Live: the header names the drill itself (the mockup's "CONVERT
            // THE WIN") and the pulsing dot carries the "still on" signal.
            dot: 'bg-amber-300 animate-pulse',
            label: isWinDrill ? 'Convert the win' : 'Hold the draw',
            labelClass: 'text-amber-200/80',
          };

  // The third stat cell: rating delta in the rated loop, the FSRS next-pass
  // date in review -- the same slot answering "what did this drill change?".
  // Practice changes nothing, so the cell is dropped rather than filled with
  // a placeholder.
  const showOutcomeCell = !isPractice;
  const outcomeValue = isReview
    ? scheduling
      ? endgameDueLabel(scheduling.due)
      : '—'
    : result?.rating
      ? result.rating.change === 0
        ? '±0'
        : `${result.rating.change > 0 ? '+' : ''}${result.rating.change}`
      : '—';
  const outcomeLabel = isReview ? 'Next review' : 'Rating';
  // A neutral (hint-assisted) solve reports ±0 in neutral grey rather than
  // the solved green -- the same information the title suffix spells out.
  const outcomeClass = isReview
    ? 'text-[#f7e5c6]'
    : result?.rating && result.rating.change < 0
      ? 'text-red-300'
      : result?.rating && result.rating.change === 0
        ? 'text-white/70'
        : 'text-[#10b981]';

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

      {/* Shared state header (live and solved). */}
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
          <p className="mt-3 text-[13px] leading-6 text-white/55">
            {isWinDrill
              ? 'The position is already winning. Play it out.'
              : 'The position is already drawn. Play it out.'}
          </p>

          <div className="mt-5 border-t border-white/5 pt-5">
            <MoveCells
              lastUserSan={lastUserSan}
              lastOpponentSan={lastOpponentSan}
              isThinking={isThinking}
            />
          </div>

          {/* One move, never a plan. The board fetches and highlights it and
              the user still plays it through the normal grading path, so the
              consequence is stated before the tap rather than after. */}
          {onRequestHint && (
            <div className="mt-4 border-t border-white/10 pt-4">
              {revealedHint ? (
                <div className="flex items-center justify-between gap-3 rounded-xl border border-sky-300/30 bg-sky-400/10 px-4 py-3">
                  <div>
                    <div className="text-[10px] font-bold uppercase tracking-[0.22em] text-sky-200/80">
                      Hint
                    </div>
                    <div className="mt-0.5 font-mono text-lg text-[#f7e5c6]">
                      {revealedHint.move_san}
                    </div>
                  </div>
                  <span className="max-w-[11rem] text-right text-[11px] leading-4 text-white/45">
                    Highlighted on the board — play it to continue.
                  </span>
                </div>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={onRequestHint}
                    disabled={isThinking}
                    className={GOLD_BUTTON_CLASS}
                  >
                    <BulbIcon />
                    <span>Show Hint</span>
                    <span className="absolute right-4 text-[#2a1a06]/70">
                      <ChevronRightIcon />
                    </span>
                  </button>
                </>
              )}
            </div>
          )}
        </>
      )}

      {/* ---------- SOLVED ---------- */}
      {result?.status === 'solved' && (
        <>
          <div className="mt-3 flex items-center gap-2">
            <span className="text-[#10b981]">
              <CheckCircleIcon />
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
          <p className="mt-3 text-[13px] leading-6 text-white/55">
            {result.resolution
              ? `Ended in ${endgameResolutionLabel(result.resolution).toLowerCase()}. `
              : ''}
            {solvedConsequenceCopy(context, hintUsed)}
          </p>

          <div
            className={`mt-5 grid ${
              showOutcomeCell ? 'grid-cols-3' : 'grid-cols-2'
            } divide-x divide-white/5 rounded-xl border border-[#10b981]/25 bg-[#10b981]/10 py-3 text-center`}
          >
            <div className="px-2">
              <div className="text-lg font-bold text-[#f7e5c6]">{moveCount}</div>
              <div className="text-[10px] uppercase tracking-wider text-white/40">
                Your moves
              </div>
            </div>
            <div className="px-2">
              <div className="text-lg font-bold text-[#f7e5c6]">
                {formatDuration(elapsedSeconds)}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-white/40">
                Time
              </div>
            </div>
            {showOutcomeCell && (
              <div className="px-2">
                <div className={`text-lg font-bold ${outcomeClass}`}>
                  {outcomeValue}
                </div>
                <div className="text-[10px] uppercase tracking-wider text-white/40">
                  {outcomeLabel}
                </div>
              </div>
            )}
          </div>

          <MoveHistory history={history} startsAsBlack={startsAsBlack} />
        </>
      )}

      {/* ---------- FAILED ---------- */}
      {result?.status === 'failed' && (
        <>
          {/* Leads the card: the failure headline replaces the state header. */}
          <div className="flex items-center gap-2">
            <span className={isPractice ? 'text-[#d9b87c]' : 'text-red-300'}>
              <BreakIcon />
            </span>
            <h2 className={HEADING_CLASS}>
              {isPractice
                ? 'Not quite.'
                : result.failure_category
                  ? ENDGAME_FAILURE_COPY[result.failure_category].title
                  : 'The technique broke down.'}
            </h2>
          </div>

          {/* The authored, position-specific explanation leads: it answers
              "why did THIS move fail", which the category text below it can
              only describe in general terms. */}
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

          <p className="mt-3 text-[13px] leading-6 text-white/55">
            {result.failure_category
              ? ENDGAME_FAILURE_COPY[result.failure_category].detail
              : 'The drill slipped away before it resolved.'}
          </p>

          <div
            className={`mt-5 grid ${
              showOutcomeCell ? 'grid-cols-3' : 'grid-cols-2'
            } divide-x divide-white/5 rounded-xl border py-3 text-center ${
              isPractice
                ? 'border-[#d9b87c]/25 bg-[#d9b87c]/10'
                : 'border-red-400/25 bg-red-500/10'
            }`}
          >
            <div className="px-2">
              <div className="text-lg font-bold text-[#f7e5c6]">{moveCount}</div>
              <div className="text-[10px] uppercase tracking-wider text-white/40">
                {isPractice ? 'Your moves' : 'Broke on'}
              </div>
            </div>
            <div className="px-2">
              <div className="text-lg font-bold text-[#f7e5c6]">
                {formatDuration(elapsedSeconds)}
              </div>
              <div className="text-[10px] uppercase tracking-wider text-white/40">
                Time
              </div>
            </div>
            {showOutcomeCell && (
              <div className="px-2">
                <div className={`text-lg font-bold ${outcomeClass}`}>
                  {outcomeValue}
                </div>
                <div className="text-[10px] uppercase tracking-wider text-white/40">
                  {outcomeLabel}
                </div>
              </div>
            )}
          </div>

          {result.resolution && (
            <div className="mt-3 text-[11px] uppercase tracking-[0.2em] text-white/40">
              Ended: {endgameResolutionLabel(result.resolution)}
            </div>
          )}

          <MoveHistory history={history} startsAsBlack={startsAsBlack} />

          {/* The settled-drill continuation. The status framing stays on the
              failed side for its whole run: "Playing out" is red, never the
              live loop's amber, because nothing about the verdict is open. */}
          {playout?.state === 'active' ? (
            <div className="mt-4 border-t border-white/10 pt-4">
              <div className="flex items-center justify-between gap-3 rounded-xl border border-red-400/25 bg-red-500/5 px-4 py-3">
                <div className="flex items-center gap-2.5">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-400" />
                  <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-red-300">
                    Playing out
                  </span>
                </div>
                <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-white/35">
                  {isThinking
                    ? 'Defender replying…'
                    : `${playoutPlies} plies · nothing recorded`}
                </span>
              </div>

              {/* Soft, dismissible exit: never blocks the board. */}
              {showPlayoutPrompt && onSkipToResult && (
                <div className="mt-3 rounded-xl border border-[#d9b87c]/30 bg-[#d9b87c]/10 px-4 py-3">
                  <p className="text-sm leading-5 text-[#f0e0c0]/90">
                    Still exploring? You can keep playing, or see the final
                    verdict now.
                  </p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <button
                      type="button"
                      onClick={onSkipToResult}
                      className="rounded-lg bg-[#d9b87c]/20 px-3 py-2 text-xs font-semibold text-[#f0e0c0] transition hover:bg-[#d9b87c]/30"
                    >
                      See final verdict
                    </button>
                    <button
                      type="button"
                      onClick={() => setPromptDismissed(true)}
                      className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-white/60 transition hover:bg-white/5"
                    >
                      Keep playing
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : playout?.state === 'stepping' ? (
            /* The ≤5-man fast-forward came back as a whole line: the user
               walks it move by move (no per-step requests), with the old
               instant jump kept as "Skip to end". */
            <div className="mt-4 rounded-xl border border-[#d9b87c]/30 bg-[#d9b87c]/10 px-4 py-3">
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2.5">
                  <span className="inline-block h-2.5 w-2.5 rounded-full bg-[#d9b87c]" />
                  <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-[#e8cfa0]">
                    Final line
                  </span>
                </div>
                <span className="text-[10px] font-semibold uppercase tracking-[0.15em] text-white/35">
                  {playout.shown} of {playout.total} plies shown
                </span>
              </div>
              <p className="mt-2 text-[11px] leading-4 text-white/40">
                The tablebase-optimal line, replayed one move at a time.
                Nothing here is recorded.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                {onStepPlayout && (
                  <button
                    type="button"
                    onClick={onStepPlayout}
                    className="rounded-lg bg-[#d9b87c]/20 px-3 py-2 text-xs font-semibold text-[#f0e0c0] transition hover:bg-[#d9b87c]/30"
                  >
                    Next move
                    {playout.next_move_san ? `: ${playout.next_move_san}` : ''}
                  </button>
                )}
                {onSkipToResult && (
                  <button
                    type="button"
                    onClick={onSkipToResult}
                    className="rounded-lg border border-white/15 px-3 py-2 text-xs font-semibold text-white/60 transition hover:bg-white/5"
                  >
                    Skip to end
                  </button>
                )}
              </div>
            </div>
          ) : playout?.state === 'done' ? (
            <div className="mt-4 rounded-xl border border-red-400/25 bg-red-500/5 px-4 py-3">
              <div className="flex items-center gap-2.5">
                <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-400" />
                <span className="text-[11px] font-bold uppercase tracking-[0.25em] text-red-300">
                  Playout ended
                </span>
              </div>
              <p className="mt-1.5 text-[11px] leading-4 text-white/40">
                {playoutResolutionCopy(playout.resolution, isPractice)}
              </p>
            </div>
          ) : onPlayItOut ? (
            <div className="mt-4 border-t border-white/10 pt-4">
              <button
                type="button"
                onClick={onPlayItOut}
                className="flex w-full items-center justify-center gap-2 rounded-xl border border-[#f7e5c6]/30 px-4 py-3 text-sm font-semibold text-[#f7e5c6] transition hover:border-[#f7e5c6]/60 hover:bg-[#f7e5c6]/5"
              >
                <PlayIcon />
                Play it out
              </button>
              {onSkipToResult && (
                <button
                  type="button"
                  onClick={onSkipToResult}
                  className="mt-2 w-full rounded-lg px-4 py-2 text-center text-xs font-semibold text-[#d9b87c]/85 transition hover:bg-[#d9b87c]/10 hover:text-[#efd9a7]"
                >
                  Skip to final result
                </button>
              )}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
