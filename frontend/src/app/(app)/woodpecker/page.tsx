'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Image from 'next/image';
import Link from 'next/link';
import type { BoardApi } from '@/components/board/ChessBoard';
import type { Puzzle } from '@/types';

const ChessBoard = dynamic(() => import('@/components/board/ChessBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full max-w-[700px] animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

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

type Feedback = 'idle' | 'correct' | 'inaccurate' | 'mistake';

function ClockIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8h.01" />
      <path d="M11 12h1v4h1" />
    </svg>
  );
}

function ExitIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" viewBox="0 0 24 24">
      <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
      <path d="m16 17 5-5-5-5" />
      <path d="M21 12H9" />
    </svg>
  );
}

function KnightIcon() {
  return (
    <svg
      className="h-20 w-20"
      viewBox="0 0 64 64"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M22 56h28" />
      <path d="M25 56V42h6l-2-4-4-2 2-4 4-2 4-6-2-4-4-2 2-4 4 2 4 4 2 6 4 4v22" />
      <path d="M27 42h22" />
      <path d="M33 18v4" />
      <path d="M27 30v4" />
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

function AlertCircleIcon({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle cx="12" cy="12" r="10" fill="currentColor" opacity="0.18" />
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="1.5" />
      <path d="M12 7v6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      <circle cx="12" cy="16.5" r="1" fill="currentColor" />
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

function CogIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.6" viewBox="0 0 24 24">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
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

function getSideToMove(puzzle: Puzzle | null) {
  if (!puzzle) return 'White';
  return (puzzle.fen || '').split(/\s+/)[1] === 'b' ? 'Black' : 'White';
}

export default function WoodpeckerPage() {
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
  const [completedCount, setCompletedCount] = useState(0);

  const currentEntry = queue?.[currentIndex] ?? null;
  const currentPuzzle = currentEntry ? puzzles[currentEntry.puzzle_id] : null;
  const sideToMove = getSideToMove(currentPuzzle ?? null);

  const total = queue?.length ?? 0;
  const remaining = Math.max(total - completedCount, 0);
  const estimatedMinutes = Math.max(1, Math.ceil(remaining * 0.75));
  const cellTotal = Math.max(total, 1);

  const clearAdvanceTimeout = useCallback(() => {
    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
      advanceTimeoutRef.current = null;
    }
  }, []);

  const loadQueue = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const res = await fetch('/api/woodpecker/queue', { cache: 'no-store' });
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
      await Promise.all(
        uniqueIds.map(async (id) => {
          const r = await fetch(`/api/puzzles/${encodeURIComponent(id)}`, { cache: 'no-store' });
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
      setPuzzles(fetched);
    } catch (e) {
      console.error('Woodpecker queue load failed:', e);
      setError(e instanceof Error ? e.message : 'Failed to load queue');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

  useEffect(() => {
    startTimeRef.current = Date.now();
    return () => clearAdvanceTimeout();
  }, [currentIndex, clearAdvanceTimeout]);

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

  const queueIsEmpty = !isLoading && queue !== null && queue.length === 0;
  const queueFinished = !isLoading && queue !== null && currentIndex >= queue.length;

  const classificationRowState = (target: Feedback) => {
    if (feedback === 'idle') {
      return {
        iconColor: 'text-white/60',
        titleColor: 'text-white/80',
        bodyOpacity: 'opacity-100',
      };
    }
    if (feedback === target) {
      return {
        iconColor: 'text-emerald-400',
        titleColor: 'text-emerald-300',
        bodyOpacity: 'opacity-100',
      };
    }
    return {
      iconColor: 'text-white/25',
      titleColor: 'text-white/30',
      bodyOpacity: 'opacity-40',
    };
  };

  const showResult = feedback !== 'idle';
  const showingBoard = !!currentPuzzle && !queueFinished && !queueIsEmpty;

  return (
    <div className="min-h-[calc(100vh-2.25rem)] -mt-2 overflow-y-auto text-white [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <div className="mx-auto max-w-[1280px] px-6 pb-12 pt-6 lg:px-10">
        {isLoading ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
              <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
              <p className="text-white/60">Loading your reviews...</p>
            </div>
          </div>
        ) : error ? (
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
        ) : queueIsEmpty || queueFinished ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} w-full max-w-md p-8 text-center shadow-2xl shadow-black/30`}>
              <Image
                src="/woodpecker-bird-v2.png"
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
        ) : showingBoard ? (
          <div className="grid items-start justify-center gap-6 xl:grid-cols-[420px_minmax(0,calc(100vh-70px))_420px]">
            {/* ============== LEFT CARD ============== */}
            <section className="flex w-full max-w-[420px] flex-col gap-5">
              <div className={`${CARD_CLASS} mx-auto w-[400px] p-6 shadow-2xl shadow-black/25`}>
                <Image
                  src="/woodpecker-bird-v2.png"
                  alt=""
                  width={120}
                  height={120}
                  className="mx-auto mb-3 h-[120px] w-[120px] object-contain"
                />
                <div className="text-center">
                  <div className="text-[11px] font-bold uppercase tracking-[0.3em] text-[#f7e5c6]/60">
                    Woodpecker
                  </div>
                  <div className="mt-1 text-xs text-white/50">Puzzle</div>
                  <div className="mt-1 text-[44px] font-bold leading-none text-[#f7e5c6]">
                    {Math.min(currentIndex + 1, cellTotal)} <span className="text-white/40">/ {cellTotal}</span>
                  </div>
                </div>

                <div className="mt-5">
                  <ProgressCells completed={currentIndex} total={cellTotal} />
                </div>

                <div className="my-6 border-t border-white/10" />

                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-full border border-white/15 text-white/70">
                    <ClockIcon />
                  </div>
                  <div className="flex-1">
                    <div className="text-[11px] uppercase tracking-wider text-white/50">Remaining</div>
                    <div className="text-2xl font-bold text-[#f7e5c6]">{remaining}</div>
                  </div>
                </div>
                <div className="mt-2 pl-[52px] text-xs text-white/50">
                  Estimated time: {estimatedMinutes} min
                </div>

                <div className="my-6 border-t border-white/10" />

                <div className="rounded-xl border border-white/10 bg-black/25 p-4">
                  <div className="mb-2 flex items-center gap-2 text-[#f7e5c6]/80">
                    <InfoIcon />
                    <span className="text-sm font-semibold">Focus</span>
                  </div>
                  <p className="pl-7 text-sm leading-relaxed text-white/60">
                    These are your reviews. Take your time and think carefully.
                  </p>
                </div>

                <div className="mt-6">
                  <Link
                    href="/puzzles"
                    onClick={handleExit}
                    className="flex w-full items-center justify-center gap-2 rounded-lg border border-[#f7e5c6]/30 bg-transparent px-6 py-3 text-base font-semibold text-[#f7e5c6] transition hover:border-[#f7e5c6]/60 hover:bg-[#f7e5c6]/5"
                  >
                    <ExitIcon />
                    Exit Review
                  </Link>
                </div>
              </div>
            </section>

            {/* ============== CENTER: CHESSBOARD ============== */}
            <section className="overflow-visible">
              <div className="relative mx-auto w-full max-w-[calc(100vh-70px)]">
                {currentEntry && (
                  <div className="pointer-events-none absolute -top-6 right-0 z-10 rounded-full px-3 py-1 text-sm text-white/70">
                    <span className="capitalize">{currentEntry.theme.replace(/_/g, ' ')}</span>
                  </div>
                )}
                <div className="w-full">
                  <ChessBoard
                    puzzle={currentPuzzle}
                    playerElo={1100}
                    onPuzzleSolved={handlePuzzleSolved}
                    onPuzzleFailed={handlePuzzleFailed}
                    onPuzzleEnd={handlePuzzleEnd}
                    apiRef={boardApi}
                  />
                </div>
                <div className="mt-3 flex items-center justify-center gap-2 text-sm text-white/60">
                  <span className="inline-block h-2 w-2 rounded-full bg-white" />
                  <span>{sideToMove} to move</span>
                </div>
              </div>
            </section>

            {/* ============== RIGHT CARD ============== */}
            <section className="flex w-full max-w-[420px] flex-col gap-5">
              <div className={`${CARD_CLASS} mx-auto w-[400px] p-6 shadow-2xl shadow-black/25`}>
                <div className="mb-1 flex items-center gap-2">
                  <span
                    className={`text-xs font-bold uppercase tracking-[0.25em] ${
                      showResult ? 'text-white/40' : 'text-emerald-400'
                    }`}
                  >
                    {showResult ? 'Result' : 'Your Move'}
                  </span>
                </div>
                <h2 className="text-xl font-semibold text-[#f7e5c6]">
                  {showResult
                    ? feedback === 'correct'
                      ? 'Nice — keep going'
                      : 'Logged for review'
                    : `Find the best move for ${sideToMove}.`}
                </h2>

                <div className="my-6 flex flex-col items-center justify-center text-[#f7e5c6]/50">
                  <KnightIcon />
                  <p className="mt-4 text-sm text-white/50">
                    {showResult
                      ? 'Move on to the next review when you\'re ready.'
                      : 'Make your move on the board'}
                  </p>
                </div>

                <div className="border-t border-white/10 pt-5">
                  <div className="mb-3 text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                    After you move
                  </div>
                  <ul className="space-y-3">
                    {(() => {
                      const correct = classificationRowState('correct');
                      const inaccurate = classificationRowState('inaccurate');
                      const mistake = classificationRowState('mistake');
                      return (
                        <>
                          <li className="flex items-start gap-3">
                            <CheckCircleIcon className="mt-0.5 h-5 w-5 shrink-0" />
                            <div className={correct.bodyOpacity}>
                              <div className={`text-sm font-semibold ${correct.titleColor}`}>Correct</div>
                              <div className="text-xs text-white/50">Great! You&apos;ve reinforced this idea.</div>
                            </div>
                          </li>
                          <li className="flex items-start gap-3">
                            <AlertCircleIcon className="mt-0.5 h-5 w-5 shrink-0" />
                            <div className={inaccurate.bodyOpacity}>
                              <div className={`text-sm font-semibold ${inaccurate.titleColor}`}>Inaccurate</div>
                              <div className="text-xs text-white/50">Not the best. Let&apos;s see why.</div>
                            </div>
                          </li>
                          <li className="flex items-start gap-3">
                            <XCircleIcon className="mt-0.5 h-5 w-5 shrink-0" />
                            <div className={mistake.bodyOpacity}>
                              <div className={`text-sm font-semibold ${mistake.titleColor}`}>Mistake</div>
                              <div className="text-xs text-white/50">This one hurts. Let&apos;s learn from it.</div>
                            </div>
                          </li>
                        </>
                      );
                    })()}
                  </ul>
                </div>

                <div className="mt-6 flex items-center justify-between border-t border-white/10 pt-5">
                  <span className="text-sm font-semibold text-white/80">Auto-advance</span>
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => setAutoAdvance((v) => !v)}
                      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                        autoAdvance ? 'bg-emerald-500' : 'bg-white/15'
                      }`}
                      aria-pressed={autoAdvance}
                      aria-label="Toggle auto-advance"
                    >
                      <span
                        className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                          autoAdvance ? 'translate-x-5' : 'translate-x-0.5'
                        }`}
                      />
                    </button>
                    <button
                      type="button"
                      className="text-white/50 transition hover:text-white"
                      aria-label="Settings"
                    >
                      <CogIcon />
                    </button>
                  </div>
                </div>

                {!autoAdvance && feedback !== 'idle' && (
                  <button
                    onClick={handleNextClick}
                    className="mt-4 w-full rounded-lg bg-emerald-500 py-2.5 text-sm font-semibold text-white shadow-lg shadow-emerald-950/40 transition hover:bg-emerald-400"
                  >
                    Next Review
                  </button>
                )}
              </div>
            </section>
          </div>
        ) : null}
      </div>
    </div>
  );
}
