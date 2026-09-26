'use client';

import { memo, useState } from 'react';

import { PUZZLE_THEME_GROUPS } from '@/lib/themes';
import { PUZZLE_THEME_PREVIEWS } from '@/lib/puzzleThemePreviews';

import ThemeMiniBoard from './ThemeMiniBoard';
import { PUZZLE_CARD_CLASS, THEME_MINI_BOARD_CLASS } from './puzzleStyles';

/**
 * The puzzles theme list, mirroring the Endgame Trainer's category card.
 * Each category has one representative miniature board; expanding a category
 * reveals text-only theme choices so the picker keeps a small board footprint.
 *
 * "All Tactics" is pinned above the scroll region and is the rated loop;
 * picking a theme swaps the board into that theme's unrated practice batch.
 * This card is the ONLY scrollable surface on the page.
 */

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

const ThemeChoice = memo(function ThemeChoice({
  label,
  active,
  onSelect,
}: {
  label: string;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onSelect}
      className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition focus-visible:outline-none ${
        active
          ? 'border-[#d9b87c]/55 bg-[#d9b87c]/10 text-[#f7e5c6]'
          : 'border-white/5 bg-black/20 text-[#f0e0c0]/80 hover:border-[#d9b87c]/35 hover:bg-black/10 hover:text-[#f0e0c0] focus-visible:border-[#d9b87c]/60'
      }`}
    >
      <span
        className={`h-1.5 w-1.5 shrink-0 rounded-full ${active ? 'bg-[#efd9a7]' : 'bg-white/25'}`}
        aria-hidden="true"
      />
      <span className="min-w-0 flex-1 truncate font-display text-sm font-semibold">
        {label}
      </span>
      {active && <span className="text-[10px] uppercase tracking-wider text-[#efd9a7]/65">Selected</span>}
    </button>
  );
});

export interface ThemePickerCardProps {
  /** The theme whose practice batch is on the board, or null (rated loop). */
  activeTheme: string | null;
  onSelect: (theme: string | null) => void;
}

export default memo(function ThemePickerCard({
  activeTheme,
  onSelect,
}: ThemePickerCardProps) {
  const [expandedGroup, setExpandedGroup] = useState<string | null>(null);

  return (
    <div
      className={`${PUZZLE_CARD_CLASS} flex min-h-0 flex-1 flex-col p-5 shadow-2xl shadow-black/25`}
    >
      <div className={LABEL_CLASS}>Choose Theme</div>

      <div className="mt-3 shrink-0">
        <button
          type="button"
          aria-pressed={activeTheme === null}
          onClick={() => onSelect(null)}
          className={`group flex w-full items-center gap-3 rounded-xl border p-1.5 pr-2 transition focus-visible:outline-none ${
            activeTheme === null
              ? 'border-[#d9b87c]/60 bg-[#d9b87c]/10'
              : 'border-white/10 bg-black/25 hover:border-[#d9b87c]/45 hover:bg-black/10 focus-visible:border-[#d9b87c]/60'
          }`}
        >
          <ThemeMiniBoard
            preview={null}
            boardId="theme-preview-all-tactics"
            className={THEME_MINI_BOARD_CLASS}
          />
          <span className="min-w-0 flex-1 text-left">
            <span
              className={`block truncate font-display text-[15px] font-semibold leading-tight ${
                activeTheme === null ? 'text-[#f7e5c6]' : 'text-[#f0e0c0]'
              }`}
            >
              All Tactics
            </span>
            <span className="mt-1 block truncate text-[11px] leading-none text-white/45">
              Every theme · rated
            </span>
          </span>
          <ChevronRightIcon className="h-5 w-5 shrink-0 text-[#d9b87c]/60 transition group-hover:translate-x-0.5 group-hover:text-[#efd9a7]" />
        </button>
      </div>

      <div className="wood-scrollbar mt-3 min-h-0 flex-1 space-y-2 overflow-y-auto overscroll-contain pr-1.5">
        {PUZZLE_THEME_GROUPS.map((group) => {
          const expanded = expandedGroup === group.name;
          const groupId = `puzzle-theme-group-${group.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
          const representativeTheme = group.themes[0];
          const preview = representativeTheme
            ? PUZZLE_THEME_PREVIEWS[representativeTheme.key] ?? null
            : null;

          return (
            <section key={group.name} aria-label={group.name}>
              <button
                type="button"
                aria-expanded={expanded}
                aria-controls={groupId}
                onClick={() => setExpandedGroup(expanded ? null : group.name)}
                className={`group flex w-full items-center gap-3 rounded-xl border p-1.5 pr-2 text-left transition focus-visible:outline-none ${
                  expanded
                    ? 'border-[#d9b87c]/45 bg-[#d9b87c]/[0.07]'
                    : 'border-white/10 bg-black/25 hover:border-[#d9b87c]/35 hover:bg-black/10 focus-visible:border-[#d9b87c]/60'
                }`}
              >
                <ThemeMiniBoard
                  preview={preview}
                  boardId={`theme-preview-${group.name.toLowerCase().replace(/[^a-z0-9]+/g, '-')}`}
                  className={THEME_MINI_BOARD_CLASS}
                />
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span
                      className={`h-1.5 w-5 shrink-0 rounded-full bg-gradient-to-r ${group.accent}`}
                      aria-hidden="true"
                    />
                    <span className="truncate font-display text-[15px] font-semibold leading-tight text-[#f0e0c0]">
                      {group.name}
                    </span>
                  </span>
                  <span className="mt-1 block truncate pl-7 text-[11px] leading-none text-white/45">
                    {group.themes.length} themes
                  </span>
                </span>
                <ChevronRightIcon
                  className={`h-5 w-5 shrink-0 text-[#d9b87c]/60 transition group-hover:text-[#efd9a7] ${
                    expanded ? 'rotate-90 text-[#efd9a7]' : ''
                  }`}
                />
              </button>

              <div id={groupId} hidden={!expanded} className="mt-2 space-y-1 pl-[3.75rem]">
                {expanded &&
                  group.themes.map((theme) => (
                    <ThemeChoice
                      key={theme.key}
                      label={theme.label}
                      active={theme.key === activeTheme}
                      onSelect={() => onSelect(theme.key)}
                    />
                  ))}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
});
