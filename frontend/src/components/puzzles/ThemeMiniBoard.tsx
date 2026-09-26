'use client';

import dynamic from 'next/dynamic';
import { memo, useEffect, useRef, useState } from 'react';

import type { PuzzleThemePreview } from '@/lib/puzzleThemePreviews';

/**
 * Miniature walnut board used in the /puzzles theme picker. Mirrors the
 * landing/TrainPageClient board treatment (same wood textures) and draws the
 * sampled key move as a gold arrow so each theme is legible at a glance.
 *
 * react-chessboard is DOM-only, so it is dynamically imported with ssr: false,
 * and rows mount it only while they are near the viewport -- the picker has
 * one board per category, so keeping every visited preview mounted would still
 * burden the live puzzle board with extra square and piece elements.
 *
 * This is a memo boundary: the main board's page re-renders (prefetch state,
 * result state) must never cascade into the live mini boards, or every drag
 * on the main board pays for re-rendering dozens of decorative ones.
 */
const MiniChessboard = dynamic(
  () => import('react-chessboard').then((module) => module.Chessboard),
  {
    ssr: false,
    loading: () => <div className="h-full w-full animate-pulse rounded-lg bg-black/35" aria-hidden="true" />,
  }
);

const START_FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w - - 0 1';
const ARROW_COLOR = '#d9b87c';

function ThemeMiniBoard({
  preview,
  boardId,
  className = '',
}: {
  /** null renders the start position (used by the "All Tactics" row). */
  preview: PuzzleThemePreview | null;
  /** react-chessboard uses this ID for its DOM lookups and drag context. */
  boardId: string;
  className?: string;
}) {
  const holderRef = useRef<HTMLDivElement | null>(null);
  const [inView, setInView] = useState(false);

  useEffect(() => {
    const holder = holderRef.current;
    if (!holder) return;

    const observer = new IntersectionObserver(
      (entries) => {
        setInView(entries.some((entry) => entry.isIntersecting));
      },
      { rootMargin: '160px' }
    );
    observer.observe(holder);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={holderRef} className={className} aria-hidden="true">
      {inView ? (
        <MiniChessboard
          options={{
            id: boardId,
            position: preview?.fen ?? START_FEN,
            boardOrientation: preview?.orientation ?? 'white',
            arrows: preview
              ? [{ startSquare: preview.from, endSquare: preview.to, color: ARROW_COLOR }]
              : [],
            allowDragging: false,
            allowDrawingArrows: false,
            showNotation: false,
            animationDurationInMs: 0,
            darkSquareStyle: {
              backgroundImage: 'url(/walnut-dark.webp)',
              backgroundSize: '140% 140%',
              backgroundPosition: 'center',
            },
            lightSquareStyle: {
              backgroundImage: 'url(/walnut-light.webp)',
              backgroundSize: '140% 140%',
              backgroundPosition: 'center',
            },
            boardStyle: { width: '100%', height: '100%' },
          }}
        />
      ) : (
        <div className="h-full w-full rounded-lg bg-black/35" />
      )}
    </div>
  );
}

export default memo(ThemeMiniBoard);
