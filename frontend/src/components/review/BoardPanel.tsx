'use client';

import { useState } from 'react';
import { Chessboard as BaseChessboard } from 'react-chessboard';

import { GameReviewMove } from '@/types';

type BoardPanelProps = {
  title: string;
  onTitleChange: (title: string) => void;
  moves: GameReviewMove[];
  activePly: number;
  onPlySelect: (ply: number) => void;
  isAnalyzing: boolean;
  hasGame: boolean;
};

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

  const clampedPly = hasGame ? Math.min(activePly, moves.length - 1) : 0;
  const currentMove = hasGame ? moves[clampedPly] : null;
  const position = currentMove?.fen ?? 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

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
            className="min-w-0 flex-1 rounded-lg border border-[#10b981]/40 bg-black/40 px-2 py-1 text-base font-semibold text-white outline-none focus:ring-2 focus:ring-[#10b981]/30"
          />
        ) : (
          <button
            type="button"
            onClick={() => setIsEditingTitle(true)}
            disabled={!hasGame}
            className="group flex min-w-0 flex-1 items-center gap-2 rounded-lg px-1 py-1 text-left transition hover:bg-white/5 disabled:cursor-not-allowed disabled:hover:bg-transparent"
          >
            <h2 className="truncate text-base font-semibold tracking-tight text-white">
              {hasGame && title ? title : 'Untitled Game'}
            </h2>
            <svg
              className="h-3.5 w-3.5 shrink-0 text-white/50 transition group-hover:text-[#10b981]"
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
            className="inline-flex items-center gap-1.5 rounded-lg border border-black/50 bg-black/30 px-2.5 py-1.5 text-xs font-semibold text-white transition hover:bg-white/5 disabled:opacity-50"
          >
            <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
              <path d="M17 1l4 4-4 4" />
              <path d="M3 11V9a4 4 0 0 1 4-4h14" />
              <path d="M7 23l-4-4 4-4" />
              <path d="M21 13v2a4 4 0 0 1-4 4H3" />
            </svg>
            Flip Board
          </button>
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

        <div className="mx-auto aspect-square w-full max-w-[calc(100vh-260px)]" style={{
          padding: '14px',
          background: 'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.png)',
          backgroundSize: 'cover',
          backgroundPosition: 'center',
          borderRadius: '6px',
          boxShadow: '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
        }}>
          <div className="relative h-full w-full">
            <div
              style={{
                position: 'absolute',
                inset: 0,
                backgroundImage: 'url("/wood-texture.png")',
                backgroundSize: 'cover',
                opacity: 0.08,
                pointerEvents: 'none',
                mixBlendMode: 'multiply' as React.CSSProperties['mixBlendMode'],
              }}
            />
            <BaseChessboard
              options={{
                position,
                boardOrientation: orientation,
                allowDragging: false,
                boardStyle: {
                  width: '100%',
                  height: '100%',
                  borderRadius: '8px',
                  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                },
                darkSquareStyle: {
                  backgroundImage: 'url(/walnut-dark.png)',
                  backgroundSize: '110% 110%',
                  backgroundPosition: 'center',
                },
                lightSquareStyle: {
                  backgroundImage: 'url(/walnut-light.png)',
                  backgroundSize: '110% 110%',
                  backgroundPosition: 'center',
                },
                darkSquareNotationStyle: { color: '#f0e0c0' },
                lightSquareNotationStyle: { color: '#3a2410' },
                animationDurationInMs: 200,
              }}
            />
            {isAnalyzing && (
              <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/40 backdrop-blur-sm">
                <div className="flex items-center gap-2 rounded-full border border-[#10b981]/30 bg-black/70 px-3 py-1.5 text-xs text-[#10b981]">
                  <span className="h-3 w-3 rounded-full border-2 border-[#10b981]/40 border-t-[#10b981] animate-spin" />
                  <span>Analyzing position...</span>
                </div>
              </div>
            )}
          </div>
        </div>

        </div>
    </section>
  );
}

function PlaybackButton({
  label,
  icon,
  onClick,
  disabled,
}: {
  label: string;
  icon: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      className="inline-flex items-center justify-center rounded-lg border border-black/50 bg-black/30 px-3 py-2.5 text-sm font-semibold text-white transition hover:bg-white/5 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {icon}
    </button>
  );
}