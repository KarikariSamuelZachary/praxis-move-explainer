'use client';

import { useEffect, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';

const PUZZLES = [
  // Back-rank mate
  { fen: '6k1/5ppp/8/8/8/8/8/R6K w - - 0 1', move: { from: 'a1', to: 'a8' } },
  // Scholar's mate
  {
    fen: 'r1bqkb1r/pppp1ppp/2n2n2/4p2Q/2B1P3/8/PPPP1PPP/RNB1K1NR w KQkq - 4 4',
    move: { from: 'h5', to: 'f7' },
  },
  // Smothered mate
  { fen: '6rk/6pp/8/4N3/8/8/8/7K w - - 0 1', move: { from: 'e5', to: 'f7' } },
];

export default function TacticBoard() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const puzzleRef = useRef(0);
  const [puzzle, setPuzzle] = useState(PUZZLES[0]);
  const [fen, setFen] = useState(PUZZLES[0].fen);
  const [showArrow, setShowArrow] = useState(false);
  const [solved, setSolved] = useState(false);
  // Bumped each cycle so react-chessboard remounts and renders the new
  // position instantly instead of sliding pieces from the previous one.
  const [cycleKey, setCycleKey] = useState(0);

  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduced) {
      setShowArrow(true);
      return;
    }

    const clearTimers = () => {
      timersRef.current.forEach(clearTimeout);
      timersRef.current = [];
    };

    const runCycle = () => {
      const current = PUZZLES[puzzleRef.current];
      setPuzzle(current);
      setFen(current.fen);
      setSolved(false);
      setShowArrow(false);
      setCycleKey((k) => k + 1);

      // Show the arrow only once the new position has appeared, then
      // play the winning move after it has telegraphed the solution.
      timersRef.current.push(
        setTimeout(() => {
          setShowArrow(true);
        }, 150)
      );

      timersRef.current.push(
        setTimeout(() => {
          const game = new Chess(current.fen);
          game.move(current.move);
          setShowArrow(false);
          setFen(game.fen());
          setSolved(true);
        }, 2400)
      );

      // Reset with the next puzzle and loop
      timersRef.current.push(
        setTimeout(() => {
          puzzleRef.current = (puzzleRef.current + 1) % PUZZLES.length;
          runCycle();
        }, 5200)
      );
    };

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          runCycle();
        } else {
          clearTimers();
        }
      },
      { threshold: 0.35 }
    );

    const wrapper = wrapperRef.current;
    if (wrapper) observer.observe(wrapper);

    return () => {
      clearTimers();
      observer.disconnect();
    };
  }, []);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div ref={wrapperRef} className="[perspective:1400px]">
      <div className="transition-transform duration-700 [transform:rotateX(6deg)_rotateZ(-1.2deg)] hover:[transform:rotateX(2deg)_rotateZ(-0.5deg)]">
        <div
          style={{
            padding: '14px',
            background: 'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.png)',
            backgroundSize: 'cover',
            backgroundPosition: 'center',
            borderRadius: '6px',
            boxShadow:
              '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 24px 70px rgba(0,0,0,0.65)',
          }}
        >
          <Chessboard
            key={cycleKey}
            options={{
              id: 'tactic-board',
              position: fen,
              allowDragging: false,
              arrows: showArrow
                ? [{ startSquare: puzzle.move.from, endSquare: puzzle.move.to, color: '#37be7e' }]
                : [],
              squareStyles: solved
                ? { [puzzle.move.to]: { backgroundColor: 'rgba(55, 190, 126, 0.45)' } }
                : {},
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
              boardStyle: { width: '100%', borderRadius: '6px' },
              animationDurationInMs: 450,
            }}
          />
        </div>
      </div>
    </div>
  );
}
