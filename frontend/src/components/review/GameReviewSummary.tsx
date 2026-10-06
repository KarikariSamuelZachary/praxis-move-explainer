'use client';

import { useMemo, useState } from 'react';

import type { GameReviewMove, MoveClassification } from '@/types';

import { ClassificationIcon } from './icons/ClassificationIcon';

export type ReviewSummaryProgress = { done: number; total: number | null };

type GameReviewSummaryProps = {
  pgn: string;
  moves: GameReviewMove[] | null;
  isAnalyzing: boolean;
  progress?: ReviewSummaryProgress | null;
  onStartReview: () => void;
};

type PlayerMeta = {
  name: string;
  rating: number | null;
};

type SummaryRowDef = {
  key: MoveClassification;
  label: string;
  whiteClass: string;
  blackClass: string;
};

const PRIMARY_ROWS: SummaryRowDef[] = [
  { key: 'brilliant', label: 'Brilliant', whiteClass: 'text-teal-300', blackClass: 'text-teal-300' },
  { key: 'great', label: 'Great', whiteClass: 'text-blue-400', blackClass: 'text-blue-400' },
  { key: 'best', label: 'Best', whiteClass: 'text-lime-400', blackClass: 'text-lime-400' },
  { key: 'excellent', label: 'Excellent', whiteClass: 'text-green-400', blackClass: 'text-green-400' },
  { key: 'mistake', label: 'Mistake', whiteClass: 'text-orange-400', blackClass: 'text-orange-400' },
  { key: 'miss', label: 'Miss', whiteClass: 'text-red-400', blackClass: 'text-red-400' },
  { key: 'blunder', label: 'Blunder', whiteClass: 'text-red-500', blackClass: 'text-red-500' },
];

const SECONDARY_ROWS: SummaryRowDef[] = [
  { key: 'good', label: 'Good', whiteClass: 'text-lime-300', blackClass: 'text-lime-300' },
  { key: 'inaccuracy', label: 'Inaccuracy', whiteClass: 'text-amber-300', blackClass: 'text-amber-300' },
  { key: 'book', label: 'Book', whiteClass: 'text-zinc-300', blackClass: 'text-zinc-300' },
];

function parsePgnTag(pgn: string, tag: string): string | null {
  const match = pgn.match(new RegExp(`\\[${tag}\\s+"([^"]*)"\\]`));
  const value = match?.[1]?.trim();
  return value ? value : null;
}

function parseRating(value: string | null): number | null {
  if (!value || value === '?' || value === '-') return null;
  const n = Number.parseInt(value, 10);
  return Number.isFinite(n) ? n : null;
}

function playerMetaFromPgn(pgn: string): { white: PlayerMeta; black: PlayerMeta } {
  const whiteName = parsePgnTag(pgn, 'White') ?? 'White';
  const blackName = parsePgnTag(pgn, 'Black') ?? 'Black';
  return {
    white: { name: whiteName, rating: parseRating(parsePgnTag(pgn, 'WhiteElo')) },
    black: { name: blackName, rating: parseRating(parsePgnTag(pgn, 'BlackElo')) },
  };
}

function epLossOf(move: GameReviewMove): number {
  const candidate = move.ep_loss ?? move.raw_ep_loss ?? 0;
  return Number.isFinite(candidate) ? Math.max(0, candidate) : 0;
}

/**
 * Chess.com-style game accuracy approximation.
 * Average expected-points loss per (non-book, non-Start) move, mapped
 * exponentially to 0-100 so one blunder costs ~8-12 points and a clean
 * game stays in the 90s. Book theory moves are excluded like chess.com.
 */
export function accuracyForMoves(moves: GameReviewMove[], color: 'white' | 'black'): number | null {
  const scored = moves.filter(
    (m) => m.color === color && m.san !== 'Start' && m.classification !== 'book',
  );
  if (scored.length === 0) return null;
  const total = scored.reduce((sum, m) => sum + epLossOf(m), 0);
  const avg = total / scored.length;
  const accuracy = 100 * Math.exp(-avg * 4);
  return Math.max(0, Math.min(100, accuracy));
}

