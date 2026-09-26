'use client';

import Link from 'next/link';

import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';

/** Rough per-review estimate: matches the landing card's 12 reviews / 8 min. */
const SECONDS_PER_REVIEW = 40;

/**
 * The Endgame Trainer's compact Woodpecker promo, reused on /puzzles in the
 * same right-column slot: the same wood-panel surface, cutout bird and moss
 * CTA. The puzzle page owns the due count (it already polls the Woodpecker
 * queue so a fresh intake can refresh the numeral), so this stays
 * presentational.
 */
export default function WoodpeckerPromoCard({
  dueCount,
  peckSignal = 0,
}: {
  dueCount: number | null;
  /**
   * Increments on the first wrong move of a puzzle; the key remount replays
   * the peck. Later retries on the same position leave the bird still.
   */
  peckSignal?: number;
}) {
  const minutes =
    dueCount !== null && dueCount > 0
      ? Math.max(1, Math.round((dueCount * SECONDS_PER_REVIEW) / 60))
      : null;

  return (
    <div className={`${WOOD_PANEL_CLASS} shrink-0`} style={WOOD_PANEL_STYLE}>
      <div className="flex items-center gap-4 p-4">
        {/* Bird burned into the wood, same treatment as the landing card,
            nudged forward with a touch of scale and a deeper drop shadow. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          key={peckSignal}
          src="/woodpecker-cutout.webp"
          alt=""
          className={`h-24 w-auto shrink-0 [scale:1.12] [filter:brightness(0.86)_sepia(0.25)_drop-shadow(0_16px_24px_rgba(0,0,0,0.6))] ${
            peckSignal > 0 ? 'woodpecker-peck' : ''
          }`}
        />

        <div className="flex min-w-0 flex-1 flex-col items-center pr-8 text-center">
          <div className="text-[10px] font-semibold uppercase tracking-[0.25em] text-wood-mute">
            Reviews Due
          </div>
          <div className="mt-1 font-display text-4xl font-semibold leading-none text-gold-bright">
            {dueCount ?? '—'}
          </div>
          {dueCount === 0 ? (
            <div className="mt-1.5 text-[11px] leading-4 text-wood-mute">
              All caught up
            </div>
          ) : minutes !== null ? (
            <div className="mt-1.5 flex items-center gap-1.5 text-[11px] leading-4 text-wood-mute">
              <span aria-hidden>◷</span> {minutes} min
              <span className="text-wood-mute/70">estimated time</span>
            </div>
          ) : null}
          <Link
            href="/woodpecker"
            className="group mt-3.5 inline-flex items-center gap-2 rounded-md bg-moss px-5 py-2.5 text-sm font-semibold text-white shadow-[0_8px_28px_rgba(46,158,91,0.35)] transition duration-300 hover:bg-moss-bright"
          >
            Start Review
            <span className="transition-transform duration-300 group-hover:translate-x-1">
              →
            </span>
          </Link>
        </div>
      </div>
    </div>
  );
}
