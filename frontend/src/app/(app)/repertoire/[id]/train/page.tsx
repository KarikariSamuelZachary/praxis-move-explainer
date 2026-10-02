'use client';

/**
 * Repertoire Train flow - mounted at /repertoire/{id}/train.
 *
 * Replaces the prior detail-page stub (Train button used to push here
 * but there was no page.tsx at this route - Next.js would 404). Three
 * view phases, all on one client page:
 *
 *   1. CONFIG - modal-floating over the repertoire header showing
 *      scope (main_lines_only toggle) and the position count that
 *      WILL be trained (read from GET /positions, never from
 *      /sessions/start - starting a session is a real mutation, not
 *      a preview). "Train" button posts /sessions/start; on 200 we
 *      transition to SESSION; on the backend's 400 "no positions to
 *      train for this mode" we render the detail string inline in
 *      the modal rather than navigating anywhere.
 *
 *   2. SESSION - quiz screen: one stored owner position at a time.
 *      The board shows the position's FEN (owner-to-move). The user
 *      plays ANY saved move at that position - the session quizzes
 *      the POSITION, so the first sideline, second sideline, or
 *      mainline may all come first. A move matching a stored row is
 *      correct; that ROW is marked completed (tracked by id, NOT by a
 *      queue index, so playing a sibling branch never consumes the
 *      presented item - no saved move can be skipped). The client
 *      fires POST /positions/{position_id}/review against the played
 *      row, auto-plays a prepared opponent reply, and follows that
 *      branch to the next uncompleted row (preferring the reply's
 *      position; otherwise the next row in tree order, which is a
 *      first-visit DFS that puts the earliest-created "main line"
 *      first). When the last uncompleted row is answered we POST
 *      /sessions/{session_id}/complete with the final tally and
 *      transition to DONE.
 *
 *      Recording-honesty contract (here, not in the backend): the
 *      client tallies counters from its own UCI comparison. A WRONG
 *      move stays on the same position for a retry (bumping only the
 *      incorrect counter); a CORRECT move advances. The session
 *      therefore cannot be stranded by app logic - every path either
 *      advances or clearly stays with feedback. But "advance anyway"
 *      must not become "lie about what was recorded": a /review that
 *      returned non-2xx OR threw bumps `reviewFailureCount`, and a
 *      non-blocking banner appears under the board ("Some attempts
 *      couldn't be recorded…") so a string of silent failures can't
 *      produce a session that looks entirely normal to the user but is
 *      entirely unrecorded server-side. We do NOT try to reconcile
 *      which individual /review calls succeeded - that doesn't map
 *      cleanly to a useful UI. A single binary success/failure state
 *      for the whole session's completion is what we surface instead.
 *      Hint clicks also count as incorrect (revealing the answer isn't
 *      solving it).
 *
 *   3. DONE - completion view: final score, link back to the
 *      repertoire detail page. Two visible variants keyed off
 *      `completeFailure` (set true when the /complete POST returned
 *      non-2xx OR threw):
 *        * recorded  - "Session complete" + score + the implicit
 *          promise that this counts toward Times Trained / Last
 *          Score on the list page (which the backend's
 *          completed_at-NOT-NULL row feeds).
 *        * unrecorded - "Session couldn't be recorded" + the same
 *          local score (the user did attempt N positions; we don't
 *          hide that) + an explicit note that this attempt WON'T
 *          count toward Times Trained / Last Score. The DB row at
 *          this point is completed_at=NULL, positions_correct=0 -
 *          the user has to know that, not be told "complete".
 *
 * Scope decisions enforced here (NOT in the detail page or anywhere
 * else):
 *   * This route is train-mode only (POST {mode: "train"}). The
 *     due-gated review mode is a separate, not-yet-built entry point.
 *   * The scope filter exposed here is only `main_lines_only`. No
 *     depth-range, no train-as-opponent. Per the project's earlier
 *     decision.
 *   * The assists mirror the puzzles board: "Hint" highlights the
 *     piece to move (green, ~4s) and "Solution" plays the stored move
 *     on the board (blue). Either reveal costs one incorrect for the
 *     position - it can no longer count as solved unaided - and
 *     "Solution" then records the position as not solved and runs the
 *     same reply/advance sequence a correct solve does.
 *   * The "opponent's last move" prompt prefix from reference-5
 *     ("Black just played d6.") is NOT derivable from the schema
 *     alone (positions don't store their parent move and the session
 *     doesn't carry paths). Per the task's instruction to fall back
 *     to simpler wording, we use "What's your move here?" with the
 *     owner's color derived from the position's side-to-move field.
 *
 * Pattern parity with src/app/(app)/repertoire/[id]/page.tsx:
 *   * Same CARD_CLASS, walnut background, color palette.
 *   * Same dynamic Chessboard import path (react-chessboard via next/dynamic).
 *   * Same drag-and-drop wiring pattern (canDragPiece + onPieceDrop).
 *   * Same `body.detail || body.error` parsing for backend error
 *     strings (so the 400 inline message reads verbatim).
 *
 * Next.js 16: `params` is a Promise to read via `use()`.
 */

import { use, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Chess, type Square } from 'chess.js';

import BoardShell from '@/components/board/BoardShell';
import ReviewShell from '@/components/review/ReviewShell';
import {
  applyUci,
  buildQuizItems,
  chooseReplyFen,
  findLinePath,
  nextQuizItem,
  normalizeFen,
  type RepertoireColor,
  type RepertoirePositionRow,
} from './train-logic';

const CARD_CLASS =
  'rounded-2xl border border-black/50 backdrop-blur-sm [background-image:linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)),url(/walnut-dark.webp)] [background-size:cover] [background-position:center] [box-shadow:0_10px_30px_rgba(0,0,0,0.55),inset_0_1px_0_rgba(255,255,255,0.06),inset_0_-1px_0_rgba(0,0,0,0.5)]';

type TrainParams = Promise<{ id: string }>;

type ApiRepertoire = {
  id: string;
  user_id: string;
  name: string;
  color: RepertoireColor;
  created_at: string;
  updated_at: string;
};

