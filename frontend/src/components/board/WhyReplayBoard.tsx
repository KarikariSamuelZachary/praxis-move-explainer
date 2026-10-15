'use client';

import { useEffect, useMemo, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';

export interface WhyReplayBoardProps {
  /** Position BEFORE W (the mistake). */
  fenBefore: string;
  /** Line to animate, starting WITH W (already truncated server-side). */
  lineUci: string[];
  /** Plies after W included (null when unknown). Shown as a badge. */
  truncatedAtPly: number | null;
}

/**
 * Read-only replay viewer for "show why" lines: prev/next steppers plus
 * auto-play, walnut board language, and a marker on the truncation point.
 * No grading, no interaction — it only plays the backend's line.
 */
export default function WhyReplayBoard({ fenBefore, lineUci, truncatedAtPly }: WhyReplayBoardProps) {
  const [ply, setPly] = useState(0);
  const [playing, setPlaying] = useState(false);

  const positions = useMemo(() => {
    const fens = [fenBefore];
    const game = new Chess(fenBefore);
    for (const uci of lineUci) {
      try {
        game.move({
          from: uci.slice(0, 2),
          to: uci.slice(2, 4),
          promotion: uci[4] ?? undefined,
        });
        fens.push(game.fen());
      } catch {
        break;
      }
    }
    return fens;
  }, [fenBefore, lineUci]);

  const maxPly = positions.length - 1;

  useEffect(() => {
    setPly(0);
    setPlaying(false);
  }, [fenBefore, lineUci]);

  useEffect(() => {
    if (!playing) return;
    if (ply >= maxPly) {
      setPlaying(false);
      return;
    }
    const timer = setTimeout(() => setPly((p) => Math.min(p + 1, maxPly)), 900);
    return () => clearTimeout(timer);
  }, [playing, ply, maxPly]);

  const lastMove = ply > 0 ? lineUci[ply - 1] : null;

  return (
    <div>
      <div className="relative aspect-square w-full overflow-hidden rounded-lg">
        <Chessboard
          options={{
            position: positions[ply] ?? fenBefore,
            boardOrientation: fenBefore.split(/\s+/)[1] === 'b' ? 'black' : 'white',
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
            boardStyle: { width: '100%', borderRadius: '8px' },
            animationDurationInMs: 200,
            allowDragging: false,
            squareStyles:
              lastMove != null
                ? {
                    [lastMove.slice(0, 2)]: { backgroundColor: 'rgba(255, 170, 0, 0.35)' },
                    [lastMove.slice(2, 4)]: { backgroundColor: 'rgba(255, 170, 0, 0.45)' },
                  }
                : {},
          }}
        />
      </div>
      <div className="mt-2 flex items-center justify-between gap-2">
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => { setPlaying(false); setPly(0); }}
            disabled={ply === 0}
            className="rounded-md border border-white/15 px-2.5 py-1 text-xs font-semibold text-white/70 transition hover:bg-white/10 disabled:opacity-30"
          >
            ⏮
          </button>
          <button
            type="button"
            onClick={() => { setPlaying(false); setPly((p) => Math.max(0, p - 1)); }}
            disabled={ply === 0}
            className="rounded-md border border-white/15 px-2.5 py-1 text-xs font-semibold text-white/70 transition hover:bg-white/10 disabled:opacity-30"
          >
            ←
          </button>
          <button
            type="button"
            onClick={() => {
              if (ply >= maxPly) setPly(0);
              setPlaying((v) => !v);
            }}
            className="rounded-md border border-white/15 px-2.5 py-1 text-xs font-semibold text-white/70 transition hover:bg-white/10"
          >
            {playing ? '⏸' : '▶'}
          </button>
          <button
            type="button"
            onClick={() => { setPlaying(false); setPly((p) => Math.min(maxPly, p + 1)); }}
            disabled={ply >= maxPly}
            className="rounded-md border border-white/15 px-2.5 py-1 text-xs font-semibold text-white/70 transition hover:bg-white/10 disabled:opacity-30"
          >
            →
          </button>
        </div>
        <span className="text-xs text-white/40">
          ply {ply}/{maxPly}
          {truncatedAtPly != null && ' · line truncated'}
        </span>
      </div>
    </div>
  );
}
