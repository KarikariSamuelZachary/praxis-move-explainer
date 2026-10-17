'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { Chess } from 'chess.js';

import {
  CalcFetchError,
  depthForRating,
  fetchCalcHint,
  fetchCalcRatings,
  fetchNextCalcExercise,
  fetchRetryToken,
  fetchWhy,
  submitCalcLine,
} from '@/lib/calc';
import { type CalcBoardProps } from '@/components/board/CalcBoard';
import WoodpeckerPromoCard from '@/components/puzzles/WoodpeckerPromoCard';
import WhyResult from '@/components/calc/WhyResult';
import { getPuzzleDifficultyLabel } from '@/lib/lichess';
import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';
import type { CalcExercise, CalcSubmitResult, CalcWhyResult } from '@/types';

// Same dynamic-import contract as the other trainer boards
// (react-chessboard is client-only).
const Board = dynamic<CalcBoardProps>(() => import('@/components/board/CalcBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

/**
 * Failure mark matching the puzzles/endgame panels.
 */
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
      aria-hidden="true"
    >
      <path d="M12 3 2.5 20h19L12 3Z" />
      <path d="M12 9v5" />
      <path d="M12 17.5h.01" />
    </svg>
  );
}

// The exact walnut card language of the Puzzles page.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'empty'; message: string }
  | { kind: 'error'; message: string };

/**
 * The Blind Calculation Trainer.
 *
 * One exercise: the server names a depth ("Calculate N moves deep") and a
 * start FEN. The user enters the full 2N-1-ply line -- their own moves AND
 * the defender replies they expect -- with zero correctness feedback. The
 * board only enforces chess legality. Submit grades to the first divergence
 * and reveals the correct continuation from there.
 *
 * Layout mirrors the Endgame Trainer: rating card left (per-depth rating),
 * board center with the same sizing contract, status/actions right.
 */
