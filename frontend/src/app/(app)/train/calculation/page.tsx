'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import { Chess } from 'chess.js';

import {
  CalcFetchError,
  depthForRating,
  fetchCalcRatings,
  fetchNextCalcExercise,
  fetchWhy,
  submitCalcLine,
} from '@/lib/calc';
import { type CalcBoardProps } from '@/components/board/CalcBoard';
import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';
import type { CalcExercise, CalcSubmitResult, CalcWhyEval, CalcWhyResult } from '@/types';

// Same dynamic-import contract as the other trainer boards
// (react-chessboard is client-only).
const ReplayBoard = dynamic(
  () => import('@/components/board/WhyReplayBoard'),
  {
    ssr: false,
    loading: () => (
      <div className="flex aspect-square w-full animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40">
        <span className="text-xs text-white/50">Loading replay…</span>
      </div>
    ),
  }
);

const Board = dynamic<CalcBoardProps>(() => import('@/components/board/CalcBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

/**
 * Backend-driven explanation rendering: only the templated headline, plain
 * eval text, and the engine line on a replay board. No LLM text anywhere.
 */
function WhyResult({
  result,
  fenBefore,
  evalText,
}: {
  result: CalcWhyResult;
  fenBefore: string;
  evalText: (e: CalcWhyEval | null) => string;
}) {
  const mateBefore = result.params['mate_before'];
  const mateAfter = result.params['mate_after'];
  return (
    <div className="mt-2">
      <p className="text-sm font-semibold text-[#f7e5c6]">{result.headline}</p>
      <p className="mt-1 text-xs text-white/60">
        Before (you): {evalText(result.eval_before)}
        {'  ·  '}After: {evalText(result.eval_after)}
      </p>
      {result.type === 'slower_win' &&
        typeof mateBefore === 'number' &&
        typeof mateAfter === 'number' && (
          <p className="mt-1 text-xs text-white/60">
            Mate in {mateBefore} → mate in {mateAfter}.
          </p>
        )}
      {result.type === 'also_fine' && (
        <p className="mt-1 text-xs text-white/60">
          The trainer asked for the main line, but your move keeps the advantage.
        </p>
      )}
      {result.line_uci.length > 0 && (
        <div className="mt-2">
          <ReplayBoard
            fenBefore={fenBefore}
            lineUci={result.line_uci}
            truncatedAtPly={result.truncated_at_ply}
          />
        </div>
      )}
    </div>
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

  const startExercise = useCallback((next: CalcExercise) => {
    setExercise(next);
    setMoves([]);
    setResult(null);
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
      setMoves((prev) => (prev.length < totalPlies ? [...prev, uci] : prev));
    },
    [resolved, isSubmitting, totalPlies]
  );

  const handleTakeback = useCallback(() => {
    if (resolved || isSubmitting) return;
    setMoves((prev) => prev.slice(0, -1));
  }, [resolved, isSubmitting]);

  const handleSubmit = useCallback(async () => {
    if (!exercise || moves.length !== totalPlies || isSubmitting) return;
    setIsSubmitting(true);
    try {
      const graded = await submitCalcLine(exercise.exercise_token, moves);
      setResult(graded);
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
  }, [exercise, moves, totalPlies, isSubmitting]);

  /** Show solution: deliberately fail to reveal the line. The failed
   * attempt is recorded (and queued for review) -- the auto-fail semantics
   * agreed for reveals, using only the existing submit path. The spoiler
   * is the last legal move, which is wrong with overwhelming probability;
   * even in the freak case it matches, grading simply reveals from later. */
  const handleShowSolution = useCallback(async () => {
    if (!exercise || resolved || isSubmitting) return;
    setIsSubmitting(true);
    try {
      const probe = new Chess(exercise.fen);
      const legal = probe.moves({ verbose: true });
      const spoiler =
        legal.length > 0
          ? (() => {
              const m = legal[legal.length - 1];
              return m.from + m.to + (m.promotion ?? '');
            })()
          : 'a2a3';
      const line = [spoiler];
      // Pad to full length so the shape validates; grading stops at the
      // first divergence anyway.
      while (line.length < totalPlies) line.push(spoiler);
      const graded = await submitCalcLine(exercise.exercise_token, line);
      setResult(graded);
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
  }, [exercise, resolved, isSubmitting, totalPlies]);

  /** Render entered plies and continuations in SAN for readability. */
  const sanFor = useCallback(
    (line: string[], fromFen: string): string[] => {
      const game = new Chess(fromFen);
      const out: string[] = [];
      for (const uci of line) {
        try {
          out.push(
            game.move({
              from: uci.slice(0, 2),
              to: uci.slice(2, 4),
              promotion: uci[4] ?? undefined,
            }).san
          );
        } catch {
          out.push(uci);
        }
      }
      return out;
    },
    []
  );

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

  /** Plain-text eval, always from your side (backend scores are user-POV). */
  const evalText = useCallback((e: CalcWhyEval | null): string => {
    if (!e) return '—';
    if (e.mate != null) {
      return e.mate > 0 ? `Mate in ${e.mate} for you` : `Mated in ${-e.mate}`;
    }
    const parts: string[] = [];
    if (e.cp != null) parts.push(`${e.cp >= 0 ? '+' : ''}${(e.cp / 100).toFixed(2)}`);
    if (e.win_prob != null) parts.push(`${Math.round(e.win_prob * 100)}%`);
    return parts.length > 0 ? parts.join(' · ') : '—';
  }, []);

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
    async (attemptId: string) => {
      setWhyState({ status: 'loading' });
      try {
        const res = await fetchWhy(attemptId);
        setWhyState({ status: 'done', result: res });
      } catch (error) {
        const fetchError = error as CalcFetchError;
        if (fetchError.status === 429 && !whyRetriedRef.current) {
          // One auto-retry after 3 s; afterwards the manual button owns it.
          whyRetriedRef.current = true;
          setTimeout(() => {
            void (async () => {
              try {
                const res = await fetchWhy(attemptId);
                setWhyState({ status: 'done', result: res });
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
                    Calculation Rating · {exercise.n} moves
                  </div>
                  <div className="mt-1.5 flex items-end gap-2.5">
                    <span className="font-display text-[34px] font-semibold leading-none text-[#f7e5c6]">
                      {rating ?? '-'}
                    </span>
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

              <div className={`${CARD_CLASS} shrink-0 p-5`}>
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                  How it works
                </div>
                <p className="mt-2 text-[13px] leading-5 text-white/70">
                  Calculate the full line in your head, then enter your moves
                  and the replies you expect. Nothing is graded until you
                  submit.
                </p>
              </div>
            </section>

            {/* ============ CENTER: BOARD ============ */}
            <section className="order-1 min-h-0 min-w-0 xl:order-none xl:mt-0">
              <div className="relative mx-auto mt-6 aspect-square w-full max-w-[calc(100vh-70px)] xl:mt-0">
                <Board
                  key={`${exercise.segment_id}:${exercise.offset}:${exercise.n}:${exerciseKey}`}
                  fen={exercise.fen}
                  moves={moves}
                  interactive={!resolved && !isSubmitting}
                  onMove={handleMove}
                />
              </div>
              <p className="mt-3 text-center text-sm text-white/60">
                Calculate <span className="font-semibold text-white">{exercise.n} moves deep</span>
                {' '}· ply {Math.min(moves.length + 1, totalPlies)} of {totalPlies}
                {exercise.side_to_move === 'black' ? ' · Black to move' : ' · White to move'}
              </p>
            </section>

            {/* ============ RIGHT: MOVES + ACTIONS ============ */}
            <section className="order-3 mx-auto flex w-full max-w-[420px] flex-col gap-5 xl:order-none xl:mx-0 xl:mt-0 xl:max-w-none xl:min-h-0 xl:h-[min(calc(100vw-800px),calc(100vh-70px))]">
              <div className="wooden-scroll xl:min-h-0 xl:overflow-y-auto">
                <div className={`${CARD_CLASS} p-5`}>
                  {!resolved ? (
                    <>
                      <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                        Your line
                      </div>
                      {moves.length === 0 ? (
                        <p className="mt-2 text-sm text-white/50">
                          Play your first move on the board — then the reply
                          you expect, alternating until the line is complete.
                        </p>
                      ) : (
                        <ol className="mt-2 space-y-1">
                          {sanFor(moves, exercise.fen).map((san, i) => (
                            <li key={i} className="flex gap-2 text-sm">
                              <span className="w-24 shrink-0 text-white/40">
                                {plyLabel(i)}
                              </span>
                              <span className="font-semibold text-white">{san}</span>
                            </li>
                          ))}
                        </ol>
                      )}
                      <div className="mt-4 flex gap-2">
                        <button
                          type="button"
                          onClick={handleTakeback}
                          disabled={moves.length === 0 || isSubmitting}
                          className="rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10 disabled:opacity-40"
                        >
                          Takeback
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleSubmit()}
                          disabled={moves.length !== totalPlies || isSubmitting}
                          className="flex-1 rounded-lg bg-[#10b981]/80 px-4 py-2 text-sm font-bold text-black transition hover:bg-[#10b981] disabled:opacity-40"
                        >
                          {isSubmitting
                            ? 'Grading…'
                            : `Submit line (${moves.length}/${totalPlies})`}
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={() => void handleShowSolution()}
                        disabled={isSubmitting}
                        className="mt-2 w-full rounded-lg border border-white/10 px-4 py-2 text-xs font-semibold text-white/50 transition hover:bg-white/5 hover:text-white/80 disabled:opacity-40"
                      >
                        Show solution (counts as failed)
                      </button>
                    </>
                  ) : (
                    <>
                      <div
                        className={`text-[11px] font-bold uppercase tracking-[0.25em] ${
                          result.solved ? 'text-[#10b981]' : 'text-red-300'
                        }`}
                      >
                        {result.solved ? 'Correct — full line!' : 'Not quite'}
                      </div>
                      {!result.solved && result.first_divergence_ply != null && (
                        <div className="mt-2 text-sm text-white/70">
                          <p>
                            Diverged at {plyLabel(result.first_divergence_ply).toLowerCase()}
                            {' '}({result.diverged_on === 'solver' ? 'your move' : 'the reply'}).
                          </p>
                          <div className="mt-2 text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                            Correct continuation
                          </div>
                          <ol className="mt-1 space-y-1">
                            {sanFor(result.continuation, divergenceFen).map((san, i) => (
                              <li key={i} className="flex gap-2 text-sm">
                                <span className="w-24 shrink-0 text-white/40">
                                  {plyLabel((result.first_divergence_ply ?? 0) + i)}
                                </span>
                                <span className="font-semibold text-white">{san}</span>
                              </li>
                            ))}
                          </ol>
                          <p className="mt-2 text-xs text-white/40">
                            This exercise was queued for review.
                          </p>
                          {result.attempt_id && (
                            <div className="mt-3 border-t border-white/10 pt-3">
                              {(whyState.status === 'idle' || whyState.status === 'error') && (
                                <>
                                  {whyState.status === 'error' && (
                                    <p role="alert" className="mb-2 text-xs text-amber-100/90">
                                      {whyState.message}
                                    </p>
                                  )}
                                  <button
                                    type="button"
                                    onClick={() => void requestWhy(result.attempt_id as string)}
                                    className="w-full rounded-lg border border-[#d9b87c]/40 bg-[#d9b87c]/10 px-4 py-2 text-sm font-semibold text-[#f7e5c6] transition hover:bg-[#d9b87c]/20"
                                  >
                                    {whyState.status === 'error' ? 'Try again' : 'Show why'}
                                  </button>
                                </>
                              )}
                              {whyState.status === 'loading' && (
                                <div className="flex items-center gap-2 py-2 text-sm text-white/60">
                                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#d9b87c] border-t-transparent" />
                                  Analyzing your move…
                                </div>
                              )}
                              {whyState.status === 'done' && (
                                <WhyResult
                                  result={whyState.result}
                                  fenBefore={divergenceFen}
                                  evalText={evalText}
                                />
                              )}
                            </div>
                          )}
                        </div>
                      )}
                      {result.rated && (
                        <p className="mt-2 text-sm text-white/70">
                          Rating: {result.rating_before} → {result.rating_after}{' '}
                          <span className={result.delta >= 0 ? 'text-[#10b981]' : 'text-red-300'}>
                            ({result.delta >= 0 ? '+' : ''}{result.delta})
                          </span>
                        </p>
                      )}
                    </>
                  )}
                </div>
              </div>

              <button
                type="button"
                onClick={() => {
                  void loadNext();
                }}
                disabled={isAdvancing}
                className={`${CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5 disabled:opacity-60`}
              >
                {isAdvancing && (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-transparent" />
                )}
                {isAdvancing ? 'Loading…' : resolved ? 'Next Exercise' : 'Skip Exercise'}
              </button>
              {nextError && (
                <div
                  role="alert"
                  className="shrink-0 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100"
                >
                  {nextError}
                </div>
              )}

              <button
                type="button"
                onClick={() => { window.location.href = '/woodpecker'; }}
                className={`${CARD_CLASS} flex w-full shrink-0 items-center justify-between p-4 text-left transition hover:bg-white/5`}
              >
                <span>
                  <span className="block text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                    Reviews due
                  </span>
                  <span className="mt-1 block text-sm font-semibold text-white">
                    {dueCount == null
                      ? 'Spaced repetition queue'
                      : dueCount === 0
                        ? 'Queue clear — nothing due'
                        : `${dueCount} review${dueCount === 1 ? '' : 's'} due`}
                  </span>
                </span>
                <span aria-hidden="true" className="text-xl text-[#d9b87c]">→</span>
              </button>
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
