'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import dynamic from 'next/dynamic';
import { Puzzle } from '@/types';
import { fetchPuzzleBatch, getPuzzleDifficultyLabel } from '@/lib/lichess';
import { PUZZLE_THEME_GROUPS } from '@/lib/themes';

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
  const loadIdRef = useRef(0);

  const loadPuzzles = useCallback(async (theme?: string) => {
    const loadId = ++loadIdRef.current;
    setIsLoading(true);
    try {
      const newPuzzles = await fetchPuzzleBatch(
        BATCH_SIZE,
        theme,
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
        <section className="mb-8 rounded-lg border border-zinc-800 bg-zinc-950/70 shadow-2xl shadow-black/20">
          <div className="flex flex-col gap-4 border-b border-zinc-800 p-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase text-emerald-400">
                Training focus
              </p>
              <h2 className="mt-1 text-lg font-semibold text-zinc-100">
                Choose a tactical theme
              </h2>
            </div>
            <button
              onClick={() => { setSelectedTheme(''); loadPuzzles(); }}
              className={`group rounded-md border px-4 py-3 text-left transition-all sm:min-w-44 ${
                selectedTheme === ''
                  ? 'border-emerald-400 bg-emerald-500 text-zinc-950 shadow-lg shadow-emerald-500/20'
                  : 'border-zinc-700 bg-zinc-900 text-zinc-300 hover:border-emerald-500/70 hover:bg-zinc-800'
              }`}
            >
              <span className="block text-sm font-semibold">All Tactics</span>
              <span className={`mt-1 block text-xs ${
                selectedTheme === '' ? 'text-emerald-950/80' : 'text-zinc-500 group-hover:text-zinc-400'
              }`}>
                No theme filter
              </span>
            </button>
          </div>

          <div className="grid max-h-[28rem] gap-4 overflow-y-auto p-4 md:grid-cols-2 xl:grid-cols-3">
            {PUZZLE_THEME_GROUPS.map((group) => (
              <div
                key={group.name}
                className="rounded-lg border border-zinc-800 bg-zinc-900/80 p-3"
              >
                <div className="mb-3 flex items-center gap-2">
                  <span className={`h-2.5 w-2.5 rounded-full bg-gradient-to-br ${group.accent}`} />
                  <h3 className="text-sm font-semibold text-zinc-100">{group.name}</h3>
                  <span className="ml-auto rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] font-medium text-zinc-500">
                    {group.themes.length}
                  </span>
                </div>

                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  {group.themes.map((theme) => {
                    const isSelected = selectedTheme === theme.key;

                    return (
                      <button
                        key={theme.key}
                        onClick={() => { setSelectedTheme(theme.key); loadPuzzles(theme.key); }}
                        className={`rounded-md border px-2.5 py-2 text-left text-xs font-medium transition-all ${
                          isSelected
                            ? 'border-emerald-400 bg-emerald-500 text-zinc-950 shadow-md shadow-emerald-500/20'
                            : 'border-zinc-800 bg-zinc-950/70 text-zinc-300 hover:border-zinc-600 hover:bg-zinc-800 hover:text-white'
                        }`}
                      >
                        {theme.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        </section>

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
