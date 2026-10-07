'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Chess, type Square } from 'chess.js';
import { Chessboard as BaseChessboard } from 'react-chessboard';
import type {
  PieceDropHandlerArgs,
  PieceHandlerArgs,
  SquareHandlerArgs,
  SquareRenderer as SquareRendererType,
} from 'react-chessboard';

import PromotionPicker, {
  type PromotionPiece,
} from '@/components/board/PromotionPicker';
import { GameReviewMove } from '@/types';

import { ClassificationIcon } from './icons/ClassificationIcon';

type BoardPanelProps = {
  position: string;
  fenBefore: string | null;
  currentMove: GameReviewMove | null;
  isAnalyzing: boolean;
  hasGame: boolean;
  showBestMove: boolean;
  allowDragging?: boolean;
  onExploreMove?: (from: string, to: string, promotion?: string) => void;
  exploreMode?: boolean;
  onToggleExplore?: () => void;
  // Capability signal: the toggle stays hidden unless the sandbox is on.
  sandboxEnabled?: boolean;
  // Explore mode: best move for the side to move at the active position.
  suggestionUci?: string | null;
  // True while the played move has no engine snapshot yet: show a neutral
  // pending marker instead of a placeholder classification badge.
  classificationPending?: boolean;
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
const EVAL_CAP_PAWNS = 7;
const EVAL_CURVE_SCALE_PAWNS = 2.25;
const EVAL_BAR_TRANSITION = 'height 420ms cubic-bezier(0.22, 1, 0.36, 1)';

type EvaluationBarState = {
  whitePercent: number;
  blackPercent: number;
  label: string;
  isMate: boolean;
};

function getEvaluationBarState(move: GameReviewMove | null): EvaluationBarState {
  const rawEvalCp = move?.eval_cp ?? 0;
  const evalCp = Number.isFinite(rawEvalCp) ? rawEvalCp : 0;
  const evalMate = move?.eval_mate;

  if (typeof evalMate === 'number' && Number.isFinite(evalMate)) {
    const whiteWins = evalMate > 0 || (evalMate === 0 && evalCp >= 0);
    return {
      whitePercent: whiteWins ? 100 : 0,
      blackPercent: whiteWins ? 0 : 100,
      label: `${whiteWins ? '' : '-'}M${Math.abs(evalMate)}`,
      isMate: true,
    };
  }

  // The API's eval_cp is centipawns. Convert to pawns before applying the
  // visual curve, then cap the curve's input so extreme scores saturate.
  const evalPawns = evalCp / 100;
  const cappedEval = Math.max(-EVAL_CAP_PAWNS, Math.min(EVAL_CAP_PAWNS, evalPawns));
  const advantage = Math.tanh(cappedEval / EVAL_CURVE_SCALE_PAWNS);
  const whitePercent = 50 + advantage * 50;

  return {
    whitePercent,
    blackPercent: 100 - whitePercent,
    label: `${evalPawns >= 0 ? '+' : ''}${evalPawns.toFixed(2)}`,
    isMate: false,
  };
}

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

type PendingPromotion = { source: string; target: string };

export default function BoardPanel({
  position,
  fenBefore,
  currentMove,
  isAnalyzing,
  hasGame,
  showBestMove,
  allowDragging = false,
  onExploreMove,
  exploreMode = false,
  onToggleExplore,
  sandboxEnabled = false,
  suggestionUci = null,
  classificationPending = false,
}: BoardPanelProps) {
  const [orientation, setOrientation] = useState<'white' | 'black'>('white');
  const [bestStep, setBestStep] = useState<'off' | 'undo' | 'best'>('off');
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [moveToPromote, setMoveToPromote] = useState<PendingPromotion | null>(null);

  const boardPosition = position || START_FEN;
  const bestMoveUci = currentMove?.best_move_uci ?? null;
  const bestMoveResultFen =
    fenBefore && bestMoveUci ? applyUciMove(fenBefore, bestMoveUci) : null;
  // Explore mode: best move for the side to move at the current position
  // (the next position's best). Playing it from the current position gives
  // the preview shown when "Best move" is toggled while exploring.
  const suggestionResultFen =
    suggestionUci && suggestionUci.length >= 4
      ? (applyUciMove(boardPosition, suggestionUci) ?? null)
      : null;

  // Persistent suggestion arrow. Outside explore mode it is the analyzed
  // move's "better move" (best_move_uci). In explore mode the board shows a
  // position the user is deciding on, so the arrow must be the best move for
  // the side to move at that position (suggestionUci), never the pre-move
  // line of the move just played. Hidden during the best-move animation.
  const arrowUci = exploreMode ? suggestionUci : bestMoveUci;
  const suggestionArrows = useMemo(() => {
    if (!hasGame || !arrowUci || arrowUci.length < 4) {
      return [];
    }
    // Review mode animates via bestStep; explore mode previews the
    // suggestion directly, so hide the arrow while that preview is up.
    if (!exploreMode && bestStep !== 'off') {
      return [];
    }
    if (exploreMode && showBestMove) {
      return [];
    }
    const from = arrowUci.slice(0, 2);
    const to = arrowUci.slice(2, 4);
    if (from === to) {
      return [];
    }
    return [{ startSquare: from, endSquare: to, color: '#10b981' }];
  }, [hasGame, bestStep, arrowUci, exploreMode, showBestMove]);

  // Adjust the animation step during render whenever the toggle flips, so the
  // board immediately shows the played move being taken back (undo) and then
  // - after a short delay - the best move being played. Explore mode skips
  // the undo replay: it previews the suggestion straight from the current
  // position, so bestStep stays off there.
  if (!exploreMode && showBestMove && bestStep === 'off') {
    setBestStep('undo');
  } else if (!showBestMove && bestStep !== 'off') {
    setBestStep('off');
  } else if (exploreMode && bestStep !== 'off') {
    setBestStep('off');
  }

  useEffect(() => {
    if (bestStep !== 'undo') return;
    const timer = setTimeout(() => setBestStep('best'), 280);
    return () => clearTimeout(timer);
  }, [bestStep]);

  const boardFen =
    exploreMode && showBestMove
      ? (suggestionResultFen ?? boardPosition)
      : bestStep === 'undo'
        ? (fenBefore ?? boardPosition)
        : bestStep === 'best'
          ? (bestMoveResultFen ?? boardPosition)
          : boardPosition;

  const game = useMemo(() => {
    try {
      return new Chess(boardFen);
    } catch {
      return null;
    }
  }, [boardFen]);

  // Clear any selection/pending promotion whenever the displayed position
  // changes (navigation, best-move animation, an accepted explore move), so
  // the orange tint and hint dots cannot leak onto a board whose pieces have
  // already moved.
  const [prevBoardFen, setPrevBoardFen] = useState(boardFen);
  if (prevBoardFen !== boardFen) {
    setPrevBoardFen(boardFen);
    setSelectedSquare(null);
    setMoveToPromote(null);
  }

  // Dragging/clicking is meaningless while the board is showing the
  // best-move animation (a different position than the review node), so
  // interactions are suspended for the duration of that animation.
  // Explore's suggestion preview also shows a different position.
  const canInteract =
    allowDragging && bestStep === 'off' && !(exploreMode && showBestMove);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!canInteract || !selectedSquare || !game) return {};
    try {
      const legalMoves = game.moves({
        square: selectedSquare as Square,
        verbose: true,
      });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        hints[move.to] = game.get(move.to as Square) ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [canInteract, game, selectedSquare]);

  const lastMoveSquares = useMemo(() => {
    if (bestStep !== 'off' || !currentMove || !fenBefore) return null;
    if (exploreMode && showBestMove) return null;
    const san = currentMove.san;
    if (!san || san === 'Start') return null;
    try {
      const board = new Chess(fenBefore);
      const played = board.move(san);
      return { from: played.from as string, to: played.to as string };
    } catch {
      return null;
    }
  }, [bestStep, currentMove, fenBefore, exploreMode, showBestMove]);

  const checkSquare = useMemo(() => {
    if (!game || !game.isCheck()) return null;
    const turn = game.turn();
    for (const row of game.board()) {
      for (const cell of row) {
        if (cell && cell.type === 'k' && cell.color === turn) {
          return cell.square as string;
        }
      }
    }
    return null;
  }, [game]);

  const displaySquareStyles = useMemo<Record<string, React.CSSProperties>>(
    () => {
      const styles: Record<string, React.CSSProperties> = {};
      if (lastMoveSquares) {
        styles[lastMoveSquares.from] = {
          backgroundColor: 'rgba(255, 213, 105, 0.32)',
        };
        styles[lastMoveSquares.to] = {
          backgroundColor: 'rgba(255, 213, 105, 0.42)',
        };
      }
      if (checkSquare) {
        styles[checkSquare] = {
          background:
            'radial-gradient(circle, rgba(239,68,68,0.85) 0%, rgba(239,68,68,0.45) 45%, rgba(239,68,68,0) 75%)',
        };
      }
      if (selectedSquare) {
        styles[selectedSquare] = {
          backgroundColor: 'rgba(255, 170, 0, 0.35)',
        };
      }
      return styles;
    },
    [lastMoveSquares, checkSquare, selectedSquare],
  );

  const squareRenderer = useCallback<SquareRendererType>(
    ({ square, children }) => {
      const hint = hintSquares[square];
      const squareStyle = displaySquareStyles[square];
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
    [hintSquares, displaySquareStyles],
  );

  const isPromotionMove = useCallback(
    (source: string, target: string): boolean => {
      if (!game) return false;
      try {
        const piece = game.get(source as Square);
        if (!piece || piece.type !== 'p') return false;
        const rank = target.charAt(1);
        return piece.color === 'w' ? rank === '8' : rank === '1';
      } catch {
        return false;
      }
    },
    [game],
  );

  const canDragPiece = useCallback(
    ({ piece }: PieceHandlerArgs) => {
      if (!game) return false;
      return piece.pieceType[0] === game.turn();
    },
    [game],
  );

  const handleSquareClick = useCallback(
    ({ square }: SquareHandlerArgs) => {
      setMoveToPromote(null);

      if (!canInteract || !onExploreMove || !game) {
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
      const isOwnPiece = clickedPiece?.color === game.turn();

      if (!selectedSquare) {
        setSelectedSquare(isOwnPiece && clickedPiece ? square : null);
        return;
      }

      if (selectedSquare === square) {
        setSelectedSquare(null);
        return;
      }

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
        } else {
          onExploreMove(selectedSquare, square);
          setSelectedSquare(null);
        }
        return;
      }

      setSelectedSquare(isOwnPiece && clickedPiece ? square : null);
    },
    [canInteract, game, isPromotionMove, onExploreMove, selectedSquare],
  );

  const handlePieceDrop = useCallback(
    ({ sourceSquare, targetSquare }: PieceDropHandlerArgs) => {
      if (!targetSquare || !canInteract || !onExploreMove) return false;
      if (isPromotionMove(sourceSquare, targetSquare)) {
        setMoveToPromote({ source: sourceSquare, target: targetSquare });
        return false;
      }
      onExploreMove(sourceSquare, targetSquare);
      return true;
    },
    [canInteract, isPromotionMove, onExploreMove],
  );

  const handlePromotionSelect = useCallback(
    (piece: PromotionPiece) => {
      if (moveToPromote && onExploreMove) {
        onExploreMove(moveToPromote.source, moveToPromote.target, piece);
      }
      setMoveToPromote(null);
      setSelectedSquare(null);
    },
    [moveToPromote, onExploreMove],
  );

  const hasPlayedMove =
    hasGame && currentMove !== null && currentMove.san !== 'Start' && !showBestMove;
  const showPlayedIcon = hasPlayedMove && !classificationPending;
  const iconCoords = hasPlayedMove
    ? squareToPercent(
        getDestinationSquare(currentMove!.san, currentMove!.color),
        orientation,
      )
    : null;

  const showBestIcon = exploreMode
    ? showBestMove && !!suggestionUci && !!suggestionResultFen
    : showBestMove && !!bestMoveUci && !!bestMoveResultFen;
  const showBestUci = exploreMode ? suggestionUci : bestMoveUci;
  const bestIconCoords =
    showBestIcon && showBestUci && showBestUci.length >= 4
      ? squareToPercent(showBestUci.slice(2, 4), orientation)
      : null;
  const evaluationBar = getEvaluationBarState(currentMove);

  return (
    <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
      {hasGame && (
        <div
          aria-hidden
          className="absolute right-full top-0 mr-3 flex h-full w-8 flex-col items-center gap-1"
        >
          <div className="relative w-3.5 flex-1 overflow-hidden rounded-[3px] border border-black/70 bg-[#111111] shadow-[0_2px_8px_rgba(0,0,0,0.65),inset_0_0_0_1px_rgba(255,255,255,0.12)]">
            <div
              className="absolute inset-x-0 top-0 bg-[#171717]"
              style={{
                height: `${evaluationBar.blackPercent}%`,
                transition: EVAL_BAR_TRANSITION,
              }}
            />
            <div
              className="absolute inset-x-0 bottom-0 bg-[#f4f4f4]"
              style={{
                height: `${evaluationBar.whitePercent}%`,
                transition: EVAL_BAR_TRANSITION,
              }}
            />
            <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-black/35" />
          </div>
          <span
            className={`min-w-8 rounded border px-0.5 py-0.5 text-center font-mono text-[10px] font-semibold leading-none shadow-[0_2px_5px_rgba(0,0,0,0.45)] ${
              evaluationBar.isMate
                ? 'border-amber-300/50 bg-[#2a1c0d] text-amber-200'
                : 'border-white/15 bg-black/65 text-white/85'
            }`}
          >
            {evaluationBar.label}
          </span>
        </div>
      )}

      <div className="relative h-full w-full">
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
                // Unique DOM id: the library measures squares with
                // document.querySelector, so co-mounted boards must never
                // share the default 'chessboard' id.
                id: 'review-board',
                position: boardFen,
                boardOrientation: orientation,
                allowDragging: canInteract,
                canDragPiece: canInteract ? canDragPiece : undefined,
                onSquareClick: canInteract ? handleSquareClick : undefined,
                onPieceDrop:
                  canInteract && onExploreMove ? handlePieceDrop : undefined,
                squareRenderer,
                squareStyles: displaySquareStyles,
                arrows: suggestionArrows,
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
            {classificationPending && iconCoords && (
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
                <div className="h-full w-full animate-pulse rounded-full border-2 border-white/60 bg-black/30" />
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
            {moveToPromote && (
              <PromotionPicker
                turn={game?.turn() ?? 'w'}
                onSelect={handlePromotionSelect}
                onCancel={() => {
                  setMoveToPromote(null);
                  setSelectedSquare(null);
                }}
              />
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

        {hasGame && sandboxEnabled && onToggleExplore && (
          <button
            type="button"
            onClick={onToggleExplore}
            aria-label={exploreMode ? 'Stop exploring' : 'Start exploring'}
            aria-pressed={exploreMode}
            title={exploreMode ? 'Stop exploring' : 'Start exploring'}
            className="absolute z-20 flex items-center justify-center transition-transform hover:scale-105 active:scale-95"
            style={{
              left: '100%',
              top: '34px',
              marginLeft: '2px',
              width: '28px',
              height: '28px',
              ...woodBoxStyle,
              cursor: 'pointer',
              ...(exploreMode
                ? {
                    boxShadow:
                      '0 0 0 2px #1a0a02, 0 0 0 1px rgba(16,185,129,0.7), 0 0 12px rgba(16,185,129,0.45), inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5)',
                  }
                : {}),
            }}
          >
            {exploreMode ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0">
                <rect x="6" y="5" width="4" height="14" rx="1" />
                <rect x="14" y="5" width="4" height="14" rx="1" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0">
                <path d="M8 5v14l11-7z" />
              </svg>
            )}
          </button>
        )}
      </div>
    </div>
  );
}
