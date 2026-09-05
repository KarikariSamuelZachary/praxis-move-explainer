'use client';

/**
 * BoardShell - reusable walnut-frame chessboard shell shared by the
 * Repertoire build/train pages so their board looks and feels
 * identical to the puzzles-page board (src/components/board/ChessBoard.tsx)
 * and the train/opponent-prep board.
 *
 * What it owns:
 *   * The walnut frame (14px padding, walnut-dark gradient, the layered
 *     box-shadow, the wood-texture overlay at 8% opacity). Matches the
 *     reference markup in ChessBoard.tsx:627-648 verbatim so an artist
 *     can't tell the two board instances apart on screen.
 *   * Walnut square styles + notation colors (the palette used by every
 *     Praxis board).
 *   * Click-to-move with hint dots/rings (squareRenderer). When a square
 *     is selected, legal moves are computed via chess.js from the
 *     current `position` FEN and rendered as dots (empty targets) or
 *     rings (captures) - same UX as the puzzles board.
 *   * Promotion dialog. When a pawn is dragged/clicked to the last rank,
 *     the dialog appears and the move only resolves once the user picks
 *     a piece. The shell leaves the dragged piece snapped back during
 *     the dialog (returns false from onPieceDrop), then re-issues the
 *     move with the chosen promotion via `onMove`.
 *
 * What it does NOT own (controlled by the parent):
 *   * The `position` FEN - the parent passes it; the shell just renders
 *     it. After a drag/click the shell calls `onMove` and trusts the
 *     parent to update `position`. The shell resets its transient
 *     selection state whenever `position` changes.
 *   * "Last-move" highlights - the shell accepts an optional
 *     `squareStyles` prop the parent can use to mark squares (e.g. the
 *     last-move from/to pair). The shell itself only paints selection
 *     dot/ring hints; if the parent wants highlights, it passes them in.
 *
 * Pattern parity with ChessBoard.tsx:
 *   * The puzzles board has its own in-file copy of this same visual +
 *     interaction markup. The shell here is the clean-room extraction
 *     of that markup, with puzzle-specific state (game rotation,
 *     expected-move comparison, opponent reply scheduling) removed -
 *     those are the puzzle page's job. Both files render the same
 *     walnut frame, the same notation colors, the same hint dot/ring
 *     geometry, the same promotion dialog. Repertoire pages use this
 *     shell; the puzzles page intentionally keeps its bespoke
 *     implementation because its state coupling to `puzzle.moves`
 *     is heavy and rewriting it is a separate task.
 */

import { useCallback, useMemo, useState } from 'react';
import dynamic from 'next/dynamic';
import { Chess, type Square } from 'chess.js';
import type {
  Arrow,
  PieceDropHandlerArgs,
  PieceHandlerArgs,
  SquareHandlerArgs,
  SquareRenderer as SquareRendererType,
} from 'react-chessboard';

const Chessboard = dynamic(
  () => import('react-chessboard').then((module) => module.Chessboard),
  {
    ssr: false,
    loading: () => (
      <div
        className="h-full w-full animate-pulse rounded-md bg-black/35"
        aria-hidden="true"
      />
    ),
  }
);

// Normalize a (possibly 4-field) FEN into a full 6-field FEN chess.js
// will accept. repertoire_positions stores FENs normalized to 4 fields
// (board, side, castling, en-passant - see services/repertoire_service.py's
// `_normalize_fen`), so the train flow passes 4-field values here.
// chess.js STRICTLY requires six space-delimited fields (its validator
// throws `Invalid FEN: must contain six space-delimited fields` on a
// 4-field string), so we append neutral halfmove=0 / fullmove=1 rather
// than rely on chess.js to fill them - which it does NOT.
function normalizeFenForChess(fen: string): string {
  const parts = fen.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 6) return parts.slice(0, 6).join(' ');
  const [board = '8/8/8/8/8/8/8/8', side = 'w', castling = '-', ep = '-'] =
    parts;
  return `${board} ${side} ${castling} ${ep} 0 1`;
}

// The visual frame around the Chessboard. Lifted verbatim from the
// puzzles ChessBoard's outer div (ChessBoard.tsx:628) so the two
// board shells cannot be told apart on screen.
const FRAME_STYLE: React.CSSProperties = {
  padding: '14px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  borderRadius: '6px',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
};

// Inner wood-texture overlay at 8% opacity, multiplied. Puzzles board
// uses the same overlay (ChessBoard.tsx:639-647) for the matte, grained
// finish.
const WOOD_OVERLAY_STYLE: React.CSSProperties = {
  position: 'absolute',
  inset: 0,
  backgroundImage: 'url("/wood-texture.webp")',
  backgroundSize: 'cover',
  opacity: 0.08,
  pointerEvents: 'none',
  mixBlendMode: 'multiply',
};

