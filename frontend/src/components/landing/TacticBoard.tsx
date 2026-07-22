'use client';

import { useEffect, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';

const TACTIC_FEN = '6k1/5ppp/8/8/8/8/8/R6K w - - 0 1';
const MOVE = { from: 'a1', to: 'a8' };

export default function TacticBoard() {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const [fen, setFen] = useState(TACTIC_FEN);
  const [showArrow, setShowArrow] = useState(false);
  const [solved, setSolved] = useState(false);

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
      setFen(TACTIC_FEN);
      setSolved(false);
      setShowArrow(true);

      // Play the winning move after the arrow has telegraphed it
      timersRef.current.push(
        setTimeout(() => {
          const game = new Chess(TACTIC_FEN);
          game.move(MOVE);
          setShowArrow(false);
          setFen(game.fen());
          setSolved(true);
        }, 2100)
      );

      // Reset and loop
      timersRef.current.push(setTimeout(runCycle, 5200));
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
            options={{
              position: fen,
              allowDragging: false,
              arrows: showArrow
                ? [{ startSquare: MOVE.from, endSquare: MOVE.to, color: '#37be7e' }]
                : [],
              squareStyles: solved
                ? { a8: { backgroundColor: 'rgba(55, 190, 126, 0.45)' } }
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