type ApiError = { detail?: string; error?: string };

type StartSessionResponse = {
  session: {
    id: string;
    repertoire_id: string;
    mode: 'review' | 'train';
    positions_total: number;
    positions_correct: number;
    attempts_total: number | null;
    started_at: string;
    completed_at: string | null;
  };
  positions: RepertoirePositionRow[];
};

type Phase = 'config' | 'session' | 'done';

function SearchBackIcon() {
  return (
    <svg
      width="18"
      height="18"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m15 18-6-6 6-6" />
    </svg>
  );
}

// Assist button styles, lifted verbatim from the puzzles
// PuzzleStatusPanel so the train page's Hint/Solution pair is visually
// identical to the puzzle solver's.
const GOLD_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl bg-gradient-to-b from-[#eacb90] to-[#c1954f] px-4 py-3.5 text-sm font-bold text-[#2a1a06] shadow-[0_10px_22px_rgba(0,0,0,0.45),inset_0_1px_0_rgba(255,255,255,0.55)] transition hover:from-[#f2d8a4] hover:to-[#cda261] disabled:cursor-not-allowed disabled:opacity-50';

const SECONDARY_BUTTON_CLASS =
  'relative flex w-full items-center justify-center gap-2.5 rounded-xl border border-white/15 bg-white/5 px-4 py-3.5 text-sm font-bold text-white/80 transition hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-50';

/** The Hint button's mark, matching the puzzles solver's. */
function BulbIcon() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.9"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M9 18h6" />
      <path d="M10 21h4" />
      <path d="M12 3a6 6 0 0 0-3.6 10.8c.5.4.8 1 .9 1.6l.1.6h5.2l.1-.6c.1-.6.4-1.2.9-1.6A6 6 0 0 0 12 3Z" />
    </svg>
  );
}

/** The Solution button's mark, matching the puzzles solver's. */
function EyeIcon() {
  return (
    <svg
      className="h-[18px] w-[18px]"
      fill="none"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.9"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function ArrowRightIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M5 12h14" />
      <path d="m13 5 7 7-7 7" />
    </svg>
  );
}