export default function CalculationPage() {
  const [exercise, setExercise] = useState<CalcExercise | null>(null);
  const [loadState, setLoadState] = useState<LoadState>({ kind: 'loading' });
  const [moves, setMoves] = useState<string[]>([]);
  const [result, setResult] = useState<CalcSubmitResult | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isAdvancing, setIsAdvancing] = useState(false);
  const [nextError, setNextError] = useState<string | null>(null);
  const [rating, setRating] = useState<number | null>(null);
  const [ratingChange, setRatingChange] = useState<number | null>(null);
  const [dueCount, setDueCount] = useState<number | null>(null);
  const [woodpeckerPeck, setWoodpeckerPeck] = useState(0);
  // Retry token swap in flight (Retry fetches a fresh token for the same
  // exercise so the re-entry can be graded again, unrated).
  const [isRetrying, setIsRetrying] = useState(false);
  // Two-stage hint: the first press highlights the piece to move, the
  // second plays that single correct ply. Every revealed ply counts, and a
  // hinted attempt stores unrated (endgame neutralization).
  const [hintsUsed, setHintsUsed] = useState(0);
  const [hintUci, setHintUci] = useState<string | null>(null);
  const [isHintLoading, setIsHintLoading] = useState(false);
  const [exerciseKey, setExerciseKey] = useState(0);
  const loadTokenRef = useRef(0);
  // "Show why" explanation state. One request per attempt: firing sets
  // loading (button disabled), the result stays in state so re-renders
  // never refetch, and every path resolves loading (never stuck).
  const [whyState, setWhyState] = useState<
    | { status: 'idle' }
    | { status: 'loading' }
    | { status: 'done'; result: CalcWhyResult }
    | { status: 'error'; message: string }
  >({ status: 'idle' });
  const whyRetriedRef = useRef(false);

  const totalPlies = exercise ? 2 * exercise.n - 1 : 0;
  const resolved = result !== null;

  // Full-line playout on the main board: your correct prefix plus the
  // revealed continuation, stepped through on "Watch the full line".
  const [playoutPlies, setPlayoutPlies] = useState<string[] | null>(null);
  const [playoutAt, setPlayoutAt] = useState(0);
  const playoutPlaying =
    playoutPlies !== null && playoutAt < playoutPlies.length;

  const startExercise = useCallback((next: CalcExercise) => {
    setExercise(next);
    setMoves([]);
    setResult(null);
    setHintsUsed(0);
    setHintUci(null);
    setIsHintLoading(false);
    setPlayoutPlies(null);
    setPlayoutAt(0);
    setRatingChange(null);
    setNextError(null);
    setWhyState({ status: 'idle' });
    whyRetriedRef.current = false;
    setExerciseKey((k) => k + 1);
    setLoadState({ kind: 'ready' });
  }, []);

  const loadNext = useCallback(async () => {
    const token = ++loadTokenRef.current;
    setIsAdvancing(true);
    setNextError(null);
    try {
      // Depth auto-selects from the entry-depth (n=3) rating band.
      const ratings = await fetchCalcRatings().catch(() => []);
      const base = ratings.find((r) => r.n === 3)?.rating ?? null;
      const n = depthForRating(base);
      const next = await fetchNextCalcExercise(n);
      if (token !== loadTokenRef.current) return;
      const mine = ratings.find((r) => r.n === next.n)?.rating ?? null;
      setRating(mine);
      startExercise(next);
      fetch('/api/calc/woodpecker/count')
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => {
          if (typeof d?.due_count === 'number') setDueCount(d.due_count);
        })
        .catch(() => {});
    } catch (error) {
      if (token !== loadTokenRef.current) return;
      const fetchError = error as CalcFetchError;
      if (fetchError.status === 404) {
        setLoadState({ kind: 'empty', message: fetchError.message });
      } else {
        setLoadState({ kind: 'error', message: fetchError.message });
      }
    } finally {
      if (token === loadTokenRef.current) setIsAdvancing(false);
    }
  }, [startExercise]);

  useEffect(() => {
    void loadNext();
    return () => {
      loadTokenRef.current += 1;
    };
  }, [loadNext]);

  const handleMove = useCallback(
    (uci: string) => {
      if (resolved || isSubmitting) return;
      // Going your own way disarms an armed hint (the count stays: the
      // move was already revealed).
      setHintUci(null);
      setMoves((prev) => (prev.length < totalPlies ? [...prev, uci] : prev));
    },
    [resolved, isSubmitting, totalPlies]
  );

  const handleTakeback = useCallback(() => {
    if (resolved || isSubmitting) return;
    // An armed hint is the newest UI change: the first press clears the
    // highlight, the next ones walk back entered moves.
    if (hintUci) {
      setHintUci(null);
      return;
    }
    setMoves((prev) => prev.slice(0, -1));
  }, [resolved, isSubmitting, hintUci]);

  const handleSubmit = useCallback(async () => {
    if (!exercise || moves.length !== totalPlies || isSubmitting) return;
    setIsSubmitting(true);
    try {
      const graded = await submitCalcLine(exercise.exercise_token, moves, hintsUsed);
      setResult(graded);
      if (!graded.solved) setWoodpeckerPeck((n) => n + 1);
      if (graded.rated && graded.rating_after != null) {
        setRating(graded.rating_after);
        setRatingChange(graded.delta);
      }
    } catch (error) {
      const fetchError = error as CalcFetchError;
      setNextError(fetchError.message);
    } finally {
      setIsSubmitting(false);
    }
  }, [exercise, moves, totalPlies, isSubmitting, hintsUsed]);

  /** Retry: swap in a fresh token for the same exercise, then re-enter
   * the line on a clean board. The re-entry grades normally but stores
   * unrated (a prior attempt on the segment always exists by then). */
  const handleRetry = useCallback(async () => {
    if (!exercise || !resolved || isRetrying) return;
    setIsRetrying(true);
    try {
      const fresh = await fetchRetryToken(exercise.exercise_token);
      startExercise({ ...exercise, exercise_token: fresh.exercise_token });
    } catch (error) {
      const fetchError = error as CalcFetchError;
      setNextError(fetchError.message);
    } finally {
      setIsRetrying(false);
    }
  }, [exercise, resolved, isRetrying, startExercise]);

  /** Two-stage hint. Stage 1 fetches the single next correct ply and only
   * highlights its piece; stage 2 plays that one move onto the line. Every
   * revealed ply counts and neutralizes the rating at submit. */
  const requestHint = useCallback(async () => {
    if (!exercise || resolved || isSubmitting || isHintLoading) return;
    if (hintUci) {
      const u = hintUci;
      setHintUci(null);
      handleMove(u);
      return;
    }
    if (moves.length >= totalPlies) return;
    setIsHintLoading(true);
    try {
      const hint = await fetchCalcHint({
        exerciseToken: exercise.exercise_token,
        pliesEntered: moves.length,
      });
      setHintUci(hint.uci);
      setHintsUsed((n) => n + 1);
    } catch (error) {
      const fetchError = error as CalcFetchError;
      setNextError(fetchError.message);
    } finally {
      setIsHintLoading(false);
    }
  }, [exercise, resolved, isSubmitting, isHintLoading, hintUci, moves.length, totalPlies, handleMove]);

  /** Play the full correct line on the main board: your correct prefix
   * plus the revealed continuation, from move one. Pressing again
   * restarts; the final position stays on the board. */
  const startLinePlayout = useCallback(() => {
    if (!exercise || !result || result.first_divergence_ply == null) return;
    setPlayoutPlies([
      ...moves.slice(0, result.first_divergence_ply),
      ...result.continuation,
    ]);
    setPlayoutAt(0);
  }, [exercise, result, moves]);

  useEffect(() => {
    if (!playoutPlies || playoutAt >= playoutPlies.length) return;
    const timer = setTimeout(
      () => setPlayoutAt((a) => Math.min(a + 1, playoutPlies.length)),
      900
    );
    return () => clearTimeout(timer);
  }, [playoutPlies, playoutAt]);

  /** What the main board shows: the playout sequence while watching,
   * otherwise your entered line. */
  const shownMoves = playoutPlies ? playoutPlies.slice(0, playoutAt) : moves;

  /** FEN at the divergence ply: replay the correct prefix (the entered
   * moves before the divergence matched, so they replay cleanly). */
  const divergenceFen = useMemo(() => {
    if (!exercise || !result || result.first_divergence_ply == null) return exercise?.fen ?? '';
    const game = new Chess(exercise.fen);
    for (const uci of moves.slice(0, result.first_divergence_ply)) {
      try {
        game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4), promotion: uci[4] ?? undefined });
      } catch {
        break;
      }
    }
    return game.fen();
  }, [exercise, result, moves]);

  const plyLabel = (i: number) =>
    i % 2 === 0 ? `Your move ${i / 2 + 1}` : `Reply ${(i + 1) / 2}`;

  /** Live prompt: whose turn it is at the entry frontier, plus the role of
   * the next ply (even plies are yours, odd plies the expected reply). */
  const frontier = useMemo(() => {
    let turn: 'w' | 'b' = 'w';
    if (exercise) {
      try {
        const game = new Chess(exercise.fen);
        for (const uci of moves) {
          game.move({
            from: uci.slice(0, 2),
            to: uci.slice(2, 4),
            promotion: uci[4] ?? undefined,
          });
        }
        turn = game.turn();
      } catch {
        turn = 'w';
      }
    }
    return {
      sideLabel: turn === 'b' ? 'Black' : 'White',
      isSolverPly: moves.length % 2 === 0,
    };
  }, [exercise, moves]);

  /** Friendly, non-JSON error copy for every why failure mode. */
  const friendlyWhyError = (status: number): string => {
    if (status === 429) return 'Engine busy, try again.';
    if (status === 503 || status === 504)
      return 'The explanation engine is unavailable right now. Try again in a bit.';
    if (status === 409) return 'This attempt can no longer be explained.';
    if (status === 404) return 'Could not find this attempt.';
    if (status === 422) return 'This attempt cannot be explained.';
    return 'Could not explain this attempt.';
  };

  const requestWhy = useCallback(
    async (attemptId: string, sub: CalcSubmitResult, entered: string[]) => {
      setWhyState({ status: 'loading' });
      const deliver = (res: CalcWhyResult) => {
        setWhyState({ status: 'done', result: res });
        // Defender divergences have no engine line: play the stored
        // replies on the main board through the standard playout.
        if (res.type === 'defender_misjudged' && sub.first_divergence_ply != null) {
          setPlayoutPlies([
            ...entered.slice(0, sub.first_divergence_ply),
            ...res.line_uci,
          ]);
          setPlayoutAt(0);
        }
      };
      try {
        deliver(await fetchWhy(attemptId));
      } catch (error) {
        const fetchError = error as CalcFetchError;
        if (fetchError.status === 429 && !whyRetriedRef.current) {
          // One auto-retry after 3 s; afterwards the manual button owns it.
          whyRetriedRef.current = true;
          setTimeout(() => {
            void (async () => {
              try {
                deliver(await fetchWhy(attemptId));
              } catch (retryError) {
                const retryFetchError = retryError as CalcFetchError;
                setWhyState({
                  status: 'error',
                  message: friendlyWhyError(retryFetchError.status),
                });
              }
            })();
          }, 3000);
          return;
        }
        setWhyState({ status: 'error', message: friendlyWhyError(fetchError.status) });
      }
    },
    []
  );

  return (
    <div className="min-h-[calc(100vh-2.5rem)] -mt-2 text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center] xl:h-[calc(100vh-2.5rem)] xl:overflow-hidden">
      <div className="mx-auto flex flex-col px-6 pb-1 lg:px-10 xl:h-full">
        {loadState.kind === 'loading' ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
              <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
              <p className="text-white/60">Loading calculation exercise...</p>
            </div>
          </div>
        ) : loadState.kind === 'empty' || loadState.kind === 'error' || !exercise ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} max-w-md px-8 py-6 text-center`}>
              <p className="text-white/70">
                {loadState.kind === 'empty' || loadState.kind === 'error'
                  ? loadState.message
                  : 'No exercise loaded.'}
              </p>
              <button
                type="button"
                onClick={() => void loadNext()}
                disabled={isAdvancing}
                className="mt-4 w-full rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10 disabled:opacity-60"
              >
                {isAdvancing ? 'Loading…' : 'Try again'}
              </button>
            </div>
          </div>
        ) : (
          <div className="grid gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[20rem_minmax(0,1fr)_22rem] xl:pt-5">
            {/* ============ LEFT: RATING ============ */}
            <section className="order-2 mt-6 flex min-h-0 flex-col gap-5 xl:order-none xl:mt-0 xl:h-[min(calc(100vw-800px),calc(100vh-70px))]">
              <div
                className={`${WOOD_PANEL_CLASS} flex shrink-0 items-center gap-4 p-5`}
                style={WOOD_PANEL_STYLE}
              >
                <img
                  src="/knight-badge.webp"
                  alt=""
                  width={402}
                  height={454}
                  className="h-16 w-auto shrink-0 drop-shadow-[0_8px_18px_rgba(0,0,0,0.6)]"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                    Calculation Rating
                  </div>
                  <div className="mt-1.5 flex items-end gap-2.5">
                    <span className="font-display text-[34px] font-semibold leading-none text-[#f7e5c6]">
                      {rating ?? '-'}
                    </span>
                    {rating != null && (
                      <span className="mb-0.5 rounded-full bg-white/10 px-2 py-0.5 text-xs font-bold text-white/60">
                        {getPuzzleDifficultyLabel(rating)}
                      </span>
                    )}
                    {rating != null && ratingChange != null && (
                      <span
                        className={`mb-0.5 rounded-full px-2 py-0.5 text-xs font-bold ${
                          ratingChange === 0
                            ? 'bg-white/10 text-white/60'
                            : ratingChange > 0
                              ? 'bg-[#10b981]/15 text-[#10b981]'
                              : 'bg-red-500/15 text-red-300'
                        }`}
                      >
                        {ratingChange === 0
                          ? '±0'
                          : `${ratingChange > 0 ? '+' : ''}${ratingChange}`}
                      </span>
                    )}
                  </div>
                  {rating == null && (
                    <p className="mt-2 text-[11px] leading-4 text-white/40">
                      Unrated - your first exercise at this depth sets it.
                    </p>
                  )}
                </div>
              </div>

              {whyState.status !== 'idle' && (
                <div className="shrink-0">
                  {whyState.status === 'loading' && (
                    <div className="flex items-center gap-2 text-sm text-white/60">
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#d9b87c] border-t-transparent" />
                      Analyzing your move…
                    </div>
                  )}
                  {whyState.status === 'error' && (
                    <p role="alert" className="text-xs text-amber-100/90">
                      {whyState.message}
                    </p>
                  )}
                  {whyState.status === 'done' && (
                    <WhyResult
                      result={whyState.result}
                      fenBefore={divergenceFen}
                    />
                  )}
                </div>
              )}
            </section>

            {/* ============ CENTER: BOARD ============ */}
            <section className="order-1 min-h-0 min-w-0 xl:order-none xl:mt-0">
              <div className="relative mx-auto mt-6 aspect-square w-full max-w-[calc(100vh-70px)] xl:mt-0">
                <Board
                  key={`${exercise.segment_id}:${exercise.offset}:${exercise.n}:${exerciseKey}`}
                  fen={exercise.fen}
                  moves={shownMoves}
                  interactive={!resolved && !isSubmitting}
                  onMove={handleMove}
                  hintFrom={hintUci ? hintUci.slice(0, 2) : null}
                />
              </div>
              <p className="mt-3 text-center text-sm text-white/60">
                Calculate <span className="font-semibold text-white">{exercise.n} moves deep</span>
                {' '}· ply {Math.min(shownMoves.length + 1, totalPlies)} of {totalPlies}
                {exercise.side_to_move === 'black' ? ' · Black to move' : ' · White to move'}
              </p>
            </section>

            {/* ============ RIGHT: MOVES + ACTIONS ============ */}
            <section className="order-3 mx-auto flex w-full max-w-[420px] flex-col gap-5 xl:order-none xl:mx-0 xl:mt-0 xl:max-w-none xl:min-h-0 xl:h-[min(calc(100vw-800px),calc(100vh-70px))]">
              <div className="wooden-scroll xl:min-h-0 xl:overflow-y-auto">
                <div className={`${CARD_CLASS} p-5`}>
                  {!resolved ? (
                    <>
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2.5">
                          <span className="inline-block h-2.5 w-2.5 animate-pulse rounded-full bg-amber-300" />
                          <span className="text-[11px] font-bold uppercase tracking-[0.3em] text-amber-200/80">
                            Calculate {exercise.n} deep
                          </span>
                        </div>
                      </div>
                      <h2 className="mt-3 font-display text-[26px] font-semibold leading-[1.15] text-[#f7e5c6]">
                        {frontier.sideLabel} to play
                      </h2>
                      <div className="mt-2 flex items-center gap-2">
                        <span className="rounded-full bg-white/10 px-2 py-0.5 text-xs font-bold text-white/60">
                          {frontier.isSolverPly ? 'your move' : 'expected reply'}
                        </span>
                        {hintsUsed > 0 && (
                          <span className="text-[11px] text-white/40">
                            {hintsUsed} hint{hintsUsed === 1 ? '' : 's'} · unrated
                          </span>
                        )}
                      </div>
                      <div className="mt-5 grid grid-cols-3 gap-3 border-t border-white/10 pt-4">
                        <button
                          type="button"
                          onClick={handleTakeback}
                          disabled={(moves.length === 0 && !hintUci) || isSubmitting}
                          className="relative flex items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-3 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span>Takeback</span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void requestHint()}
                          disabled={
                            isHintLoading ||
                            isSubmitting ||
                            moves.length >= totalPlies
                          }
                          className="relative flex items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-2 py-3 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <span>
                            {hintUci ? 'Solution' : 'Hint'}
                          </span>
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleSubmit()}
                          disabled={moves.length !== totalPlies || isSubmitting}
                          title="Submit line"
                          className="relative flex items-center justify-center gap-2 rounded-xl bg-[#10b981]/80 px-2 py-3 text-sm font-bold text-black transition hover:bg-[#10b981] disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          <span>
                            {isSubmitting ? 'Grading…' : 'Submit'}
                          </span>
                        </button>
                      </div>
                    </>
                  ) : result.solved ? (
                    <div className="flex flex-col items-center justify-center gap-2.5 py-6 text-center">
                      <span className="text-[#10b981]">
                        <svg
                          className="h-10 w-10"
                          fill="none"
                          stroke="currentColor"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          strokeWidth="2"
                          viewBox="0 0 24 24"
                          aria-hidden="true"
                        >
                          <circle cx="12" cy="12" r="9" />
                          <path d="m8.5 12 2.5 2.5 4.5-5" />
                        </svg>
                      </span>
                      <h2 className="font-display text-[30px] font-semibold leading-[1.15] text-[#f7e5c6]">
                        Solved.
                      </h2>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="text-[#d9b87c]">
                          <BreakIcon />
                        </span>
                        <h2 className="font-display text-[26px] font-semibold leading-[1.15] text-[#f7e5c6]">
                          Not quite.
                        </h2>
                      </div>
                      {result.first_divergence_ply != null && (
                        <div className="mt-2 text-sm text-white/70">
                          <p>
                            Diverged at {plyLabel(result.first_divergence_ply).toLowerCase()}
                            {' '}({result.diverged_on === 'solver' ? 'your move' : 'the reply'}).
                          </p>
                          <div
                            className={`mt-4 grid gap-3 ${
                              result.attempt_id ? 'grid-cols-3' : 'grid-cols-2'
                            }`}
                          >
                            <button
                              type="button"
                              onClick={startLinePlayout}
                              className="relative flex items-center justify-center gap-2 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-2 py-3 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261]"
                            >
                              <span>
                                {playoutPlies
                                  ? playoutPlaying
                                    ? 'Playing…'
                                    : 'Replay'
                                  : 'Watch line'}
                              </span>
                            </button>
                            <button
                              type="button"
                              onClick={() => void handleRetry()}
                              disabled={isRetrying}
                              className="relative flex items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-3 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              <span>{isRetrying ? '…' : 'Retry'}</span>
                            </button>
                            {result.attempt_id && (
                              <button
                                type="button"
                                onClick={() => void requestWhy(result.attempt_id as string, result, moves)}
                                disabled={
                                  whyState.status === 'loading' ||
                                  whyState.status === 'done'
                                }
                                className="relative flex items-center justify-center gap-2 rounded-xl border border-white/15 bg-white/5 px-2 py-3 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50"
                              >
                                <span>
                                  {whyState.status === 'loading'
                                    ? 'Analyzing…'
                                    : whyState.status === 'done'
                                      ? 'Explained'
                                      : whyState.status === 'error'
                                        ? 'Try again'
                                        : 'Show why'}
                                </span>
                              </button>
                            )}
                          </div>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>

              {resolved && (
                <button
                  type="button"
                  onClick={() => {
                    void loadNext();
                  }}
                  disabled={isAdvancing || isRetrying}
                  className={`${CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5 disabled:opacity-60`}
                >
                  {isAdvancing && (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-transparent" />
                  )}
                  {isAdvancing ? 'Loading…' : 'Next'}
                </button>
              )}
              {nextError && (
                <div
                  role="alert"
                  className="shrink-0 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100"
                >
                  {nextError}
                </div>
              )}

              <WoodpeckerPromoCard dueCount={dueCount} peckSignal={woodpeckerPeck} />
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
