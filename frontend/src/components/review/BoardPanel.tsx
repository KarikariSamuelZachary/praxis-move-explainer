'use client';

import { CSSProperties, useState } from 'react';
import { Chessboard as BaseChessboard } from 'react-chessboard';

import { GameReviewMove } from '@/types';
import MoveList from './MoveList';

type BoardPanelProps = {
  title: string;
  onTitleChange: (title: string) => void;
  moves: GameReviewMove[];
  activePly: number;
  onPlySelect: (ply: number) => void;
  isAnalyzing: boolean;
  hasGame: boolean;
};

const STYLED_PROPS = {
  customDarkSquareStyle: { backgroundColor: '#b58863' },
  customLightSquareStyle: { backgroundColor: '#f0d9b5' },
} as const;

export default function BoardPanel({
  title,
  onTitleChange,
  moves,
  activePly,
  onPlySelect,
  isAnalyzing,
  hasGame,
}: BoardPanelProps) {
  const [orientation, setOrientation] = useState<'white' | 'black'>('white');
  const [isEditingTitle, setIsEditingTitle] = useState(false);
  const [theme, setTheme] = useState<'classic' | 'green' | 'midnight'>('classic');

  const clampedPly = hasGame ? Math.min(activePly, moves.length - 1) : 0;
  const currentMove = hasGame ? moves[clampedPly] : null;
  const position = currentMove?.fen ?? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

  const squareStyles = theme === 'midnight' ? {
    customDarkSquareStyle: { backgroundColor: '#1e293b' },
    customLightSquareStyle: { backgroundColor: '#475569' },
  } : theme === 'green' ? {
    customDarkSquareStyle: { backgroundColor: '#4a7c59' },
    customLightSquareStyle: { backgroundColor: '#f0d9b5' },
  } : STYLED_PROPS;

  return (
    <section className="flex h-full flex-col gap-4 overflow-y-auto rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      <header className="flex flex-wrap items-center justify-between gap-3">
        {isEditingTitle ? (
          <input
            type="text"
            value={title}
            onChange={(event) => onTitleChange(event.target.value)}
            onBlur={() => setIsEditingTitle(false)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === 'Escape') {
                setIsEditingTitle(false);
              }
            }}
            autoFocus
            placeholder="Untitled Game"
            className="min-w-0 flex-1 rounded-lg border border-emerald-500/40 bg-black/40 px-2 py-1 text-base font-semibold text-zinc-100 outline-none focus:ring-2 focus:ring-emerald-500/30"
          />
        ) : (
          <button
            type="button"
            onClick={() => setIsEditingTitle(true)}
            disabled={!hasGame}
            className="group flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 py-1 text-left transition hover:bg-white/5 disabled:cursor-not-allowed disabled:hover:bg-transparent"
          >
            <h2 className="truncate text-base font-semibold tracking-tight text-zinc-100">
              {hasGame && title ? title : 'Untitled Game'}
            </h2>
            <svg
              className="h-3.5 w-3.5 shrink-0 text-zinc-500 transition group-hover:text-emerald-300"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
              viewBox="0 0 24 24"
              aria-hidden
            >
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
            </svg>
          </button>
        )}

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setOrientation((current) => current === 'white' ? 'black' : 'white')}
            disabled={!hasGame}
            className="inline-flex items-center gap-1.5 rounded-lg border border-black/40 bg-black/40 px-2.5 py-1.5 text-xs text-zinc-200 transition hover:border-amber-900/50 hover:text-white disabled:opacity-50"
          >
            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
              <path d="M17 1l4 4-4 4" />
              <path d="M3 11V9a4 4 0 0 1 4-4h14" />
              <path d="M7 23l-4-4 4-4" />
              <path d="M21 13v2a4 4 0 0 1-4 4H3" />
            </svg>
            Flip Board
          </button>

          <label className="relative">
            <select
              value={theme}
              onChange={(event) => setTheme(event.target.value as typeof theme)}
              disabled={!hasGame}
              className="cursor-pointer appearance-none rounded-lg border border-black/40 bg-black/40 px-2.5 py-1.5 pr-7 text-xs text-zinc-200 transition hover:border-amber-900/50 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <option value="classic">Classic</option>
              <option value="green">Green</option>
              <option value="midnight">Midnight</option>
            </select>
            <svg className="pointer-events-none absolute right-2 top-1/2 h-3 w-3 -translate-y-1/2 text-zinc-400" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
              <path d="m6 9 6 6 6-6" />
            </svg>
          </label>
        </div>
      </header>

      <div className="flex flex-col items-center gap-3">
        <div className="grid w-full grid-cols-5 gap-2">
          <PlaybackButton
            label="First"
            disabled={!hasGame || clampedPly === 0}
            onClick={() => onPlySelect(0)}
            icon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.4" viewBox="0 0 24 24" aria-hidden>
                <path d="M19 20 9 12l10-8" />
                <path d="M5 19V5" />
              </svg>
            }
          />
          <PlaybackButton
            label="Previous"
            disabled={!hasGame || clampedPly === 0}
            onClick={() => onPlySelect(Math.max(0, clampedPly - 1))}
            icon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.4" viewBox="0 0 24 24" aria-hidden>
                <path d="m15 18-6-6 6-6" />
              </svg>
            }
          />
          <PlaybackButton
            label="Next"
            primary
            disabled={!hasGame || clampedPly >= moves.length - 1}
            onClick={() => onPlySelect(Math.min(moves.length - 1, clampedPly + 1))}
            icon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.4" viewBox="0 0 24 24" aria-hidden>
                <path d="m9 18 6-6-6-6" />
              </svg>
            }
          />
          <PlaybackButton
            label="Last"
            primary
            disabled={!hasGame || clampedPly >= moves.length - 1}
            onClick={() => onPlySelect(moves.length - 1)}
            icon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2.4" viewBox="0 0 24 24" aria-hidden>
                <path d="m5 4 10 8L5 20" />
                <path d="M19 5v14" />
              </svg>
            }
          />
          <PlaybackButton
            label="Reset"
            disabled={!hasGame}
            onClick={() => onPlySelect(0)}
            icon={
              <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                <path d="M3 12a9 9 0 1 0 3-6.7" />
                <path d="M3 4v5h5" />
              </svg>
            }
          />
        </div>

        <div className="relative mx-auto aspect-square w-full max-w-[560px] overflow-hidden rounded-2xl border border-black/60 shadow-2xl shadow-black/50">
          <Chessboard
            position={position}
            boardOrientation={orientation}
            allowDragging={false}
            boardStyle={{
              width: '100%',
              height: '100%',
              borderRadius: '0',
            }}
            animationDurationInMs={250}
            {...squareStyles}
          />
          {isAnalyzing && (
            <div className="absolute inset-0 flex items-center justify-center bg-black/40 backdrop-blur-sm">
              <div className="flex items-center gap-2 rounded-full border border-emerald-400/30 bg-black/70 px-3 py-1.5 text-xs text-emerald-200">
                <span className="h-3 w-3 rounded-full border-2 border-emerald-300/40 border-t-emerald-200 animate-spin" />
                <span>Analyzing position...</span>
              </div>
            </div>
          )}
        </div>

        {hasGame ? (
          <MoveList
            moves={moves}
            activePly={clampedPly}
            onPlySelect={onPlySelect}
          />
        ) : (
          <div className="w-full rounded-2xl border border-dashed border-amber-900/40 bg-black/30 p-3 text-center text-xs text-zinc-500">
            The move list will appear here once a game is imported.
          </div>
        )}
      </div>
    </section>
  );
}

function PlaybackButton({
  label,
  icon,
  onClick,
  disabled,
  primary,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className={`inline-flex items-center justify-center rounded-xl border px-3 py-2.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
        primary
          ? 'border-emerald-500/40 bg-emerald-500/15 text-emerald-200 hover:border-emerald-400 hover:bg-emerald-500/25'
          : 'border-black/40 bg-black/40 text-zinc-200 hover:border-amber-900/50 hover:text-white'
      }`}
    >
      {icon}
    </button>
  );
}

type ChessboardProps = {
  position: string;
  boardOrientation: 'white' | 'black';
  allowDragging?: boolean;
  boardStyle?: CSSProperties;
  animationDurationInMs?: number;
  customDarkSquareStyle: CSSProperties;
  customLightSquareStyle: CSSProperties;
};

function Chessboard({
  customDarkSquareStyle,
  customLightSquareStyle,
  ...props
}: ChessboardProps) {
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
