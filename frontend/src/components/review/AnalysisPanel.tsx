'use client';

import { useEffect, useRef, useState, type CSSProperties } from 'react';

import { GameReviewMove } from '@/types';

import { ClassificationIcon } from './icons/ClassificationIcon';

type ReviewExplanation = NonNullable<GameReviewMove['explanation']>;

type AnalysisPanelProps = {
  currentMove: GameReviewMove | null;
  hasGame: boolean;
  /** Full mainline rows including the synthetic Start row (for the list). */
  moves?: GameReviewMove[] | null;
  explanation: ReviewExplanation | null;
  coachError?: string | null;
  isAskingCoach: boolean;
  onAskCoach: () => void;
  moveNumberLabel: string;
  activePly: number;
  lastPly: number;
  onPlySelect: (ply: number) => void;
  /** Review mode: pre-move "should have played". Explore mode: suggestion
   *  for the side to move at the current position (next position's best). */
  bestMoveSan: string | null;
  showBestMove: boolean;
  onToggleBestMove: () => void;
  exploreMode?: boolean;
  exploreError?: string | null;
  // True while the played move has no engine snapshot yet: no badge.
  classificationPending?: boolean;
};

const GOLD_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

const SECONDARY_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl border border-white/15 bg-white/5 px-4 py-3.5 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50';

const woodBoxStyle: CSSProperties = {
  borderRadius: '4px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 4px 12px rgba(0,0,0,0.5)',
};
const MOVE_PHRASE: Record<GameReviewMove['classification'], (san: string) => string> = {
  book: (san) => `${san} is a book move`,
  brilliant: (san) => `${san} is brilliant`,
  great: (san) => `${san} is a great move`,
  best: (san) => `${san} is the best move`,
  excellent: (san) => `${san} is excellent`,
  good: (san) => `${san} is good`,
  inaccuracy: (san) => `${san} is an inaccuracy`,
  mistake: (san) => `${san} is a mistake`,
  miss: (san) => `${san} is a miss`,
  blunder: (san) => `${san} is a blunder`,
};

function formatEval(move: GameReviewMove | null): string {
  if (!move || move.san === 'Start') {
    return '';
  }
  const mate = move.eval_mate;
  if (typeof mate === 'number' && Number.isFinite(mate) && mate !== 0) {
    return mate > 0 ? `M${Math.abs(mate)}` : `-M${Math.abs(mate)}`;
  }
  const cp = Number.isFinite(move.eval_cp) ? move.eval_cp : 0;
  const pawns = cp / 100;
  return `${pawns >= 0 ? '+' : ''}${pawns.toFixed(2)}`;
}

/**
 * Chess.com-style list icons: only the decisive moments get a marker, plus
 * the book move that ends the opening sequence. Everything else (best,
 * excellent, good, inaccuracy) renders as plain SAN in the list.
 */
function listIconFor(
  move: GameReviewMove,
  nextMove: GameReviewMove | null,
): GameReviewMove['classification'] | null {
  if (!move || move.san === 'Start') {
    return null;
  }
  switch (move.classification) {
    case 'mistake':
    case 'blunder':
    case 'brilliant':
    case 'great':
    case 'miss':
      return move.classification;
    case 'book':
      if (!nextMove || nextMove.san === 'Start' || nextMove.classification !== 'book') {
        return 'book';
      }
      return null;
    default:
      return null;
  }
}

