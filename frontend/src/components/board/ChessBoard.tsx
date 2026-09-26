'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess, type Move, type Square } from 'chess.js';
import { Chessboard, type SquareRenderer } from 'react-chessboard';

import PromotionPicker from '@/components/board/PromotionPicker';
import { Puzzle } from '@/types';

export type BoardApi = {
  showSolution: () => void;
  showHint: () => void;
  resetPuzzle: () => void;
};

interface ChessBoardProps {
  puzzle: Puzzle;
  onPuzzleSolved: (timeSeconds: number) => void;
  onPuzzleFailed: () => void;
  onPuzzleEnd?: () => void;
  apiRef?: React.MutableRefObject<BoardApi | null>;
}

type PuzzleState =
  | 'animating_initial'
  | 'playing'
  | 'waiting_for_opponent'
  | 'solved'
  | 'showing_solution';

type HighlightSquares = Record<string, object>;

type PendingPromotionMove = {
  source: string;
  target: string;
};

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';

function applyUciMove(game: Chess, uciMove: string): Move {
  const from = uciMove.slice(0, 2);
  const to = uciMove.slice(2, 4);
  const promotion = uciMove.length > 4 ? uciMove[4] : undefined;

  return game.move({ from, to, promotion });
}

function buildHighlight(from: string, to: string, color: string): HighlightSquares {
  return {
    [from]: { backgroundColor: color },
    [to]: { backgroundColor: color },
  };
}

function buildInitialGame(puzzle: Puzzle): Chess {
  return new Chess(puzzle.fen || START_FEN);
}

function getPuzzleOrientation(puzzle: Puzzle): 'white' | 'black' {
  const turn = (puzzle.fen || START_FEN).split(/\s+/)[1];
  return turn === 'b' ? 'black' : 'white';
}

function buildInitialHighlight(puzzle: Puzzle): HighlightSquares {
  if (!puzzle.previousMove || puzzle.previousMove.length < 4) {
    return {};
  }

  return buildHighlight(
    puzzle.previousMove.slice(0, 2),
    puzzle.previousMove.slice(2, 4),
    'rgba(255, 170, 0, 0.35)'
  );
}

