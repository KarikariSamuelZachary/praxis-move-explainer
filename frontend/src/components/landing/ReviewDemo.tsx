'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';
import gsap from 'gsap';

import {
  ClassificationIcon,
  type Classification,
} from '@/components/review/icons/ClassificationIcon';

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const PGN_TEXT =
  '1. e4 e5 2. Nf3 Nc6 3. Bc4 Nd4 4. Nxe5 Qg5 5. Nxf7 Qxg2 6. Rf1 Qxe4+ 7. Be2 Nf3#';
const MOVES = [
  'e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1c4', 'c6d4', 'f3e5',
  'd8g5', 'e5f7', 'g5g2', 'h1f1', 'g2e4', 'c4e2', 'd4f3',
];

const MOVE_CLASSIFICATIONS: Classification[] = [
  'best',        // 1. e4
  'best',        // 1... e5
  'best',        // 2. Nf3
  'best',        // 2... Nc6
  'best',        // 3. Bc4
  'inaccuracy',  // 3... Nd4
  'mistake',     // 4. Nxe5??
  'best',        // 4... Qg5!
  'blunder',     // 5. Nxf7??
  'best',        // 5... Qxg2!
  'good',        // 6. Rf1
  'best',        // 6... Qxe4+
  'good',        // 7. Be2
  'best',        // 7... Nf3#!
];

const ICON_SIZE_PERCENT = 5.5;
const ICON_MARGIN_PERCENT = 0.5;
const SQUARE_PERCENT = 100 / 8;

function squareToPercent(square: string) {
  const file = square.charCodeAt(0) - 97;
  const rank = parseInt(square[1], 10) - 1;
  return { col: file, row: 7 - rank };
}

const EXPLANATION =
  'White grabs a poisoned pawn. After 4...Qg5! the knight is trapped and the kingside collapses — three moves later the game ends in a mating net.';

const woodBoxStyle: React.CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

const FEEDBACK_ROWS = [
  { move: '2. Nf3', classification: 'best' as Classification, evalText: '+0.21' },
  { move: '3... Nd4', classification: 'inaccuracy' as Classification, evalText: '-0.62' },
  { move: '4. Nxe5', classification: 'mistake' as Classification, evalText: '-2.10' },
];

type Phase = 'idle' | 'typing' | 'loading' | 'playing' | 'analysis' | 'done';

