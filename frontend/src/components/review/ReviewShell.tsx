'use client';

import { ReactNode, useState } from 'react';

type ReviewShellProps = {
  importPanel: ReactNode;
  boardPanel: ReactNode;
  analysisPanel: ReactNode;
  defaultLeftCollapsed?: boolean;
  defaultRightCollapsed?: boolean;
};

export default function ReviewShell({
  importPanel,
  boardPanel,
  analysisPanel,
  defaultLeftCollapsed = false,
  defaultRightCollapsed = false,
}: ReviewShellProps) {
  const [leftCollapsed, setLeftCollapsed] = useState(defaultLeftCollapsed);
  const [rightCollapsed, setRightCollapsed] = useState(defaultRightCollapsed);

  const gridCols = (() => {
    if (leftCollapsed && rightCollapsed) {
      return 'xl:grid-cols-[3.25rem_minmax(0,1fr)_3.25rem]';
    }
    if (leftCollapsed) {
      return 'xl:grid-cols-[3.25rem_minmax(0,1fr)_22rem]';
    }
    if (rightCollapsed) {
      return 'xl:grid-cols-[20rem_minmax(0,1fr)_3.25rem]';
    }
    return 'xl:grid-cols-[20rem_minmax(0,1fr)_22rem]';
  })();

  return (
    <div className="relative h-full w-full">
      <div className={`grid h-full grid-cols-1 gap-3 transition-all duration-300 ease-in-out lg:grid-cols-[18rem_minmax(0,1fr)] lg:gap-4 ${gridCols}`}>
        <CollapseRail
          side="left"
          collapsed={leftCollapsed}
          onToggle={() => setLeftCollapsed((value) => !value)}
        >
          {importPanel}
        </CollapseRail>

        <div className="min-h-0 min-w-0">{boardPanel}</div>

        <CollapseRail
          side="right"
          collapsed={rightCollapsed}
          onToggle={() => setRightCollapsed((value) => !value)}
        >
          {analysisPanel}
        </CollapseRail>
      </div>
    </div>
  );
}

function CollapseRail({
  side,
  collapsed,
  onToggle,
  children,
}: {
  side: 'left' | 'right';
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const expandLabel = side === 'left' ? 'Expand import panel' : 'Expand analysis panel';
  const collapseLabel = side === 'left' ? 'Collapse import panel' : 'Collapse analysis panel';
  const chevronClass = side === 'left' ? 'rotate-180' : '';

  if (collapsed) {
    return (
      <div className="hidden h-full xl:flex items-start justify-start">
        <button
          type="button"
          onClick={onToggle}
          aria-label={expandLabel}
          title={expandLabel}
          className="mt-2 inline-flex h-10 w-10 items-center justify-center rounded-full border border-amber-900/40 bg-black/60 text-zinc-200 shadow-lg shadow-black/40 transition hover:border-emerald-500/40 hover:text-emerald-200"
        >
          <svg
            className={`h-4 w-4 transition ${chevronClass}`}
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            viewBox="0 0 24 24"
            aria-hidden
          >
            <path d="m9 18 6-6-6-6" />
          </svg>
        </button>
      </div>
    );
  }

  const collapseButtonPosition =
    side === 'left'
      ? 'right-2 xl:-right-3 top-2'
      : 'left-2 xl:-left-3 top-2';

  return (
    <div className="h-full min-h-0 min-w-0">
      <div className="relative h-full">
        {children}
        <button
          type="button"
          onClick={onToggle}
          aria-label={collapseLabel}
          title={collapseLabel}
          className={`hidden xl:inline-flex absolute z-10 h-7 w-7 items-center justify-center rounded-full border border-amber-900/40 bg-black/70 text-zinc-300 shadow-lg shadow-black/50 transition hover:border-emerald-500/40 hover:text-emerald-200 ${collapseButtonPosition}`}
        >
          <svg
            className={`h-3.5 w-3.5 transition ${chevronClass}`}
            fill="none"
            stroke="currentColor"
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
            viewBox="0 0 24 24"
            aria-hidden
          >
            <path d="m15 18-6-6 6-6" />
          </svg>
        </button>
      </div>
    </div>
  );
}
