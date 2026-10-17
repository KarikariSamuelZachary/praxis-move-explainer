'use client';

import dynamic from 'next/dynamic';

import type { CalcWhyResult } from '@/types';

// Same dynamic-import contract as the other trainer boards
// (react-chessboard is client-only).
const ReplayBoard = dynamic(
  () => import('@/components/board/WhyReplayBoard'),
  {
    ssr: false,
    loading: () => (
      <div className="flex aspect-square w-full animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40">
        <span className="text-xs text-white/50">Loading replay…</span>
      </div>
    ),
  }
);

/**
 * Backend-driven explanation rendering: the templated headline and the
 * engine line on a replay board. No LLM text anywhere. Shared by the
 * calculation trainer (solve attempts) and the woodpecker calculation
 * tab (review attempts) so both verdicts read identically.
 *
 * `compact` tightens the headline for the review tab's side column.
 */
export default function WhyResult({
  result,
  fenBefore,
  compact = false,
}: {
  result: CalcWhyResult;
  fenBefore: string;
  compact?: boolean;
}) {
  const headlineClass = compact
    ? 'font-display text-[16px] font-semibold leading-[1.3] text-[#f7e5c6]'
    : 'font-display text-[22px] font-semibold leading-[1.25] text-[#f7e5c6]';
  const mateBefore = result.params['mate_before'];
  const mateAfter = result.params['mate_after'];
  // Defender divergences need no engine and carry no evals: the headline
  // names the refutation and the stored replies play on the main board
  // (started by the caller), so there is nothing to render here.
  if (result.type === 'defender_misjudged') {
    return (
      <div className="mt-2">
        <h3 className={headlineClass}>{result.headline}</h3>
      </div>
    );
  }
  return (
    <div className="mt-2">
      <h3 className={headlineClass}>{result.headline}</h3>
      {result.type === 'slower_win' &&
        typeof mateBefore === 'number' &&
        typeof mateAfter === 'number' && (
          <p className="mt-1 text-xs text-white/60">
            Mate in {mateBefore} → mate in {mateAfter}.
          </p>
        )}
      {result.type === 'also_fine' && (
        <p className="mt-1 text-xs text-white/60">
          The trainer asked for the main line, but your move keeps the advantage.
        </p>
      )}
      {result.line_uci.length > 0 && (
        <div className="mt-3">
          <ReplayBoard fenBefore={fenBefore} lineUci={result.line_uci} />
        </div>
      )}
    </div>
  );
}
