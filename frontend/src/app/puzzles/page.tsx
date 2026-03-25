'use client';

import { useState, useEffect, useCallback } from 'react';
import dynamic from 'next/dynamic';
import { Puzzle } from '@/types';
import { fetchPuzzleBatch, getPuzzleDifficultyLabel, PUZZLE_THEMES } from '@/lib/lichess';

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
  const [selectedTheme, setSelectedTheme] = useState<string>('');
  const [cycle, setCycle] = useState(1);
  const [showStats, setShowStats] = useState(false);

  const loadPuzzles = useCallback(async (theme?: string) => {
    setIsLoading(true);
    try {
      const newPuzzles = await fetchPuzzleBatch(
        BATCH_SIZE,
        theme,
        PLAYER_ELO - 200,
        PLAYER_ELO + 300
      );
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
    loadPuzzles(selectedTheme);
    setCycle((prev) => prev + 1);
  }

  const currentPuzzle = puzzles[currentIndex];
  const progressPercent = puzzles.length > 0
    ? Math.round(((currentIndex) / puzzles.length) * 100)
    : 0;

  return (
    <div className="min-h-screen bg-zinc-900 text-white">
      {/* Header */}
      <div className="border-b border-zinc-800 bg-zinc-900/80 backdrop-blur-sm sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <h1 className="text-xl font-bold text-white">
              ♟ Praxis
            </h1>
            <span className="text-zinc-500 text-sm">Woodpecker Trainer</span>
          </div>

          <div className="flex items-center gap-4">
            {/* Cycle indicator */}
            <div className="flex items-center gap-2 bg-zinc-800 rounded-lg px-3 py-1.5">
              <span className="text-zinc-400 text-xs">CYCLE</span>
              <span className="text-emerald-400 font-bold">{cycle}</span>
            </div>

            {/* Session stats */}
            <div className="flex items-center gap-3 text-sm">
              <span className="text-emerald-400">✓ {sessionStats.solved}</span>
              <span className="text-red-400">✗ {sessionStats.failed}</span>
            </div>

            {/* ELO badge */}
            <div className="bg-indigo-900/50 border border-indigo-700 rounded-lg px-3 py-1.5">
              <span className="text-indigo-300 text-sm font-medium">{PLAYER_ELO} ELO</span>
            </div>
          </div>
        </div>

        {/* Progress bar */}
        <div className="h-0.5 bg-zinc-800">
          <div
            className="h-full bg-emerald-500 transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      <div className="max-w-6xl mx-auto px-4 py-8">
        {/* Theme selector */}
        <div className="mb-6 flex items-center gap-3 flex-wrap">
          <span className="text-zinc-400 text-sm font-medium">Focus:</span>
          <button
            onClick={() => { setSelectedTheme(''); loadPuzzles(''); }}
            className={`px-3 py-1 rounded-full text-sm transition-colors ${
              selectedTheme === ''
                ? 'bg-emerald-600 text-white'
                : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
            }`}
          >
            All Tactics
          </button>
          {PUZZLE_THEMES.slice(0, 8).map((theme: string) => (
            <button
              key={theme}
              onClick={() => { setSelectedTheme(theme); loadPuzzles(theme); }}
              className={`px-3 py-1 rounded-full text-sm capitalize transition-colors ${
                selectedTheme === theme
                  ? 'bg-emerald-600 text-white'
                  : 'bg-zinc-800 text-zinc-400 hover:bg-zinc-700'
              }`}
            >
              {theme.replace(/([A-Z])/g, ' $1').trim()}
            </button>
          ))}
        </div>

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
