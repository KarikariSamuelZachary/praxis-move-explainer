'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import { Puzzle } from '@/types';
import { fetchPuzzleBatch, getPuzzleDifficultyLabel } from '@/lib/lichess';

// Dynamically import chessboard to avoid SSR issues
const ChessBoard = dynamic(() => import('@/components/board/ChessBoard'), {
  ssr: false,
  loading: () => (
    <div className="w-[480px] h-[480px] bg-zinc-800 rounded-lg animate-pulse flex items-center justify-center">
      <span className="text-zinc-500">Loading board...</span>
    </div>
  ),
});

const PLAYER_ELO = 1500; // TODO: get from user settings/auth
const BATCH_SIZE = 10;

export default function PuzzlesPage() {
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
  const loadIdRef = useRef(0);

  const loadPuzzles = useCallback(async () => {
    const loadId = ++loadIdRef.current;
    setIsLoading(true);
    try {
      const newPuzzles = await fetchPuzzleBatch(
        BATCH_SIZE,
        undefined,
        PLAYER_ELO - 200,
        PLAYER_ELO + 300
      );
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

  function handlePuzzleSolved(timeSeconds: number) {
    setSessionStats((prev) => ({
      ...prev,
      solved: prev.solved + 1,
      totalTime: prev.totalTime + timeSeconds,
    }));
  }

  function handlePuzzleFailed() {
    setSessionStats((prev) => ({ ...prev, failed: prev.failed + 1 }));
  }

  function handleNextPuzzle() {
    if (currentIndex + 1 >= puzzles.length) {
      // Session complete - start new Woodpecker cycle
      setCycle((prev) => prev + 1);
      setShowStats(true);
    } else {
      setCurrentIndex((prev) => prev + 1);
    }
  }

  function startNewSession() {
    setShowStats(false);
    loadPuzzles();
    setCycle((prev) => prev + 1);
  }

  const currentPuzzle = puzzles[currentIndex];
  const progressPercent = puzzles.length > 0
    ? Math.round(((currentIndex) / puzzles.length) * 100)
    : 0;

  return (
    <div className="min-h-screen bg-zinc-950 text-white">
      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Main content */}
        {isLoading ? (
          <div className="flex items-center justify-center h-96">
            <div className="text-center">
              <div className="w-8 h-8 border-2 border-emerald-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
              <p className="text-zinc-400">Loading puzzles...</p>
            </div>
          </div>
        ) : showStats ? (
          /* Session complete screen */
          <div className="flex items-center justify-center h-96">
            <div className="text-center bg-zinc-800 rounded-2xl p-8 border border-zinc-700 max-w-md w-full">
              <div className="text-5xl mb-4">🎯</div>
              <h2 className="text-2xl font-bold mb-2">Cycle {cycle - 1} Complete!</h2>
              <p className="text-zinc-400 mb-6">
                The Woodpecker Method works by repetition. Start the next cycle to reinforce these patterns.
              </p>
              <div className="grid grid-cols-3 gap-4 mb-6">
                <div className="bg-zinc-700 rounded-lg p-3">
                  <div className="text-2xl font-bold text-emerald-400">{sessionStats.solved}</div>
                  <div className="text-xs text-zinc-400">Solved</div>
                </div>
                <div className="bg-zinc-700 rounded-lg p-3">
                  <div className="text-2xl font-bold text-red-400">{sessionStats.failed}</div>
                  <div className="text-xs text-zinc-400">Failed</div>
                </div>
                <div className="bg-zinc-700 rounded-lg p-3">
                  <div className="text-2xl font-bold text-blue-400">
                    {sessionStats.solved > 0
                      ? Math.round(sessionStats.totalTime / sessionStats.solved)
                      : 0}s
                  </div>
                  <div className="text-xs text-zinc-400">Avg Time</div>
                </div>
              </div>
              <button
                onClick={startNewSession}
                className="w-full py-3 bg-emerald-600 hover:bg-emerald-500 rounded-xl font-semibold transition-colors"
              >
                Start Cycle {cycle} →
              </button>
            </div>
          </div>
        ) : currentPuzzle ? (
          <div>
            {/* Puzzle counter */}
            <div className="flex items-center justify-between mb-4">
              <p className="text-zinc-400 text-sm">
                Puzzle {currentIndex + 1} of {puzzles.length} •{' '}
                <span className="text-zinc-300">
                  {getPuzzleDifficultyLabel(currentPuzzle.rating)}
                </span>
              </p>
              <a
                href={currentPuzzle.gameUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="text-zinc-500 hover:text-zinc-300 text-xs transition-colors"
              >
                View on Lichess ↗
              </a>
            </div>

            <ChessBoard
              puzzle={currentPuzzle}
              playerElo={PLAYER_ELO}
              onPuzzleSolved={handlePuzzleSolved}
              onPuzzleFailed={handlePuzzleFailed}
              onNextPuzzle={handleNextPuzzle}
            />
          </div>
        ) : (
          <div className="flex items-center justify-center h-96">
            <p className="text-zinc-400">No puzzles available. Try refreshing.</p>
          </div>
        )}
      </div>
    </div>
  );
}
