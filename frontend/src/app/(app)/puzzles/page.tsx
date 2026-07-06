'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import Image from 'next/image';
import Link from 'next/link';
import { Puzzle } from '@/types';
import { fetchPuzzleBatch, getPuzzleDifficultyLabel } from '@/lib/lichess';
import type { BoardApi } from '@/components/board/ChessBoard';

// Dynamically import chessboard to avoid SSR issues
const ChessBoard = dynamic(() => import('@/components/board/ChessBoard'), {
  ssr: false,
  loading: () => (
    <div className="flex aspect-square w-full max-w-[700px] animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
      <span className="text-white/60">Loading board...</span>
    </div>
  ),
});

const BATCH_SIZE = 10;
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';
const WEEK_DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const RATING_DATA = [1814, 1828, 1851, 1840, 1860, 1854, 1876];

function formatTheme(theme: string) {
  return theme.replace(/([A-Z])/g, ' $1').trim();
}

function getSideToMove(puzzle: Puzzle) {
  return (puzzle.fen || '').split(/\s+/)[1] === 'b' ? 'black' : 'white';
}

function RatingSparkline() {
  const width = 330;
  const height = 96;
  const padding = 8;
  const min = Math.min(...RATING_DATA);
  const max = Math.max(...RATING_DATA);
  const range = Math.max(max - min, 1);
  const points = RATING_DATA.map((value, index) => {
    const x = padding + (index * (width - padding * 2)) / (RATING_DATA.length - 1);
    const y = height - padding - ((value - min) / range) * (height - padding * 2);
    return { x, y };
  });
  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
  const areaPath = `${path} L ${points[points.length - 1].x} ${height - padding} L ${points[0].x} ${height - padding} Z`;

  return (
    <svg className="mt-4 h-16 w-full overflow-visible" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Seven day tactical rating trend">
      <defs>
        <linearGradient id="rating-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor="#10b981" stopOpacity="0.38" />
          <stop offset="100%" stopColor="#10b981" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#rating-fill)" />
      <path d={path} fill="none" stroke="#10b981" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />
      {points.map((point) => (
        <circle key={`${point.x}-${point.y}`} cx={point.x} cy={point.y} fill="#10b981" r="3" stroke="rgba(255,255,255,0.45)" strokeWidth="1" />
      ))}
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.5" viewBox="0 0 24 24">
      <path d="m5 12 4 4L19 6" />
    </svg>
  );
}

function HintIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M9 18h6" />
      <path d="M10 22h4" />
      <path d="M8.5 14.5A6 6 0 1 1 15.5 14c-.8.5-1.5 1.3-1.5 2.2V17h-4v-.8c0-.7-.5-1.3-1.5-1.7Z" />
    </svg>
  );
}

function EyeIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function PlayAgainIcon() {
  return (
    <svg className="h-5 w-5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24">
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-15 6.7L3 16" />
      <path d="M3 21v-5h5" />
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

export default function PuzzlesPage() {
  const boardApi = useRef<BoardApi | null>(null);
  const [puzzles, setPuzzles] = useState<Puzzle[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(true);
  const [sessionStats, setSessionStats] = useState({
    solved: 0,
    failed: 0,
    totalTime: 0,
  });
  const [cycle, setCycle] = useState(1);
  const [showStats, setShowStats] = useState(false);
  const [puzzleEnded, setPuzzleEnded] = useState(false);
  const [currentRating, setCurrentRating] = useState<number | null>(null);
  const loadIdRef = useRef(0);
  const hasScoredAttemptRef = useRef(false);

  const loadPuzzles = useCallback(async () => {
    const loadId = ++loadIdRef.current;
    setIsLoading(true);
    try {
      const newPuzzles = await fetchPuzzleBatch(BATCH_SIZE);
      if (loadId !== loadIdRef.current) return;
      setPuzzles(newPuzzles);
      setCurrentIndex(0);
      setSessionStats({ solved: 0, failed: 0, totalTime: 0 });
    } catch (error) {
      console.error('Failed to load puzzles:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadPuzzles();
  }, [loadPuzzles]);

  useEffect(() => {
    fetch('/api/user/rating')
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.tactical_rating != null) {
          setCurrentRating(data.tactical_rating);
        }
      })
      .catch((error) => console.error('Failed to fetch user rating:', error));
  }, []);

  useEffect(() => {
    hasScoredAttemptRef.current = false;
  }, [currentIndex]);

  function handlePuzzleSolved(timeSeconds: number) {
    setSessionStats((prev) => ({
      ...prev,
      solved: prev.solved + 1,
      totalTime: prev.totalTime + timeSeconds,
    }));

    if (!hasScoredAttemptRef.current) {
      hasScoredAttemptRef.current = true;
      const puzzle = puzzles[currentIndex];
      if (puzzle) {
        fetch('/api/puzzles/rating', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            puzzle_id: puzzle.id,
            puzzle_rating: puzzle.rating,
            solved: true,
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
    }
  }

  function handlePuzzleFailed() {
    setSessionStats((prev) => ({ ...prev, failed: prev.failed + 1 }));

    if (!hasScoredAttemptRef.current) {
      hasScoredAttemptRef.current = true;
      const puzzle = puzzles[currentIndex];
      if (puzzle) {
        fetch('/api/puzzles/rating', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            puzzle_id: puzzle.id,
            puzzle_rating: puzzle.rating,
            solved: false,
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
    }
  }

  function handleNextPuzzle() {
    setPuzzleEnded(false);
    if (currentIndex + 1 >= puzzles.length) {
      // Session complete - start new Woodpecker cycle
      setCycle((prev) => prev + 1);
      setShowStats(true);
    } else {
      setCurrentIndex((prev) => prev + 1);
    }
  }

  const handlePuzzleEnd = useCallback(() => {
    setPuzzleEnded(true);
  }, []);

  const handlePlayAgain = useCallback(() => {
    boardApi.current?.resetPuzzle();
    setPuzzleEnded(false);
  }, []);

  function startNewSession() {
    setShowStats(false);
    setPuzzleEnded(false);
    loadPuzzles();
    setCycle((prev) => prev + 1);
  }

  const currentPuzzle = puzzles[currentIndex];
  const displayedThemes = currentPuzzle?.themes?.slice(0, 4) ?? [];
  const themeCount = currentPuzzle ? Math.max(new Set(currentPuzzle.themes).size, 1) : 0;
  const reviewsDue = Math.max(puzzles.length - sessionStats.solved, 0);
  const isCurrentPuzzleSolved = sessionStats.solved > currentIndex;

  return (
    <div className="min-h-[calc(100vh-2.25rem)] -mt-2 text-white [background-image:url(/walnut-dark.png)] [background-size:cover] [background-position:center]">
      <div className="mx-auto max-w-[1280px] pb-1 px-6 lg:px-10">
        {/* Main content */}
        {isLoading ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
              <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
              <p className="text-white/60">Loading puzzles...</p>
            </div>
          </div>
        ) : showStats ? (
          /* Session complete screen */
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} w-full max-w-md p-8 text-center shadow-2xl shadow-black/30`}>
              <Image src="/woodpecker-bird-v2.png" alt="" width={54} height={54} className="mx-auto mb-4 h-[54px] w-[54px] object-contain" />
              <h2 className="mb-2 text-2xl font-bold">Cycle {cycle - 1} Complete!</h2>
              <p className="mb-6 text-white/60">
                The Woodpecker Method works by repetition. Start the next cycle to reinforce these patterns.
              </p>
              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                  <div className="text-2xl font-bold text-[#10b981]">{sessionStats.solved}</div>
                  <div className="text-xs text-white/60">Solved</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                  <div className="text-2xl font-bold text-red-400">{sessionStats.failed}</div>
                  <div className="text-xs text-white/60">Failed</div>
                </div>
                <div className="rounded-lg border border-white/10 bg-white/5 p-3">
                  <div className="text-2xl font-bold text-white">
                    {sessionStats.solved > 0
                      ? Math.round(sessionStats.totalTime / sessionStats.solved)
                      : 0}s
                  </div>
                  <div className="text-xs text-white/60">Avg Time</div>
                </div>
              </div>
              <button
                onClick={startNewSession}
                className="w-full rounded-xl bg-[#10b981] py-3 font-semibold text-white shadow-lg shadow-emerald-950/40 transition-colors hover:bg-emerald-400"
              >
                Start Cycle {cycle}
              </button>
            </div>
          </div>
        ) : currentPuzzle ? (
          <div className="grid items-start justify-center gap-6 xl:grid-cols-[minmax(0,calc(100vh-70px))_420px]">
            <section className="overflow-visible">
              <div className="relative mx-auto mt-[24px] w-full max-w-[calc(100vh-70px)]">
                {currentRating !== null && (
                  <div className="pointer-events-none absolute -top-6 right-0 z-10 rounded-full px-3 py-1 text-sm text-white/70">
                    {currentRating}
                  </div>
                )}
                <div className="w-full">
                  <ChessBoard
                    puzzle={currentPuzzle}
                    playerElo={currentRating ?? 1100}
                    onPuzzleSolved={handlePuzzleSolved}
                    onPuzzleFailed={handlePuzzleFailed}
                    onPuzzleEnd={handlePuzzleEnd}
                    apiRef={boardApi}
                  />
                </div>
              </div>
            </section>

            <section className="flex w-full max-w-[420px] flex-col space-y-5 mt-[24px]">
<div className={`${CARD_CLASS} mx-auto w-[400px] p-6 shadow-2xl shadow-black/25`}>
                {/* Bird image, centered */}
                <Image src="/woodpecker-bird-v2.png" alt="" width={250} height={250} className="mx-auto h-[250px] w-[250px] shrink-0 object-contain" />

                {/* Content block: centered, no gap from image */}
                <div className="mt-0 text-center">
                  <div className="text-sm font-normal text-[#f7e5c6]/60">Reviews Due</div>
                  <div className="mt-1 text-[50px] font-bold leading-none text-[#f7e5c6]">{reviewsDue}</div>
                </div>

                {/* Button: full-width, rounded, comfortable padding */}
                <Link href="/woodpecker" className="mt-5 flex w-full items-center justify-center rounded-lg border border-[#f7e5c6]/30 bg-transparent px-6 py-[15px] text-xl font-bold text-[#f7e5c6] transition hover:border-[#f7e5c6]/60 hover:bg-[#f7e5c6]/5">
                  Go to Woodpecker
                </Link>
              </div>

              <div className="grid grid-cols-2 gap-3">
                {puzzleEnded ? (
                  <>
                    <button
                      type="button"
                      onClick={handlePlayAgain}
                      className={`${CARD_CLASS} flex h-14 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
                    >
                      <PlayAgainIcon />
                      Play Again
                    </button>
                    <button
                      type="button"
                      onClick={handleNextPuzzle}
                      className={`${CARD_CLASS} flex h-14 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
                    >
                      <NextIcon />
                      Next Puzzle
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => boardApi.current?.showHint()}
                      className={`${CARD_CLASS} flex h-14 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
                    >
                      <HintIcon />
                      Hint
                    </button>
                    <button
                      type="button"
                      onClick={() => boardApi.current?.showSolution()}
                      className={`${CARD_CLASS} flex h-14 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5`}
                    >
                      <EyeIcon />
                      Show Solution
                    </button>
                  </>
                )}
              </div>
            </section>
          </div>
        ) : (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} px-8 py-6 text-white/60`}>
              No puzzles available. Try refreshing.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
