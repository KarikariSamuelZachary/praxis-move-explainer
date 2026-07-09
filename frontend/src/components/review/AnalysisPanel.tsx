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
  plyIndex: number;
  totalMoves: number;
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
  plyIndex,
  totalMoves,
}: AnalysisPanelProps) {
  const cpLoss = currentMove?.cp_loss ?? 0;
  const classificationStyle = currentMove ? CLASSIFICATION_ROW[currentMove.classification] : null;

  return (
    <aside className="flex h-full flex-col gap-4 overflow-y-auto rounded-[24px] border border-black/50 p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.png)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      <header className="flex items-center justify-between">
        <h2 className="text-sm font-semibold tracking-wide text-emerald-300">AI Analysis</h2>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-amber-900/40 bg-black/40 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-amber-200">
          Stockfish 16
        </span>
      </header>

      <section className="rounded-2xl border border-black/40 bg-black/40 p-4 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
        <div className="flex items-baseline justify-between">
          <span className="text-[10px] uppercase tracking-[0.22em] text-zinc-500">Evaluation</span>
          {hasGame && (
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">
              {plyIndex + 1} / {totalMoves}
            </span>
          )}
        </div>
        <p className={`mt-1 text-3xl font-semibold tracking-tight ${hasGame ? 'text-zinc-100' : 'text-zinc-600'}`}>
          {hasGame ? formatEval(cpLoss) : '—'}
        </p>
        <EvalGraph currentPly={hasGame ? plyIndex : -1} totalPlies={hasGame ? totalMoves : 0} />
      </section>

      {hasGame && currentMove ? (
        <>
          <section className="rounded-2xl border border-black/40 bg-black/40 p-4 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
            <p className="text-[10px] uppercase tracking-[0.22em] text-zinc-500">{moveNumberLabel}</p>
            <div className="mt-2 flex items-center gap-2">
              <span className="font-mono text-2xl font-semibold text-zinc-100">{currentMove.san}</span>
              {classificationStyle && (
                <span className={`inline-flex items-center gap-1 rounded-full bg-black/40 px-2 py-0.5 text-[10px] font-medium ${classificationStyle.tone}`}>
                  <span aria-hidden>{classificationStyle.icon}</span>
                  {classificationStyle.label}
                </span>
              )}
            </div>
            <p className="mt-2 text-[11px] text-zinc-500">
              {currentMove.cp_loss} cp loss · {currentMove.color} to move
            </p>
          </section>

          <section className="rounded-2xl border border-black/40 bg-black/40 p-4 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
            <p className="text-[10px] uppercase tracking-[0.22em] text-zinc-500">Best Move</p>
            <p className="mt-2 inline-flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1 font-mono text-sm text-emerald-200">
              <span aria-hidden>♞</span>
              {bestMovePlaceholder(currentMove)}
              <span className="ml-auto text-[10px] text-emerald-300/70">+0.00</span>
            </p>
            <p className="mt-3 text-[10px] uppercase tracking-[0.22em] text-zinc-500">Why this move?</p>
            <p className="mt-1 text-xs leading-6 text-zinc-300">
              Best-move reasoning will appear here once per-position engine data is wired up.
            </p>
          </section>

          {explanation ? (
            <section className="rounded-2xl border border-indigo-500/30 bg-indigo-500/10 p-4">
              <div className="flex items-center gap-2 text-indigo-200">
                <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                  <path d="M12 8a3 3 0 0 0-3 3v1a3 3 0 0 0 6 0v-1a3 3 0 0 0-3-3z" />
                  <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
                  <path d="M12 17v4" />
                  <path d="M8 21h8" />
                </svg>
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.22em]">Coach&apos;s Notes</h3>
              </div>
              <p className="mt-3 text-xs leading-6 text-indigo-50/95">{explanation.explanation}</p>
              {explanation.concept && (
                <span className="mt-3 inline-flex items-center gap-1 rounded-full border border-indigo-400/20 bg-black/30 px-2 py-0.5 text-[10px] text-indigo-100">
                  <span>Concept</span>
                  <span className="font-medium">{explanation.concept}</span>
                </span>
              )}
              {explanation.tip && (
                <p className="mt-3 rounded-xl border border-black/40 bg-black/30 px-3 py-2 text-xs text-zinc-200">
                  {explanation.tip}
                </p>
              )}
            </section>
          ) : (
            <section className="rounded-2xl border border-dashed border-amber-900/40 bg-black/30 p-4">
              <div className="flex items-center gap-2 text-zinc-300">
                <svg className="h-4 w-4" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                  <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
                </svg>
                <h3 className="text-[11px] font-semibold uppercase tracking-[0.22em]">Coach&apos;s Notes</h3>
              </div>
              <p className="mt-2 text-xs leading-6 text-zinc-400">
                Ask the coach to break down why this move was played.
              </p>
              <button
                type="button"
                onClick={onAskCoach}
                disabled={isAskingCoach}
                className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-xl border border-sky-500/30 bg-sky-500/10 px-3 py-2 text-xs font-medium text-sky-200 transition hover:border-sky-400 hover:bg-sky-500/20 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isAskingCoach ? (
                  <>
                    <span className="h-3 w-3 rounded-full border-2 border-sky-300/40 border-t-sky-200 animate-spin" />
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

          <section className="rounded-2xl border border-black/40 bg-black/40 p-4 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.05)]">
            <p className="text-[10px] uppercase tracking-[0.22em] text-zinc-500">Game Quality</p>
            <div className="mt-2 flex items-center gap-3">
              <QualityRing score={mockQualityScore(currentMove.classification)} />
              <p className="text-xs leading-5 text-zinc-300">
                {qualityLabel(currentMove.classification)}
              </p>
            </div>
            <button
              type="button"
              disabled
              className="mt-3 inline-flex w-full items-center justify-center gap-1.5 rounded-xl border border-black/40 bg-black/30 px-3 py-1.5 text-[11px] text-zinc-400 disabled:opacity-60"
            >
              <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" viewBox="0 0 24 24" aria-hidden>
                <path d="M3 6h18" />
                <path d="M3 12h18" />
                <path d="M3 18h18" />
              </svg>
              Show Engine Lines
            </button>
          </section>
        </>
      ) : (
        <section className="rounded-2xl border border-dashed border-amber-900/40 bg-black/30 p-4 text-xs leading-6 text-zinc-500">
          Per-move analysis will populate here once a game is imported.
        </section>
      )}
    </aside>
  );
}

function formatEval(cpLoss: number): string {
  if (cpLoss === 0) {
    return '+0.00';
  }
  const sign = cpLoss > 0 ? '+' : '-';
  return `${sign}${(cpLoss / 100).toFixed(2)}`;
}

function bestMovePlaceholder(move: GameReviewMove): string {
  return `analysis pending (${move.san})`;
}

function mockQualityScore(classification: GameReviewMove['classification']): number {
  const map: Record<GameReviewMove['classification'], number> = {
    book: 95,
    best: 98,
    excellent: 92,
    good: 85,
    inaccuracy: 72,
    mistake: 58,
    blunder: 40,
  };
  return map[classification];
}

function qualityLabel(classification: GameReviewMove['classification']): string {
  const map: Record<GameReviewMove['classification'], string> = {
    book: 'This is a strong position. Keep playing the book moves.',
    best: 'This is a strong game. You played well and had good control of the position.',
    excellent: 'An excellent move. The position remains in your favor.',
    good: 'A solid move. Small refinement possible.',
    inaccuracy: 'This move is imprecise. A better option was available.',
    mistake: 'A clear mistake. Consider alternatives on the next review.',
    blunder: 'A critical error. The position shifts decisively against you.',
  };
  return map[classification];
}

function QualityRing({ score }: { score: number }) {
  const radius = 22;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference - (score / 100) * circumference;
  const color = score >= 80 ? '#10b981' : score >= 60 ? '#facc15' : '#f87171';

  return (
    <div className="relative flex h-14 w-14 shrink-0 items-center justify-center">
      <svg className="h-14 w-14 -rotate-90" viewBox="0 0 56 56" aria-hidden>
        <circle cx="28" cy="28" r={radius} stroke="rgba(255,255,255,0.08)" strokeWidth="6" fill="none" />
        <circle
          cx="28"
          cy="28"
          r={radius}
          stroke={color}
          strokeWidth="6"
          fill="none"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          style={{ transition: 'stroke-dashoffset 200ms ease-out' }}
        />
      </svg>
      <span className="absolute text-sm font-semibold text-zinc-100">{score}</span>
    </div>
  );
}

function EvalGraph({ currentPly, totalPlies }: { currentPly: number; totalPlies: number }) {
  const width = 240;
  const height = 48;
  if (totalPlies <= 0) {
    return (
      <div className="mt-3 h-12 rounded-xl border border-black/40 bg-black/30" aria-hidden />
    );
  }

  const path = Array.from({ length: totalPlies }, (_, i) => {
    const ratio = i / Math.max(totalPlies - 1, 1);
    const swing = Math.sin(ratio * Math.PI * 2) * 0.4;
    const y = height / 2 - swing * (height / 2 - 4);
    const x = (ratio * (width - 4)) + 2;
    return `${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
  }).join(' ');

  const cursorX = totalPlies > 1
    ? (currentPly / (totalPlies - 1)) * (width - 4) + 2
    : width / 2;

  return (
    <svg className="mt-3 h-12 w-full" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Position evaluation over time">
      <path d={path} fill="none" stroke="#10b981" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" opacity="0.6" />
      <line x1={cursorX} x2={cursorX} y1={0} y2={height} stroke="rgba(255,255,255,0.3)" strokeDasharray="2 3" strokeWidth="1" />
    </svg>
  );
}
