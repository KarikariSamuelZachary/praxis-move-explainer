'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { Chessboard } from 'react-chessboard';
import { Chess } from 'chess.js';
import { Puzzle, ExplanationResponse } from '@/types';
import { getColorToPlayFromFen } from '@/lib/lichess';

interface ChessBoardProps {
  puzzle: Puzzle;
  playerElo: number;
  onPuzzleSolved: (timeSeconds: number) => void;
  onPuzzleFailed: () => void;
  onNextPuzzle: () => void;
}

type PuzzleState = 'playing' | 'wrong_move' | 'solved' | 'showing_solution';

export default function ChessBoardComponent({
  puzzle,
  playerElo,
  onPuzzleSolved,
  onPuzzleFailed,
  onNextPuzzle,
}: ChessBoardProps) {
  const [game, setGame] = useState<Chess>(new Chess(puzzle.fen));
  const [puzzleState, setPuzzleState] = useState<PuzzleState>('playing');
  const [currentMoveIndex, setCurrentMoveIndex] = useState(0);
  const [explanation, setExplanation] = useState<ExplanationResponse | null>(null);
  const [isLoadingExplanation, setIsLoadingExplanation] = useState(false);
  const [highlightSquares, setHighlightSquares] = useState<Record<string, object>>({});
  const [lastWrongMove, setLastWrongMove] = useState<string | null>(null);
  const [lastWrongFen, setLastWrongFen] = useState<string | null>(null);
  const startTimeRef = useRef<number>(Date.now());

  const boardOrientation = getColorToPlayFromFen(puzzle.fen);

  useEffect(() => {
    const newGame = new Chess(puzzle.fen);
    setGame(newGame);
    setPuzzleState('playing');
    setCurrentMoveIndex(0);
    setExplanation(null);
    setLastWrongMove(null);
    setLastWrongFen(null);
    setHighlightSquares({});
    startTimeRef.current = Date.now();
  }, [puzzle]);

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
      if (!response.ok) throw new Error(`API error ${response.status}`);
      const data: ExplanationResponse = await response.json();
      setExplanation(data);
    } catch (error) {
      console.error('Failed to fetch explanation:', error);
      setExplanation({
        explanation: isCorrect
          ? 'This is the best move in the position.'
          : 'This move is not the most accurate. Look for a more forcing continuation.',
        concept: 'Tactics',
        tip: 'Look for checks, captures, and threats first.',
      });
    } finally {
      setIsLoadingExplanation(false);
    }
  }, [playerElo, puzzle.themes]);

  const makeOpponentMove = useCallback((
    currentGame: Chess,
    moveIndex: number
  ) => {
    if (moveIndex >= puzzle.moves.length) return;

    setTimeout(() => {
      const opponentMove = puzzle.moves[moveIndex];
      const newGame = new Chess(currentGame.fen());
      try {
        const from = opponentMove.slice(0, 2);
        const to = opponentMove.slice(2, 4);
        const promotion = opponentMove.length > 4 ? opponentMove[4] : undefined;
        newGame.move({ from, to, promotion });
        setGame(newGame);
        setCurrentMoveIndex(moveIndex + 1);
        setHighlightSquares({
          [from]: { backgroundColor: 'rgba(255, 170, 0, 0.3)' },
          [to]: { backgroundColor: 'rgba(255, 170, 0, 0.3)' },
        });
      } catch {
        console.error('Invalid opponent move:', opponentMove);
      }
    }, 600);
  }, [puzzle.moves]);

  function onDrop(sourceSquare: string, targetSquare: string, pieceType: string) {
    if (puzzleState !== 'playing') return false;

    const expectedMove = puzzle.moves[currentMoveIndex];
    if (!expectedMove) return false;

    const expectedFrom = expectedMove.slice(0, 2);
    const expectedTo = expectedMove.slice(2, 4);
    const promotion = expectedMove.length > 4 ? expectedMove[4] : undefined;
    const isCorrectMove = sourceSquare === expectedFrom && targetSquare === expectedTo;

    if (!isCorrectMove) {
      // Wrong move — save it for manual explain, flash red, let them try again
      const wrongMove = `${sourceSquare}${targetSquare}`;
      setLastWrongMove(wrongMove);
      setLastWrongFen(game.fen());
      setPuzzleState('wrong_move');
      onPuzzleFailed();

      setHighlightSquares({
        [sourceSquare]: { backgroundColor: 'rgba(255, 50, 50, 0.4)' },
        [targetSquare]: { backgroundColor: 'rgba(255, 50, 50, 0.4)' },
      });

      setTimeout(() => {
        setPuzzleState('playing');
        setHighlightSquares({});
      }, 900);

      return false;
    }

    // Correct move
    const newGame = new Chess(game.fen());
    try {
      newGame.move({
        from: sourceSquare,
        to: targetSquare,
        promotion: promotion || (
          pieceType.toLowerCase() === 'p' &&
          (targetSquare[1] === '8' || targetSquare[1] === '1') ? 'q' : undefined
        ),
      });
    } catch {
      return false;
    }

    setGame(newGame);
    setLastWrongMove(null);
    setLastWrongFen(null);
    setHighlightSquares({
      [sourceSquare]: { backgroundColor: 'rgba(0, 200, 100, 0.4)' },
      [targetSquare]: { backgroundColor: 'rgba(0, 200, 100, 0.4)' },
    });

    const nextMoveIndex = currentMoveIndex + 1;

    if (nextMoveIndex >= puzzle.moves.length) {
      setPuzzleState('solved');
      const timeSeconds = Math.floor((Date.now() - startTimeRef.current) / 1000);
      onPuzzleSolved(timeSeconds);
      // Auto-explain on solve
      fetchExplanation(newGame.fen(), expectedMove, true);
    } else {
      makeOpponentMove(newGame, nextMoveIndex);
      setCurrentMoveIndex(nextMoveIndex + 1);
    }

    return true;
  }

  function handleShowSolution() {
    const moveToShow = puzzle.moves[currentMoveIndex];
    if (!moveToShow) return;
    setPuzzleState('showing_solution');
    const from = moveToShow.slice(0, 2);
    const to = moveToShow.slice(2, 4);
    setHighlightSquares({
      [from]: { backgroundColor: 'rgba(100, 150, 255, 0.6)' },
      [to]: { backgroundColor: 'rgba(100, 150, 255, 0.6)' },
    });
    fetchExplanation(game.fen(), moveToShow, true);
  }

  function handleExplainWrongMove() {
    if (!lastWrongMove || !lastWrongFen) return;
    fetchExplanation(lastWrongFen, lastWrongMove, false);
  }

  return (
    <div className="flex flex-col lg:flex-row gap-6 w-full max-w-5xl mx-auto">
      <div className="flex-shrink-0">
        <div className="relative">
          <Chessboard
            options={{
              position: game.fen(),
              onPieceDrop: ({ piece, sourceSquare, targetSquare }) => {
                if (!targetSquare) return false;
                return onDrop(sourceSquare, targetSquare, piece.pieceType);
              },
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
            }}
          />

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
              <span key={theme} className="px-2 py-1 bg-zinc-700 rounded-md text-xs text-zinc-300 capitalize">
                {theme.replace(/([A-Z])/g, ' $1').trim()}
              </span>
            ))}
          </div>
        </div>

        <div className={`rounded-xl p-4 border transition-all ${
          puzzleState === 'solved' ? 'bg-emerald-900/40 border-emerald-600'
          : puzzleState === 'wrong_move' ? 'bg-red-900/40 border-red-600'
          : puzzleState === 'showing_solution' ? 'bg-indigo-900/40 border-indigo-600'
          : 'bg-zinc-800 border-zinc-700'
        }`}>
          <p className="text-sm font-medium text-zinc-200">
            {puzzleState === 'playing' && `Find the best move for ${boardOrientation}`}
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
                <p className="text-zinc-200 text-sm leading-relaxed">{explanation?.explanation}</p>
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
              Solve the puzzle to get an explanation.<br />
              <span className="text-zinc-600 text-xs">Stuck? Click "Show Solution" for help.</span>
            </p>
          </div>
        )}
      </div>
    </div>
  );
}