export type BoardShellProps = {
  position: string;
  orientation: 'white' | 'black';
  allowDragging?: boolean;
  canDragPiece?: (args: PieceHandlerArgs) => boolean;
  /**
   * Returns true to accept the move (the parent will update `position`
   * so the shell re-renders the new state), false to snap back (the
   * parent leaves `position` untouched and the dragged piece visually
   * returns). For pawn promotions, this is called only after the user
   * picks a promotion piece from the dialog.
   */
  onMove: (source: string, target: string, promotion?: string) => boolean;
  /** Optional external square highlights (e.g. last-move from/to). */
  squareStyles?: Record<string, React.CSSProperties>;
  /** Static overlay arrows (e.g. repertoire continuation arrows). */
  arrows?: Arrow[];
  /** Hide notation labels under pieces (e.g. for thumbnail boards). */
  showNotation?: boolean;
  className?: string;
};

type PendingPromotion = { source: string; target: string };

export default function BoardShell({
  position,
  orientation,
  allowDragging = true,
  canDragPiece,
  onMove,
  squareStyles,
  arrows,
  showNotation = true,
  className,
}: BoardShellProps) {
  // chess.js instance keyed off `position`. Recomputed on each position
  // change so the legal-move hint dots stay consistent with the board
  // the user is looking at. We wrap in try/catch because chess.js's
  // constructor throws on malformed FENs - the shell renders an empty
  // 8x8 board in that case rather than crashing the page.
  const game = useMemo(() => {
    try {
      return new Chess(normalizeFenForChess(position));
    } catch {
      return new Chess();
    }
  }, [position]);

  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [moveToPromote, setMoveToPromote] = useState<PendingPromotion | null>(
    null
  );

  // Reset selection whenever the position changes. The React-recommended
  // "adjust some state when a prop changes" pattern (store the prior
  // value as state, compare during render, call the other setters if
  // it differs - see https://react.dev/reference/react/useState#storing-
  // information-from-previous-renders) - explicit because the new
  // strict `react-hooks/refs` lint rule disallows touching refs in
  // render. Doing the reset inline during render (NOT in an effect)
  // means the next commit paints the cleared state on the first try;
  // an effect-based reset would trigger a cascading second render.
  //
  // Without this, the orange selection highlight + hint dots stay live
  // on a board whose pieces have already moved (after a successful
  // drag/click the parent updates `position`; the stale selection is
  // visually wrong for the new position).
  const [prevPosition, setPrevPosition] = useState(position);
  if (prevPosition !== position) {
    setPrevPosition(position);
    setSelectedSquare(null);
    setMoveToPromote(null);
  }

  // Hint dots/rings for the legal destinations of the selected piece.
  // Empty map when nothing is selected (so the squareRenderer renders
  // the unmodified squares - the children render normally).
  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare) return {};
    try {
      const legalMoves = game.moves({
        square: selectedSquare as Square,
        verbose: true,
      });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        const targetPiece = game.get(move.to as Square);
        hints[move.to] = targetPiece ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [game, selectedSquare]);

  // Selection-driven square highlight (the orange tint on the
  // selected square) merged with any external highlights the parent
  // passes via `squareStyles`. The parent's styles win on conflict -
  // its intent (e.g. an accepted-move green) is more specific than the
  // shell's generic selection tint.
  const mergedSquareStyles = useMemo<Record<string, React.CSSProperties>>(
    () => {
      if (!selectedSquare) return squareStyles ?? {};
      return {
        [selectedSquare]: { backgroundColor: 'rgba(255, 170, 0, 0.35)' },
        ...(squareStyles ?? {}),
      };
    },
    [selectedSquare, squareStyles]
  );

  // squareRenderer: per-cell paint. Combines the hint dot/ring (from
  // selectedSquare) + the selection/background styles (from
  // mergedSquareStyles). Matches the puzzles board geometry so a user
  // can't tell them apart by interaction feel.
  const squareRenderer = useCallback<SquareRendererType>(
    ({ square, children }) => {
      const hint = hintSquares[square];
      const squareStyle = mergedSquareStyles[square];
      return (
        <div className="relative h-full w-full" style={squareStyle}>
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
    [hintSquares, mergedSquareStyles]
  );

  // Helper: does `source`→`target` on this position constitute a pawn
  // promotion? Used by both drag and click-to-move paths so the
  // promotion dialog fires identically in either input mode.
  const isPromotionMove = useCallback(
    (source: string, target: string): boolean => {
      try {
        const piece = game.get(source as Square);
        if (!piece || piece.type !== 'p') return false;
        const rank = target.charAt(1);
        return piece.color === 'w' ? rank === '8' : rank === '1';
      } catch {
        return false;
      }
    },
    [game]
  );

  // Click-to-move handler. Mirrors the puzzles board's onSquareClick
  // shape (ChessBoard.tsx:451) but routes the resolved move through
  // `onMove` rather than puzzle-specific validation.
  const handleSquareClick = useCallback(
    ({ piece, square }: SquareHandlerArgs) => {
      setMoveToPromote(null);

      if (!allowDragging) {
        setSelectedSquare(null);
        return;
      }

      const clickedPiece = (() => {
        try {
          return game.get(square as Square);
        } catch {
          return null;
        }
      })();
      const turn = game.turn();
      const isOwnPiece = clickedPiece?.color === turn;

      if (!selectedSquare) {
        setSelectedSquare(
          isOwnPiece && clickedPiece ? square : null
        );
        return;
      }

      if (selectedSquare === square) {
        setSelectedSquare(null);
        return;
      }

      // Try to move from selectedSquare → square.
      let isLegal = false;
      try {
        isLegal = game
          .moves({ square: selectedSquare as Square, verbose: true })
          .some((move) => move.to === square);
      } catch {
        isLegal = false;
      }

      if (isLegal) {
        if (isPromotionMove(selectedSquare, square)) {
          setMoveToPromote({ source: selectedSquare, target: square });
          // Don't deselect - keep the source highlighted while the
          // dialog is open so the user has visual continuity.
        } else {
          const accepted = onMove(selectedSquare, square, undefined);
          setSelectedSquare(accepted ? null : selectedSquare);
        }
        return;
      }

      // Not a legal move from selected → reselect the new square if
      // it's the side-to-move's piece; otherwise clear selection.
      setSelectedSquare(isOwnPiece && clickedPiece ? square : null);
      void piece;
    },
    [allowDragging, game, isPromotionMove, onMove, selectedSquare]
  );

  // Drag-and-drop. Same shape as the puzzles board's onPieceDrop
  // (ChessBoard.tsx:692) with the promotion gate folded in. We return
  // false on promotion (snapback visually) and stash the pending pair
  // for the dialog; the parent's onMove is only called once the user
  // picks a piece, after which we return true so the re-issue attempt
  // registers as visually accepted.
  const handlePieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs) => {
      if (!targetSquare) return false;
      if (!allowDragging) return false;

      if (isPromotionMove(sourceSquare, targetSquare)) {
        setMoveToPromote({ source: sourceSquare, target: targetSquare });
        return false; // snap back; re-applied on promotion pick
      }
      return onMove(sourceSquare, targetSquare, undefined);
    },
    [allowDragging, isPromotionMove, onMove]
  );

  // Promotion dialog pick.
  const handlePromotionSelect = useCallback(
    (piece?: string) => {
      if (moveToPromote && piece) {
        onMove(moveToPromote.source, moveToPromote.target, piece);
      }
      setMoveToPromote(null);
      setSelectedSquare(null);
      return true;
    },
    [moveToPromote, onMove]
  );

  const promotionDialogTurn = game.turn();

  return (
    <div
      className={`relative h-full w-full ${className ?? ''}`}
      style={FRAME_STYLE}
    >
      <div className="relative h-full w-full">
        <div style={WOOD_OVERLAY_STYLE} aria-hidden="true" />
        <Chessboard
          options={{
            position,
            boardOrientation: orientation,
            showNotation,
            allowDragging,
            canDragPiece,
            onSquareClick: handleSquareClick,
            onPieceDrop: handlePieceDrop,
            squareRenderer,
            squareStyles: mergedSquareStyles,
            arrows,
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
              height: '100%',
              borderRadius: '8px',
              boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            },
            animationDurationInMs: 200,
          }}
        />

        {moveToPromote && (
          <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/60 backdrop-blur-sm">
            <div className="rounded-xl border border-zinc-700 bg-zinc-800 p-4 shadow-2xl">
              <h3 className="mb-4 text-center font-medium text-white">
                Promote to
              </h3>
              <div className="flex gap-2">
                {['q', 'r', 'b', 'n'].map((piece) => (
                  <button
                    key={piece}
                    type="button"
                    onClick={() => handlePromotionSelect(piece)}
                    className="flex h-14 w-14 items-center justify-center rounded-lg bg-zinc-700 pb-2 text-4xl transition-colors hover:bg-emerald-600"
                    aria-label={`Promote to ${piece.toUpperCase()}`}
                  >
                    {promotionDialogTurn === 'w'
                      ? piece === 'q'
                        ? '♕'
                        : piece === 'r'
                        ? '♖'
                        : piece === 'b'
                        ? '♗'
                        : '♘'
                      : piece === 'q'
                      ? '♛'
                      : piece === 'r'
                      ? '♜'
                      : piece === 'b'
                      ? '♝'
                      : '♞'}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => {
                  setMoveToPromote(null);
                  setSelectedSquare(null);
                }}
                className="mt-4 w-full rounded-lg bg-zinc-700/50 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-700"
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}