'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Image from 'next/image';

import { Puzzle } from '@/types';
import { fetchPuzzleBatch, getPuzzleDifficultyLabel } from '@/lib/lichess';
import { puzzleThemeLabel } from '@/lib/themes';
import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';
import type { BoardApi } from '@/components/board/ChessBoard';
import PuzzleStatusPanel from '@/components/puzzles/PuzzleStatusPanel';
import ThemePickerCard from '@/components/puzzles/ThemePickerCard';
import WoodpeckerPromoCard from '@/components/puzzles/WoodpeckerPromoCard';
import { PUZZLE_CARD_CLASS } from '@/components/puzzles/puzzleStyles';

// Dynamically import chessboard to avoid SSR issues
const ChessBoard = dynamic(() => import('@/components/board/ChessBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full max-w-[700px] animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

const SLOW_THRESHOLD_FLOOR_SECONDS = 45;
const SLOW_THRESHOLD_SECONDS_PER_RATING_POINT_GAP = 1 / 10;
const SLOW_THRESHOLD_CAP_SECONDS = 180;

const BATCH_SIZE = 10;
const WOODPECKER_SKIP_MESSAGES: Record<string, string> = {
  daily_cap_reached: 'Daily practice queue limit reached - try again tomorrow.',
};

function getSideToMove(puzzle: Puzzle) {
  return (puzzle.fen || '').split(/\s+/)[1] === 'b' ? 'black' : 'white';
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

export default function PuzzlesPage() {
  const boardApi = useRef<BoardApi | null>(null);
  const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [result, setResult] = useState<'solved' | 'failed' | null>(null);
  const [selectedTheme, setSelectedTheme] = useState<string | null>(null);
  const [themeLoadError, setThemeLoadError] = useState<string | null>(null);
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [currentRating, setCurrentRating] = useState<number | null>(null);
  const [reviewsDue, setReviewsDue] = useState<number | null>(null);
  const [woodpeckerNotice, setWoodpeckerNotice] = useState<string | null>(null);
  const [woodpeckerPeck, setWoodpeckerPeck] = useState(0);
  const [isFetchingMore, setIsFetchingMore] = useState(false);
  const loadIdRef = useRef(0);
  const hasScoredAttemptRef = useRef(false);
  const hasPeckedRef = useRef(false);
  const fetchingMoreRef = useRef(false);
  const stuckAtEndRef = useRef(false);
  const advanceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const settingsRef = useRef<HTMLDivElement>(null);

  const clearAdvanceTimeout = useCallback(() => {
    if (advanceTimeoutRef.current) {
      clearTimeout(advanceTimeoutRef.current);
      advanceTimeoutRef.current = null;
    }
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
    if (!autoAdvance) {
      clearAdvanceTimeout();
    }
  }, [autoAdvance, clearAdvanceTimeout]);

  useEffect(() => {
    return () => clearAdvanceTimeout();
  }, [currentIndex, clearAdvanceTimeout]);

  const fetchReviewsDue = useCallback(() => {
    fetch('/api/woodpecker/queue', { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (Array.isArray(data)) {
          setReviewsDue(data.length);
        }
      })
      .catch((error) => console.error('Failed to fetch woodpecker queue:', error));
  }, []);

  // Themed practice is unrated: a theme only swaps the puzzle source, so the
  // batch is filtered server-side and, unlike the rated loop, never falls back
  // to unrelated classic puzzles (an empty theme should say so, not lie).
  const loadPuzzles = useCallback(async () => {
    const loadId = ++loadIdRef.current;
    setIsLoading(true);
    setThemeLoadError(null);
    try {
      const newPuzzles = await fetchPuzzleBatch(
        BATCH_SIZE,
        selectedTheme ?? undefined,
        undefined,
        undefined,
        { allowFallback: selectedTheme === null }
      );
      if (loadId !== loadIdRef.current) return;
      setPuzzles(newPuzzles);
      setCurrentIndex(0);
    } catch (error) {
      if (loadId !== loadIdRef.current) return;
      console.error('Failed to load puzzles:', error);
      setPuzzles([]);
      setThemeLoadError(
        selectedTheme
          ? `No ${puzzleThemeLabel(selectedTheme)} puzzles available right now.`
          : 'Could not load puzzles.'
      );
    } finally {
      if (loadId === loadIdRef.current) setIsLoading(false);
    }
  }, [selectedTheme]);

  // Background prefetch: keep the buffer topped up so play never stalls.
  // Append another BATCH_SIZE whenever <=3 unsolved puzzles remain.
  const prefetchPuzzles = useCallback(async () => {
    if (fetchingMoreRef.current) return;
    fetchingMoreRef.current = true;
    setIsFetchingMore(true);
    try {
      const more = await fetchPuzzleBatch(
        BATCH_SIZE,
        selectedTheme ?? undefined,
        undefined,
        undefined,
        { allowFallback: selectedTheme === null }
      );
      if (more && more.length) {
        setPuzzles((prev) => [...prev, ...more]);
      }
    } catch (error) {
      console.error('Failed to prefetch puzzles:', error);
    } finally {
      fetchingMoreRef.current = false;
      setIsFetchingMore(false);
    }
  }, [selectedTheme]);

  useEffect(() => {
    void loadPuzzles();
  }, [loadPuzzles]);

  useEffect(() => {
    if (isLoading || puzzles.length === 0) return;
    if (puzzles.length - currentIndex <= 3) {
      prefetchPuzzles();
    }
  }, [currentIndex, puzzles.length, isLoading, prefetchPuzzles]);

  // Race fallback: if the player clicked Next at the very end of the
  // loaded buffer, auto-advance as soon as the prefetched puzzles land.
  useEffect(() => {
    if (stuckAtEndRef.current && currentIndex + 1 < puzzles.length) {
      stuckAtEndRef.current = false;
      setCurrentIndex((i) => i + 1);
    }
  }, [puzzles.length, currentIndex]);

  useEffect(() => {
    fetch('/api/user/rating')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.tactical_rating != null) {
          setCurrentRating(data.tactical_rating);
        }
      })
      .catch((error) => console.error('Failed to fetch user rating:', error));

    fetchReviewsDue();
  }, [fetchReviewsDue]);

  useEffect(() => {
    hasScoredAttemptRef.current = false;
    hasPeckedRef.current = false;
    setWoodpeckerNotice(null);
    setResult(null);
  }, [currentIndex]);

  function surfaceWoodpeckerSkip(data: unknown) {
    if (!data || typeof data !== 'object') return;
    const response = data as { skipped?: unknown; reason?: unknown };
    if (response.skipped !== true || typeof response.reason !== 'string') return;

    const message = WOODPECKER_SKIP_MESSAGES[response.reason];
    if (message) {
      setWoodpeckerNotice(message);
    }
  }

  function updatePuzzleRating(puzzle: Puzzle, solved: boolean) {
    fetch('/api/puzzles/rating', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        puzzle_id: puzzle.id,
        puzzle_rating: puzzle.rating,
        solved,
      }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.new_rating != null) {
          setCurrentRating(data.new_rating);
        }
      })
      .catch((error) => console.error('Failed to update rating:', error));
  }

  function enqueueWoodpeckerReview(
    puzzle: Puzzle,
    sourceReason: 'slow_solution' | 'wrong_answer',
  ) {
    fetch('/api/woodpecker/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        puzzle_id: puzzle.id,
        theme: puzzle.themes[0] ?? 'middlegame',
        source_reason: sourceReason,
      }),
    })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        surfaceWoodpeckerSkip(data);
        fetchReviewsDue();
      })
      .catch((error) => console.error('Failed to add woodpecker entry:', error));
  }

  function handlePuzzleSolved(timeSeconds: number) {
    if (!hasScoredAttemptRef.current) {
      hasScoredAttemptRef.current = true;
      const puzzle = puzzles[currentIndex];
      if (puzzle) {
        setResult('solved');

        // Snapshot the rating BEFORE this solve so the slow-threshold
        // computation uses the player's rating as it stood going into
        // this puzzle, not the value updated asynchronously by the
        // rating call below.
        const userRatingBefore = currentRating ?? 0;
        const ratingGap = Math.max(0, puzzle.rating - userRatingBefore);
        const threshold = Math.min(
          SLOW_THRESHOLD_CAP_SECONDS,
          SLOW_THRESHOLD_FLOOR_SECONDS +
            Math.floor(
              ratingGap * SLOW_THRESHOLD_SECONDS_PER_RATING_POINT_GAP,
            ),
        );

        // Theme practice is unrated: no tactical_rating history is written.
        if (selectedTheme === null) {
          updatePuzzleRating(puzzle, true);
        }

        if (timeSeconds > threshold) {
          enqueueWoodpeckerReview(puzzle, 'slow_solution');
        }
      }
    }
  }

  function handlePuzzleFailed() {
    setResult('failed');
    if (!hasScoredAttemptRef.current) {
      hasScoredAttemptRef.current = true;
      const puzzle = puzzles[currentIndex];
      if (puzzle) {
        // Theme practice is unrated: no tactical_rating history is written.
        if (selectedTheme === null) {
          updatePuzzleRating(puzzle, false);
        }

        enqueueWoodpeckerReview(puzzle, 'wrong_answer');
      }
    }
  }

  function handlePuzzleWrongMove() {
    // One peck per puzzle: the first mistake gets the bird's attention,
    // retries on the same position keep it still.
    if (hasPeckedRef.current) return;
    hasPeckedRef.current = true;
    setWoodpeckerPeck((n) => n + 1);
  }

  const handleNextPuzzle = useCallback(() => {
    clearAdvanceTimeout();
    if (currentIndex + 1 < puzzles.length) {
      setCurrentIndex((prev) => prev + 1);
    } else {
      // Ran out of prefetched puzzles - kick off a fetch and wait for
      // it to land; the effect above will auto-advance the index.
      stuckAtEndRef.current = true;
      prefetchPuzzles();
    }
  }, [clearAdvanceTimeout, currentIndex, prefetchPuzzles, puzzles.length]);

  function handleShowSolution() {
    // Treat revealing the solution before a recorded solve like the existing
    // wrong-answer path. The same ref also prevents re-intake after a correct
    // solve or a prior wrong/reveal attempt on this puzzle.
    const shouldIntake = !hasScoredAttemptRef.current;
    if (shouldIntake) {
      handlePuzzleFailed();
    }
    boardApi.current?.showSolution();
  }

  const handlePuzzleEnd = useCallback(() => {
    if (autoAdvance) {
      clearAdvanceTimeout();
      advanceTimeoutRef.current = setTimeout(handleNextPuzzle, 1500);
    }
  }, [autoAdvance, clearAdvanceTimeout, handleNextPuzzle]);

  const handleThemeSelect = useCallback((theme: string | null) => {
    setSelectedTheme((current) => (current === theme ? current : theme));
  }, []);

  const currentPuzzle = puzzles[currentIndex];
  const sideToMoveLabel =
    currentPuzzle && getSideToMove(currentPuzzle) === 'black' ? 'Black' : 'White';
  const themeLabel = selectedTheme ? puzzleThemeLabel(selectedTheme) : null;

  return (
    <div className="min-h-[calc(100vh-2.5rem)] -mt-2 text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center] xl:h-[calc(100vh-2.5rem)] xl:overflow-hidden">
      <div className="mx-auto flex flex-col px-6 pb-1 lg:px-10 xl:h-full">
        {isLoading && puzzles.length === 0 && !themeLoadError ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${PUZZLE_CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
              <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
              <p className="text-white/60">Loading puzzles...</p>
            </div>
          </div>
        ) : themeLoadError && puzzles.length === 0 ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${PUZZLE_CARD_CLASS} max-w-md px-8 py-6 text-center`}>
              <p className="text-white/70">{themeLoadError}</p>
              <button
                type="button"
                onClick={() => {
                  if (selectedTheme) {
                    handleThemeSelect(null);
                  } else {
                    void loadPuzzles();
                  }
                }}
                className="mt-4 w-full rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10"
              >
                {selectedTheme ? 'Back to All Tactics' : 'Try again'}
              </button>
            </div>
          </div>
        ) : (
        <div className="grid gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[20rem_minmax(0,1fr)_22rem] xl:pt-5">
          {/* ============ LEFT: RATING + THEME LIST ============ */}
          <section className="order-2 mt-6 flex min-h-0 flex-col gap-5 xl:order-none xl:mt-0">
            <div
              className={`${WOOD_PANEL_CLASS} flex shrink-0 items-center gap-4 p-5`}
              style={WOOD_PANEL_STYLE}
            >
              {/* The badge is decoration: the rating itself is text. */}
              <Image
                src="/knight-badge.webp"
                alt=""
                width={402}
                height={454}
                className="h-16 w-auto shrink-0 drop-shadow-[0_8px_18px_rgba(0,0,0,0.6)]"
              />
              <div className="min-w-0 flex-1">
                <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                  Tactical Rating
                </div>
                <div className="mt-1.5 flex items-end gap-2.5">
                  <span className="font-display text-[34px] font-semibold leading-none text-[#f7e5c6]">
                    {currentRating ?? '—'}
                  </span>
                  {currentRating != null && (
                    <span className="mb-0.5 rounded-full bg-white/10 px-2 py-0.5 text-xs font-bold text-white/60">
                      {getPuzzleDifficultyLabel(currentRating)}
                    </span>
                  )}
                </div>
                {currentRating == null && (
                  <p className="mt-2 text-[11px] leading-4 text-white/40">
                    Unrated — solve a puzzle to set it.
                  </p>
                )}
              </div>
            </div>

            {/* The theme list: the page's one scroll region, and the switcher
                for the board's puzzle source. */}
            <ThemePickerCard
              activeTheme={selectedTheme}
              onSelect={handleThemeSelect}
            />
          </section>

          {/* ============ CENTER: CHESSBOARD ============ */}
          <section className="order-1 min-h-0 min-w-0 xl:order-none xl:mt-0">
            <div className="relative mx-auto mt-6 aspect-square w-full max-w-[calc(100vh-70px)] xl:mt-0">
              {currentPuzzle && (
                <>
                  <div className="w-full">
                    <ChessBoard
                      puzzle={currentPuzzle}
                      onPuzzleSolved={handlePuzzleSolved}
                      onPuzzleFailed={handlePuzzleFailed}
                      onPuzzleWrongMove={handlePuzzleWrongMove}
                      onPuzzleEnd={handlePuzzleEnd}
                      apiRef={boardApi}
                    />
                  </div>

                  {/* Source swap: the old puzzle stays put under a brief
                      overlay until the new theme's batch is in hand. */}
                  {isLoading && (
                    <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/55 backdrop-blur-[2px]">
                      <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-black/70 px-5 py-3 text-sm text-white/80">
                        <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#d9b87c] border-t-transparent" />
                        {themeLabel
                          ? `Loading ${themeLabel} puzzles…`
                          : 'Loading rated tactics…'}
                      </div>
                    </div>
                  )}
                </>
              )}

              {currentPuzzle && (
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
                    aria-label="Puzzle settings"
                    aria-haspopup="dialog"
                    aria-expanded={isSettingsOpen}
                    title="Puzzle settings"
                  >
                    <SettingsIcon />
                  </button>

                  {isSettingsOpen && (
                    <div
                      role="dialog"
                      aria-label="Puzzle settings"
                      className="absolute right-0 top-full mt-2 w-60 rounded-xl border border-[#d9b87c]/30 bg-[#1b120d]/95 p-3 text-white shadow-[0_18px_42px_rgba(0,0,0,0.52),inset_0_1px_0_rgba(255,255,255,0.08)] backdrop-blur-xl"
                    >
                      <div className="flex items-center justify-between border-b border-white/10 pb-2">
                        <span className="text-[10px] font-bold uppercase tracking-[0.24em] text-[#f7e5c6]/65">
                          Puzzle settings
                        </span>
                        <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[#37be7e]">
                          {autoAdvance ? 'Auto' : 'Manual'}
                        </span>
                      </div>

                      <div className="mt-3 flex items-center justify-between gap-4">
                        <div>
                          <div className="text-sm font-semibold text-[#f7e5c6]">Auto-advance</div>
                          <div className="mt-1 text-[11px] leading-4 text-white/45">
                            Continue after the puzzle ends
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

                      <div className="mt-3 border-t border-white/10 pt-2 text-[10px] text-white/40">
                        {autoAdvance
                          ? 'Next puzzle loads after a short pause.'
                          : 'Use Next Puzzle when you are ready.'}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </section>

          {/* ============ RIGHT: STATUS + WOODPECKER ============ */}
          <section className="order-3 mx-auto flex w-full max-w-[420px] flex-col gap-5 xl:order-none xl:mx-0 xl:mt-0 xl:max-w-none xl:min-h-0">
            {/* Invisible scroll fallback, same as the trainer's right
                column: only the tall resolved states on a short screen
                ever need it; the Next control below stays pinned. */}
            <div className="wooden-scroll xl:min-h-0 xl:overflow-y-auto">
              {currentPuzzle && (
                <PuzzleStatusPanel
                  sideToMoveLabel={sideToMoveLabel}
                  themeLabel={themeLabel}
                  result={result}
                  onHint={() => boardApi.current?.showHint()}
                  onShowSolution={handleShowSolution}
                />
              )}
            </div>

            {/* The Endgame Trainer's compact Woodpecker format. */}
            <WoodpeckerPromoCard
              dueCount={reviewsDue}
              peckSignal={woodpeckerPeck}
            />

            {woodpeckerNotice && (
              <div
                role="status"
                aria-live="polite"
                className="flex shrink-0 items-start justify-between gap-3 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100"
              >
                <span>{woodpeckerNotice}</span>
                <button
                  type="button"
                  aria-label="Dismiss notification"
                  onClick={() => setWoodpeckerNotice(null)}
                  className="shrink-0 text-lg leading-none text-amber-100/70 transition hover:text-amber-100"
                >
                  ×
                </button>
              </div>
            )}

            {result && (
              <button
                type="button"
                onClick={handleNextPuzzle}
                disabled={isFetchingMore}
                className={`${PUZZLE_CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5 disabled:opacity-60`}
              >
                {isFetchingMore ? (
                  <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-transparent" />
                ) : (
                  <NextIcon />
                )}
                {isFetchingMore ? 'Loading…' : 'Next Puzzle'}
              </button>
            )}
          </section>
        </div>
        )}
      </div>
    </div>
  );
}
