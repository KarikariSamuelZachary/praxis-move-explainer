'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Chess, Move } from 'chess.js';
import { Chessboard } from 'react-chessboard';

import { ExplanationResponse, Puzzle } from '@/types';

interface ChessBoardProps {
  puzzle: Puzzle;
  playerElo: number;
  onPuzzleSolved: (timeSeconds: number) => void;
  onPuzzleFailed: () => void;
  onNextPuzzle: () => void;
}

type PuzzleState =
  | 'playing'
  | 'waiting_for_opponent'
  | 'wrong_move'
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
  const [lastWrongMove, setLastWrongMove] = useState<string | null>(null);
  const [lastWrongFen, setLastWrongFen] = useState<string | null>(null);
  const [moveToPromote, setMoveToPromote] = useState<PendingPromotionMove | null>(null);

  const startTimeRef = useRef<number>(Date.now());
  const gameRef = useRef<Chess>(initialGame);
  const currentMoveIndexRef = useRef<number>(0);
  const puzzleKeyRef = useRef<string>('');
  const opponentMoveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wrongFlashTimeoutRef = useRef<number | null>(null);

  const boardOrientation = game.turn() === 'w' ? 'white' : 'black';

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

  useEffect(() => {
    const nextGame = buildInitialGame(puzzle);

    clearOpponentMoveTimeout();
    clearWrongFlashTimeout();
    puzzleKeyRef.current = `${puzzle.id}:${puzzle.fen}:${puzzle.moves.join(' ')}`;
    startTimeRef.current = Date.now();
    setBoardState(nextGame, 0);
    setHighlightSquares(buildInitialHighlight(puzzle));
    setPuzzleState('playing');
    setExplanation(null);
    setIsLoadingExplanation(false);
    setLastWrongMove(null);
    setLastWrongFen(null);
    setMoveToPromote(null);

    return () => {
      clearOpponentMoveTimeout();
      clearWrongFlashTimeout();
    };
  }, [clearOpponentMoveTimeout, clearWrongFlashTimeout, puzzle, setBoardState]);

  const fetchExplanation = useCallback(async (
    fen: string,
    move: string,
    isCorrect: boolean
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
      const wrongMove = `${sourceSquare}${targetSquare}${promotionChoice ?? ''}`;
      setLastWrongMove(wrongMove);
      setLastWrongFen(gameRef.current.fen());
      setPuzzleState('wrong_move');
      onPuzzleFailed();
      clearWrongFlashTimeout();
      setHighlightSquares(
        buildHighlight(sourceSquare, targetSquare, 'rgba(255, 50, 50, 0.4)')
      );

      wrongFlashTimeoutRef.current = window.setTimeout(() => {
        if (puzzleKeyRef.current) {
          setHighlightSquares(buildInitialHighlight(puzzle));
        }
      }, 900);

      return false;
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
    setLastWrongMove(null);
    setLastWrongFen(null);
    clearWrongFlashTimeout();
    setHighlightSquares(
      buildHighlight(sourceSquare, targetSquare, 'rgba(0, 200, 100, 0.4)')
    );

    if (opponentReplyIndex >= puzzle.moves.length) {
      setPuzzleState('solved');
      const timeSeconds = Math.floor((Date.now() - startTimeRef.current) / 1000);
      onPuzzleSolved(timeSeconds);
      fetchExplanation(currentFen, playedMove.san, true);
      return true;
    }

    setPuzzleState('playing');
    scheduleOpponentMove(nextGame, opponentReplyIndex);
    return true;
  }, [clearWrongFlashTimeout, fetchExplanation, onPuzzleFailed, onPuzzleSolved, puzzle, scheduleOpponentMove, setBoardState]);

  const onDrop = useCallback((sourceSquare: string, targetSquare: string, pieceType: string) => {
    if (puzzleState === 'waiting_for_opponent' || puzzleState === 'solved' || puzzleState === 'showing_solution') {
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

    setPuzzleState('showing_solution');
    setHighlightSquares(
      buildHighlight(
        moveToShow.slice(0, 2),
        moveToShow.slice(2, 4),
        'rgba(100, 150, 255, 0.6)'
      )
    );
    fetchExplanation(gameRef.current.fen(), trySanFromUci(gameRef.current.fen(), moveToShow), true);
  }, [fetchExplanation, puzzle.moves]);

  const handleExplainWrongMove = useCallback(() => {
    if (!lastWrongMove || !lastWrongFen) {
      return;
    }

    fetchExplanation(lastWrongFen, trySanFromUci(lastWrongFen, lastWrongMove), false);
  }, [fetchExplanation, lastWrongFen, lastWrongMove]);

  return (
    <div className="flex flex-col lg:flex-row gap-6 w-full max-w-5xl mx-auto">
      <div className="flex-shrink-0">
        <div className="relative">
          <Chessboard
            options={{
              position: game.fen(),
              boardOrientation,
              squareStyles: highlightSquares,
              boardStyle: {
                width: 'min(480px, 100vw - 2rem)',
                borderRadius: '8px',
                boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
              },
              darkSquareStyle: { backgroundColor: '#4a7c59' },
              lightSquareStyle: { backgroundColor: '#f0d9b5' },
              animationDurationInMs: 200,
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

          {puzzleState === 'solved' && (
            <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/40 backdrop-blur-sm">
              <div className="text-center">
                <div className="text-5xl mb-2">✓</div>
                <div className="text-white text-2xl font-bold">Puzzle Solved!</div>
              </div>
            </div>
          )}
        </div>

        <div className="flex gap-3 mt-4">
          {puzzleState === 'playing' && (
            <button
              onClick={handleShowSolution}
              className="flex-1 py-2 px-4 rounded-lg bg-zinc-700 hover:bg-zinc-600 text-white text-sm font-medium transition-colors"
            >
              Show Solution
            </button>
          )}
          {(puzzleState === 'solved' || puzzleState === 'showing_solution') && (
            <button
              onClick={onNextPuzzle}
              className="flex-1 py-2 px-4 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-sm font-medium transition-colors"
            >
              Next Puzzle →
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
          puzzleState === 'solved'
            ? 'bg-emerald-900/40 border-emerald-600'
            : puzzleState === 'wrong_move'
            ? 'bg-red-900/40 border-red-600'
            : puzzleState === 'showing_solution'
            ? 'bg-indigo-900/40 border-indigo-600'
            : puzzleState === 'waiting_for_opponent'
            ? 'bg-amber-900/30 border-amber-600'
            : 'bg-zinc-800 border-zinc-700'
        }`}>
          <p className="text-sm font-medium text-zinc-200">
            {puzzleState === 'playing' && `Find the best move for ${boardOrientation}`}
            {puzzleState === 'waiting_for_opponent' && 'Opponent is responding...'}
            {puzzleState === 'wrong_move' && '✗ Not the best move — try again'}
            {puzzleState === 'solved' && '🎉 Puzzle complete!'}
            {puzzleState === 'showing_solution' && '💡 Here is the solution:'}
          </p>

          {puzzleState === 'wrong_move' && lastWrongMove && (
            <button
              onClick={handleExplainWrongMove}
              disabled={isLoadingExplanation}
              className="mt-3 w-full py-2 px-4 rounded-lg bg-zinc-700 hover:bg-zinc-600 disabled:opacity-50 text-white text-sm font-medium transition-colors"
            >
              {isLoadingExplanation ? 'Thinking...' : 'Why was that wrong?'}
            </button>
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
