'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';

import {
  EndgameFetchError,
  EndgamePlayoutStatus,
  fetchNextEndgamePosition,
  fetchNextEndgamePracticePosition,
  practiceCategoryLabel,
  submitEndgamePracticeMove,
  type EndgameSubmitMove,
} from '@/lib/endgames';
import DrillStatusPanel from '@/components/endgames/DrillStatusPanel';
import PracticeCategoriesCard, {
  type PracticeCategoriesCardProps,
} from '@/components/endgames/PracticeCategoriesCard';
import WoodpeckerPromoCard from '@/components/endgames/WoodpeckerPromoCard';
import { WOOD_PANEL_CLASS, WOOD_PANEL_STYLE } from '@/lib/woodPanel';
import type { EndgameBoardProps } from '@/components/board/EndgameBoard';
import { EndgameMoveResponse, EndgamePosition } from '@/types';

// Same dynamic-import contract as the Puzzles board (react-chessboard is
// client-only). Typed for the trainer's move response so the resolved
// panel can read `rating` / `review_capture`.
const EndgameBoard = dynamic<EndgameBoardProps<EndgameMoveResponse>>(
  () => import('@/components/board/EndgameBoard'),
  {
    ssr: false,
    loading: () => (
      <div className="flex aspect-square w-full max-w-[700px] animate-pulse items-center justify-center rounded-lg border border-white/10 bg-black/40 backdrop-blur-sm">
        <span className="text-white/60">Loading board...</span>
      </div>
    ),
  }
);

// The exact walnut card language of the Puzzles page.
const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'ready' }
  | { kind: 'empty'; message: string }
  | { kind: 'error'; message: string };

function NextIcon() {
  return (
    <svg
      className="h-5 w-5"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="2"
      viewBox="0 0 24 24"
    >
      <path d="M5 12h14" />
      <path d="m12 5 7 7-7 7" />
    </svg>
  );
}

/** Which pool the current drill is drawn from: the rated loop, or one
 * practice category. `source` is 'rated' or a category key. */
type DrillSource = 'rated' | string;

/**
 * The Endgame Trainer.
 *
 * One page, two drill sources sharing one layout: the rated loop (null) and
 * any practice category. Picking a category in the left card swaps the board
 * and panel to that category's session right here -- no route change; the
 * practice session's grading goes through the practice endpoint (nothing
 * rated or queued) while the rated loop keeps writing the rating. The gold
 * highlight marks the active row, and "Rated drills" in the card header
 * returns to the loop.
 *
 * Layout contract: on desktop (xl and up) the page is sized to the viewport
 * and never scrolls. Only the category list inside the left card scrolls --
 * it owns `min-h-0`/`flex-1` inside that card, so it takes whatever height is
 * left under the card's heading. The board follows the game review page's
 * exact contract (fill the middle column, capped at calc(100vh-70px), no
 * page max-width), so the two boards render the same on-screen size and a
 * short laptop screen shrinks the board instead of pushing the columns
 * off-screen. The right column keeps an invisible scroll fallback for the
 * resolved drill panel, whose failure copy and move list can still outgrow a
 * very short viewport. Below xl the three columns stack and the page scrolls
 * normally.
 */
