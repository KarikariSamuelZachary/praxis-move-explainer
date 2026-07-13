'use client';

import { useState } from 'react';
import { Chessboard as BaseChessboard } from 'react-chessboard';

import { GameReviewMove } from '@/types';

import { ClassificationIcon } from './icons/ClassificationIcon';

type BoardPanelProps = {
  moves: GameReviewMove[];
  activePly: number;
  isAnalyzing: boolean;
  hasGame: boolean;
};

const woodBoxStyle: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.png)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

const ICON_SIZE_PERCENT = 5.5;
const ICON_MARGIN_PERCENT = 0.5;
const SQUARE_PERCENT = 100 / 8;

function getDestinationSquare(san: string, color: 'white' | 'black'): string {
  const s = san.replace(/[!?+#]+$/, '').replace(/=[QRBN]$/, '');
  if (s === 'O-O-O' || s === '0-0-0') return color === 'white' ? 'c1' : 'c8';
  if (s === 'O-O' || s === '0-0') return color === 'white' ? 'g1' : 'g8';
  return s.slice(-2);
}

function squareToPercent(square: string, orientation: 'white' | 'black') {
  const file = square.charCodeAt(0) - 97;
  const rank = parseInt(square[1], 10) - 1;
  const col = orientation === 'white' ? file : 7 - file;
  const row = orientation === 'white' ? 7 - rank : rank;
  return { col, row };
}

export default function BoardPanel({
  moves,
  activePly,
  isAnalyzing,
  hasGame,
}: BoardPanelProps) {
  const [orientation, setOrientation] = useState<'white' | 'black'>('white');

  const clampedPly = hasGame ? Math.min(activePly, moves.length - 1) : 0;
  const currentMove = hasGame ? moves[clampedPly] : null;
  const position =
    currentMove?.fen ??
    'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

  const showIcon = hasGame && activePly > 0 && currentMove;
  const iconCoords = showIcon
    ? squareToPercent(
        getDestinationSquare(currentMove!.san, currentMove!.color),
        orientation,
      )
    : null;

  return (
    <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
      <div
        className="h-full w-full"
        style={{
          padding: '14px',
          ...woodBoxStyle,
          borderRadius: '6px',
          boxShadow:
            '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
        }}
      >
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
          {showIcon && iconCoords && (
            <div
              style={{
                position: 'absolute',
                left: `${iconCoords.col * SQUARE_PERCENT + SQUARE_PERCENT - ICON_SIZE_PERCENT - ICON_MARGIN_PERCENT}%`,
                top: `${iconCoords.row * SQUARE_PERCENT + ICON_MARGIN_PERCENT}%`,
                width: `${ICON_SIZE_PERCENT}%`,
                aspectRatio: '1 / 1',
                pointerEvents: 'none',
                zIndex: 5,
              }}
            >
              <ClassificationIcon
                classification={currentMove!.classification}
                size="100%"
              />
            </div>
          )}
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

      <button
        type="button"
        onClick={() => setOrientation((o) => (o === 'white' ? 'black' : 'white'))}
        aria-label="Flip board"
        className="absolute z-20 flex items-center justify-center transition-transform hover:scale-105 active:scale-95"
        style={{
          left: '100%',
          top: 0,
          marginLeft: '2px',
          width: '28px',
          height: '28px',
          ...woodBoxStyle,
          cursor: 'pointer',
        }}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="#f0e0c0"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M4 9 Q12 2 20 9" />
          <path d="M17 6 L20 9 L17 12" />
          <path d="M20 15 Q12 22 4 15" />
          <path d="M7 12 L4 15 L7 18" />
        </svg>
      </button>
    </div>
  );
}