'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess, Move, Square } from 'chess.js';
import { Chessboard, type SquareRenderer } from 'react-chessboard';

import { ExplanationResponse, Puzzle } from '@/types';

interface ChessBoardProps {
  puzzle: Puzzle;
  playerElo: number;
  onPuzzleSolved: (timeSeconds: number) => void;
  onPuzzleFailed: () => void;
  onNextPuzzle: () => void;
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

function trySanFromUci(fen: string, uciMove: string): string {
  try {
    const probe = new Chess(fen);
    return applyUciMove(probe, uciMove).san;
  } catch {
    return uciMove;
  }
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
  playerElo,
  onPuzzleSolved,
  onPuzzleFailed,
  onNextPuzzle,
}: ChessBoardProps) {
  const initialGame = buildInitialGame(puzzle);

  const [game, setGame] = useState<Chess>(initialGame);
  const [highlightSquares, setHighlightSquares] = useState<HighlightSquares>(() => buildInitialHighlight(puzzle));
  const [puzzleState, setPuzzleState] = useState<PuzzleState>('playing');
  const [explanation, setExplanation] = useState<ExplanationResponse | null>(null);
  const [isLoadingExplanation, setIsLoadingExplanation] = useState(false);
  const [wrongMoveMessage, setWrongMoveMessage] = useState<string | null>(null);
  const [hintSquare, setHintSquare] = useState<string | null>(null);
  const [moveToPromote, setMoveToPromote] = useState<PendingPromotionMove | null>(null);
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);

  const startTimeRef = useRef<number>(Date.now());
  const gameRef = useRef<Chess>(initialGame);
  const currentMoveIndexRef = useRef<number>(0);
  const puzzleKeyRef = useRef<string>('');
  const opponentMoveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrongFlashTimeoutRef = useRef<number | null>(null);
  const hintTimeoutRef = useRef<number | null>(null);
  const snapbackTimeoutRef = useRef<number | null>(null);
  const initialMoveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autoAdvanceTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [wasSolutionViewed, setWasSolutionViewed] = useState(false);

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

  const clearAutoAdvanceTimeout = useCallback(() => {
    if (autoAdvanceTimeoutRef.current) {
      clearTimeout(autoAdvanceTimeoutRef.current);
      autoAdvanceTimeoutRef.current = null;
    }
  }, []);

  const resetPuzzle = useCallback(() => {
    if (!puzzle) return;

    const freshGame = buildInitialGame(puzzle);
    gameRef.current = freshGame;
    setGame(freshGame);
    currentMoveIndexRef.current = 0;
    setPuzzleState('playing');
    setWrongMoveMessage(null);
    setHintSquare(null);
    setExplanation(null);
    setIsLoadingExplanation(false);
    setMoveToPromote(null);
    setSelectedSquare(null);
    clearOpponentMoveTimeout();
    clearWrongFlashTimeout();
    clearHintTimeout();
    clearSnapbackTimeout();
    clearInitialMoveTimeout();
    clearAutoAdvanceTimeout();
    setWasSolutionViewed(false);
    startTimeRef.current = Date.now();
    setHighlightSquares(buildInitialHighlight(puzzle));
  }, [clearAutoAdvanceTimeout, clearHintTimeout, clearInitialMoveTimeout, clearOpponentMoveTimeout, clearSnapbackTimeout, clearWrongFlashTimeout, puzzle]);

  useEffect(() => {
    clearOpponentMoveTimeout();
    clearWrongFlashTimeout();
    clearHintTimeout();
    clearSnapbackTimeout();
    clearInitialMoveTimeout();
    clearAutoAdvanceTimeout();
    puzzleKeyRef.current = `${puzzle.id}:${puzzle.fen}:${puzzle.moves.join(' ')}`;
    setWasSolutionViewed(false);
    setExplanation(null);
    setIsLoadingExplanation(false);
    setWrongMoveMessage(null);
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
      clearAutoAdvanceTimeout();
    };
  }, [clearAutoAdvanceTimeout, clearHintTimeout, clearInitialMoveTimeout, clearOpponentMoveTimeout, clearSnapbackTimeout, clearWrongFlashTimeout, puzzle, setBoardState]);

  const fetchExplanation = useCallback(async (
    fen: string,
    move: string,
    isCorrect: boolean,
    moveHistory: string[] = []
  ) => {
    setIsLoadingExplanation(true);
    setExplanation(null);

    try {
      const response = await fetch('/api/explain', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          fen,
          move,
          isCorrect,
          playerElo,
          puzzleThemes: puzzle.themes,
          moveHistory,
        }),
      });

      if (response.ok) {
        const data: ExplanationResponse = await response.json();
        setExplanation(data);
      } else {
        console.warn(`Explanation API returned ${response.status}`);
        setExplanation({
          explanation: isCorrect
            ? 'This is the strongest continuation in the position.'
            : 'This move misses the most forcing continuation in the puzzle.',
          concept: 'Tactics',
          tip: 'Look for checks, captures, and threats before quieter moves.',
        });
      }
    } catch (error) {
      console.error('Failed to fetch explanation:', error);
      setExplanation({
        explanation: isCorrect
          ? 'This is the strongest continuation in the position.'
          : 'This move misses the most forcing continuation in the puzzle.',
        concept: 'Tactics',
        tip: 'Look for checks, captures, and threats before quieter moves.',
      });
    } finally {
      setIsLoadingExplanation(false);
    }
  }, [playerElo, puzzle.themes]);

  const scheduleOpponentMove = useCallback((baseGame: Chess, moveIndex: number) => {
    const opponentMove = puzzle.moves[moveIndex];
    if (!opponentMove) {
      setPuzzleState('solved');
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
        return;
      }

      setPuzzleState('playing');
    }, 600);
  }, [clearOpponentMoveTimeout, puzzle.moves, setBoardState]);

  const validateAndMakeMove = useCallback((
    sourceSquare: string,
    targetSquare: string,
    promotionChoice?: string
  ) => {
    clearSnapbackTimeout();
    setGame(gameRef.current);
    setWrongMoveMessage(null);
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
      setWrongMoveMessage('Not the best move — try again');

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
    let playedMove: Move;
    try {
      playedMove = nextGame.move({
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
      fetchExplanation(
        nextGame.fen(),
        playedMove.san,
        true,
        gameRef.current.history()
      );

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

      // Auto-advance after 1.5 seconds
      autoAdvanceTimeoutRef.current = setTimeout(() => {
        if (scheduledPuzzleKey === puzzleKeyRef.current) {
          onNextPuzzle();
        }
      }, 1500);

      return true;
    }

    setHighlightSquares(
      buildHighlight(sourceSquare, targetSquare, 'rgba(0, 200, 100, 0.4)')
    );

    setPuzzleState('playing');
    scheduleOpponentMove(nextGame, opponentReplyIndex);
    return true;
  }, [clearHintTimeout, clearSnapbackTimeout, clearWrongFlashTimeout, fetchExplanation, onPuzzleFailed, onPuzzleSolved, onNextPuzzle, puzzle, scheduleOpponentMove, setBoardState]);

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
    setWasSolutionViewed(true);
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
    setWrongMoveMessage(null);
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
      return;
    }

    setPuzzleState('showing_solution');
    scheduleOpponentMove(nextGame, nextIndex);
  }, [clearHintTimeout, clearWrongFlashTimeout, puzzle.moves, scheduleOpponentMove, setBoardState]);

  const handleGetHint = useCallback(() => {
    const expectedMove = puzzle.moves[currentMoveIndexRef.current];
    if (!expectedMove) {
      return;
    }

    const fromSquare = expectedMove.slice(0, 2);
    clearHintTimeout();
    setHintSquare(fromSquare);

    hintTimeoutRef.current = window.setTimeout(() => {
      if (puzzleKeyRef.current) {
        setHintSquare(null);
        hintTimeoutRef.current = null;
      }
    }, 2000);
  }, [clearHintTimeout, puzzle.moves]);

  return (
    <div className="flex flex-col lg:flex-row gap-6 w-full max-w-5xl mx-auto">
      <div className="flex-shrink-0">
        <div className="relative">
          <Chessboard
            options={{
              position: game.fen(),
              boardOrientation,
              squareStyles: displayedSquareStyles,
              boardStyle: {
                width: 'min(480px, 100vw - 2rem)',
                borderRadius: '8px',
                boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
              },
              darkSquareStyle: { backgroundColor: '#4a7c59' },
              lightSquareStyle: { backgroundColor: '#f0d9b5' },
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
            <div className="absolute inset-0 flex items-center justify-center bg-black/60 z-10 rounded-lg backdrop-blur-sm">
              <div className="bg-zinc-800 p-4 rounded-xl shadow-2xl border border-zinc-700">
                <h3 className="text-white text-center mb-4 font-medium">Promote to</h3>
                <div className="flex gap-2">
                  {['q', 'r', 'b', 'n'].map((piece) => (
                    <button
                      key={piece}
                      onClick={() => onPromotionPieceSelect(piece)}
                      className="w-14 h-14 bg-zinc-700 hover:bg-emerald-600 rounded-lg text-4xl flex items-center justify-center transition-colors pb-2"
                    >
                      {game.turn() === 'w'
                        ? (piece === 'q' ? '♕' : piece === 'r' ? '♖' : piece === 'b' ? '♗' : '♘')
                        : (piece === 'q' ? '♛' : piece === 'r' ? '♜' : piece === 'b' ? '♝' : '♞')}
                    </button>
                  ))}
                </div>
                <button
                  onClick={() => setMoveToPromote(null)}
                  className="mt-4 w-full py-2 bg-zinc-700/50 hover:bg-zinc-700 rounded-lg text-zinc-300 text-sm transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>

        <div className="flex gap-3 mt-4">
          {puzzleState === 'playing' && !wrongMoveMessage && (
            <button
              onClick={handleShowSolution}
              className="flex-1 py-2 px-4 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-white text-sm font-medium transition-colors"
            >
              Show Solution
            </button>
          )}
          {puzzleState === 'solved' && wasSolutionViewed && (
            <button
              onClick={resetPuzzle}
              className="flex-1 py-2 px-4 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-white text-sm font-medium transition-colors"
            >
              ↺ Play Again
            </button>
          )}
        </div>
      </div>

      <div className="flex-1 flex flex-col gap-4">
        <div className="bg-zinc-800 rounded-xl p-4 border border-zinc-700">
          <div className="flex items-center justify-between mb-2">
            <span className="text-zinc-400 text-sm font-medium">PUZZLE</span>
            <span className="text-emerald-400 font-bold">♟ {puzzle.rating}</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {puzzle.themes.slice(0, 4).map((theme) => (
              <span
                key={theme}
                className="px-2 py-1 bg-zinc-700 rounded-md text-xs text-zinc-300 capitalize"
              >
                {theme.replace(/([A-Z])/g, ' $1').trim()}
              </span>
            ))}
          </div>
        </div>

        <div className={`rounded-xl p-4 border transition-all ${
          puzzleState === 'solved' && !wasSolutionViewed
            ? 'bg-emerald-900/40 border-emerald-600'
            : puzzleState === 'solved' && wasSolutionViewed
            ? 'bg-indigo-900/40 border-indigo-600'
            : wrongMoveMessage
            ? 'bg-red-900/40 border-red-600'
            : puzzleState === 'showing_solution'
            ? 'bg-indigo-900/40 border-indigo-600'
            : puzzleState === 'animating_initial' || puzzleState === 'waiting_for_opponent'
            ? 'bg-amber-900/30 border-amber-600'
            : 'bg-zinc-800 border-zinc-700'
        }`}>
          <p className="text-sm font-medium text-zinc-200">
            {puzzleState === 'animating_initial' && 'Opponent is playing...'}
            {puzzleState === 'playing' && !wrongMoveMessage && `Find the best move for ${boardOrientation}`}
            {puzzleState === 'waiting_for_opponent' && 'Opponent is responding...'}
            {wrongMoveMessage && `✗ ${wrongMoveMessage}`}
            {puzzleState === 'solved' && !wasSolutionViewed && 'Puzzle complete!'}
            {puzzleState === 'solved' && wasSolutionViewed && 'Solution complete'}
            {puzzleState === 'showing_solution' && 'Here is the solution:'}
          </p>

          {wrongMoveMessage && (
            <div className="mt-3 flex gap-2">
              <button
                onClick={handleGetHint}
                className="flex-1 py-2 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium transition-colors"
              >
                Get a Hint
              </button>
              <button
                onClick={handleShowSolution}
                className="flex-1 py-2 px-4 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-white text-sm font-medium transition-colors"
              >
                View Solution
              </button>
            </div>
          )}
        </div>

        {(isLoadingExplanation || explanation) && (
          <div className="bg-zinc-800 rounded-xl p-4 border border-zinc-700 flex-1">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-zinc-400 text-sm font-medium">
                {puzzleState === 'solved' ? '✓ WHY THIS WORKS' : '💡 COACH'}
              </span>
              {explanation?.concept && (
                <span className="px-2 py-0.5 bg-indigo-900/60 border border-indigo-700 rounded-md text-xs text-indigo-300">
                  {explanation.concept}
                </span>
              )}
            </div>

            {isLoadingExplanation ? (
              <div className="space-y-2 animate-pulse">
                <div className="h-3 bg-zinc-700 rounded w-full" />
                <div className="h-3 bg-zinc-700 rounded w-5/6" />
                <div className="h-3 bg-zinc-700 rounded w-4/6" />
              </div>
            ) : (
              <div className="space-y-3">
                <p className="text-zinc-200 text-sm leading-relaxed">
                  {explanation?.explanation}
                </p>
                {explanation?.tip && (
                  <div className="border-t border-zinc-700 pt-3">
                    <p className="text-xs text-zinc-400 font-medium mb-1">💡 PATTERN TIP</p>
                    <p className="text-zinc-300 text-sm">{explanation.tip}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {puzzleState === 'playing' && !explanation && !isLoadingExplanation && (
          <div className="bg-zinc-800/50 rounded-xl p-6 border border-zinc-700 border-dashed flex items-center justify-center flex-1">
            <p className="text-zinc-500 text-sm text-center leading-relaxed">
              Solve the puzzle to get an explanation.
              <br />
              <span className="text-zinc-600 text-xs">
                Stuck? Click &quot;Show Solution&quot; for help.
              </span>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
