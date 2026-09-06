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
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

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

  useEffect(() => {
    loadQueue();
  }, [loadQueue]);

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

  const showResult = feedback !== 'idle';
  const hasQueueRemaining = !isLoading && queue !== null && !queueFinished && !queueIsEmpty;
  const showingBoard = !!currentPuzzle && hasQueueRemaining;
  const puzzleUnavailable = hasQueueRemaining && !currentPuzzle;

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-hidden px-6 pb-[10px] pt-6 text-white lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      <div className="mx-auto h-full w-full max-w-[1760px]">
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
        ) : puzzleUnavailable ? (
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
        ) : showingBoard ? (
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
                    playerElo={1100}
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
        ) : null}
      </div>
    </div>
  );
}