export default function AnalysisPanel({
  currentMove,
  hasGame,
  moves = null,
  explanation,
  coachError,
  isAskingCoach,
  onAskCoach,
  moveNumberLabel,
  activePly,
  lastPly,
  onPlySelect,
  bestMoveSan,
  showBestMove,
  onToggleBestMove,
  exploreMode = false,
  exploreError = null,
  classificationPending = false,
}: AnalysisPanelProps) {
  const [playing, setPlaying] = useState(false);
  const listRef = useRef<HTMLDivElement | null>(null);
  const activeRowRef = useRef<HTMLDivElement | null>(null);

  const atFirst = activePly <= 0;
  const atLast = activePly >= lastPly;
  const disabledAll = !hasGame;
  const isStart = !currentMove || currentMove.san === 'Start';

  // Auto-play: step forward ~1s per ply until the final move, then stop.
  // Paused while exploring (the list navigates the mainline only).
  useEffect(() => {
    if (!playing || disabledAll || atLast || exploreMode) {
      return;
    }
    const timer = setTimeout(() => {
      const next = Math.min(lastPly, activePly + 1);
      onPlySelect(next);
      if (next >= lastPly) {
        setPlaying(false);
      }
    }, 1000);
    return () => clearTimeout(timer);
  }, [playing, activePly, lastPly, disabledAll, atLast, exploreMode, onPlySelect]);

  // Keep the active move visible in the list.
  useEffect(() => {
    activeRowRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
  }, [activePly]);

  const phrase = (() => {
    if (!hasGame || !currentMove || classificationPending) {
      return null;
    }
    if (currentMove.san === 'Start') {
      return 'Starting position';
    }
    return MOVE_PHRASE[currentMove.classification]?.(currentMove.san) ?? currentMove.san;
  })();
  const evalLabel = formatEval(classificationPending ? null : currentMove);

  const mainline = moves ?? [];
  const pairCount = Math.max(0, Math.ceil(Math.max(0, mainline.length - 1) / 2));
  const mainlineSanAtPly = (ply: number): string | null => mainline[ply]?.san ?? null;
  // In a variation the board SAN differs from the mainline SAN at the same
  // ply: skip the list highlight so it never claims the wrong move.
  const highlightPly =
    currentMove && mainlineSanAtPly(activePly) === currentMove.san ? activePly : -1;
  // No game yet: hide the transport controls entirely - there is nothing to step through.
  const showNav = hasGame && !!currentMove;

  return (
    <aside className="flex h-full min-h-0 min-w-0 flex-col gap-2 overflow-hidden rounded-[24px] border border-black/50 bg-[#1e1c1a] p-4 [background-image:linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.55)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]">
      {!hasGame || !currentMove ? (
        <section className="flex flex-1 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-white/10 bg-black/30 p-6 text-center">
          <p className="text-sm font-semibold text-white/80">No game to review yet</p>
          <p className="max-w-[26ch] text-sm leading-6 text-white/60">
            Import a game on the left to see your overview here, then press Start
            Review for the move-by-move breakdown.
          </p>
        </section>
      ) : (
        <>
          {/* Verdict card: walnut inset, cream text, gold eval */}
          <section className="shrink-0 rounded-xl border border-[#f7e5c6]/15 bg-black/40 p-3 [box-shadow:inset_0_1px_0_rgba(255,255,255,0.06)]">
            <div className="flex items-center justify-between gap-2">
              <div className="flex min-w-0 items-center gap-2">
                {!classificationPending && !isStart && (
                  <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center">
                    <ClassificationIcon classification={currentMove.classification} size={24} />
                  </span>
                )}
                <p className="truncate text-[15px] font-medium text-[#f7e5c6]">
                  {classificationPending ? (
                    <span className="inline-flex items-center gap-2 text-white/60">
                      <span className="h-3 w-3 animate-pulse rounded-full border border-white/30" />
                      Analyzing…
                    </span>
                  ) : (
                    (phrase ?? moveNumberLabel)
                  )}
                </p>
              </div>
              {evalLabel && (
                <span className="shrink-0 rounded-md bg-[#eacb90]/15 px-2 py-1 font-mono text-[13px] font-bold text-[#eacb90] ring-1 ring-[#eacb90]/30">
                  {evalLabel}
                </span>
              )}
            </div>
            <p className="mt-0.5 truncate text-[11px] text-white/40">{moveNumberLabel}</p>

            {/* Coach explanation lives inside the verdict card when present */}
            {!exploreMode && (explanation || isAskingCoach) && (
              <div className="mt-2 border-t border-white/10 pt-2">
                {isAskingCoach && !explanation ? (
                  <p className="flex items-center gap-2 text-xs text-white/60">
                    <span className="h-3 w-3 animate-spin rounded-full border-2 border-white/20 border-t-white" />
                    Coach is thinking…
                  </p>
                ) : explanation ? (
                  <p className="text-xs leading-5 text-white/75">{explanation.explanation}</p>
                ) : null}
              </div>
            )}
          </section>

          {/* Explain / Best move */}
          <div className="grid shrink-0 grid-cols-2 gap-3">
            <button
              type="button"
              onClick={onAskCoach}
              disabled={isAskingCoach || atFirst || exploreMode || disabledAll}
              title={
                exploreMode
                  ? 'Exit explore to ask the coach'
                  : atFirst
                    ? 'Advance to a move first'
                    : undefined
              }
              className={SECONDARY_BUTTON_CLASS}
            >
              {isAskingCoach ? (
                <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
              ) : null}
              <span>{isAskingCoach ? 'Thinking…' : 'Explain'}</span>
            </button>
            <button
              type="button"
              onClick={onToggleBestMove}
              disabled={disabledAll || classificationPending || !bestMoveSan}
              className={GOLD_BUTTON_CLASS}
              title={
                exploreMode
                  ? 'Best move for the side to move in this position'
                  : 'Best move that should have been played instead'
              }
            >
              <span>
                {!bestMoveSan || classificationPending
                  ? 'Best move'
                  : showBestMove
                    ? `Hide (${bestMoveSan})`
                    : `Best: ${bestMoveSan}`}
              </span>
            </button>
          </div>

          {(exploreError || coachError) && !exploreMode && coachError && (
            <p className="shrink-0 text-xs leading-5 text-amber-300/90">{coachError}</p>
          )}
          {exploreMode && exploreError && (
            <p className="shrink-0 text-xs leading-5 text-amber-300/90">{exploreError}</p>
          )}

          {/* Move list with filtered icons */}
          <div
            ref={listRef}
            className="wood-scrollbar min-h-0 flex-1 overflow-y-auto rounded-xl border border-black/40 bg-black/40 p-1.5"
            aria-label="Move list"
          >
            {pairCount === 0 ? (
              <p className="p-3 text-xs text-white/40">No moves yet.</p>
            ) : (
              Array.from({ length: pairCount }, (_, rowIndex) => {
                const moveNo = rowIndex + 1;
                const whitePly = rowIndex * 2 + 1;
                const blackPly = rowIndex * 2 + 2;
                const white = mainline[whitePly] ?? null;
                const black = blackPly < mainline.length ? (mainline[blackPly] ?? null) : null;
                const isActiveRow = highlightPly === whitePly || highlightPly === blackPly;
                return (
                  <div
                    key={moveNo}
                    ref={isActiveRow ? activeRowRef : undefined}
                    className={`grid grid-cols-[2rem_minmax(0,1fr)_minmax(0,1fr)] items-stretch gap-1 rounded-lg px-1 py-0.5 ${
                      isActiveRow ? 'bg-white/[0.04]' : ''
                    }`}
                  >
                    <span className="flex items-center justify-center font-mono text-xs text-white/40">
                      {moveNo}.
                    </span>
                    <MoveCell
                      move={white}
                      ply={whitePly}
                      nextMove={mainline[whitePly + 1] ?? null}
                      isActive={highlightPly === whitePly}
                      onSelect={onPlySelect}
                      disabled={disabledAll}
                    />
                    {black ? (
                      <MoveCell
                        move={black}
                        ply={blackPly}
                        nextMove={mainline[blackPly + 1] ?? null}
                        isActive={highlightPly === blackPly}
                        onSelect={onPlySelect}
                        disabled={disabledAll}
                      />
                    ) : (
                      <span />
                    )}
                  </div>
                );
              })
            )}
          </div>
        </>
      )}

      {/* Movement buttons: |< < play/pause > >| - wooden boxes matching the board.
          Hidden until a game exists; there is nothing to step through before that. */}
      {showNav && (
      <div className="grid shrink-0 grid-cols-5 gap-1.5">
        <NavButton
          label="First move"
          disabled={disabledAll || atFirst}
          onClick={() => onPlySelect(0)}
          cornerStyle={{ borderRadius: '4px 4px 4px 24px' }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <rect x="3" y="4" width="2.5" height="16" rx="1" />
            <path d="M21 4 L9 12 L21 20 Z" />
          </svg>
        </NavButton>
        <NavButton
          label="Previous move"
          disabled={disabledAll || atFirst}
          onClick={() => onPlySelect(Math.max(0, activePly - 1))}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <path d="M18 4 L6 12 L18 20 Z" />
          </svg>
        </NavButton>
        <button
          type="button"
          onClick={() => setPlaying((value) => !value)}
          disabled={disabledAll || atLast || exploreMode}
          aria-label={playing ? 'Pause auto-play' : 'Auto-play moves'}
          title={exploreMode ? 'Exit explore to auto-play' : undefined}
          className="flex h-8 items-center justify-center transition-transform hover:scale-105 active:scale-95 disabled:pointer-events-none disabled:opacity-40"
          style={{ cursor: disabledAll || atLast || exploreMode ? 'default' : 'pointer', ...woodBoxStyle }}
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
          disabled={disabledAll || atLast}
          onClick={() => onPlySelect(Math.min(lastPly, activePly + 1))}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <path d="M6 4 L18 12 L6 20 Z" />
          </svg>
        </NavButton>
        <NavButton
          label="Last move"
          disabled={disabledAll || atLast}
          onClick={() => onPlySelect(lastPly)}
          cornerStyle={{ borderRadius: '4px 4px 24px 4px' }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="#f0e0c0" aria-hidden>
            <path d="M3 4 L15 12 L3 20 Z" />
            <rect x="18.5" y="4" width="2.5" height="16" rx="1" />
          </svg>
        </NavButton>
      </div>
      )}
    </aside>
  );
}

function MoveCell({
  move,
  ply,
  nextMove,
  isActive,
  onSelect,
  disabled,
}: {
  move: GameReviewMove | null;
  ply: number;
  nextMove: GameReviewMove | null;
  isActive: boolean;
  onSelect: (ply: number) => void;
  disabled: boolean;
}) {
  if (!move) {
    return <span />;
  }
  const icon = listIconFor(move, nextMove);
  return (
    <button
      type="button"
      onClick={() => onSelect(ply)}
      disabled={disabled}
      className={`flex min-w-0 items-center gap-1 rounded-md px-1.5 py-1.5 font-mono text-[13px] transition ${
        isActive ? 'bg-white/15 font-bold text-white' : 'text-white/80 hover:bg-white/10'
      } disabled:cursor-default`}
      aria-current={isActive ? 'true' : undefined}
      title={`${ply}: ${move.san}`}
    >
      {icon && (
        <span className="inline-flex h-4 w-4 shrink-0 items-center justify-center">
          <ClassificationIcon classification={icon} size={16} />
        </span>
      )}
      <span className="truncate">{move.san}</span>
    </button>
  );
}

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
      style={{ cursor: disabled ? 'default' : 'pointer', ...woodBoxStyle, ...cornerStyle }}
    >
      {children}
    </button>
  );
}
