'use client';

import { useCallback, useEffect, useState } from 'react';

import {
  EndgameFetchError,
  fetchEndgamePracticeCategories,
  practiceCategoryBlurb,
  practiceCategoryIcon,
  practiceCategoryLabel,
} from '@/lib/endgames';
import { EndgamePracticeCategory } from '@/types';

/**
 * The trainer's category list: one row per material category the backend can
 * actually serve, straight from the practice endpoint's own list (so a row
 * always opens onto real positions).
 *
 * Rows do not navigate: picking one swaps the drill ON this page to that
 * category's practice session (the route shell was retired), so the active
 * row carries the gold highlight and "Rated drills" leaves the session. This
 * card is also the ONLY scrollable surface on the trainer page: the page
 * itself is sized to the viewport, and the row list takes whatever height is
 * left under the card's heading.
 */

// The exact walnut card language of the Puzzles page.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

const LABEL_CLASS =
  'text-[11px] font-bold uppercase tracking-[0.25em] text-white/40';

function ChevronRightIcon({ className = '' }: { className?: string }) {
  return (
    <svg
      className={className}
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path d="m9 6 6 6-6 6" />
    </svg>
  );
}

export interface PracticeCategoriesCardProps {
  /** The category whose session is on the board, or null (rated loop). */
  activeCategory: string | null;
  /** Switch the board to this category's practice session. */
  onSelect: (category: string) => void;
  /** Leave the practice session and return to the rated loop. */
  onExitPractice: () => void;
}

export default function PracticeCategoriesCard({
  activeCategory,
  onSelect,
  onExitPractice,
}: PracticeCategoriesCardProps) {
  const [categories, setCategories] = useState<EndgamePracticeCategory[] | null>(
    null
  );
  const [error, setError] = useState<string | null>(null);
  // Bumped by the Retry button; the fetch effect keys off it so a retry is a
  // plain re-run of the same loader.
  const [retryToken, setRetryToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await fetchEndgamePracticeCategories();
        if (!cancelled) setCategories(list);
      } catch (fetchError) {
        if (!cancelled) {
          setError(
            fetchError instanceof EndgameFetchError
              ? fetchError.message
              : 'Could not load categories.'
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [retryToken]);

  const retry = useCallback(() => {
    setError(null);
    setRetryToken((token) => token + 1);
  }, []);

  return (
    <div
      className={`${CARD_CLASS} flex min-h-0 flex-1 flex-col p-5 shadow-2xl shadow-black/25`}
    >
      <div className={LABEL_CLASS}>Choose Endgame category</div>
      {activeCategory && (
        <button
          type="button"
          onClick={onExitPractice}
          className="mt-3 flex w-full shrink-0 items-center justify-center gap-2 rounded-lg border border-[#f7e5c6]/25 px-3 py-2 text-[11px] font-bold uppercase tracking-[0.15em] text-[#f7e5c6]/90 transition hover:border-[#f7e5c6]/50 hover:bg-[#f7e5c6]/5"
        >
          Rated drills
        </button>
      )}

      {/* The page's one scroll region: the list itself. */}
      <div
        className={`wood-scrollbar min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain pr-1.5 ${
          activeCategory ? 'mt-3' : 'mt-4'
        }`}
      >
        {error ? (
          <div className="rounded-xl border border-white/10 bg-black/25 px-4 py-3 text-sm text-white/60">
            <p>{error}</p>
            <button
              type="button"
              onClick={retry}
              className="mt-3 rounded-lg border border-white/20 bg-white/5 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-white/10"
            >
              Retry
            </button>
          </div>
        ) : categories === null ? (
          Array.from({ length: 6 }).map((_, index) => (
            <div
              key={index}
              className="flex animate-pulse items-center gap-3 rounded-xl border border-white/5 bg-black/20 p-1.5 pr-2"
            >
              <div className="h-12 w-[4.5rem] shrink-0 rounded-lg bg-white/5" />
              <div className="flex-1 space-y-2">
                <div className="h-3 w-2/3 rounded bg-white/10" />
                <div className="h-2.5 w-1/2 rounded bg-white/5" />
              </div>
            </div>
          ))
        ) : categories.length === 0 ? (
          <p className="rounded-xl border border-white/10 bg-black/25 px-4 py-3 text-sm text-white/55">
            No sourced endgame positions to study right now.
          </p>
        ) : (
          categories.map((category) => {
            const active = category.category === activeCategory;
            const blurb =
              practiceCategoryBlurb(category.category) ??
              `${category.position_count.toLocaleString()} positions`;
            return (
              <button
                key={category.category}
                type="button"
                aria-pressed={active}
                onClick={() => onSelect(category.category)}
                className={`group flex w-full items-center gap-3 rounded-xl border p-1.5 pr-2 transition focus-visible:outline-none ${
                  active
                    ? 'border-[#d9b87c]/60 bg-[#d9b87c]/10'
                    : 'border-white/10 bg-black/25 hover:border-[#d9b87c]/45 hover:bg-black/10 focus-visible:border-[#d9b87c]/60'
                }`}
              >
                {/* Decorative: the row's name carries the meaning. */}
                <img
                  src={practiceCategoryIcon(category.category)}
                  alt=""
                  loading="lazy"
                  className="h-12 w-[4.5rem] shrink-0 rounded-lg object-cover shadow-[0_3px_10px_rgba(0,0,0,0.5)]"
                />
                <span className="min-w-0 flex-1 text-left">
                  <span
                    className={`block truncate font-display text-[15px] font-semibold leading-tight ${
                      active ? 'text-[#f7e5c6]' : 'text-[#f0e0c0]'
                    }`}
                  >
                    {practiceCategoryLabel(category.category)}
                  </span>
                  <span className="mt-1 block truncate text-[11px] leading-none text-white/45">
                    {blurb}
                  </span>
                </span>
                <ChevronRightIcon
                  className={`h-5 w-5 shrink-0 transition group-hover:translate-x-0.5 ${
                    active
                      ? 'text-[#efd9a7]'
                      : 'text-[#d9b87c]/60 group-hover:text-[#efd9a7]'
                  }`}
                />
              </button>
            );
          })
        )}
      </div>
    </div>
  );
}