function initials(name: string): string {
  const trimmed = name.trim();
  if (!trimmed || trimmed === '?') return '?';
  const parts = trimmed.split(/[\s_]+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return trimmed.slice(0, 2).toUpperCase();
}

function percentOf(progress: ReviewSummaryProgress | null | undefined): number | null {
  if (!progress || !progress.total || progress.total <= 0) return null;
  return Math.max(0, Math.min(100, Math.round((progress.done / progress.total) * 100)));
}

export default function GameReviewSummary({
  pgn,
  moves,
  isAnalyzing,
  progress,
  onStartReview,
}: GameReviewSummaryProps) {
  const [showMore, setShowMore] = useState(false);

  const players = useMemo(() => playerMetaFromPgn(pgn), [pgn]);

  const counts = useMemo(() => {
    const white = {} as Record<MoveClassification, number>;
    const black = {} as Record<MoveClassification, number>;
    const all: MoveClassification[] = [
      'book', 'brilliant', 'great', 'best', 'excellent',
      'good', 'inaccuracy', 'mistake', 'miss', 'blunder',
    ];
    for (const key of all) {
      white[key] = 0;
      black[key] = 0;
    }
    for (const move of moves ?? []) {
      if (move.san === 'Start') continue;
      if (move.color === 'white') white[move.classification] += 1;
      else black[move.classification] += 1;
    }
    return { white, black };
  }, [moves]);

  const accuracies = useMemo(() => {
    if (!moves || moves.length === 0) return { white: null, black: null };
    return {
      white: accuracyForMoves(moves, 'white'),
      black: accuracyForMoves(moves, 'black'),
    };
  }, [moves]);

  const percent = percentOf(progress);
  const hasResult = !isAnalyzing && moves && moves.length > 0;
  const visibleRows = showMore ? [...PRIMARY_ROWS, ...SECONDARY_ROWS] : PRIMARY_ROWS;

  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col gap-3 overflow-hidden rounded-[24px] border border-black/50 bg-[#1e1c1a] p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      {/* Header */}
      <div className="flex min-w-0 shrink-0 items-center gap-2">
        <span className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[#7cb342] text-[11px] font-bold text-white">
          ✓
        </span>
        <h2 className="min-w-0 flex-1 truncate text-sm font-bold text-white">
          Game Review
        </h2>
      </div>

      {/* Progress (analyzing only) */}
      {isAnalyzing && (
        <div className="shrink-0" aria-live="polite">
          <div className="relative h-7 overflow-hidden rounded-md border border-black/60 bg-black/60">
            <div
              className="absolute inset-y-0 left-0 bg-[#3d3a36] transition-[width] duration-500"
              style={{ width: percent !== null ? `${percent}%` : '4%' }}
            />
            <span className="absolute inset-0 flex items-center justify-center font-mono text-[11px] font-semibold text-white/90">
              {percent !== null ? `${percent}%` : progress ? `Analyzing ${progress.done}…` : 'Analyzing…'}
            </span>
          </div>
        </div>
      )}

      {/* Scrollable summary */}
      <div className="wood-scrollbar min-h-0 min-w-0 flex-1 overflow-y-auto overflow-x-hidden pr-0.5">
        {/* Players header */}
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,76px)_28px_minmax(0,76px)] items-start gap-x-1">
          <span className="truncate pt-4 text-xs font-medium text-white/70">Players</span>
          <PlayerHead
            name={players.white.name}
            rating={players.white.rating}
            color="white"
            loading={isAnalyzing}
          />
          <span className="pt-4" />
          <PlayerHead
            name={players.black.name}
            rating={players.black.rating}
            color="black"
            loading={isAnalyzing}
          />
        </div>

        {/* Accuracy row */}
        <div className="mt-2 grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,76px)_28px_minmax(0,76px)] items-center gap-x-1">
          <span className="truncate text-xs font-medium text-white/70">Accuracy</span>
          <div className="flex justify-center">
            {isAnalyzing || accuracies.white === null ? (
              <span className="h-7 w-14 animate-pulse rounded-md bg-white/15" aria-label="White accuracy loading" />
            ) : (
              <span
                className="inline-flex h-7 min-w-14 items-center justify-center rounded-md bg-white px-2 font-mono text-sm font-bold text-black"
                title={`White accuracy ${accuracies.white.toFixed(1)}`}
              >
                {accuracies.white.toFixed(1)}
              </span>
            )}
          </div>
          <span />
          <div className="flex justify-center">
            {isAnalyzing || accuracies.black === null ? (
              <span className="h-7 w-14 animate-pulse rounded-md bg-white/10" aria-label="Black accuracy loading" />
            ) : (
              <span
                className="inline-flex h-7 min-w-14 items-center justify-center rounded-md bg-black/60 px-2 font-mono text-sm font-bold text-white ring-1 ring-white/10"
                title={`Black accuracy ${accuracies.black.toFixed(1)}`}
              >
                {accuracies.black.toFixed(1)}
              </span>
            )}
          </div>
        </div>

        <div className="my-3 border-t border-white/10" />

        {/* Classification rows */}
        <div className="flex min-w-0 flex-col">
          {visibleRows.map((row) => (
            <div
              key={row.key}
              className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(0,76px)_28px_minmax(0,76px)] items-center gap-x-1 py-[5px]"
            >
              <span className="truncate text-[13px] font-medium text-white/85">{row.label}</span>
              <span className={`text-center font-mono text-[15px] font-bold tabular-nums ${row.whiteClass}`}>
                {isAnalyzing ? 0 : counts.white[row.key]}
              </span>
              <span className="flex justify-center">
                <ClassificationIcon classification={row.key} size={20} />
              </span>
              <span className={`text-center font-mono text-[15px] font-bold tabular-nums ${row.blackClass}`}>
                {isAnalyzing ? 0 : counts.black[row.key]}
              </span>
            </div>
          ))}
        </div>

        <button
          type="button"
          onClick={() => setShowMore((v) => !v)}
          className="mt-1 shrink-0 text-[11px] font-semibold text-white/40 transition hover:text-white/75"
        >
          {showMore ? 'Show less' : `Show more (${SECONDARY_ROWS.map((r) => r.label).join(' · ')})`}
        </button>

        {!hasResult && !isAnalyzing && (
          <p className="mt-2 text-[11px] leading-5 text-white/40">
            Import a game to see per-player accuracies and move quality counts.
          </p>
        )}
      </div>

      {/* Start Review CTA */}
      <button
        type="button"
        onClick={onStartReview}
        disabled={!hasResult}
        className="inline-flex w-full shrink-0 items-center justify-center rounded-lg bg-gradient-to-b from-[#8bc34a] to-[#689f38] px-4 py-3 text-[15px] font-extrabold tracking-wide text-white shadow-[0_4px_14px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.35)] transition hover:brightness-110 active:scale-[0.99] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:brightness-100"
      >
        {isAnalyzing ? (
          <span className="inline-flex items-center gap-2">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-white" />
            Reviewing…
          </span>
        ) : (
          'Start Review'
        )}
      </button>
    </aside>
  );
}

