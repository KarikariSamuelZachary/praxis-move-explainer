'use client';

import { useMemo, useState } from 'react';

import GameReview from '@/components/review/GameReview';
import { GameReviewMove } from '@/types';

const REVIEW_DATA: GameReviewMove[] = [
  { fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', san: 'e4', color: 'white', classification: 'book', cp_loss: 0 },
  { fen: 'rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2', san: 'c5', color: 'black', classification: 'book', cp_loss: 0 },
  { fen: 'rnbqkbnr/pp1ppppp/8/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2', san: 'Nf3', color: 'white', classification: 'best', cp_loss: 5 },
  { fen: 'rnbqkbnr/pp2pppp/3p4/2p5/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3', san: 'd6', color: 'black', classification: 'good', cp_loss: 40 },
  { fen: 'rnbqkbnr/pp2pppp/3p4/2p5/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 1 3', san: 'Bc4', color: 'white', classification: 'inaccuracy', cp_loss: 85 },
  {
    fen: 'rnbqkbnr/1p2pppp/p2p4/2p5/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4',
    san: 'a6',
    color: 'black',
    classification: 'blunder',
    cp_loss: 350,
    explanation: {
      explanation: 'This ignores the threat on f7.',
      concept: 'King Safety',
    },
  },
];

type ReviewMode = 'input' | 'loading' | 'review';

export default function ReviewPage() {
  const [mode, setMode] = useState<ReviewMode>('input');
  const [pgnInput, setPgnInput] = useState('');

  const canAnalyze = useMemo(() => pgnInput.trim().length > 0, [pgnInput]);

  function handleAnalyzeGame() {
    if (!canAnalyze) {
      return;
    }

    setMode('loading');
    window.setTimeout(() => {
      setMode('review');
    }, 2000);
  }

  function handleStartOver() {
    setMode('input');
    setPgnInput('');
  }

  return (
    <main className="min-h-screen bg-zinc-950 px-4 py-10 text-white sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
        {mode === 'input' && (
          <section className="mx-auto w-full max-w-4xl rounded-[32px] border border-zinc-800 bg-[radial-gradient(circle_at_top,_rgba(34,197,94,0.12),_rgba(9,9,11,0.96)_58%)] p-6 shadow-2xl shadow-black/30 sm:p-8">
            <div className="max-w-2xl">
              <p className="text-xs uppercase tracking-[0.26em] text-zinc-500">Game Review</p>
              <h1 className="mt-3 text-3xl font-semibold tracking-tight text-zinc-50 sm:text-4xl">
                Paste A PGN To Review Every Move
              </h1>
              <p className="mt-3 text-sm leading-7 text-zinc-400 sm:text-base">
                Drop in a full PGN, let the coach analyze it, and then step through the game move by move with classifications and notes.
              </p>
            </div>

            <div className="mt-8">
              <label htmlFor="pgn-input" className="mb-3 block text-sm font-medium text-zinc-300">
                PGN Input
              </label>
              <textarea
                id="pgn-input"
                value={pgnInput}
                onChange={(event) => setPgnInput(event.target.value)}
                placeholder='Paste your PGN here, for example: [Event "Casual Game"] ...'
                className="min-h-[260px] w-full rounded-[24px] border border-zinc-800 bg-zinc-950/80 px-5 py-4 font-mono text-sm leading-7 text-zinc-100 outline-none transition placeholder:text-zinc-600 focus:border-emerald-500/60 focus:ring-2 focus:ring-emerald-500/20"
              />
            </div>

            <div className="mt-6 flex justify-end">
              <button
                type="button"
                onClick={handleAnalyzeGame}
                disabled={!canAnalyze}
                className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 px-5 py-3 text-sm font-medium text-emerald-200 transition hover:border-emerald-400 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:border-zinc-800 disabled:bg-zinc-900/40 disabled:text-zinc-600"
              >
                Analyze Game
              </button>
            </div>
          </section>
        )}

        {mode === 'loading' && (
          <section className="mx-auto flex min-h-[420px] w-full max-w-4xl items-center justify-center rounded-[32px] border border-zinc-800 bg-[radial-gradient(circle_at_top,_rgba(56,189,248,0.14),_rgba(9,9,11,0.96)_58%)] p-6 shadow-2xl shadow-black/30 sm:p-8">
            <div className="text-center">
              <div className="mx-auto mb-5 h-12 w-12 rounded-full border-2 border-sky-400/40 border-t-sky-300 animate-spin" />
              <p className="text-xs uppercase tracking-[0.26em] text-zinc-500">Analysis In Progress</p>
              <h2 className="mt-3 text-2xl font-semibold tracking-tight text-zinc-50">
                Coach is analyzing your game...
              </h2>
              <p className="mt-3 max-w-md text-sm leading-7 text-zinc-400">
                Evaluating every move, classifying critical moments, and preparing your review timeline.
              </p>
            </div>
          </section>
        )}

        {mode === 'review' && (
          <>
            <div className="flex justify-start">
              <button
                type="button"
                onClick={handleStartOver}
                className="rounded-2xl border border-zinc-700 bg-zinc-900 px-4 py-2.5 text-sm font-medium text-zinc-200 transition hover:border-zinc-500 hover:bg-zinc-800"
              >
                ← Start Over
              </button>
            </div>
            <GameReview gameData={REVIEW_DATA} />
          </>
        )}
      </div>
    </main>
  );
}