export default function ChessBoardComponent({
  puzzle,
  onPuzzleSolved,
  onPuzzleFailed,
  onPuzzleEnd,
  apiRef,
}: ChessBoardProps) {
  const [game, setGame] = useState<Chess>(() => buildInitialGame(puzzle));
  const [highlightSquares, setHighlightSquares] = useState<HighlightSquares>(() => buildInitialHighlight(puzzle));
  const [puzzleState, setPuzzleState] = useState<PuzzleState>('playing');
  const [hintSquare, setHintSquare] = useState<string | null>(null);
  const [moveToPromote, setMoveToPromote] = useState<PendingPromotionMove | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);

  const startTimeRef = useRef<number>(0);
  const gameRef = useRef<Chess>(game);
  const currentMoveIndexRef = useRef<number>(0);
  const puzzleKeyRef = useRef<string>('');
  const opponentMoveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrongFlashTimeoutRef = useRef<number | null>(null);
  const hintTimeoutRef = useRef<number | null>(null);
  const snapbackTimeoutRef = useRef<number | null>(null);
  const initialMoveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const boardOrientation = getPuzzleOrientation(puzzle);

  const setBoardState = useCallback((nextGame: Chess, nextMoveIndex: number) => {
    gameRef.current = nextGame;
    currentMoveIndexRef.current = nextMoveIndex;
    setGame(nextGame);
  }, []);

  const clearOpponentMoveTimeout = useCallback(() => {
    if (opponentMoveTimeoutRef.current) {
      clearTimeout(opponentMoveTimeoutRef.current);
      opponentMoveTimeoutRef.current = null;
    }
  }, []);

  const clearWrongFlashTimeout = useCallback(() => {
    if (wrongFlashTimeoutRef.current) {
      clearTimeout(wrongFlashTimeoutRef.current);
      wrongFlashTimeoutRef.current = null;
    }
  }, []);

  const clearHintTimeout = useCallback(() => {
    if (hintTimeoutRef.current) {
      clearTimeout(hintTimeoutRef.current);
      hintTimeoutRef.current = null;
    }
  }, []);

  const clearSnapbackTimeout = useCallback(() => {
    if (snapbackTimeoutRef.current) {
      clearTimeout(snapbackTimeoutRef.current);
      snapbackTimeoutRef.current = null;
    }
  }, []);

  const clearInitialMoveTimeout = useCallback(() => {
    if (initialMoveTimeoutRef.current) {
      clearTimeout(initialMoveTimeoutRef.current);
      initialMoveTimeoutRef.current = null;
    }
  }, []);

  const resetPuzzle = useCallback(() => {
    const freshGame = buildInitialGame(puzzle);
    gameRef.current = freshGame;
    setGame(freshGame);
    currentMoveIndexRef.current = 0;
    setPuzzleState('playing');
    setHintSquare(null);
    setMoveToPromote(null);
    setSelectedSquare(null);
    clearOpponentMoveTimeout();
    clearWrongFlashTimeout();
    clearHintTimeout();
    clearSnapbackTimeout();
    clearInitialMoveTimeout();
    startTimeRef.current = Date.now();
    setHighlightSquares(buildInitialHighlight(puzzle));
  }, [clearHintTimeout, clearInitialMoveTimeout, clearOpponentMoveTimeout, clearSnapbackTimeout, clearWrongFlashTimeout, puzzle]);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    clearOpponentMoveTimeout();
    clearWrongFlashTimeout();
    clearHintTimeout();
    clearSnapbackTimeout();
    clearInitialMoveTimeout();
    puzzleKeyRef.current = `${puzzle.id}:${puzzle.fen}:${puzzle.moves.join(' ')}`;
    setHintSquare(null);
    setMoveToPromote(null);
    setSelectedSquare(null);

    if (puzzle.initialFen && puzzle.previousMove) {
      // Start from position before opponent's move, then animate it
      const preMoveGame = new Chess(puzzle.initialFen);
      setBoardState(preMoveGame, 0);
      setHighlightSquares({});
      setPuzzleState('animating_initial');

      const scheduledPuzzleKey = puzzleKeyRef.current;

      // Delay before starting the animation (let board render)
      initialMoveTimeoutRef.current = setTimeout(() => {
        if (scheduledPuzzleKey !== puzzleKeyRef.current) return;

        const postMoveGame = new Chess(puzzle.initialFen!);
        try {
          applyUciMove(postMoveGame, puzzle.previousMove!);
        } catch {
          // Fallback: skip animation, go straight to playing
          const fallbackGame = buildInitialGame(puzzle);
          setBoardState(fallbackGame, 0);
          startTimeRef.current = Date.now();
          setPuzzleState('playing');
          setHighlightSquares(buildInitialHighlight(puzzle));
          return;
        }

        setBoardState(postMoveGame, 0);
        setHighlightSquares(
          buildHighlight(
            puzzle.previousMove!.slice(0, 2),
            puzzle.previousMove!.slice(2, 4),
            'rgba(255, 170, 0, 0.35)'
          )
        );

        // Wait for animation to complete (200ms) + 600ms delay, then unlock
        initialMoveTimeoutRef.current = setTimeout(() => {
          if (scheduledPuzzleKey !== puzzleKeyRef.current) return;
          startTimeRef.current = Date.now();
          setPuzzleState('playing');
        }, 800);
      }, 300);
    } else {
      // No initial move to animate (fallback puzzles or missing data)
      const nextGame = buildInitialGame(puzzle);
      setBoardState(nextGame, 0);
      setHighlightSquares(buildInitialHighlight(puzzle));
      setPuzzleState('playing');
      startTimeRef.current = Date.now();
    }

    return () => {
      clearOpponentMoveTimeout();
      clearWrongFlashTimeout();
      clearHintTimeout();
      clearSnapbackTimeout();
      clearInitialMoveTimeout();
    };
  }, [clearHintTimeout, clearInitialMoveTimeout, clearOpponentMoveTimeout, clearSnapbackTimeout, clearWrongFlashTimeout, puzzle, setBoardState]);
  /* eslint-enable react-hooks/set-state-in-effect */

  const scheduleOpponentMove = useCallback((baseGame: Chess, moveIndex: number) => {
    const opponentMove = puzzle.moves[moveIndex];
    if (!opponentMove) {
      setPuzzleState('solved');
      onPuzzleEnd?.();
      return;
    }

    const scheduledPuzzleKey = puzzleKeyRef.current;
    clearOpponentMoveTimeout();
    setPuzzleState('waiting_for_opponent');

    opponentMoveTimeoutRef.current = setTimeout(() => {
      if (scheduledPuzzleKey !== puzzleKeyRef.current) {
        return;
      }

      const nextGame = new Chess(baseGame.fen());

      try {
        applyUciMove(nextGame, opponentMove);
      } catch (error) {
        console.error('Failed to apply opponent move:', error);
        setPuzzleState('playing');
        return;
      }

      const nextPlayerMoveIndex = moveIndex + 1;
      setBoardState(nextGame, nextPlayerMoveIndex);
      setHighlightSquares(
        buildHighlight(
          opponentMove.slice(0, 2),
          opponentMove.slice(2, 4),
          'rgba(255, 170, 0, 0.35)'
        )
      );

      if (nextPlayerMoveIndex >= puzzle.moves.length) {
        setPuzzleState('solved');
        onPuzzleEnd?.();
        return;
      }

      setPuzzleState('playing');
    }, 600);
  }, [clearOpponentMoveTimeout, onPuzzleEnd, puzzle.moves, setBoardState]);

  const validateAndMakeMove = useCallback((
    sourceSquare: string,
    targetSquare: string,
    promotionChoice?: string
  ) => {
    clearSnapbackTimeout();
    setGame(gameRef.current);
    setHintSquare(null);
    clearHintTimeout();

    const expectedMove = puzzle.moves[currentMoveIndexRef.current];
    if (!expectedMove) {
      return false;
    }

    const expectedFrom = expectedMove.slice(0, 2);
    const expectedTo = expectedMove.slice(2, 4);
    const expectedPromotion = expectedMove.length > 4 ? expectedMove[4] : undefined;

    const isCorrectMove =
      sourceSquare === expectedFrom &&
      targetSquare === expectedTo &&
      (promotionChoice ? promotionChoice === expectedPromotion : !expectedPromotion);

    if (!isCorrectMove) {
      onPuzzleFailed();
      clearWrongFlashTimeout();

      // Temporarily apply wrong move so piece lands visually
      const originalFen = gameRef.current.fen();
      const wrongGame = new Chess(originalFen);
      try {
        wrongGame.move({
          from: sourceSquare,
          to: targetSquare,
          promotion: promotionChoice ?? undefined,
        });
      } catch {
        return false;
      }
      setGame(wrongGame);

      // Red highlight on destination square only
      setHighlightSquares({
        [targetSquare]: { backgroundColor: 'rgba(239, 68, 68, 0.5)' },
      });
      const scheduledPuzzleKey = puzzleKeyRef.current;

      // Snap back after 400ms
      snapbackTimeoutRef.current = window.setTimeout(() => {
        if (scheduledPuzzleKey === puzzleKeyRef.current) {
          setGame(gameRef.current);
        }
      }, 400);

      // Clear red highlight after 800ms
      wrongFlashTimeoutRef.current = window.setTimeout(() => {
        if (scheduledPuzzleKey === puzzleKeyRef.current) {
          setHighlightSquares(buildInitialHighlight(puzzle));
        }
      }, 800);

      return true;
    }

    const currentFen = gameRef.current.fen();
    const nextGame = new Chess(currentFen);
    try {
      nextGame.move({
        from: sourceSquare,
        to: targetSquare,
        promotion: promotionChoice ?? undefined,
      });
    } catch {
      return false;
    }

    const opponentReplyIndex = currentMoveIndexRef.current + 1;
    setBoardState(nextGame, opponentReplyIndex);
    clearWrongFlashTimeout();

    if (opponentReplyIndex >= puzzle.moves.length) {
      setPuzzleState('solved');
      const timeSeconds = Math.floor((Date.now() - startTimeRef.current) / 1000);
      onPuzzleSolved(timeSeconds);
      onPuzzleEnd?.();

      // Emerald green highlight on final destination square
      setHighlightSquares({
        [targetSquare]: { backgroundColor: 'rgba(16, 185, 129, 0.6)' },
      });

      // Clear green highlight after 1 second
      const scheduledPuzzleKey = puzzleKeyRef.current;
      setTimeout(() => {
        if (scheduledPuzzleKey === puzzleKeyRef.current) {
          setHighlightSquares({});
        }
      }, 1000);

      return true;
    }

    setHighlightSquares(
      buildHighlight(sourceSquare, targetSquare, 'rgba(0, 200, 100, 0.4)')
    );

    setPuzzleState('playing');
    scheduleOpponentMove(nextGame, opponentReplyIndex);
    return true;
  }, [clearHintTimeout, clearSnapbackTimeout, clearWrongFlashTimeout, onPuzzleEnd, onPuzzleFailed, onPuzzleSolved, puzzle, scheduleOpponentMove, setBoardState]);

  const onDrop = useCallback((sourceSquare: string, targetSquare: string, pieceType: string) => {
    setSelectedSquare(null);

    if (puzzleState === 'animating_initial' || puzzleState === 'waiting_for_opponent' || puzzleState === 'solved' || puzzleState === 'showing_solution') {
      return false;
    }

    const legalMove = gameRef.current
      .moves({ square: sourceSquare as Square, verbose: true })
      .some((move) => move.to === targetSquare);
    if (!legalMove) {
      return false;
    }

    if (
      pieceType.toLowerCase() === 'p' &&
      (targetSquare[1] === '8' || targetSquare[1] === '1')
    ) {
      setMoveToPromote({ source: sourceSquare, target: targetSquare });
      return false;
    }

    return validateAndMakeMove(sourceSquare, targetSquare);
  }, [puzzleState, validateAndMakeMove]);

  const onSquareClick = useCallback(({ square }: { piece: { pieceType: string } | null; square: string }) => {
    if (puzzleState === 'animating_initial' || puzzleState === 'waiting_for_opponent' || puzzleState === 'solved' || puzzleState === 'showing_solution') {
      setSelectedSquare(null);
      return;
    }

    const clickedPiece = gameRef.current.get(square as Square);
    const isOwnPiece = clickedPiece?.color === gameRef.current.turn();

    if (!selectedSquare) {
      setSelectedSquare(isOwnPiece ? square : null);
      return;
    }

    if (selectedSquare === square) {
      setSelectedSquare(null);
      return;
    }

    const sourcePiece = gameRef.current.get(selectedSquare as Square);
    const legalMove = gameRef.current
      .moves({ square: selectedSquare as Square, verbose: true })
      .some((move) => move.to === square);

    if (legalMove && sourcePiece) {
      onDrop(selectedSquare, square, sourcePiece.type);
      return;
    }

    setSelectedSquare(isOwnPiece ? square : null);
  }, [onDrop, puzzleState, selectedSquare]);

  const displayedSquareStyles = useMemo(() => {
    let styles = highlightSquares;

    if (selectedSquare) {
      styles = {
        ...styles,
        [selectedSquare]: {
          ...styles[selectedSquare],
          backgroundColor: 'rgba(255, 170, 0, 0.35)',
        },
      };
    }

    if (hintSquare) {
      styles = {
        ...styles,
        [hintSquare]: {
          ...styles[hintSquare],
          backgroundColor: 'rgba(16, 185, 129, 0.4)',
        },
      };
    }

    return styles;
  }, [highlightSquares, hintSquare, selectedSquare]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare) {
      return {};
    }

    try {
      const legalMoves = game.moves({ square: selectedSquare as Square, verbose: true });
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

  const squareRenderer = useCallback<SquareRenderer>(({ square, children }) => {
    const hint = hintSquares[square];
    const squareStyle = displayedSquareStyles[square];
    return (
      <div className="relative w-full h-full" style={squareStyle}>
        {hint === 'dot' && (
          <div
            className="pointer-events-none absolute left-1/2 top-1/2 h-[30%] w-[30%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/25"
          />
        )}
        {hint === 'ring' && (
          <div
            className="pointer-events-none absolute left-1/2 top-1/2 h-[88%] w-[88%] -translate-x-1/2 -translate-y-1/2 rounded-full border-[6px] border-black/25"
          />
        )}
        {children}
      </div>
    );
  }, [hintSquares, displayedSquareStyles]);

  const onPromotionPieceSelect = useCallback((piece?: string) => {
    if (piece && moveToPromote) {
      validateAndMakeMove(moveToPromote.source, moveToPromote.target, piece);
    }

    setMoveToPromote(null);
    return true;
  }, [moveToPromote, validateAndMakeMove]);

  const handleShowSolution = useCallback(() => {
    const moveToShow = puzzle.moves[currentMoveIndexRef.current];
    if (!moveToShow) {
      return;
    }

    const currentFen = gameRef.current.fen();
    const nextGame = new Chess(currentFen);

    try {
      applyUciMove(nextGame, moveToShow);
    } catch {
      return;
    }

    const nextIndex = currentMoveIndexRef.current + 1;
    setBoardState(nextGame, nextIndex);
    clearWrongFlashTimeout();
    setHintSquare(null);
    clearHintTimeout();
    setHighlightSquares(
      buildHighlight(
        moveToShow.slice(0, 2),
        moveToShow.slice(2, 4),
        'rgba(100, 150, 255, 0.6)'
      )
    );

    if (nextIndex >= puzzle.moves.length) {
      setPuzzleState('solved');
      onPuzzleEnd?.();
      return;
    }

    setPuzzleState('showing_solution');
    scheduleOpponentMove(nextGame, nextIndex);
  }, [clearHintTimeout, clearWrongFlashTimeout, onPuzzleEnd, puzzle.moves, scheduleOpponentMove, setBoardState]);

  const showHint = useCallback(() => {
    if (puzzleState !== 'playing') {
      return;
    }

    const moveToShow = puzzle.moves[currentMoveIndexRef.current];
    if (!moveToShow) {
      return;
    }

    const from = moveToShow.slice(0, 2);
    setHintSquare(from);
    clearHintTimeout();
    hintTimeoutRef.current = window.setTimeout(() => {
      setHintSquare(null);
      hintTimeoutRef.current = null;
    }, 4000);
  }, [clearHintTimeout, puzzle.moves, puzzleState]);

  useEffect(() => {
    if (apiRef) {
      apiRef.current = { showSolution: handleShowSolution, showHint, resetPuzzle };
    }
    return () => {
      if (apiRef) {
        apiRef.current = null;
      }
    };
  }, [apiRef, handleShowSolution, resetPuzzle, showHint]);

  return (
    <div
      style={{
        padding: '14px',
        background: 'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        borderRadius: '6px',
        boxShadow: '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
      }}
    >
      <div className="relative">
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
        <Chessboard
          options={{
            id: 'puzzle-board',
            position: game.fen(),
            boardOrientation,
            squareStyles: displayedSquareStyles,
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
            darkSquareNotationStyle: {
              color: '#f0e0c0',
            },
            lightSquareNotationStyle: {
              color: '#3a2410',
            },
            boardStyle: {
              width: '100%',
              borderRadius: '8px',
              boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            },
              animationDurationInMs: 200,
              allowDragging: true,
              canDragPiece: ({ piece, square }) => {
                if (
                  !square ||
                  puzzleState === 'animating_initial' ||
                  puzzleState === 'waiting_for_opponent' ||
                  puzzleState === 'solved' ||
                  puzzleState === 'showing_solution'
                ) {
                  return false;
                }

                return piece.pieceType[0] === gameRef.current.turn();
              },
              onSquareClick,
              squareRenderer,
              onPieceDrop: ({ piece, sourceSquare, targetSquare }) => {
                if (!targetSquare) {
                  return false;
                }

                return onDrop(sourceSquare, targetSquare, piece.pieceType);
              },
            }}
        />

          {moveToPromote && (
            <PromotionPicker
              turn={game.turn()}
              onSelect={onPromotionPieceSelect}
              onCancel={() => setMoveToPromote(null)}
            />
          )}
      </div>
    </div>
  );
}