function PlayerHead({
  name,
  rating,
  color,
  loading,
}: {
  name: string;
  rating: number | null;
  color: 'white' | 'black';
  loading: boolean;
}) {
  const label = rating !== null ? `${name} (${rating})` : name;
  return (
    <div className="flex min-w-0 flex-col items-center gap-1">
      <span className="w-full truncate text-center text-[11px] font-bold text-white" title={label}>
        {loading ? <span className="mx-auto block h-3 w-12 animate-pulse rounded bg-white/15" /> : name}
      </span>
      {loading ? (
        <span className="h-10 w-10 animate-pulse rounded-md bg-white/10" />
      ) : (
        <span
          className={`flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-md text-[13px] font-extrabold ${
            color === 'white'
              ? 'bg-[#e8e8e8] text-[#333] ring-2 ring-[#7cb342]'
              : 'bg-[#3a3a3a] text-white ring-1 ring-white/15'
          }`}
          title={label}
          aria-label={label}
        >
          {color === 'white' && name === 'White' ? (
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor" aria-hidden>
              <path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.38-1 1.72V7h1.5a.5.5 0 0 1 .4.8l-1.3 1.7a3.5 3.5 0 0 1 2.2 3.25c0 .5-.11.97-.3 1.4l1.6 2.05H7l1.6-2.05a3.5 3.5 0 0 1-.3-1.4 3.5 3.5 0 0 1 2.2-3.25L9.1 7.8a.5.5 0 0 1 .4-.8H11V5.72A2 2 0 0 1 12 2ZM7 18h10a1 1 0 0 1 1 1v1H6v-1a1 1 0 0 1 1-1Z" />
            </svg>
          ) : color === 'black' && name === 'Black' ? (
            <svg viewBox="0 0 24 24" className="h-6 w-6" fill="currentColor" aria-hidden>
              <path d="M12 2a2 2 0 0 1 2 2c0 .74-.4 1.38-1 1.72V7h1.5a.5.5 0 0 1 .4.8l-1.3 1.7a3.5 3.5 0 0 1 2.2 3.25c0 .5-.11.97-.3 1.4l1.6 2.05H7l1.6-2.05a3.5 3.5 0 0 1-.3-1.4 3.5 3.5 0 0 1 2.2-3.25L9.1 7.8a.5.5 0 0 1 .4-.8H11V5.72A2 2 0 0 1 12 2ZM7 18h10a1 1 0 0 1 1 1v1H6v-1a1 1 0 0 1 1-1Z" />
            </svg>
          ) : (
            initials(name)
          )}
        </span>
      )}
    </div>
  );
}
