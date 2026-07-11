'use client';

import { GameReviewMove } from '@/types';

type ReviewExplanation = NonNullable<GameReviewMove['explanation']>;

type AnalysisPanelProps = {
  currentMove: GameReviewMove | null;
  hasGame: boolean;
  explanation: ReviewExplanation | null;
  isAskingCoach: boolean;
  onAskCoach: () => void;
  moveNumberLabel: string;
};

const CLASSIFICATION_ROW: Record<
  GameReviewMove['classification'],
  { label: string; icon: string; tone: string }
> = {
  book: { label: 'Book', icon: '📘', tone: 'text-sky-300' },
  best: { label: 'Best Move', icon: '⭐', tone: 'text-emerald-300' },
  excellent: { label: 'Excellent', icon: '✨', tone: 'text-teal-300' },
  good: { label: 'Good', icon: '👍', tone: 'text-lime-300' },
  inaccuracy: { label: 'Inaccuracy', icon: '!', tone: 'text-amber-300' },
  mistake: { label: 'Mistake', icon: '?', tone: 'text-orange-300' },
  blunder: { label: 'Blunder', icon: '✗', tone: 'text-rose-300' },
};

export default function AnalysisPanel({
  currentMove,
  hasGame,
  explanation,
  isAskingCoach,
  onAskCoach,
  moveNumberLabel,
}: AnalysisPanelProps) {
  const classificationStyle = currentMove ? CLASSIFICATION_ROW[currentMove.classification] : null;

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      {hasGame && currentMove ? (
        <>
          <section className="rounded-2xl border border-black/40 bg-black/40 p-4 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
            <p className="text-[10px] uppercase tracking-[0.22em] text-white/50">{moveNumberLabel}</p>
            <div className="mt-2 flex items-center gap-2">
              <span className="font-mono text-2xl font-semibold text-white">{currentMove.san}</span>
              {classificationStyle && (
                <span className={`inline-flex items-center gap-1 rounded-full bg-black/40 px-2 py-0.5 text-[10px] font-medium ${classificationStyle.tone}`}>
                  <span aria-hidden>{classificationStyle.icon}</span>
                  {classificationStyle.label}
                </span>
              )}
            </div>
            <p className="mt-2 text-[11px] text-white/50">
              {currentMove.cp_loss} cp loss · {currentMove.color} to move
            </p>
          </section>

          {explanation ? (
            <section className="rounded-2xl border border-[#f7e5c6]/20 bg-black/30 p-4">
              <div className="flex items-center gap-2 text-[#f7e5c6]">
                <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                  <path d="M12 8a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0v-1a3 3 0 0 0-3-3z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <path d="M12 17v4" />
                  <path d="M8 21h8" />
                </svg>
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.22em]">Coach&apos;s Notes</h3>
              </div>
              <p className="mt-3 text-xs leading-6 text-white/80">{explanation.explanation}</p>
            </section>
          ) : (
            <section className="rounded-2xl border border-dashed border-white/10 bg-black/30 p-4">
              <div className="flex items-center gap-2 text-white/70">
                <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.22em]">Coach&apos;s Notes</h3>
              </div>
              <p className="mt-2 text-xs leading-6 text-white/50">
                Ask the coach to break down why this move was played.
              </p>
              <button
                type="button"
                onClick={onAskCoach}
                disabled={isAskingCoach}
                className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl bg-[#10b981] px-3 py-2 text-xs font-semibold text-white shadow-lg shadow-emerald-950/40 transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-zinc-700 disabled:text-zinc-400 disabled:shadow-none"
              >
                {isAskingCoach ? (
                  <>
                    <span className="h-3 w-3 rounded-full border-2 border-white/40 border-t-white animate-spin" />
                    <span>Coach is thinking...</span>
                  </>
                ) : (
                  <>
                    <span>Ask Coach to explain this move</span>
                  </>
                )}
              </button>
            </section>
          )}
        </>
      ) : (
        <section className="rounded-2xl border border-dashed border-white/10 bg-black/30 p-4 text-xs leading-6 text-white/50">
          Per-move analysis will populate here once a game is imported.
        </section>
      )}
    </aside>
  );
}
