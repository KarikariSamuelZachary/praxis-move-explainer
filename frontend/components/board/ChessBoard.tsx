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

type PuzzleState = 'playing' | 'correct_move' | 'wrong_move' | 'solved' | 'showing_solution';

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
  const [wrongMoveSquare, setWrongMoveSquare] = useState<string | null>(null);
  const [highlightSquares, setHighlightSquares] = useState<Record<string, object>>({});
  const startTimeRef = useRef<number>(Date.now());

  const boardOrientation = getColorToPlayFromFen(puzzle.fen);

  // Reset when puzzle changes
  useEffect(() => {
    const newGame = new Chess(puzzle.fen);
    setGame(newGame);
    setPuzzleState('playing');
    setCurrentMoveIndex(0);
    setExplanation(null);
    setWrongMoveSquare(null);
    setHighlightSquares({});
    startTimeRef.current = Date.now();
  }, [puzzle]);

  // Fetch explanation from API
  const fetchExplanation = useCallback(async (
    fen: string,
    move: string,
    isCorrect: boolean
  ) => {
    setIsLoadingExplanation(true);
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
      if (!response.ok) {
        throw new Error(`Explanation API returned ${response.status}`);
      }
      const data: ExplanationResponse = await response.json();
      setExplanation(data);
    } catch (error) {
      console.error('Failed to fetch explanation:', error);
    } finally {
      setIsLoadingExplanation(false);
    }
  }, [playerElo, puzzle.themes]);

  // Make opponent's response move automatically
  const makeOpponentMove = useCallback((
    currentGame: Chess,
    moveIndex: number
  ) => {
    if (moveIndex >= puzzle.moves.length) return;

    setTimeout(() => {
      const opponentMove = puzzle.moves[moveIndex];
      const newGame = new Chess(currentGame.fen());

      try {
        // Convert UCI format (e2e4) to move object
        const from = opponentMove.slice(0, 2);
        const to = opponentMove.slice(2, 4);
        const promotion = opponentMove.length > 4 ? opponentMove[4] : undefined;

        newGame.move({ from, to, promotion });
        setGame(newGame);
        setCurrentMoveIndex(moveIndex + 1);

        // Highlight opponent's move
        setHighlightSquares({
          [from]: { backgroundColor: 'rgba(255, 170, 0, 0.4)' },
          [to]: { backgroundColor: 'rgba(255, 170, 0, 0.4)' },
        });
      } catch {
        console.error('Invalid opponent move:', opponentMove);
      }
    }, 500);
  }, [puzzle.moves]);

  // Handle player's move attempt
  function onDrop(sourceSquare: string, targetSquare: string, pieceType: string) {
    if (puzzleState !== 'playing' && puzzleState !== 'correct_move') return false;

    const expectedMove = puzzle.moves[currentMoveIndex];
    const expectedFrom = expectedMove.slice(0, 2);
    const expectedTo = expectedMove.slice(2, 4);
    const promotion = expectedMove.length > 4 ? expectedMove[4] : undefined;

    const isCorrectMove =
      sourceSquare === expectedFrom && targetSquare === expectedTo;

    if (!isCorrectMove) {
      // Wrong move
      setPuzzleState('wrong_move');
      setWrongMoveSquare(targetSquare);
      onPuzzleFailed();

      // Shake effect - reset after delay
      setTimeout(() => {
        setPuzzleState('playing');
        setWrongMoveSquare(null);
      }, 1000);

      // Get explanation for the wrong attempt
      fetchExplanation(game.fen(), `${sourceSquare}${targetSquare}`, false);
      return false;
    }

    // Correct move - apply it
    const newGame = new Chess(game.fen());
    try {
      newGame.move({
        from: sourceSquare,
        to: targetSquare,
        promotion: promotion || (
          pieceType.toLowerCase() === 'p' && (targetSquare[1] === '8' || targetSquare[1] === '1')
            ? 'q'
            : undefined
        ),
      });
    } catch {
      return false;
    }

    setGame(newGame);

    // Highlight correct move
    setHighlightSquares({
      [sourceSquare]: { backgroundColor: 'rgba(0, 200, 100, 0.4)' },
      [targetSquare]: { backgroundColor: 'rgba(0, 200, 100, 0.4)' },
    });

    const nextMoveIndex = currentMoveIndex + 1;

    // Check if puzzle is complete
    if (nextMoveIndex >= puzzle.moves.length) {
      setPuzzleState('solved');
      const timeSeconds = Math.floor((Date.now() - startTimeRef.current) / 1000);
      onPuzzleSolved(timeSeconds);
      fetchExplanation(newGame.fen(), expectedMove, true);
    } else {
      setPuzzleState('correct_move');
      // Make opponent's response
      makeOpponentMove(newGame, nextMoveIndex);
    }

    return true;
  }

  // Show solution move by move
  function handleShowSolution() {
    setPuzzleState('showing_solution');
    const moveToShow = puzzle.moves[currentMoveIndex];
    if (!moveToShow) return;

    const from = moveToShow.slice(0, 2);
    const to = moveToShow.slice(2, 4);

    setHighlightSquares({
      [from]: { backgroundColor: 'rgba(100, 150, 255, 0.6)' },
      [to]: { backgroundColor: 'rgba(100, 150, 255, 0.6)' },
    });

    fetchExplanation(game.fen(), moveToShow, false);
  }

  // Custom square styles
  function getCustomSquareStyles() {
    const styles: Record<string, object> = { ...highlightSquares };

    if (wrongMoveSquare) {
      styles[wrongMoveSquare] = { backgroundColor: 'rgba(255, 50, 50, 0.5)' };
    }

    return styles;
  }

  return (
    <div className="flex flex-col lg:flex-row gap-6 w-full max-w-5xl mx-auto">
      {/* Chess Board */}
      <div className="flex-shrink-0">
        <div className="relative">
          <Chessboard
            options={{
              id: 'praxis-board',
              position: game.fen(),
              onPieceDrop: ({ piece, sourceSquare, targetSquare }) => {
                if (!targetSquare) {
                  return false;
                }

                return onDrop(sourceSquare, targetSquare, piece.pieceType);
              },
              boardOrientation,
              squareStyles: getCustomSquareStyles(),
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

          {/* Puzzle solved overlay */}
          {puzzleState === 'solved' && (
            <div className="absolute inset-0 flex items-center justify-center rounded-lg bg-black/40 backdrop-blur-sm">
              <div className="text-center">
                <div className="text-5xl mb-2">✓</div>
                <div className="text-white text-2xl font-bold">Puzzle Solved!</div>
              </div>
            </div>
          )}
        </div>

        {/* Board controls */}
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

      {/* Explanation Panel */}
      <div className="flex-1 flex flex-col gap-4">
        {/* Puzzle Info */}
        <div className="bg-zinc-800 rounded-xl p-4 border border-zinc-700">
          <div className="flex items-center justify-between mb-2">
            <span className="text-zinc-400 text-sm font-medium">PUZZLE INFO</span>
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

        {/* Status indicator */}
        <div className={`rounded-xl p-4 border transition-all ${
          puzzleState === 'solved'
            ? 'bg-emerald-900/40 border-emerald-600'
            : puzzleState === 'wrong_move'
            ? 'bg-red-900/40 border-red-600'
            : 'bg-zinc-800 border-zinc-700'
        }`}>
          <p className="text-sm font-medium text-zinc-300">
            {puzzleState === 'playing' && `Find the best move for ${boardOrientation}`}
            {puzzleState === 'correct_move' && '✓ Correct! Keep going...'}
            {puzzleState === 'wrong_move' && '✗ Not the best move. Try again.'}
            {puzzleState === 'solved' && '🎉 Excellent! Puzzle complete.'}
            {puzzleState === 'showing_solution' && 'Here is the solution:'}
          </p>
        </div>

        {/* LLM Explanation */}
        {(isLoadingExplanation || explanation) && (
          <div className="bg-zinc-800 rounded-xl p-4 border border-zinc-700 flex-1">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-zinc-400 text-sm font-medium">COACH EXPLANATION</span>
              {explanation?.concept && (
                <span className="px-2 py-0.5 bg-indigo-900/60 border border-indigo-700 rounded-md text-xs text-indigo-300">
                  {explanation.concept}
                </span>
              )}
            </div>

            {isLoadingExplanation ? (
              <div className="space-y-2 animate-pulse">
                <div className="h-3 bg-zinc-700 rounded w-full"></div>
                <div className="h-3 bg-zinc-700 rounded w-5/6"></div>
                <div className="h-3 bg-zinc-700 rounded w-4/6"></div>
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

        {/* Explain button - shows before explanation loads */}
        {puzzleState === 'playing' && !explanation && !isLoadingExplanation && (
          <div className="bg-zinc-800/50 rounded-xl p-4 border border-zinc-700 border-dashed flex items-center justify-center flex-1">
            <p className="text-zinc-500 text-sm text-center">
              Make a move to get an AI explanation
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
