'use client';

import { useEffect, useMemo, useState, type CSSProperties } from 'react';
import { Chess } from 'chess.js';
import { Chessboard } from 'react-chessboard';

export interface WhyReplayBoardProps {
  /** Position BEFORE W (the mistake). */
  fenBefore: string;
  /** Line to animate, starting WITH W (already truncated server-side). */
  lineUci: string[];
}

/**
 * Read-only replay viewer for "show why" lines: prev/next steppers plus
 * auto-play, walnut board language, and a marker on the truncation point.
 * No grading, no interaction — it only plays the backend's line.
 *
 * The board wears the same walnut frame as every other trainer board
 * (BoardShell.tsx): 14px walnut-dark padding, layered shadow, 8%-opacity
 * wood overlay.
 */
const FRAME_STYLE: CSSProperties = {
  padding: '14px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  borderRadius: '6px',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
};

const WOOD_OVERLAY_STYLE: CSSProperties = {
  position: 'absolute',
  inset: 0,
  backgroundImage: 'url("/wood-texture.webp")',
  backgroundSize: 'cover',
  opacity: 0.08,
  pointerEvents: 'none',
  mixBlendMode: 'multiply',
};

// Wooden transport boxes verbatim from the game review page
// (AnalysisPanel): walnut-dark gradient, layered shadow, round outer
// corners on the first/last boxes, filled-cream glyphs.
const WOOD_BOX_STYLE: CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};

function NavButton({
  label,
  disabled,
  onClick,
  cornerStyle,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  cornerStyle?: CSSProperties;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={label}
      className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
      style={{ cursor: disabled ? 'default' : 'pointer', ...WOOD_BOX_STYLE, ...cornerStyle }}
    >
      {children}
    </button>
  );
}
export default function WhyReplayBoard({ fenBefore, lineUci }: WhyReplayBoardProps) {
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
      <div style={FRAME_STYLE}>
        <div className="relative aspect-square w-full overflow-hidden rounded-lg">
          <div style={WOOD_OVERLAY_STYLE} aria-hidden="true" />
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
      </div>
      <div className="mt-2 grid shrink-0 grid-cols-5 gap-1.5">
        <NavButton
          label="First move"
          disabled={ply === 0}
          onClick={() => { setPlaying(false); setPly(0); }}
          cornerStyle={{ borderRadius: '4px 4px 4px 24px' }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <rect x="3" y="4" width="2.5" height="16" rx="1" />
            <path d="M21 4 L9 12 L21 20 Z" />
          </svg>
        </NavButton>
        <NavButton
          label="Previous move"
          disabled={ply === 0}
          onClick={() => { setPlaying(false); setPly((p) => Math.max(0, p - 1)); }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <path d="M18 4 L6 12 L18 20 Z" />
          </svg>
        </NavButton>
        <button
          type="button"
          onClick={() => {
            if (ply >= maxPly) setPly(0);
            setPlaying((v) => !v);
          }}
          aria-label={playing ? 'Pause auto-play' : 'Auto-play moves'}
          className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
          style={{ cursor: 'pointer', ...WOOD_BOX_STYLE }}
        >
          {playing ? (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
              <rect x="6" y="5" width="4" height="14" rx="1" />
              <rect x="14" y="5" width="4" height="14" rx="1" />
            </svg>
          ) : (
            <svg width="14" height="14" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
              <path d="M8 5v14l11-7z" />
            </svg>
          )}
        </button>
        <NavButton
          label="Next move"
          disabled={ply >= maxPly}
          onClick={() => { setPlaying(false); setPly((p) => Math.min(maxPly, p + 1)); }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <path d="M6 4 L18 12 L6 20 Z" />
          </svg>
        </NavButton>
        <NavButton
          label="Last move"
          disabled={ply >= maxPly}
          onClick={() => { setPlaying(false); setPly(maxPly); }}
          cornerStyle={{ borderRadius: '4px 4px 24px 4px' }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <path d="M3 4 L15 12 L3 20 Z" />
            <rect x="18.5" y="4" width="2.5" height="16" rx="1" />
          </svg>
        </NavButton>
      </div>
    </div>
  );
}
