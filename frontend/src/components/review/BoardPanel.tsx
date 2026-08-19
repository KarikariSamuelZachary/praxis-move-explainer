'use client';

import { useEffect, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard as BaseChessboard } from 'react-chessboard';

import { GameReviewMove } from '@/types';

import { ClassificationIcon } from './icons/ClassificationIcon';

type BoardPanelProps = {
  moves: GameReviewMove[];
  activePly: number;
  isAnalyzing: boolean;
  hasGame: boolean;
  showBestMove: boolean;
};

const woodBoxStyle: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

const ICON_SIZE_PERCENT = 5.5;
const ICON_MARGIN_PERCENT = 0.5;
const SQUARE_PERCENT = 100 / 8;

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

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

function applyUciMove(fen: string, uci: string): string | null {
  try {
    const chess = new Chess(fen);
    const from = uci.slice(0, 2);
    const to = uci.slice(2, 4);
    const promotion = uci.length > 4 ? uci[4] : undefined;
    chess.move({ from, to, promotion });
    return chess.fen();
  } catch {
    return null;
  }
}

export default function BoardPanel({
  moves,
  activePly,
  isAnalyzing,
  hasGame,
  showBestMove,
}: BoardPanelProps) {
  const [orientation, setOrientation] = useState<'white' | 'black'>('white');
  const [bestStep, setBestStep] = useState<'off' | 'undo' | 'best'>('off');

  const clampedPly = hasGame ? Math.min(activePly, moves.length - 1) : 0;
  const currentMove = hasGame ? moves[clampedPly] : null;
  const position = currentMove?.fen ?? START_FEN;

  const bestMoveUci = currentMove?.best_move_uci ?? null;
  const fenBefore =
    hasGame && activePly > 0 ? (moves[activePly - 1]?.fen ?? null) : null;
  const bestMoveResultFen =
    fenBefore && bestMoveUci ? applyUciMove(fenBefore, bestMoveUci) : null;

  // Adjust the animation step during render whenever the toggle flips, so the
  // board immediately shows the played move being taken back (undo) and then
  // — after a short delay — the best move being played.
  if (showBestMove && bestStep === 'off') {
    setBestStep('undo');
  } else if (!showBestMove && bestStep !== 'off') {
    setBestStep('off');
  }

  useEffect(() => {
    if (bestStep !== 'undo') return;
    const timer = setTimeout(() => setBestStep('best'), 280);
    return () => clearTimeout(timer);
  }, [bestStep]);

  const boardFen =
    bestStep === 'undo'
      ? (fenBefore ?? position)
      : bestStep === 'best'
        ? (bestMoveResultFen ?? position)
        : position;

  const showPlayedIcon = hasGame && activePly > 0 && currentMove && !showBestMove;
  const iconCoords = showPlayedIcon
    ? squareToPercent(
        getDestinationSquare(currentMove!.san, currentMove!.color),
        orientation,
      )
    : null;

  const showBestIcon = showBestMove && !!bestMoveUci && !!bestMoveResultFen;
  const bestIconCoords = showBestIcon
    ? squareToPercent(bestMoveUci.slice(2, 4), orientation)
    : null;

  // Evaluation bar: position score from White's perspective (positive =
  // White better). Only the winning side's half fills — it grows outward from
  // the centre divider — while the losing side stays empty.
  const evalCp = currentMove?.eval_cp ?? 0;
  const advantage = Math.min(
    1,
    2 / (1 + Math.exp(-Math.abs(evalCp) * 0.00368208)) - 1,
  );
  const whiteFillPct = evalCp > 0 ? advantage * 50 : 0;
  const blackFillPct = evalCp < 0 ? advantage * 50 : 0;
  const evalLabel =
    Math.abs(evalCp) < 0.05
      ? '0.0'
      : `${evalCp > 0 ? '+' : ''}${Math.abs(evalCp).toFixed(1)}`;

  return (
    <div className="mx-auto flex w-full max-w-[calc(100vh-70px)] items-center gap-3">
      {hasGame && (
        <div
          aria-hidden
          className="flex w-3.5 shrink-0 flex-col items-center gap-1 self-stretch"
        >
          <div className="relative w-full flex-1 overflow-hidden rounded-full border border-black/50 bg-[#404040] shadow-[0_2px_8px_rgba(0,0,0,0.5)]">
            <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-white/20" />
            <div
              className="absolute inset-x-0 bg-[#101010] transition-all duration-300"
              style={{ bottom: '50%', height: `${blackFillPct}%` }}
            />
            <div
              className="absolute inset-x-0 bg-[#f0f0f0] transition-all duration-300"
              style={{ top: '50%', height: `${whiteFillPct}%` }}
            />
          </div>
          <span className="font-mono text-[10px] leading-none text-white/70">
            {evalLabel}
          </span>
        </div>
      )}

      <div className="relative aspect-square flex-1">
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
                backgroundImage: 'url("/wood-texture.webp")',
                backgroundSize: 'cover',
                opacity: 0.08,
                pointerEvents: 'none',
                mixBlendMode: 'multiply' as React.CSSProperties['mixBlendMode'],
              }}
            />
            <BaseChessboard
              options={{
                position: boardFen,
                boardOrientation: orientation,
                allowDragging: false,
                boardStyle: {
                  width: '100%',
                  height: '100%',
                  borderRadius: '8px',
                  boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
                },
                darkSquareStyle: {
                  backgroundImage: 'url(/walnut-dark.webp)',
                  backgroundSize: '110% 110%',
                  backgroundPosition: 'center',
                },
                lightSquareStyle: {
                  backgroundImage: 'url(/walnut-light.webp)',
                  backgroundSize: '110% 110%',
                  backgroundPosition: 'center',
                },
                darkSquareNotationStyle: { color: '#f0e0c0' },
                lightSquareNotationStyle: { color: '#3a2410' },
                animationDurationInMs: 200,
              }}
            />
            {showBestIcon && bestIconCoords && (
              <div
                style={{
                  position: 'absolute',
                  left: `${bestIconCoords.col * SQUARE_PERCENT + SQUARE_PERCENT - ICON_SIZE_PERCENT - ICON_MARGIN_PERCENT}%`,
                  top: `${bestIconCoords.row * SQUARE_PERCENT + ICON_MARGIN_PERCENT}%`,
                  width: `${ICON_SIZE_PERCENT}%`,
                  aspectRatio: '1 / 1',
                  pointerEvents: 'none',
                  zIndex: 5,
                }}
              >
                <ClassificationIcon classification="best" size="100%" />
              </div>
            )}
            {showPlayedIcon && iconCoords && (
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
    </div>
  );
}
