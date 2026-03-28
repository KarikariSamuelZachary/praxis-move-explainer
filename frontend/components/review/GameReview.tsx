'use client';

import { CSSProperties, useEffect, useMemo, useState } from 'react';
import { Chessboard as BaseChessboard } from 'react-chessboard';

import { GameReviewMove } from '@/types';

type GameReviewProps = {
  gameData?: GameReviewMove[];
};

type ReviewExplanation = NonNullable<GameReviewMove['explanation']>;

type StyledChessboardProps = {
  position: string;
  boardOrientation: 'white' | 'black';
  allowDragging?: boolean;
  boardStyle?: CSSProperties;
  animationDurationInMs?: number;
  customDarkSquareStyle: CSSProperties;
  customLightSquareStyle: CSSProperties;
};

const DEFAULT_GAME_DATA: GameReviewMove[] = [
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

const CLASSIFICATION_STYLES: Record<GameReviewMove['classification'], {
  icon: string;
  label: string;
  badgeClassName: string;
}> = {
  book: {
    icon: '📘',
    label: 'Book',
    badgeClassName: 'bg-sky-500/15 text-sky-300 ring-sky-400/30',
  },
  best: {
    icon: '⭐',
    label: 'Best',
    badgeClassName: 'bg-emerald-500/15 text-emerald-300 ring-emerald-400/30',
  },
  excellent: {
    icon: '✨',
    label: 'Excellent',
    badgeClassName: 'bg-teal-500/15 text-teal-300 ring-teal-400/30',
  },
  good: {
    icon: '👍',
    label: 'Good',
    badgeClassName: 'bg-lime-500/15 text-lime-300 ring-lime-400/30',
  },
  inaccuracy: {
    icon: '⚠️',
    label: 'Inaccuracy',
    badgeClassName: 'bg-amber-500/15 text-amber-300 ring-amber-400/30',
  },
  mistake: {
    icon: '❗',
    label: 'Mistake',
    badgeClassName: 'bg-orange-500/15 text-orange-300 ring-orange-400/30',
  },
  blunder: {
    icon: '❌',
    label: 'Blunder',
    badgeClassName: 'bg-rose-500/15 text-rose-300 ring-rose-400/30',
  },
};

function formatMoveNumber(activePly: number): string {
  const fullMove = Math.floor(activePly / 2) + 1;
  const suffix = activePly % 2 === 0 ? 'White' : 'Black';
  return `Move ${fullMove} · ${suffix}`;
}

function Chessboard({
  customDarkSquareStyle,
  customLightSquareStyle,
  ...props
}: StyledChessboardProps) {
  return (
    <BaseChessboard
      options={{
        ...props,
        darkSquareStyle: customDarkSquareStyle,
        lightSquareStyle: customLightSquareStyle,
      }}
    />
  );
}

export default function GameReview({
  gameData = DEFAULT_GAME_DATA,
}: GameReviewProps) {
  const [activePly, setActivePly] = useState(0);
  const [loadingExplanation, setLoadingExplanation] = useState(false);
  const [fetchedExplanation, setFetchedExplanation] = useState<ReviewExplanation | null>(null);

  const safeGameData = gameData.length > 0 ? gameData : DEFAULT_GAME_DATA;
  const clampedActivePly = Math.min(activePly, safeGameData.length - 1);
  const currentMove = safeGameData[clampedActivePly];
  const classificationStyle = CLASSIFICATION_STYLES[currentMove.classification];
  const displayedExplanation = currentMove.explanation ?? fetchedExplanation;

  const progressLabel = useMemo(
    () => `${clampedActivePly + 1} / ${safeGameData.length}`,
    [clampedActivePly, safeGameData.length]
  );

  useEffect(() => {
    setFetchedExplanation(null);
    setLoadingExplanation(false);
  }, [activePly]);

  async function handleAskCoach() {
    setLoadingExplanation(true);

    try {
      const response = await fetch('/api/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen: safeGameData[clampedActivePly].fen,
          move: safeGameData[clampedActivePly].san,
          isCorrect: false,
        }),
      });

      if (!response.ok) {
        throw new Error(`Explanation API returned ${response.status}`);
      }

      const data = await response.json() as ReviewExplanation;
      setFetchedExplanation(data);
    } catch (error) {
      console.error('Failed to fetch review explanation:', error);
    } finally {
      setLoadingExplanation(false);
    }
  }

  return (
    <section className="w-full rounded-[28px] border border-zinc-800 bg-[radial-gradient(circle_at_top,_rgba(30,41,59,0.85),_rgba(9,9,11,0.96)_58%)] p-4 text-white shadow-2xl shadow-black/30 sm:p-6">
      <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-xs uppercase tracking-[0.24em] text-zinc-500">Game Review</p>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight text-zinc-50">Step Through The Analysis</h2>
          <p className="mt-1 text-sm text-zinc-400">
            Review each evaluated move like a media player, with classifications and coach notes.
          </p>
        </div>

        <div className="inline-flex items-center gap-2 rounded-full border border-zinc-700 bg-zinc-900/80 px-4 py-2 text-sm text-zinc-300">
          <span className="text-zinc-500">Ply</span>
          <span className="font-medium text-zinc-100">{progressLabel}</span>
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,480px)_minmax(320px,1fr)]">
        <div className="overflow-hidden rounded-[24px] border border-zinc-800 bg-zinc-950/70 p-3 sm:p-4">
          <div className="mb-4 flex flex-col gap-3 sm:flex-row">
            <button
              type="button"
              onClick={() => setActivePly((ply) => Math.max(0, ply - 1))}
              disabled={clampedActivePly === 0}
              className="flex-1 rounded-2xl border border-zinc-700 bg-zinc-900 px-4 py-3 text-sm font-medium text-zinc-200 transition hover:border-zinc-500 hover:bg-zinc-800 disabled:cursor-not-allowed disabled:border-zinc-800 disabled:bg-zinc-900/40 disabled:text-zinc-600"
            >
              ← Previous Move
            </button>
            <button
              type="button"
              onClick={() => setActivePly((ply) => Math.min(safeGameData.length - 1, ply + 1))}
              disabled={clampedActivePly === safeGameData.length - 1}
              className="flex-1 rounded-2xl border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm font-medium text-emerald-200 transition hover:border-emerald-400 hover:bg-emerald-500/20 disabled:cursor-not-allowed disabled:border-zinc-800 disabled:bg-zinc-900/40 disabled:text-zinc-600"
            >
              Next Move →
            </button>
          </div>

          <div className="mx-auto aspect-square w-full max-w-[440px] overflow-hidden rounded-[18px]">
            <Chessboard
              position={currentMove.fen}
              boardOrientation={currentMove.color === 'white' ? 'black' : 'white'}
              allowDragging={false}
              boardStyle={{
                width: '100%',
                height: '100%',
                borderRadius: '18px',
                boxShadow: '0 24px 60px rgba(0,0,0,0.35)',
              }}
              animationDurationInMs={250}
              customDarkSquareStyle={{ backgroundColor: '#4a7c59' }}
              customLightSquareStyle={{ backgroundColor: '#f0d9b5' }}
            />
          </div>
        </div>

        <div className="flex flex-col gap-4">
          <div className="rounded-[24px] border border-zinc-800 bg-zinc-950/70 p-5">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-xs uppercase tracking-[0.22em] text-zinc-500">
                  {formatMoveNumber(clampedActivePly)}
                </p>
                <div className="mt-2 flex items-center gap-3">
                  <span className="text-3xl font-semibold text-zinc-50">{currentMove.san}</span>
                  <span className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-medium ring-1 ${classificationStyle.badgeClassName}`}>
                    <span>{classificationStyle.icon}</span>
                    <span>{classificationStyle.label}</span>
                  </span>
                </div>
              </div>

              <div className="rounded-2xl border border-zinc-800 bg-zinc-900/90 px-4 py-3 text-right">
                <p className="text-[11px] uppercase tracking-[0.22em] text-zinc-500">CP Loss</p>
                <p className="mt-1 text-2xl font-semibold text-zinc-100">{currentMove.cp_loss}</p>
              </div>
            </div>

            <div className="mt-5 grid gap-3 sm:grid-cols-2">
              <div className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">Player</p>
                <p className="mt-2 text-lg font-medium capitalize text-zinc-100">{currentMove.color}</p>
              </div>
              <div className="rounded-2xl border border-zinc-800 bg-zinc-900/80 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-zinc-500">Position</p>
                <p className="mt-2 text-sm text-zinc-300">
                  Board set to the position after <span className="font-medium text-zinc-100">{currentMove.san}</span>.
                </p>
              </div>
            </div>
          </div>

          {displayedExplanation ? (
            <div className="rounded-[24px] border border-indigo-500/20 bg-indigo-500/10 p-5">
              <div className="flex items-center gap-2 text-indigo-200">
                <span className="text-lg">🧠</span>
                <h3 className="text-sm font-semibold uppercase tracking-[0.22em]">Coach&apos;s Notes</h3>
              </div>

              <p className="mt-4 text-sm leading-7 text-indigo-50/95">
                {displayedExplanation.explanation}
              </p>

              {displayedExplanation.concept && (
                <div className="mt-4 inline-flex items-center gap-2 rounded-full border border-indigo-400/20 bg-zinc-950/40 px-3 py-1 text-xs text-indigo-100">
                  <span>Concept</span>
                  <span className="font-medium">{displayedExplanation.concept}</span>
                </div>
              )}

              {displayedExplanation.tip && (
                <p className="mt-4 rounded-2xl border border-zinc-800 bg-zinc-950/50 px-4 py-3 text-sm text-zinc-200">
                  {displayedExplanation.tip}
                </p>
              )}
            </div>
          ) : (
            <div className="rounded-[24px] border border-dashed border-zinc-800 bg-zinc-950/40 p-5">
              <div className="flex items-center gap-2 text-zinc-300">
                <span className="text-lg">💬</span>
                <h3 className="text-sm font-semibold uppercase tracking-[0.22em]">Coach&apos;s Notes</h3>
              </div>
              <p className="mt-3 text-sm leading-7 text-zinc-400">
                This move doesn&apos;t have an explanation yet, but you can still ask the coach to break it down.
              </p>
              <button
                type="button"
                onClick={handleAskCoach}
                disabled={loadingExplanation}
                className="mt-4 inline-flex items-center justify-center rounded-2xl border border-sky-500/30 bg-sky-500/10 px-4 py-3 text-sm font-medium text-sky-200 transition hover:border-sky-400 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:border-zinc-800 disabled:bg-zinc-900/40 disabled:text-zinc-500"
              >
                {loadingExplanation ? 'Coach is thinking...' : 'Ask Coach to explain this move'}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