export default function EndgamesPage() {
  const [position, setPosition] = useState<EndgamePosition | null>(null);
  const [loadState, setLoadState] = useState<LoadState>({ kind: 'loading' });
  const [nextError, setNextError] = useState<string | null>(null);
  const [isAdvancing, setIsAdvancing] = useState(false);

  // Drill source: null = the rated loop; a category key = that category's
  // practice session, graded by the practice endpoint on this same page.
  const [practiceCategory, setPracticeCategory] = useState<string | null>(null);
  const [isSwitching, setIsSwitching] = useState(false);

  const [result, setResult] = useState<EndgameMoveResponse | null>(null);
  const [playout, setPlayout] = useState<EndgamePlayoutStatus | null>(null);
  // The two assists: how many hints/reveals this attempt has used (sent with
  // every move; the server reads it when the drill resolves), plus one
  // request counter per button so repeat presses are distinct.
  const [hintsUsed, setHintsUsed] = useState(0);
  const [hintRequest, setHintRequest] = useState(0);
  const [solutionRequest, setSolutionRequest] = useState(0);
  // True while the current attempt is a "Retry" replay: its graded moves
  // still resolve the drill, but the backend writes no rating for them.
  const [isRetry, setIsRetry] = useState(false);
  const [isThinking, setIsThinking] = useState(false);

  const [rating, setRating] = useState<number | null>(null);
  const [ratingChange, setRatingChange] = useState<number | null>(null);

  // Random draws (practice especially) can repeat a position; the per-drill
  // key remounts the board so a repeat never inherits prior state.
  const [drillKey, setDrillKey] = useState(0);

  const bufferedPositionRef = useRef<EndgamePosition | null>(null);
  const bufferedSourceRef = useRef<DrillSource | null>(null);
  const prefetchingRef = useRef(false);
  // Guards the async draws: a fetch that resolves after the user has moved
  // on (another category, a newer draw) must never start its drill.
  const loadTokenRef = useRef(0);

  const startDrill = useCallback((next: EndgamePosition) => {
    setPosition(next);
    setResult(null);
    setPlayout(null);
    setHintsUsed(0);
    setHintRequest(0);
    setSolutionRequest(0);
    setIsRetry(false);
    setIsThinking(false);
    setNextError(null);
    setDrillKey((key) => key + 1);
    setLoadState({ kind: 'ready' });
  }, []);

  /** "Retry": replay the same position as a fresh, UNRATED attempt. */
  const handleRetry = useCallback(() => {
    if (!position) return;
    startDrill(position);
    // After startDrill's reset: the retry attempt is unrated because the
    // drill's first resolution already moved the rating.
    setIsRetry(true);
  }, [position, startDrill]);

  const prefetchNext = useCallback(async (source: DrillSource) => {
    if (prefetchingRef.current || bufferedPositionRef.current) return;
    prefetchingRef.current = true;
    try {
      bufferedPositionRef.current =
        source === 'rated'
          ? await fetchNextEndgamePosition()
          : await fetchNextEndgamePracticePosition(source);
      bufferedSourceRef.current = source;
    } catch {
      // Silent: the Next click retries and surfaces any error then.
    } finally {
      prefetchingRef.current = false;
    }
  }, []);

  useEffect(() => {
    const token = ++loadTokenRef.current;

    fetch('/api/user/rating')
      .then((response) => (response.ok ? response.json() : null))
      .then((data) => {
        if (data?.endgame_trainer_rating != null) {
          setRating(data.endgame_trainer_rating);
        }
      })
      .catch((error) => console.error('Failed to fetch endgame rating:', error));

    (async () => {
      // A "Recommended For You" deep link (?category=...) opens that
      // practice session directly. An unknown or empty category falls
      // through to the rated loop rather than erroring the whole page.
      const requestedCategory = new URLSearchParams(
        window.location.search
      ).get('category');
      if (requestedCategory) {
        try {
          const first =
            await fetchNextEndgamePracticePosition(requestedCategory);
          if (token !== loadTokenRef.current) return;
          setPracticeCategory(requestedCategory);
          startDrill(first);
          void prefetchNext(requestedCategory);
          return;
        } catch {
          // Bad deep link: fall through to the rated loop below.
        }
      }

      try {
        const first = await fetchNextEndgamePosition();
        if (token !== loadTokenRef.current) return;
        startDrill(first);
        void prefetchNext('rated');
      } catch (error) {
        if (token !== loadTokenRef.current) return;
        const fetchError = error as EndgameFetchError;
        if (fetchError.status === 404) {
          setLoadState({ kind: 'empty', message: fetchError.message });
        } else {
          setLoadState({ kind: 'error', message: fetchError.message });
        }
      }
    })();
  }, [prefetchNext, startDrill]);

  /** Swap the board's drill source without leaving the page: a category key
   * opens that category's practice session, 'rated' returns to the loop.
   * The current drill stays on the board (under a brief overlay) until the
   * new position is in hand, then everything resets together. */
  const switchSession = useCallback(
    async (source: DrillSource) => {
      const token = ++loadTokenRef.current;
      setIsSwitching(true);
      setNextError(null);
      try {
        const next =
          source === 'rated'
            ? await fetchNextEndgamePosition()
            : await fetchNextEndgamePracticePosition(source);
        if (token !== loadTokenRef.current) return;
        setPracticeCategory(source === 'rated' ? null : source);
        startDrill(next);
        void prefetchNext(source);
      } catch (error) {
        if (token !== loadTokenRef.current) return;
        const fetchError =
          error instanceof EndgameFetchError
            ? error
            : new EndgameFetchError('Could not load a position.', 0, true);
        setNextError(
          fetchError.status === 404
            ? (source === 'rated'
                ? 'No drills are available in your rating range right now.'
                : 'No positions are available in this category right now.')
            : fetchError.message
        );
      } finally {
        if (token === loadTokenRef.current) setIsSwitching(false);
      }
    },
    [prefetchNext, startDrill]
  );

  const handleNextDrill = useCallback(async () => {
    if (isAdvancing) return;
    const source: DrillSource = practiceCategory ?? 'rated';
    const token = ++loadTokenRef.current;
    setIsAdvancing(true);
    setNextError(null);
    try {
      // Only a buffer drawn for THIS source is usable (a prefetch started
      // before a switch can still be in flight).
      const buffered =
        bufferedSourceRef.current === source
          ? bufferedPositionRef.current
          : null;
      bufferedPositionRef.current = null;
      bufferedSourceRef.current = null;
      const next =
        buffered ??
        (source === 'rated'
          ? await fetchNextEndgamePosition()
          : await fetchNextEndgamePracticePosition(source));
      if (token !== loadTokenRef.current) return;
      startDrill(next);
      void prefetchNext(source);
    } catch (error) {
      if (token !== loadTokenRef.current) return;
      const fetchError =
        error instanceof EndgameFetchError
          ? error
          : new EndgameFetchError('Could not load the next drill.', 0, true);
      setNextError(
        fetchError.status === 404
          ? (source === 'rated'
              ? 'No drills are available in your rating range right now.'
              : 'No more positions are available in this category right now.')
          : fetchError.message
      );
    } finally {
      setIsAdvancing(false);
    }
  }, [isAdvancing, practiceCategory, prefetchNext, startDrill]);

  const handleHintRevealed = useCallback(() => {
    // Counted only on a successful reveal, from either assist ("Hint" or
    // "Show move"). From here every submission carries the running count; the
    // rated route neutralizes the rating on a hint-assisted solve.
    setHintsUsed((count) => count + 1);
  }, []);

  const handleThinkingChange = useCallback((thinking: boolean) => {
    setIsThinking(thinking);
  }, []);

  const handleDrillResolved = useCallback(
    (resolved: EndgameMoveResponse) => {
      setResult(resolved);
      // Practice writes nothing: only a rated drill moves the rating.
      if (!practiceCategory && resolved.rating) {
        setRating(resolved.rating.new_rating);
        setRatingChange(resolved.rating.change);
      }
    },
    [practiceCategory]
  );

  // Practice grades through its own endpoint (nothing recorded); the rated
  // loop keeps the board's default submitter.
  const practiceSubmitMove = useCallback<
    EndgameSubmitMove<EndgameMoveResponse>
  >(
    (submission) => {
      if (!position) {
        return Promise.reject(new Error('No practice position is loaded.'));
      }
      return submitEndgamePracticeMove({
        position_id: position.id,
        ...submission,
      });
    },
    [position]
  );

  const onCategorySelect = useCallback<PracticeCategoriesCardProps['onSelect']>(
    (category) => {
      void switchSession(category);
    },
    [switchSession]
  );

  const onExitPractice = useCallback(() => {
    void switchSession('rated');
  }, [switchSession]);

  return (
    <div className="min-h-[calc(100vh-2.5rem)] -mt-2 text-white [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center] xl:h-[calc(100vh-2.5rem)] xl:overflow-hidden">
      <div className="mx-auto flex flex-col px-6 pb-1 lg:px-10 xl:h-full">
        {loadState.kind === 'loading' ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} px-10 py-8 text-center shadow-2xl shadow-black/30`}>
              <div className="mx-auto mb-4 h-9 w-9 animate-spin rounded-full border-2 border-[#10b981] border-t-transparent" />
              <p className="text-white/60">Loading endgame drills...</p>
            </div>
          </div>
        ) : loadState.kind === 'empty' || loadState.kind === 'error' || !position ? (
          <div className="flex h-[70vh] items-center justify-center">
            <div className={`${CARD_CLASS} max-w-md px-8 py-6 text-center`}>
              <p className="text-white/70">
                {loadState.kind === 'empty' || loadState.kind === 'error'
                  ? loadState.message
                  : 'No drill loaded.'}
              </p>
              <button
                type="button"
                onClick={() => void switchSession(practiceCategory ?? 'rated')}
                disabled={isSwitching}
                className="mt-4 w-full rounded-lg border border-white/20 bg-white/5 px-4 py-2 text-sm font-semibold text-white transition hover:bg-white/10 disabled:opacity-60"
              >
                {isSwitching ? 'Loading…' : 'Try again'}
              </button>
            </div>
          </div>
        ) : (
          <div className="grid gap-6 xl:min-h-0 xl:flex-1 xl:grid-cols-[20rem_minmax(0,1fr)_22rem] xl:pt-5">
            {/* ============ LEFT: RATING + CATEGORY LIST ============ */}
            <section className="order-2 mt-6 flex min-h-0 flex-col gap-5 xl:order-none xl:mt-0">
              <div
                className={`${WOOD_PANEL_CLASS} flex shrink-0 items-center gap-4 p-5`}
                style={WOOD_PANEL_STYLE}
              >
                {/* The badge is decoration: the rating itself is text. */}
                <img
                  src="/knight-badge.webp"
                  alt=""
                  width={402}
                  height={454}
                  className="h-16 w-auto shrink-0 drop-shadow-[0_8px_18px_rgba(0,0,0,0.6)]"
                />
                <div className="min-w-0 flex-1">
                  <div className="text-[11px] font-bold uppercase tracking-[0.25em] text-white/40">
                    Endgame Rating
                  </div>
                  <div className="mt-1.5 flex items-end gap-2.5">
                    <span className="font-display text-[34px] font-semibold leading-none text-[#f7e5c6]">
                      {rating ?? '—'}
                    </span>
                    {rating != null && ratingChange != null && (
                      <span
                        className={`mb-0.5 rounded-full px-2 py-0.5 text-xs font-bold ${
                          ratingChange === 0
                            ? 'bg-white/10 text-white/60'
                            : ratingChange > 0
                              ? 'bg-[#10b981]/15 text-[#10b981]'
                              : 'bg-red-500/15 text-red-300'
                        }`}
                      >
                        {ratingChange === 0
                          ? '±0'
                          : `${ratingChange > 0 ? '+' : ''}${ratingChange}`}
                      </span>
                    )}
                  </div>
                  {rating == null && (
                    <p className="mt-2 text-[11px] leading-4 text-white/40">
                      Unrated — your first resolved drill sets it.
                    </p>
                  )}
                </div>
              </div>

              {/* The category list: the page's one scroll region, and the
                  switcher for the board's drill source. */}
              <PracticeCategoriesCard
                activeCategory={practiceCategory}
                onSelect={onCategorySelect}
                onExitPractice={onExitPractice}
              />
            </section>

            {/* ============ CENTER: CHESSBOARD ============ */}
            <section className="order-1 min-h-0 min-w-0 xl:order-none xl:mt-0">
              {/* Same sizing contract as the game review page (BoardPanel):
                  fill the column, capped at calc(100vh-70px) so the square
                  plus its frame always fits the viewport height. */}
              <div className="relative mx-auto mt-6 aspect-square w-full max-w-[calc(100vh-70px)] xl:mt-0">
                <EndgameBoard
                  key={`${position.id}:${drillKey}`}
                  position={position}
                  submitMove={
                    practiceCategory ? practiceSubmitMove : undefined
                  }
                  playout={playout?.state === 'active'}
                  onPlayoutResolved={() => setPlayout({ state: 'done' })}
                  hintsUsed={hintsUsed}
                  retry={isRetry}
                  hintRequest={hintRequest}
                  solutionRequest={solutionRequest}
                  onHintRevealed={handleHintRevealed}
                  onThinkingChange={handleThinkingChange}
                  onDrillResolved={handleDrillResolved}
                />

                {/* Source swap: the old drill stays put under a brief
                    overlay until the new one is in hand. */}
                {isSwitching && (
                  <div
                    data-switch-overlay
                    className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/55 backdrop-blur-[2px]"
                  >
                    <div className="flex items-center gap-3 rounded-xl border border-white/10 bg-black/70 px-5 py-3 text-sm text-white/80">
                      <span className="h-4 w-4 animate-spin rounded-full border-2 border-[#d9b87c] border-t-transparent" />
                      {practiceCategory
                        ? `Loading ${practiceCategoryLabel(practiceCategory)} drills…`
                        : 'Loading rated drills…'}
                    </div>
                  </div>
                )}
              </div>
            </section>

            {/* ============ RIGHT: STATUS + ACTION ============ */}
            <section className="order-3 mx-auto flex w-full max-w-[420px] flex-col gap-5 xl:order-none xl:mx-0 xl:mt-0 xl:max-w-none xl:min-h-0">
              {/* Invisible scroll fallback: only the tall resolved states on
                  a short screen ever need it; the Next control below stays
                  pinned either way. */}
              <div className="wooden-scroll xl:min-h-0 xl:overflow-y-auto">
                <DrillStatusPanel
                  key={`${position.id}:${drillKey}`}
                  context={practiceCategory ? 'practice' : 'rated'}
                  position={position}
                  result={result}
                  isThinking={isThinking}
                  onPlayItOut={() => setPlayout({ state: 'active' })}
                  onRetry={handleRetry}
                  playout={playout}
                  onRequestHint={() => setHintRequest((n) => n + 1)}
                  onRequestSolution={() => setSolutionRequest((n) => n + 1)}
                />
              </div>

              {/* The landing page's Woodpecker card, compacted for the
                  column: the spaced-repetition pitch inside the drill loop. */}
              <WoodpeckerPromoCard />

              {nextError && (
                <div
                  role="alert"
                  className="shrink-0 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-3 text-sm text-amber-100"
                >
                  {nextError}
                </div>
              )}

              {result && (
                <button
                  type="button"
                  onClick={handleNextDrill}
                  disabled={isAdvancing}
                  className={`${CARD_CLASS} flex h-14 w-full shrink-0 items-center justify-center gap-3 text-sm font-semibold text-white transition hover:bg-white/5 disabled:opacity-60`}
                >
                  {isAdvancing ? (
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/40 border-t-transparent" />
                  ) : (
                    <NextIcon />
                  )}
                  {isAdvancing
                    ? 'Loading…'
                    : practiceCategory
                      ? 'Next Position'
                      : 'Next Drill'}
                </button>
              )}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