export default function RepertoireTrainPage({
  params,
}: {
  params: TrainParams;
}) {
  const { id } = use(params);
  const router = useRouter();

  const [name, setName] = useState<string | null>(null);
  const [color, setColor] = useState<RepertoireColor | null>(null);

  const [phase, setPhase] = useState<Phase>('config');
  const [mainLinesOnly, setMainLinesOnly] = useState(false);
  const [totalPositionCount, setTotalPositionCount] = useState<number | null>(null);
  const [countError, setCountError] = useState<string | null>(null);
  const [countLoading, setCountLoading] = useState(true);

  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionPositions, setSessionPositions] = useState<RepertoirePositionRow[]>([]);
  // Quiz items the session has ANSWERED (one owner row per completed
  // move). Tracked by row id, NOT by a list index: the user may answer
  // a different saved move than the one presented (any saved line can
  // come first), so the presented item must stay uncompleted when they
  // branch away, and the played row is the one that counts.
  const [completedIds, setCompletedIds] = useState<ReadonlySet<string>>(
    new Set()
  );
  // The owner row whose position is currently on the board. null only
  // between session start and the first render after /sessions/start.
  const [currentItemId, setCurrentItemId] = useState<string | null>(null);
  const [correctCount, setCorrectCount] = useState(0);
  const [incorrectCount, setIncorrectCount] = useState(0);
  const [startPending, setStartPending] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [reviewPending, setReviewPending] = useState(false);
  const [completePending, setCompletePending] = useState(false);
  // Assist highlights for the current position: the Hint reveal paints
  // the from-square green, the Solution reveal paints from/to blue -
  // the same colors the puzzles board uses. Cleared when the position
  // resolves or the hint timeout lapses.
  const [highlightSquares, setHighlightSquares] = useState<
    Record<string, React.CSSProperties>
  >({});
  const hintTimeoutRef = useRef<number | null>(null);
  // Transient wrong-attempt feedback ("Incorrect - try again"). The
  // session no longer advances on a wrong move, so the user needs an
  // explicit signal the attempt was registered and rejected.
  const [attemptFeedback, setAttemptFeedback] = useState<string | null>(null);
  // Auto-played opponent reply FEN. While set, the board renders
  // this FEN instead of the current quiz item's FEN, and input is
  // blocked. Cleared when the reply sequence finishes advancing.
  const [autoMoveFen, setAutoMoveFen] = useState<string | null>(null);
  // Keyed by position row id: the position that ALREADY registered its
  // single "incorrect" (from a wrong move OR a hint click). Any later
  // wrong move or hint on the SAME position must not add another
  // incorrect - only the first miss counts. Resetting is implicit:
  // the value is compared against the current position's id, which
  // changes when the session advances.
  const incorrectCountedPosRef = useRef<string | null>(null);

  // Recording-honesty state (added after the kill-mid-session test
  // exposed that the DONE phase would render "Session complete · X%"
  // from the CLIENT'S local tally even when the /complete POST
  // failed and the DB row stayed completed_at=NULL /
  // positions_correct=0. Mirrored by the per-/review counter so a
  // flaky connection during the quiz doesn't silently produce a
  // session that looks entirely normal but is entirely unrecorded
  // server-side.)
  //
  // `reviewFailureCount` - number of positions in the current
  // session whose /review POST returned non-2xx OR threw. Drives a
  // non-blocking banner under the board that appears as soon as the
  // count is > 0 and persists for the rest of the session. Reset to
  // 0 in handleStart when a new session begins.
  //
  // `completeFailure` - set true in the final /complete fetch's
  // failure branch (non-2xx OR throw); false on success. The DONE
  // render reads this to pick "Session complete" vs "Session
  // couldn't be recorded" + the note about Times Trained / Last
  // Score. It is a SINGLE binary signal for the WHOLE session - we
  // do not attempt to reconcile which individual /review calls
  // succeeded (per the task: doesn't map cleanly to a useful UI;
  // the session-level recorded-or-not is what the DB can answer).
  const [reviewFailureCount, setReviewFailureCount] = useState(0);
  const [completeFailure, setCompleteFailure] = useState(false);

  // `shownAtRef` records the wall-clock time the current position was
  // displayed. Reset on each position advance. Read by the /review
  // POST to compute time_taken_ms (per the task spec - elapsed time
  // since this position was shown). useRef avoids re-renders on read.
  const shownAtRef = useRef<number>(Date.now());

  // Quiz items in tree order (see buildQuizItems): every saved owner
  // move, with the earliest-created branch first. sessionPositions
  // keeps the full row list so the auto-reply lookup can find the
  // opponent's prepared response to whichever branch the user plays.
  const quizItems = useMemo(
    () => buildQuizItems(sessionPositions, color),
    [sessionPositions, color]
  );

  const currentItem = useMemo(
    () => quizItems.find((p) => p.id === currentItemId) ?? null,
    [quizItems, currentItemId]
  );
  const total = quizItems.length;
  const completedCount = completedIds.size;

  // Board orientation is FIXED to the repertoire owner's color for
  // the whole session - even when the quiz reaches an opponent ply,
  // the board keeps the owner's viewpoint and the user drags the
  // opposite-color piece from that same angle.
  const boardOrientation: 'white' | 'black' =
    color === 'black' ? 'black' : 'white';

  // --- Header metadata fetch (name + color for the modal title and
  // session header). Mirrors the detail page's pattern: dedicated
  // single-repertoire GET, no list-page derivation.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(
          `/api/repertoires/${encodeURIComponent(id)}`,
          { cache: 'no-store' }
        );
        if (!res.ok) return;
        const item = (await res.json()) as ApiRepertoire;
        if (!cancelled && item) {
          setName(item.name);
          setColor(item.color);
        }
      } catch {
        // Silent - header falls back to "Repertoire".
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id]);

  // --- Position count fetch (the count the config modal shows).
  // GET /positions returns EVERY stored row for this repertoire -
  // BOTH owner and opponent rows now (the writer persists every ply).
  // Training quizzes only the OWNER's moves, so the count we display
  // filters to owner-side rows via the FEN's side-to-move field.
  // Wait for `color` to be known before fetching so we can filter
  // client-side; until then the count stays null and the modal shows
  // a loading state.
  useEffect(() => {
    if (color === null) return;
    let cancelled = false;
    (async () => {
      setCountLoading(true);
      setCountError(null);
      try {
        const res = await fetch(
          `/api/repertoires/${encodeURIComponent(id)}/positions`,
          { cache: 'no-store' }
        );
        if (!res.ok) {
          const body = (await res.json().catch(() => ({}))) as ApiError;
          throw new Error(
            body.detail ?? body.error ?? `Positions load failed (${res.status})`
          );
        }
        const rows = (await res.json()) as RepertoirePositionRow[];
        if (!cancelled && Array.isArray(rows)) {
          // Train quizzes OWNER positions only (one item per stored
          // owner move - diverging moves at the same FEN are all
          // trained, not deduped away), with the opponent's stored
          // reply auto-played on the board. Match that here so the
          // modal's "Positions that will be trained" matches what the
          // user actually gets.
          setTotalPositionCount(buildQuizItems(rows, color).length);
        }
      } catch (err) {
        if (!cancelled) {
          setCountError(
            err instanceof Error ? err.message : 'Failed to load positions'
          );
        }
      } finally {
        if (!cancelled) {
          setCountLoading(false);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [id, color]);

  // --- Train button handler: POST /sessions/start, transition on 200.
  // On 400 (no positions to train) we surface the backend's detail
  // string inline in the modal rather than navigating anywhere.
  const handleStart = useCallback(async () => {
    if (startPending) return;
    setStartPending(true);
    setStartError(null);
    try {
      const res = await fetch(
        `/api/repertoires/${encodeURIComponent(id)}/sessions/start`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            mode: 'train',
            main_lines_only: mainLinesOnly,
          }),
        }
      );
      if (!res.ok) {
        const body = (await res.json().catch(() => ({}))) as ApiError;
        throw new Error(
          body.detail ?? body.error ?? `Start failed (${res.status})`
        );
      }
      const data = (await res.json()) as StartSessionResponse;
      if (!data?.session?.id || !Array.isArray(data.positions)) {
        throw new Error('Backend returned an unexpected start-session payload');
      }
      // First presentation: the root's earliest-created branch (tree
      // order), so the session begins at the start position. All
      // completed-tracking starts empty for the new session.
      const items = buildQuizItems(data.positions, color);
      setSessionId(data.session.id);
      setSessionPositions(data.positions);
      setCompletedIds(new Set());
      setCurrentItemId(items[0]?.id ?? null);
      setCorrectCount(0);
      setIncorrectCount(0);
      setAutoMoveFen(null);
      // Reset the recording-honesty state for the new session -
      // a previous session's /review failures or /complete failure
      // must not bleed into the new one's banner / DONE framing.
      setReviewFailureCount(0);
      setCompleteFailure(false);
      shownAtRef.current = Date.now();
      setPhase('session');
    } catch (err) {
      setStartError(
        err instanceof Error ? err.message : 'Failed to start training session'
      );
    } finally {
      setStartPending(false);
    }
  }, [color, id, mainLinesOnly, startPending]);

  // --- Fire the /review FSRS recording for one resolved position in
  // the BACKGROUND. It must NOT gate the move feedback / reply
  // animation - awaiting the HTTP round-trip on every move is what
  // makes the flow feel laggy. The promise still surfaces failures
  // (non-2xx or throw) into `reviewFailureCount` so the
  // recording-honesty banner appears. NOTE: the URL has no `/review`
  // suffix - the Next.js proxy route lives at
  // /api/repertoires/positions/[position_id] and its POST handler
  // appends `/review` when forwarding to the backend (see
  // src/app/api/repertoires/positions/[position_id]/route.ts).
  // Calling `/…/{id}/review` directly would 404 on the proxy and
  // (wrongly) trip the recording-honesty banner below.
  const recordReview = useCallback(
    (reviewedRowId: string, solvedCorrectly: boolean, timeTakenMs: number) => {
      void (async () => {
        try {
          const res = await fetch(
            `/api/repertoires/positions/${encodeURIComponent(reviewedRowId)}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                solved_correctly: solvedCorrectly,
                time_taken_ms: timeTakenMs,
              }),
            }
          );
          if (!res.ok) {
            // Surface the backend detail to the console for debugging;
            // the /complete tally at the end is the authoritative
            // correctness record. Bump `reviewFailureCount` so the
            // banner under the board appears - the user has to know
            // this attempt's FSRS update didn't land, not see it
            // silently swallowed.
            const body = (await res.json().catch(() => ({}))) as ApiError;
            console.warn(
              `Review POST failed (${res.status}):`,
              body.detail ?? body.error
            );
            setReviewFailureCount((c) => c + 1);
          }
        } catch (err) {
          // Thrown fetch (network unreachable / DNS failure / etc.) -
          // same treatment as a non-2xx: log + bump the counter so
          // the in-session banner appears.
          console.warn('Review POST threw:', err);
          setReviewFailureCount((c) => c + 1);
        }
      })();
    },
    []
  );

  // --- Resolve a saved move at the CURRENT position: record the
  // /review result, mark the PLAYED row completed, then auto-play the
  // opponent's stored reply and follow that branch to the next
  // uncompleted item (or /complete when none remain). Shared by a
  // correct drag (solvedCorrectly=true) and the Solution reveal
  // (solvedCorrectly=false, blue highlight).
  //
  // Branch-following rules:
  //   * The played row is what gets completed, NOT the presented item.
  //     "Any saved line can come first": if the user plays a sibling
  //     branch, that branch is credited and the presented item stays
  //     queued for a later pass - no item is silently skipped.
  //   * If the played row was already completed, it still counts as a
  //     correct move (and its /review lands) but completes nothing.
  //   * The next item is the first uncompleted row at the reply's
  //     position when one exists (follow the branch), else the first
  //     uncompleted row in tree order (line-switch back to untouched
  //     material).
  const finishResolvedPosition = useCallback(
    (
      playedUci: string,
      reviewedRowId: string,
      solvedCorrectly: boolean,
      wasNewCompletion: boolean,
      highlight: Record<string, React.CSSProperties> = {}
    ) => {
      if (!currentItem || !sessionId) return;
      const timeTakenMs = Math.max(0, Date.now() - shownAtRef.current);
      setReviewPending(true);
      recordReview(reviewedRowId, solvedCorrectly, timeTakenMs);

      const completedAfter = new Set(completedIds);
      completedAfter.add(reviewedRowId);

      const newlyCountedIncorrect =
        !solvedCorrectly &&
        incorrectCountedPosRef.current !== currentItem.id;
      if (wasNewCompletion) {
        if (solvedCorrectly) setCorrectCount((c) => c + 1);
        setCompletedIds(completedAfter);
      }
      if (!solvedCorrectly && newlyCountedIncorrect) {
        setIncorrectCount((c) => c + 1);
        incorrectCountedPosRef.current = currentItem.id;
      }

      shownAtRef.current = Date.now();
      // Paint the assist highlight (empty for a normal correct drag,
      // which also clears any lingering hint green) for the duration
      // of the reply animation.
      setHighlightSquares(highlight);

      const afterUser = applyUci(currentItem.fen, playedUci);
      const uncompletedFens = new Set(
        quizItems
          .filter((p) => !completedAfter.has(p.id))
          .map((p) => normalizeFen(p.fen))
      );
      // The opponent reply to auto-play: prefer the prepared reply
      // that leads toward the user's uncompleted material.
      const replyFen = afterUser
        ? chooseReplyFen(sessionPositions, afterUser, uncompletedFens)
        : null;
      const nextItem = nextQuizItem(quizItems, completedAfter, replyFen);

      const doAdvance = () => {
        // Clear the auto-reply overlay BEFORE the handoff so the board
        // renders the NEXT item's real FEN. Keeping it pinned here was
        // the bug: when the next quiz item belongs to a different line
        // the board stayed frozen on the just-finished line's last
        // position and the session appeared stuck.
        setAutoMoveFen(null);
        setHighlightSquares({});
        if (nextItem === null) {
          // Every quiz item has been answered - call /complete and
          // transition to DONE. Build the FINAL tally at call time
          // (not from state closures). A reveal
          // (solvedCorrectly=false) contributes no correct answer; its
          // incorrect delta is added explicitly because the setState
          // above hasn't committed yet.
          const finalCorrect =
            correctCount + (solvedCorrectly && wasNewCompletion ? 1 : 0);
          // Total attempts: every solved position plus every position
          // that needed a retry, a hint, or the solution. This is the
          // SAME number the DONE screen below displays as the score
          // denominator, and the backend stores it so the repertoire
          // list's "Last Score" shows the same accuracy this screen
          // shows.
          const attemptsTotal =
            finalCorrect + incorrectCount + (newlyCountedIncorrect ? 1 : 0);
          setCompletePending(true);
          // Default to "recorded" - flipped to true on any failure
          // path below. Either way, the catch / !ok blocks set
          // completeFailed and the finally transitions to DONE; the
          // DONE render reads `completeFailure` to pick the honest
          // variant.
          let completeFailed = false;
          (async () => {
            try {
              const completeRes = await fetch(
                `/api/repertoires/sessions/${encodeURIComponent(sessionId)}/complete`,
                {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({
                    positions_correct: finalCorrect,
                    attempts_total: attemptsTotal,
                  }),
                }
              );
              if (!completeRes.ok) {
                const body = (await completeRes.json().catch(() => ({}))) as ApiError;
                console.warn(
                  `Complete POST failed (${completeRes.status}):`,
                  body.detail ?? body.error
                );
                completeFailed = true;
              }
            } catch (err) {
              console.warn('Complete POST threw:', err);
              completeFailed = true;
            } finally {
              setCompleteFailure(completeFailed);
              setCompletePending(false);
              setPhase('done');
            }
          })();
        } else {
          const sameLine =
            replyFen !== null &&
            normalizeFen(nextItem.fen) === normalizeFen(replyFen);
          const handoff = () => {
            setCurrentItemId(nextItem.id);
            setReviewPending(false);
          };
          if (sameLine) {
            // The reply already left the board AT the next item's
            // position - nothing more to animate, just hand off.
            handoff();
            return;
          }
          // LINE SWITCH: the next item belongs to a different line.
          // Replay the new line from the START position - all its
          // stored plies, both colors, ~450ms each - so the user sees
          // the sideline develop, then stop AT the position where it
          // becomes their turn (a slightly longer hold before the
          // input unlocks).
          const path = findLinePath(sessionPositions, nextItem.fen);
          if (path.length >= 2) {
            setAutoMoveFen(path[0]);
            path.forEach((fen, idx) => {
              if (idx === 0) return;
              window.setTimeout(() => setAutoMoveFen(fen), idx * 250);
            });
            window.setTimeout(() => {
              setAutoMoveFen(null);
              handoff();
            }, (path.length - 1) * 250 + 450);
          } else {
            // Unreachable target (shouldn't happen - the target is a
            // stored row reachable from start by construction); fall
            // back to a plain handoff.
            handoff();
          }
        }
      };
      if (!afterUser) {
        // Shouldn't happen - handleDrop validated the move - but if
        // chess.js refused it just advance without a reply.
        doAdvance();
        return;
      }
      // First beat: show the FEN after the user's move so the user
      // sees their own piece change before the opponent's reply.
      setAutoMoveFen(afterUser);
      if (replyFen) {
        // Second beat: play the opponent's stored reply after a
        // short pause so the user can register the first move
        // before the board shifts again.
        window.setTimeout(() => {
          setAutoMoveFen(replyFen);
        }, 250);
      }
      // Quick in-line cadence: reply plays at ~250ms and doAdvance
      // runs at ~600ms. For a same-line handoff the board is already
      // sitting on the next position; for a line switch doAdvance
      // starts the from-start replay instead.
      const paceMs = replyFen ? 600 : 350;
      window.setTimeout(() => {
        doAdvance();
      }, paceMs);
      return;
    },
    [
      completedIds,
      correctCount,
      currentItem,
      incorrectCount,
      quizItems,
      recordReview,
      sessionId,
      sessionPositions,
    ]
  );

  // --- Quiz attempt handler. "Accept either move": a position with
  // several saved branches counts the attempt correct when the played
  // UCI matches ANY row stored at this position's FEN - the session
  // quizzes the POSITION, and any saved line may be played first. The
  // /review POST is fired against the MATCHING row's id (the branch
  // actually played) so the FSRS scheduling update lands on the right
  // row, and that row is the one marked completed - the presented item
  // stays queued when the user branches away, so no saved move is ever
  // skipped. A wrong move stays on the position for a retry.
  const handleAttempt = useCallback(
    async (uci: string) => {
      if (reviewPending) return;
      if (!currentItem || !sessionId) return;
      // A new user attempt wipes any auto-reply overlay from the
      // prior position so the dragged piece lands on the real
      // current item's position.
      setAutoMoveFen(null);
      const posKey = normalizeFen(currentItem.fen);
      const matchingRow = sessionPositions.find(
        (p) => normalizeFen(p.fen) === posKey && p.move === uci
      );
      if (!matchingRow) {
        // Tally. A WRONG move stays on the SAME position for a retry -
        // it only bumps the incorrect counter (and fires /review with
        // solved_correctly=false). Only the FIRST miss on this
        // position adds an incorrect (subsequent wrong moves - or
        // hint clicks - on the same position don't stack). The
        // session still stays on the position for a retry regardless.
        setReviewPending(true);
        recordReview(
          currentItem.id,
          false,
          Math.max(0, Date.now() - shownAtRef.current)
        );
        if (incorrectCountedPosRef.current !== currentItem.id) {
          setIncorrectCount((c) => c + 1);
          incorrectCountedPosRef.current = currentItem.id;
        }
        shownAtRef.current = Date.now();
        setAttemptFeedback('Incorrect - try again.');
        window.setTimeout(() => setAttemptFeedback(null), 3000);
        setReviewPending(false);
        return;
      }
      finishResolvedPosition(
        uci,
        matchingRow.id,
        true,
        !completedIds.has(matchingRow.id)
      );
    },
    [
      completedIds,
      currentItem,
      finishResolvedPosition,
      recordReview,
      reviewPending,
      sessionPositions,
      sessionId,
    ]
  );

  // --- Board drag handler. Same pattern as the detail page:
  // owner-turn positions accept drops; the resulting UCI is sent to
  // handleAttempt for comparison + /review + advance. `promotion` is
  // supplied by BoardShell when the user picks a piece from the
  // promotion dialog (or 'q' default for non-dialog drag paths).
  const handleDrop = useCallback(
    (
      sourceSquare: string,
      targetSquare: string,
      promotion?: string
    ): boolean => {
      if (!color || reviewPending || !currentItem) return false;
      // Every stored row is its side-to-move by construction;
      // canDragPiece already restricts drags to the moving side's
      // pieces, so no extra color gate is needed here.
      // Verify the move is legal at this FEN (rejects accidental
      // drops to invalid squares). Use the 4-field FEN + ' 0 1' so
      // chess.js's validator is happy (4-field alone fails
      // validation; the rest of the board rendering only uses the
      // first field anyway).
      const fenForValidation = `${normalizeFen(currentItem.fen)} 0 1`;
      let nextUci = `${sourceSquare}${targetSquare}`;
      try {
        const game = new Chess(fenForValidation);
        const played = game.move({
          from: sourceSquare as Square,
          to: targetSquare as Square,
          promotion: promotion ?? 'q',
        });
        if (played.promotion) {
          nextUci = `${sourceSquare}${targetSquare}${played.promotion}`;
        }
      } catch {
        return false;
      }
      // Fire and forget - handleAttempt owns its own pending flag
      // and counters; we just kick it off and accept the drop
      // visually.
      void handleAttempt(nextUci);
      return true;
    },
    [color, currentItem, handleAttempt, reviewPending]
  );

  // --- Hint button: highlights the piece to move (green, ~4s) like
  // the puzzles board's "Hint", and costs one incorrect per position -
  // revealing the answer means the position can't also count as
  // solved unaided. Any saved move at this FEN counts as correct (the
  // "accept either move" rule), so we highlight the current quiz
  // row's stored move.
  const handleHint = useCallback(() => {
    if (!currentItem || reviewPending) return;
    // A hint costs ONE incorrect per position - once this position has
    // already registered its incorrect (from a wrong move or a prior
    // reveal), further hint clicks add nothing.
    if (incorrectCountedPosRef.current !== currentItem.id) {
      setIncorrectCount((c) => c + 1);
      incorrectCountedPosRef.current = currentItem.id;
    }
    setHighlightSquares({
      [currentItem.move.slice(0, 2)]: {
        backgroundColor: 'rgba(16, 185, 129, 0.4)',
      },
    });
    if (hintTimeoutRef.current !== null) {
      window.clearTimeout(hintTimeoutRef.current);
    }
    hintTimeoutRef.current = window.setTimeout(() => {
      setHighlightSquares({});
      hintTimeoutRef.current = null;
    }, 4000);
  }, [currentItem, reviewPending]);

  // --- Solution button: plays the stored move on the board (blue
  // highlight from/to, like the puzzles board's "Solution"), records
  // the position as not solved, then runs the same reply/advance
  // sequence a correct solve does. The presented row is what gets
  // revealed (and completed) - a solution always resolves the item the
  // session is currently asking for.
  const handleShowSolution = useCallback(() => {
    if (!currentItem || !sessionId || reviewPending) return;
    const move = currentItem.move;
    if (!applyUci(currentItem.fen, move)) return;
    finishResolvedPosition(move, currentItem.id, false, true, {
      [move.slice(0, 2)]: { backgroundColor: 'rgba(100, 150, 255, 0.6)' },
      [move.slice(2, 4)]: { backgroundColor: 'rgba(100, 150, 255, 0.6)' },
    });
  }, [currentItem, finishResolvedPosition, reviewPending, sessionId]);

  // --- Back to detail page (config back button + DONE "Back to
  // repertoire" link both reuse this).
  const handleBackToDetail = useCallback(() => {
    void router.push(`/repertoire/${encodeURIComponent(id)}`);
  }, [id, router]);

  // ----- Render helpers ---------------------------------------------

  const configCountLabel = useMemo(() => {
    if (totalPositionCount === null) return null;
    if (mainLinesOnly) {
      // Toggle on: show "up to N" - the actual filtered count would
      // require running classify_repertoire_lines client-side (no
      // equivalent helper exists; the backend does this server-side
      // during /sessions/start). The "up to" prefix makes the upper
      // bound explicit.
      return { kind: 'upTo' as const, value: totalPositionCount };
    }
    return { kind: 'exact' as const, value: totalPositionCount };
  }, [mainLinesOnly, totalPositionCount]);

  // ----- Phase: CONFIG ---------------------------------------------

  if (phase === 'config') {
    return (
      <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto px-4 py-4 text-white sm:px-6 sm:py-6 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
        {/* Back button - top-left, sits above the centered card so
            it doesn't drag the card off-center. */}
        <button
          type="button"
          onClick={handleBackToDetail}
          aria-label="Back to repertoire"
          className="absolute left-4 top-4 z-10 flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60 sm:left-6 sm:top-6"
        >
          <SearchBackIcon />
        </button>

        {/* Centered config card - horizontally + vertically centered
            over the walnut backdrop so the modal reads as the focal
            point once the page is entered. */}
        <div className="flex min-h-full items-center justify-center py-12">
          <div className={`${CARD_CLASS} w-full max-w-md p-6 sm:p-8`}>
            <div className="flex flex-col gap-6">
              {/* Modal heading */}
              <h2 className="text-center font-display text-xl font-bold text-[#efd9a7]">
                Training session
              </h2>

              {/* Position count display */}
              <div className="flex flex-col items-center gap-2 py-2">
                <p className="text-sm text-[#a79b8a]">
                  Positions that will be trained
                </p>
                {countLoading ? (
                  <div
                    className="h-12 w-24 animate-pulse rounded-md bg-black/40"
                    aria-label="Loading position count"
                  />
                ) : countError ? (
                  <p className="text-center text-sm text-red-300" role="alert">
                    {countError}
                  </p>
                ) : configCountLabel ? (
                  <p className="font-display text-5xl font-bold tabular-nums text-[#efd9a7]">
                    {configCountLabel.kind === 'upTo' ? 'Up to ' : ''}
                    {configCountLabel.value}
                  </p>
                ) : (
                  <p className="text-sm text-[#a79b8a]">0</p>
                )}
              </div>

              {/* Train button */}
              <button
                type="button"
                disabled={
                  startPending ||
                  countLoading ||
                  totalPositionCount === null ||
                  totalPositionCount === 0
                }
                onClick={() => void handleStart()}
                className="group flex h-14 items-center justify-center gap-3 rounded-full border-2 border-[#d9b87c] bg-black/40 px-6 text-base font-bold uppercase tracking-wider text-[#efd9a7] transition hover:bg-[#d9b87c]/15 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {startPending ? (
                  <span className="animate-pulse">Starting…</span>
                ) : (
                  <span>Train</span>
                )}
              </button>

              {/* Scope toggle (main_lines_only) */}
              <label className="flex cursor-pointer items-center justify-between rounded-xl border border-white/5 bg-black/30 px-4 py-3 transition hover:border-white/10">
                <span className="flex flex-col gap-0.5">
                  <span className="text-sm font-semibold text-[#efd9a7]">
                    Main lines only
                  </span>
                  <span className="text-xs text-[#a79b8a]">
                    Train only the repertoire&apos;s main-line positions.
                  </span>
                </span>
                <span className="relative inline-block">
                  <input
                    type="checkbox"
                    checked={mainLinesOnly}
                    onChange={(e) => setMainLinesOnly(e.target.checked)}
                    className="peer sr-only"
                    aria-label="Toggle main lines only scope"
                  />
                  <span className="block h-6 w-11 rounded-full bg-black/60 transition peer-checked:bg-[#d9b87c]/70" />
                  <span className="absolute left-0.5 top-0.5 block h-5 w-5 rounded-full bg-[#a79b8a] transition peer-checked:translate-x-5 peer-checked:bg-[#efd9a7]" />
                </span>
              </label>

              {/* Inline error from /sessions/start (e.g. 400 empty) */}
              {startError && (
                <div
                  className="rounded-xl border border-red-400/30 bg-red-400/10 px-4 py-3 text-sm text-red-300"
                  role="alert"
                >
                  {startError}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ----- Phase: DONE ------------------------------------------------

  if (phase === 'done') {
    const total = quizItems.length;
    // Wrong moves no longer advance, so the session reaches DONE with
    // every item completed. `correctCount` counts only UNAIDED solves -
    // a Solution reveal completes its item without a correct - so the
    // honest score is ACCURACY: correct solves over total attempts
    // (retries + hints + reveals in the denominator via
    // incorrectCount).
    const attemptTotal = correctCount + incorrectCount;
    const pct =
      attemptTotal > 0 ? Math.round((correctCount / attemptTotal) * 100) : 0;
    return (
      <div className="relative h-[calc(100vh-3rem)] w-full overflow-y-auto px-4 py-4 text-white sm:px-6 sm:py-6 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
        {/* Back button - top-left, sits above the centered card. */}
        <button
          type="button"
          onClick={handleBackToDetail}
          aria-label="Back to repertoire"
          className="absolute left-4 top-4 z-10 flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60 sm:left-6 sm:top-6"
        >
          <SearchBackIcon />
        </button>

        {/* Centered completion card. */}
        <div className="flex min-h-full items-center justify-center py-12">
          <div className={`${CARD_CLASS} w-full max-w-md p-6 sm:p-8`}>
            <div className="flex flex-col items-center gap-6 text-center">
              <h2 className="font-display text-2xl font-bold uppercase tracking-wider text-[#efd9a7]">
                {completeFailure
                  ? 'Session couldn\u2019t be recorded'
                  : 'Session complete'}
              </h2>
              {/* On recording failure we still show the local score
                  (the user DID attempt N positions - hiding it would
                  be its own lie of omission) but add a one-line note
                  that explicitly drops the implication that this
                  attempt counts toward the list page's Times Trained
                  / Last Score aggregates. The DB row at this point
                  is completed_at=NULL, positions_correct=0; the
                  list page's aggregates are derived from
                  completed_at IS NOT NULL rows, so this attempt will
                  NOT be reflected there and we say so. */}
              {completeFailure && (
                <p className="text-sm text-red-300" role="status">
                  Your progress wasn&apos;t saved - this attempt won&apos;t
                  count toward Times Trained or Last Score.
                </p>
              )}
              <p className="text-sm text-[#a79b8a]">
                {total === 0
                  ? 'No positions were trained.'
                  : `${correctCount} of ${total} positions · ${incorrectCount} incorrect`}
              </p>
              <p className="font-display text-5xl font-bold tabular-nums text-[#efd9a7]">
                {pct}%
              </p>
              {/* If a string of /review failures hit during the
                  session AND /complete also failed, surface a count
                  here too so the user knows it wasn't one cosmic
                  ray - a flaky connection produced N unpersisted
                  FSRS updates and the session tally didn't land
                  either. Cheap to include; only renders when
                  relevant. */}
              {completeFailure && reviewFailureCount > 0 && (
                <p className="text-xs text-[#a79b8a]/85" role="status">
                  {reviewFailureCount} of {total} attempt
                  {reviewFailureCount === 1 ? '' : 's'} also
                  couldn&apos;t be recorded during the session.
                </p>
              )}
              <button
                type="button"
                onClick={handleBackToDetail}
                className="flex h-12 items-center gap-2 rounded-full border-2 border-[#d9b87c] bg-black/40 px-6 text-sm font-bold uppercase tracking-wider text-[#efd9a7] transition hover:bg-[#d9b87c]/15"
              >
                <ArrowRightIcon />
                <span>
                  Back to {name ?? 'repertoire'}
                </span>
              </button>
              {completePending && (
                <p className="text-xs text-[#a79b8a]/70">
                  Recording session…
                </p>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ----- Phase: SESSION ---------------------------------------------

  return (
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto px-6 pb-1 pt-6 text-white lg:overflow-hidden lg:px-10 xl:pt-5 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      {/*
        Session layout - mirrored from the build page (ReviewShell) so a
        user moving between the two routes sees no board-size jump.
        The fixed left rail carries only the back button (the build
        page's name + Train button chrome is intentionally omitted
        here - the train flow has nothing to author or label). Board
        panel (center) holds the quiz board. Analysis panel (right)
        holds the "What's your move?" prompt card with counters + hint.
      */}
      <ReviewShell
        leftCollapsible={false}
        importPanel={
          <button
            type="button"
            onClick={handleBackToDetail}
            aria-label="Leave session"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-black/50 bg-black/40 text-[#efd9a7] transition hover:bg-black/60"
          >
            <SearchBackIcon />
          </button>
        }
        boardPanel={
          <div className="relative mx-auto aspect-square w-full max-w-[calc(100vh-70px)]">
            {/*
              Board - the current position's FEN is 4-field (matches
              `_normalize_fen` server-side); react-chessboard only
              reads the first FEN field for piece placement, so the
              4-field value renders fine. BoardShell wraps it with
              the same walnut frame + click-to-move + promotion
              dialog the puzzles page uses, so the train flow's board
              is visually + interactionally identical.
            */}
            {currentItem ? (
              <BoardShell
                position={autoMoveFen ?? currentItem.fen}
                orientation={boardOrientation}
                allowDragging={!reviewPending}
                canDragPiece={({ piece }) => {
                  if (reviewPending || !color || !currentItem) return false;
                  // The session includes BOTH sides' plies, so the
                  // draggable pieces are whichever color is on turn
                  // in the CURRENT position (not the repertoire
                  // owner's color).
                  const side = currentItem.fen.split(/\s+/)[1];
                  return piece.pieceType[0] === side;
                }}
                onMove={(source, target, promotion) =>
                  handleDrop(source, target, promotion ?? 'q')
                    ? true
                    : false
                }
                squareStyles={highlightSquares}
              />
            ) : (
              <div
                className={`${CARD_CLASS} flex h-full w-full items-center justify-center p-6 text-center text-sm text-[#a79b8a]`}
              >
                No positions to train.
              </div>
            )}
          </div>
        }
        analysisPanel={
          <aside className="wood-scrollbar flex h-full min-h-0 flex-col gap-4 overflow-y-auto pr-1">
            {currentItem && (
              <div className={`${CARD_CLASS} relative flex flex-col gap-4 overflow-hidden p-5`}>
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute -right-14 -top-16 h-40 w-40 rounded-full bg-[#d9b87c]/10 blur-3xl"
                />

                <div className="relative">
                  <h2 className="font-display text-2xl font-semibold leading-tight tracking-tight text-[#f7e5c6]">
                    What&apos;s your move?
                  </h2>
                </div>

                {/* Wrong-attempt feedback - a rejected move keeps the
                    user on this position, so say so explicitly. */}
                {attemptFeedback && (
                  <p
                    role="status"
                    aria-live="polite"
                    className="rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-center text-xs text-red-200"
                  >
                    {attemptFeedback}
                  </p>
                )}

                <div className="grid grid-cols-2 divide-x divide-white/[0.08] overflow-hidden rounded-xl border border-white/[0.08] bg-black/25">
                  <div className="flex items-center gap-2.5 px-3.5 py-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-emerald-300/20 bg-emerald-300/[0.08] text-emerald-200/90">
                      <svg
                        className="h-4 w-4"
                        viewBox="0 0 20 20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        aria-hidden="true"
                      >
                        <path d="m4.5 10.2 3.4 3.3 7.6-7.1" />
                      </svg>
                    </span>
                    <div className="min-w-0">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#a79b8a]">
                        Correct
                      </p>
                      <p className="mt-0.5 font-display text-xl font-semibold leading-none tabular-nums text-[#e4d6bd]">
                        {correctCount}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2.5 px-3.5 py-3">
                    <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-rose-300/15 bg-rose-300/[0.06] text-rose-200/80">
                      <svg
                        className="h-3.5 w-3.5"
                        viewBox="0 0 20 20"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.8"
                        strokeLinecap="round"
                        aria-hidden="true"
                      >
                        <path d="m6 6 8 8M14 6l-8 8" />
                      </svg>
                    </span>
                    <div className="min-w-0">
                      <p className="text-[10px] font-semibold uppercase tracking-[0.14em] text-[#a79b8a]">
                        Incorrect
                      </p>
                      <p className="mt-0.5 font-display text-xl font-semibold leading-none tabular-nums text-[#e4d6bd]">
                        {incorrectCount}
                      </p>
                    </div>
                  </div>
                </div>

                <section
                  aria-label="Session progress"
                  className="rounded-xl border border-white/[0.08] bg-black/25 px-3.5 py-3"
                >
                  <div className="mb-2 flex items-center justify-between gap-3">
                    <span className="text-[10px] font-semibold uppercase tracking-[0.18em] text-[#a79b8a]">
                      Progress
                    </span>
                    <span className="font-mono text-xs font-semibold tabular-nums text-[#efd9a7]">
                      {completedCount} / {total}
                    </span>
                  </div>
                  <div
                    role="progressbar"
                    aria-label="Positions completed"
                    aria-valuemin={0}
                    aria-valuemax={total}
                    aria-valuenow={completedCount}
                    className="h-2 overflow-hidden rounded-full bg-black/60 ring-1 ring-white/[0.06]"
                  >
                    <div
                      className="h-full rounded-full bg-gradient-to-r from-[#b98c4b] to-[#efd9a7] shadow-[0_0_12px_rgba(217,184,124,0.3)] transition-[width] duration-300"
                      style={{
                        width: `${
                          total > 0 ? Math.max(0, Math.min(100, (completedCount / total) * 100)) : 0
                        }%`,
                      }}
                    />
                  </div>
                  <p className="mt-2 text-xs text-[#a79b8a]/80">
                    {completedCount} of {total} positions completed
                  </p>
                </section>

                {/* Assists - the puzzles solver's Hint/Solution pair:
                    the same gold/secondary buttons and icons. "Hint"
                    highlights the piece to move; "Solution" plays the
                    stored move and advances. Either reveal costs one
                    incorrect for the position. */}
                <div className="border-t border-white/10 pt-4">
                  <div className="grid grid-cols-2 gap-3">
                    <button
                      type="button"
                      onClick={handleHint}
                      className={GOLD_BUTTON_CLASS}
                    >
                      <BulbIcon />
                      <span>Hint</span>
                    </button>
                    <button
                      type="button"
                      onClick={handleShowSolution}
                      className={SECONDARY_BUTTON_CLASS}
                    >
                      <EyeIcon />
                      <span>Solution</span>
                    </button>
                  </div>
                </div>

                {/* Recording-honesty banner - appears as soon as ANY
                    /review call in this session has failed (non-2xx OR
                    thrown) and persists for the rest of the session.
                    Non-blocking: the user can still drag the next move
                    and the session still advances. */}
                {reviewFailureCount > 0 && (
                  <div
                    role="status"
                    aria-live="polite"
                    className="flex items-center gap-2 rounded-xl border border-red-400/30 bg-red-400/10 px-3 py-2 text-xs text-red-300"
                  >
                    <span>
                      Some attempts couldn&apos;t be recorded - your
                      session may not be fully saved. ({reviewFailureCount}/
                      {completedCount} so far.)
                    </span>
                  </div>
                )}
              </div>
            )}
          </aside>
        }
      />
    </div>
  );
}