export default function ReviewDemo() {
  const rootRef = useRef<HTMLDivElement>(null);
  const evalRef = useRef<HTMLSpanElement>(null);
  const sparkRef = useRef<SVGPathElement>(null);
  const timersRef = useRef<ReturnType<typeof setTimeout>[]>([]);
  const intervalsRef = useRef<ReturnType<typeof setInterval>[]>([]);
  const hasRunRef = useRef(false);

  const [phase, setPhase] = useState<Phase>('idle');
  const [typedPgn, setTypedPgn] = useState('');
  const [fen, setFen] = useState(START_FEN);
  const [typedExplanation, setTypedExplanation] = useState('');
  const [visibleRows, setVisibleRows] = useState(0);
  const [currentMoveIndex, setCurrentMoveIndex] = useState<number | null>(null);

  const clearAll = useCallback(() => {
    timersRef.current.forEach(clearTimeout);
    timersRef.current = [];
    intervalsRef.current.forEach(clearInterval);
    intervalsRef.current = [];
  }, []);

  const later = useCallback(
    (ms: number, fn: () => void) => {
      timersRef.current.push(setTimeout(fn, ms));
    },
    []
  );

  const typeText = useCallback(
    (full: string, speed: number, onChar: (text: string) => void, onDone: () => void) => {
      let i = 0;
      const id = setInterval(() => {
        i += 1;
        onChar(full.slice(0, i));
        if (i >= full.length) {
          clearInterval(id);
          onDone();
        }
      }, speed);
      intervalsRef.current.push(id);
    },
    []
  );

  const run = useCallback(() => {
    clearAll();
    setTypedPgn('');
    setTypedExplanation('');
    setVisibleRows(0);
    setFen(START_FEN);
    setCurrentMoveIndex(null);
    setPhase('typing');

    if (evalRef.current) evalRef.current.textContent = '+0.20';
    if (sparkRef.current) {
      sparkRef.current.style.strokeDashoffset = '1';
    }

    // 1. PGN types itself
    typeText(PGN_TEXT, 26, setTypedPgn, () => {
      setPhase('loading');

      // 2. Load Game "clicks", then the board plays through.
      //    Moves are spaced 700ms apart with a 200ms animation (matching
      //    the real game review BoardPanel) so each piece lands cleanly
      //    before the next move starts — no overshoot.
      later(900, () => {
        setPhase('playing');
        const game = new Chess(START_FEN);

        MOVES.forEach((uci, index) => {
          later(500 + index * 700, () => {
            game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4) });
            setFen(game.fen());
            setCurrentMoveIndex(index);

            if (index === MOVES.length - 1) {
              setPhase('analysis');
            }
          });
        });
      });
    });
  }, [clearAll, later, typeText]);

  // 3. Analysis phase: eval swings, sparkline draws, rows appear, explanation types
  useEffect(() => {
    if (phase !== 'analysis') return;

    const counter = { value: 0.2 };
    gsap.to(counter, {
      value: -2.4,
      duration: 2.2,
      ease: 'power2.inOut',
      onUpdate: () => {
        if (evalRef.current) {
          const v = counter.value;
          evalRef.current.textContent = `${v >= 0 ? '+' : ''}${v.toFixed(2)}`;
        }
      },
    });

    if (sparkRef.current) {
      gsap.to(sparkRef.current, {
        strokeDashoffset: 0,
        duration: 2.2,
        ease: 'power2.inOut',
      });
    }

    FEEDBACK_ROWS.forEach((_, index) => {
      later(700 + index * 420, () => setVisibleRows(index + 1));
    });

    later(2100, () => {
      typeText(EXPLANATION, 14, setTypedExplanation, () => setPhase('done'));
    });
  }, [phase, later, typeText]);

  // Start when scrolled into view (once)
  /* eslint-disable react-hooks/set-state-in-effect */
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const root = rootRef.current;
    if (!root) return;

    if (reduced) {
      setTypedPgn(PGN_TEXT);
      const game = new Chess(START_FEN);
      MOVES.forEach((uci) => game.move({ from: uci.slice(0, 2), to: uci.slice(2, 4) }));
      setFen(game.fen());
      setCurrentMoveIndex(MOVES.length - 1);
      setVisibleRows(FEEDBACK_ROWS.length);
      setTypedExplanation(EXPLANATION);
      setPhase('done');
      if (evalRef.current) evalRef.current.textContent = '-2.40';
      if (sparkRef.current) sparkRef.current.style.strokeDashoffset = '0';
      return;
    }

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !hasRunRef.current) {
          hasRunRef.current = true;
          run();
        }
      },
      { threshold: 0.3 }
    );
    observer.observe(root);

    return () => {
      observer.disconnect();
      clearAll();
    };
  }, [run, clearAll]);
  /* eslint-enable react-hooks/set-state-in-effect */

  return (
    <div
      ref={rootRef}
      className="overflow-hidden rounded-xl border border-white/10 bg-zinc-950/70 shadow-[0_30px_80px_rgba(0,0,0,0.55)] backdrop-blur-sm"
    >
      {/* Window header */}
      <div className="flex items-center justify-between border-b border-white/5 px-5 py-3">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-red-400/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber-400/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-moss/70" />
        </div>
        <span className="text-xs font-medium tracking-widest text-wood-mute">
          Game Review
        </span>
        <button
          type="button"
          onClick={run}
          title="Replay demo"
          className="text-wood-mute transition hover:rotate-[-120deg] hover:text-gold-bright"
        >
          ⟳
        </button>
      </div>

      <div className="grid gap-5 p-5 md:grid-cols-[1fr_auto_1fr]">
        {/* Import panel */}
        <div className="flex flex-col rounded-lg border border-white/5 bg-walnut-900/60 p-4">
          <div className="mb-3 text-xs font-semibold uppercase tracking-[0.2em] text-wood-mute">
            Import Game
          </div>
          <div className="min-h-[120px] flex-1 rounded-md border border-white/10 bg-black/40 p-3 font-mono text-[11px] leading-5 text-emerald-100/80">
            {typedPgn}
            {phase === 'typing' && (
              <span className="ml-0.5 inline-block h-3.5 w-[7px] animate-pulse bg-moss-bright align-middle" />
            )}
          </div>
          <button
            type="button"
            tabIndex={-1}
            className={`mt-3 rounded-md py-2 text-xs font-semibold text-white transition ${
              phase === 'loading'
                ? 'scale-95 bg-moss-bright shadow-[0_0_24px_rgba(55,190,126,0.6)]'
                : 'bg-moss'
            }`}
          >
            Load Game
          </button>
        </div>

        {/* Board — same wood-box wrapper structure as the real game
            review BoardPanel so square-width measurement and piece
            animation behave identically. */}
        <div className="relative mx-auto aspect-square w-full max-w-[260px] self-center md:w-[280px] md:max-w-none">
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
              <Chessboard
                options={{
                  id: 'review-demo-board',
                  position: fen,
                  allowDragging: false,
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
              {currentMoveIndex !== null && (
                <MoveIconOverlay moveIndex={currentMoveIndex} />
              )}
            </div>
          </div>
        </div>

        {/* AI Analysis panel */}
        <div className="flex flex-col rounded-lg border border-white/5 bg-walnut-900/60 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-[0.2em] text-wood-mute">
              AI Analysis
            </span>
            <span className="rounded border border-white/10 px-1.5 py-0.5 text-[10px] text-wood-mute">
              Stockfish 16
            </span>
          </div>

          <div className="mt-3 flex items-end justify-between">
            <div>
              <div className="text-[10px] uppercase tracking-widest text-wood-mute">
                Evaluation
              </div>
              <span ref={evalRef} className="font-mono text-xl font-semibold text-cream">
                +0.20
              </span>
            </div>
            <svg viewBox="0 0 120 40" className="h-10 w-28">
              <path
                ref={sparkRef}
                d="M2 12 C 16 11, 26 14, 38 13 S 58 15, 66 20 S 82 30, 94 32 S 112 35, 118 36"
                fill="none"
                stroke={phase === 'analysis' || phase === 'done' ? '#f87171' : '#37be7e'}
                strokeWidth="2"
                strokeLinecap="round"
                pathLength={1}
                strokeDasharray={1}
                strokeDashoffset={1}
              />
            </svg>
          </div>

          <div className="mt-3 space-y-1.5">
            {FEEDBACK_ROWS.map((row, index) => (
              <div
                key={row.move}
                className={`flex items-center gap-2 rounded border border-white/5 bg-black/30 px-2.5 py-1.5 text-[11px] transition-opacity duration-300 ${
                  index < visibleRows ? 'opacity-100' : 'opacity-0'
                }`}
              >
                <ClassificationIcon classification={row.classification} size={18} />
                <span className="font-mono text-cream/90">{row.move}</span>
                <span className="ml-auto font-mono text-wood-mute">{row.evalText}</span>
              </div>
            ))}
          </div>

          <div className="mt-3 border-t border-white/5 pt-3">
            <div className="text-[10px] font-semibold uppercase tracking-widest text-gold">
              Why this move?
            </div>
            <p className="mt-1.5 min-h-[72px] text-[11px] leading-5 text-cream/75">
              {typedExplanation}
              {(phase === 'analysis' || phase === 'done') &&
                typedExplanation.length < EXPLANATION.length && (
                  <span className="ml-0.5 inline-block h-3 w-[6px] animate-pulse bg-gold align-middle" />
                )}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function MoveIconOverlay({ moveIndex }: { moveIndex: number }) {
  const uci = MOVES[moveIndex];
  if (!uci) return null;
  const destSquare = uci.slice(2, 4);
  const { col, row } = squareToPercent(destSquare);

  return (
    <div
      style={{
        position: 'absolute',
        left: `${col * SQUARE_PERCENT + SQUARE_PERCENT - ICON_SIZE_PERCENT - ICON_MARGIN_PERCENT}%`,
        top: `${row * SQUARE_PERCENT + ICON_MARGIN_PERCENT}%`,
        width: `${ICON_SIZE_PERCENT}%`,
        aspectRatio: '1 / 1',
        pointerEvents: 'none',
        zIndex: 5,
      }}
    >
      <ClassificationIcon classification={MOVE_CLASSIFICATIONS[moveIndex]} size="100%" />
    </div>
  );
}
