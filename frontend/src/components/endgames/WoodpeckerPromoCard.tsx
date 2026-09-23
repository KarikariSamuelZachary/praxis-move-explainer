'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';

import { fetchEndgameWoodpeckerCount } from '@/lib/endgames';
import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';

/** Rough per-review estimate: matches the landing card's 12 reviews / 8 min. */
const SECONDS_PER_REVIEW = 40;

/**
 * The landing page's Woodpecker card, compacted for the trainer's right
 * column: the same wood-panel surface, cutout bird and moss CTA, scaled to
 * the 22rem column (smaller bird, numeral and paddings).
 *
 * The count is real -- the user's due endgame reviews -- so the landing's
 * scroll-triggered countdown-to-zero flourish is deliberately absent: a real
 * number must not animate down to a fake zero.
 */
export default function WoodpeckerPromoCard() {
  const [dueCount, setDueCount] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchEndgameWoodpeckerCount()
      .then((count) => {
        if (!cancelled) setDueCount(count);
      })
      .catch((error) => {
        // Decorative: a failed count leaves the numeral on "—" rather than
        // surfacing an error the drill screen does not need.
        console.error('Failed to fetch the endgame review count:', error);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const minutes =
    dueCount !== null && dueCount > 0
      ? Math.max(1, Math.round((dueCount * SECONDS_PER_REVIEW) / 60))
      : null;

  return (
    <div
      className={`${WOOD_PANEL_CLASS} shrink-0`}
      style={WOOD_PANEL_STYLE}
    >
      <div className="flex items-center gap-4 p-4">
        {/* Bird burned into the wood, same treatment as the landing card. */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/woodpecker-cutout.webp"
          alt=""
          className="h-24 w-auto shrink-0 [filter:brightness(0.82)_sepia(0.25)_drop-shadow(0_8px_14px_rgba(0,0,0,0.5))]"
        />

        {/* Centered in its own column, then nudged back toward the bird with
            a little right padding so the block doesn't sit fully forward. */}
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
