'use client';

import { useCallback, useMemo, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard, type SquareRenderer } from 'react-chessboard';

import PromotionPicker, { type PromotionPiece } from '@/components/board/PromotionPicker';

export interface CalcBoardProps {
  /** Exercise start FEN (position before the first entered ply). */
  fen: string;
  /** Entered plies, UCI, oldest first. The board replays them over fen. */
  moves: string[];
  /** False once submitted (or while grading): board locks. */
  interactive: boolean;
  /** Called with the UCI of each legal drop (either color: the user enters
   * both their own moves and the defender replies they expect). */
  onMove: (uci: string) => void;
}

type PendingPromotion = { from: string; to: string } | null;

/**
 * Transcription board for Blind Calculation: no grading, no hints, no
 * correctness feedback of any kind. Illegal drops snap back; everything
 * else is recorded verbatim for the eventual submit.
 */
export default function CalcBoard({ fen, moves, interactive, onMove }: CalcBoardProps) {
  const [pendingPromotion, setPendingPromotion] = useState<PendingPromotion>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);

  const positionFen = useMemo(() => {
    const game = new Chess(fen);
    for (const uci of moves) {
      try {
        game.move({
          from: uci.slice(0, 2),
          to: uci.slice(2, 4),
          promotion: uci[4] ?? undefined,
        });
      } catch {
        break;
      }
    }
    return game.fen();
  }, [fen, moves]);

  const turn = useMemo(() => positionFen.split(' ')[1] ?? 'w', [positionFen]);
  const orientation = fen.split(/\s+/)[1] === 'b' ? 'black' : 'white';

  const pushMove = useCallback(
    (from: string, to: string, promotion?: PromotionPiece) => {
      const game = new Chess(positionFen);
      let move;
      try {
        move = game.move({ from, to, promotion });
      } catch {
        return false;
      }
      setSelectedSquare(null);
      onMove(move.from + move.to + (move.promotion ?? ''));
      return true;
    },
    [positionFen, onMove]
  );

  const onSquareClick = useCallback(
    ({ square }: { piece: { pieceType: string } | null; square: string }) => {
      if (!interactive) {
        setSelectedSquare(null);
        return;
      }
      const game = new Chess(positionFen);
      const clickedPiece = game.get(square as never) as
        | { color?: string }
        | undefined;
      const isOwnPiece = clickedPiece?.color === game.turn();
      if (!selectedSquare) {
        setSelectedSquare(isOwnPiece ? square : null);
        return;
      }
      if (selectedSquare === square) {
        setSelectedSquare(null);
        return;
      }
      const legal = game
        .moves({ square: selectedSquare as never, verbose: true })
        .some((m) => (m as { to: string }).to === square);
      if (!legal) {
        setSelectedSquare(isOwnPiece ? square : null);
        return;
      }
      const promo = game
        .moves({ square: selectedSquare as never, verbose: true })
        .some(
          (m) =>
            (m as { to: string }).to === square &&
            (m as { promotion?: string }).promotion
        );
      if (promo) {
        setPendingPromotion({ from: selectedSquare, to: square });
        return;
      }
      pushMove(selectedSquare, square);
    },
    [interactive, positionFen, selectedSquare, pushMove]
  );

  // Same dot/ring language as the endgame board: dots on quiet targets,
  // rings on captures, amber wash on the selected square.
  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare || !interactive) return {};
    try {
      const game = new Chess(positionFen);
      const legalMoves = game.moves({
        square: selectedSquare as never,
        verbose: true,
      }) as { to: string }[];
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        hints[move.to] = game.get(move.to as never) ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [positionFen, interactive, selectedSquare]);

  const squareRenderer = useCallback<SquareRenderer>(
    ({ square, children }) => {
      const hint = hintSquares[square];
      return (
        <div
          className="relative h-full w-full"
          style={
            square === selectedSquare
              ? { backgroundColor: 'rgba(255, 170, 0, 0.35)' }
              : undefined
          }
        >
          {hint === 'dot' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[30%] w-[30%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/25" />
          )}
          {hint === 'ring' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[88%] w-[88%] -translate-x-1/2 -translate-y-1/2 rounded-full border-[6px] border-black/25" />
          )}
          {children}
        </div>
      );
    },
    [hintSquares, selectedSquare]
  );
  const onDrop = useCallback(
    (sourceSquare: string, targetSquare: string) => {
      if (!interactive) return false;
      const game = new Chess(positionFen);
      const candidates = game.moves({
        square: sourceSquare as never,
        verbose: true,
      }) as { to: string; promotion?: string }[];
      if (!candidates.some((m) => m.to === targetSquare)) return false;
      if (candidates.some((m) => m.to === targetSquare && m.promotion)) {
        setPendingPromotion({ from: sourceSquare, to: targetSquare });
        return false;
      }
      return pushMove(sourceSquare, targetSquare);
    },
    [interactive, positionFen, pushMove]
  );

  return (
    <div className="relative h-full w-full">
      <Chessboard
        options={{
          position: positionFen,
          boardOrientation: orientation,
          squareStyles: {},
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
          boardStyle: {
            width: '100%',
            borderRadius: '8px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
          },
          animationDurationInMs: 200,
          allowDragging: interactive,
          onSquareClick,
          squareRenderer,
          onPieceDrop: ({ sourceSquare, targetSquare }) => {
            if (!targetSquare) return false;
            return onDrop(sourceSquare, targetSquare);
          },
        }}
      />
      {pendingPromotion && (
        <PromotionPicker
          turn={(turn === 'b' ? 'b' : 'w') as 'w' | 'b'}
          onSelect={(piece) => {
            const { from, to } = pendingPromotion;
            setPendingPromotion(null);
            pushMove(from, to, piece);
          }}
          onCancel={() => setPendingPromotion(null)}
        />
      )}
    </div>
  );
}
