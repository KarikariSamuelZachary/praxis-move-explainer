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
 *   2. SESSION - quiz screen: one stored position at a time. The
 *      board shows the position's FEN (owner-to-move). The user
 *      drags a legal move; we compare UCI vs the stored
 *      position.move field client-side, fire
 *      POST /positions/{position_id}/review with the comparison
 *      result, and tally correct/incorrect counters. After the
 *      last position we POST /sessions/{session_id}/complete with
 *      the final tally and transition to DONE.
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
 *   * The hint button is a no-op (visible "not yet available"
 *     message) - there is no hint source anywhere in the schema, so
 *     the reference's "Show hint" affordance is honored visually but
 *     not fabricated.
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
  findLinePath,
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

function TrainIcon() {
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
      <path d="M9 5 3 12l6 7" />
    </svg>
  );
}

function BookIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20" />
    </svg>
  );
}

function LightbulbIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M9 18h6" />
      <path d="M10 22h4" />
      <path d="M12 2a7 7 0 0 0-4 12.7V17h8v-2.3A7 7 0 0 0 12 2Z" />
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
  const [currentIdx, setCurrentIdx] = useState(0);
  const [correctCount, setCorrectCount] = useState(0);
  const [incorrectCount, setIncorrectCount] = useState(0);
  const [startPending, setStartPending] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [reviewPending, setReviewPending] = useState(false);
  const [completePending, setCompletePending] = useState(false);
  const [hintMessage, setHintMessage] = useState<string | null>(null);
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

  // Quiz items: EVERY owner-side row is its own quiz item - one per
  // saved move, even when several moves share the same position FEN
  // (e.g. both e4 AND f4 stored against the start position). Deduping
  // by FEN used to collapse diverging branches into a single item and
  // silently dropped moves like f4 from the session. The opponent's
  // stored reply is still auto-played on the board after each correct
  // answer instead of being its own quiz. sessionPositions keeps the
  // full row list so the auto-reply lookup can find the opponent's
  // chosen response.
  const quizItems = useMemo(
    () => buildQuizItems(sessionPositions, color),
    [sessionPositions, color]
  );

  const currentPosition =
    currentIdx < quizItems.length ? quizItems[currentIdx] : null;
  const total = quizItems.length;
  const completedCount = currentIdx; // # of positions fully attempted

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
      setSessionId(data.session.id);
      setSessionPositions(data.positions);
      setCurrentIdx(0);
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
  }, [id, mainLinesOnly, startPending]);

  // --- Quiz attempt handler. "Accept either move": a position with
  // several saved branches counts the attempt correct when the played
  // UCI matches ANY row stored at this position's FEN - the session
  // quizzed the POSITION, not one specific branch. The /review POST
  // is fired against the MATCHING row's id (the branch actually
  // played) so the FSRS scheduling update lands on the right row.
  // Fire POST /review, tally counters, advance. If this was the last
  // position, call /complete and transition to DONE.
  const handleAttempt = useCallback(
    async (uci: string) => {
      if (reviewPending) return;
      if (!currentPosition || !sessionId) return;
      // A new user attempt wipes any auto-reply overlay from the
      // prior position so the dragged piece lands on the real
      // current item's position.
      setAutoMoveFen(null);
      const posKey = normalizeFen(currentPosition.fen);
      const matchingRow = sessionPositions.find(
        (p) => normalizeFen(p.fen) === posKey && p.move === uci
      );
      const correct = Boolean(matchingRow);
      const reviewedRowId = matchingRow ? matchingRow.id : currentPosition.id;
      const timeTakenMs = Math.max(
        0,
        Date.now() - shownAtRef.current
      );

      setReviewPending(true);
      // Fire the /review FSRS recording in the BACKGROUND. It must NOT
      // gate the move feedback / reply animation - awaiting the HTTP
      // round-trip on every move is what makes the flow feel laggy.
      // The promise still surfaces failures (non-2xx or throw) into
      // `reviewFailureCount` so the recording-honesty banner appears.
      // NOTE: the URL has no `/review` suffix - the Next.js proxy
      // route lives at /api/repertoires/positions/[position_id] and
      // its POST handler appends `/review` when forwarding to the
      // backend (see src/app/api/repertoires/positions/[position_id]/
      // route.ts). Calling `/…/{id}/review` directly would 404 on the
      // proxy and (wrongly) trip the recording-honesty banner below.
      void (async () => {
        try {
          const res = await fetch(
            `/api/repertoires/positions/${encodeURIComponent(reviewedRowId)}`,
            {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                solved_correctly: correct,
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
      // Tally. A WRONG move stays on the SAME position for a retry -
      // it only bumps the incorrect counter (and fires /review above
      // with solved_correctly=false). Only a CORRECT move advances.
      if (!correct) {
        // Only the FIRST miss on this position adds an incorrect
        // (subsequent wrong moves - or hint clicks - on the same
        // position don't stack). The session still stays on the
        // position for a retry regardless.
        if (incorrectCountedPosRef.current !== currentPosition.id) {
          setIncorrectCount((c) => c + 1);
          incorrectCountedPosRef.current = currentPosition.id;
        }
        shownAtRef.current = Date.now();
        setAttemptFeedback('Incorrect - try again.');
        window.setTimeout(() => setAttemptFeedback(null), 3000);
        setReviewPending(false);
        return;
      }
      setCorrectCount((c) => c + 1);
      const nextIdx = currentIdx + 1;
      shownAtRef.current = Date.now();
      // Auto-play the opponent's stored reply: look up the row whose
      // FEN equals the position after the user's move, briefly show
      // that intermediate position, then show the reply and advance.
      // If no reply exists (line ended), just advance. The whole
      // sequence runs with reviewPending=true so drops are blocked.
      const afterUser = applyUci(currentPosition.fen, uci);
      const isFinalItem = nextIdx >= total;
      // Opponent reply to the user's just-played move: the stored row
      // whose FEN equals the position after the user's move.
      const replyRow = afterUser
        ? sessionPositions.find(
            (p) => normalizeFen(p.fen) === normalizeFen(afterUser)
          )
        : null;
      const replyFen =
        afterUser && replyRow ? applyUci(afterUser, replyRow.move) : null;

      const doAdvance = () => {
        // Clear the auto-reply overlay BEFORE the index advances so
        // the board renders the NEXT item's real FEN. Keeping it
        // pinned here was the bug: when the next quiz item belongs
        // to a different line (e.g. the start position has several
        // first moves), the board stayed frozen on the just-finished
        // line's last position and the session appeared stuck. The
        // applyUci EP-strip makes the reply FEN equal to the next
        // same-line item's stored FEN, so clearing the overlay does
        // not hide the opponent's last move - the next item shows the
        // same position.
        setAutoMoveFen(null);
        if (isFinalItem) {
          // Last quiz item attempted - call /complete and transition.
          // Build the FINAL tally at call time (not from state
          // closures) so the value we send matches the visible
          // counters.
          const finalCorrect = correctCount + 1;
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
                  body: JSON.stringify({ positions_correct: finalCorrect }),
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
          const nextItem = quizItems[nextIdx];
          const sameLine =
            replyFen !== null &&
            normalizeFen(nextItem.fen) === normalizeFen(replyFen);
          const handoff = () => {
            setCurrentIdx(nextIdx);
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
    [currentPosition, currentIdx, correctCount, quizItems, reviewPending, sessionId, sessionPositions, total]
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
      if (!color || reviewPending || !currentPosition) return false;
      // Every stored row is its side-to-move by construction;
      // canDragPiece already restricts drags to the moving side's
      // pieces, so no extra color gate is needed here.
      // Verify the move is legal at this FEN (rejects accidental
      // drops to invalid squares). Use the 4-field FEN + ' 0 1' so
      // chess.js's validator is happy (4-field alone fails
      // validation; the rest of the board rendering only uses the
      // first field anyway).
      const fenForValidation = `${normalizeFen(currentPosition.fen)} 0 1`;
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
    [color, currentPosition, handleAttempt, reviewPending]
  );

  // --- Hint button handler. Shows the SAN of one correct move at
  // the current position AND costs one incorrect - revealing the
  // answer means the position can't also count as solved unaided.
  // Any saved move at this FEN counts as
  // correct (the "accept either move" rule), so we pick the current
  // quiz row's stored move - its SAN is computed from the position's
  // FEN + UCI via chess.js. Falls back to the raw UCI if chess.js
  // can't parse the move (stale/corrupt row - shouldn't happen).
  const handleShowHint = useCallback(() => {
    if (!currentPosition) {
      setHintMessage('No position to hint.');
      window.setTimeout(() => setHintMessage(null), 3000);
      return;
    }
    // A hint costs ONE incorrect per position - once this position has
    // already registered its incorrect (from a wrong move or a prior
    // hint), further hint clicks add nothing.
    if (incorrectCountedPosRef.current !== currentPosition.id) {
      setIncorrectCount((c) => c + 1);
      incorrectCountedPosRef.current = currentPosition.id;
    }
    try {
      const game = new Chess(`${normalizeFen(currentPosition.fen)} 0 1`);
      const m = game.move({
        from: currentPosition.move.slice(0, 2),
        to: currentPosition.move.slice(2, 4),
        promotion: currentPosition.move.length > 4 ? currentPosition.move[4] : undefined,
      });
      setHintMessage(`Try: ${m.san}`);
    } catch {
      setHintMessage(`Try: ${currentPosition.move}`);
    }
    window.setTimeout(() => setHintMessage(null), 5000);
  }, [currentPosition]);

  // --- Cancel/back to detail page (config modal Cancel + DONE
  // "Back to repertoire" link both reuse this).
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

  const configScopeTitle = mainLinesOnly
    ? 'Main lines only'
    : 'Entire repertoire';

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
              {/* Scope title */}
              <div className="flex items-center gap-3">
                <span className="text-2xl text-[#efd9a7]" aria-hidden="true">
                  <BookIcon />
                </span>
                <h2 className="font-display text-xl font-bold text-[#efd9a7]">
                  {configScopeTitle}
                </h2>
              </div>

              {/* Position count display */}
              <div className="flex flex-col items-center gap-2 py-2">
                <p className="text-sm text-[#a79b8a]">
                  Positions that will be trained:
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
                  <>
                    <TrainIcon />
                    <span>Train</span>
                    <ArrowRightIcon />
                  </>
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

              {/* Footer actions - matches the reference's
                  "Additional training settings" + "Cancel" layout,
                  with the settings link collapsed to a static label
                  (out of scope for v1). */}
              <div className="flex items-center justify-between gap-3 border-t border-white/5 pt-4 text-sm">
                <span className="text-[#a79b8a]/60">
                  Additional training settings
                </span>
                <button
                  type="button"
                  onClick={handleBackToDetail}
                  className="font-bold uppercase tracking-wider text-[#d9b87c] transition hover:text-[#efd9a7]"
                >
                  Cancel
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ----- Phase: DONE ------------------------------------------------

  if (phase === 'done') {
    const total = quizItems.length;
    // Wrong moves (and hint clicks) no longer advance the session, so
    // correctCount always ends at `total` - the honest score is
    // ACCURACY: correct solves over total attempts (retries + hints
    // included in the denominator via incorrectCount).
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
    <div className="relative -mt-2 h-[calc(100vh-2.5rem)] w-full overflow-y-auto px-6 pb-[10px] pt-6 text-white lg:overflow-hidden lg:px-10 [background-image:url(/walnut-dark.webp)] [background-size:cover] [background-position:center]">
      {/*
        Session layout - mirrored from the build page (ReviewShell) so a
        user moving between the two routes sees no board-size jump.
        Import panel (left) carries only the back button (the build
        page's name + Train button chrome is intentionally omitted
        here - the train flow has nothing to author or label). Board
        panel (center) holds the quiz board. Analysis panel (right)
        holds the "White to move / What's your move?" prompt card with
        counters + hint.
      */}
      <ReviewShell
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
            {currentPosition ? (
              <BoardShell
                position={autoMoveFen ?? currentPosition.fen}
                orientation={boardOrientation}
                allowDragging={!reviewPending}
                canDragPiece={({ piece }) => {
                  if (reviewPending || !color || !currentPosition) return false;
                  // The session includes BOTH sides' plies, so the
                  // draggable pieces are whichever color is on turn
                  // in the CURRENT position (not the repertoire
                  // owner's color).
                  const side = currentPosition.fen.split(/\s+/)[1];
                  return piece.pieceType[0] === side;
                }}
                onMove={(source, target, promotion) =>
                  handleDrop(source, target, promotion ?? 'q')
                    ? true
                    : false
                }
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
          <aside className="flex h-full flex-col gap-4 overflow-hidden">
            {currentPosition && (
              <div className={`${CARD_CLASS} flex flex-col gap-4 p-5`}>
                {/* Prompt. Per the task: opponent's prior move is NOT
                    derivable from the schema, so we use the suggested
                    fallback wording ("What's your move here?"). We DO
                    derive the owner color from the position's side-to-
                    move field so the prompt reflects whose move it is. */}
                <p className="text-center text-base font-medium text-[#efd9a7]">
                  {(() => {
                    const side = currentPosition.fen.split(/\s+/)[1];
                    const label =
                      side === 'w' ? 'White' : side === 'b' ? 'Black' : 'Your';
                    return `${label} to move. What\u2019s your move?`;
                  })()}
                </p>

                {/* Wrong-attempt feedback - a rejected move keeps the
                    user on this position, so say so explicitly. */}
                {attemptFeedback && (
                  <p
                    role="status"
                    aria-live="polite"
                    className="rounded-full border border-red-400/30 bg-red-400/10 px-3 py-1 text-center text-xs text-red-300"
                  >
                    {attemptFeedback}
                  </p>
                )}

                {/* Counters row + progress bar (matches reference-5
                    layout) */}
                <div className="flex w-full flex-col gap-3">
                  <div className="flex items-center justify-between gap-4 px-2 text-sm">
                    <span className="font-display font-bold text-emerald-300">
                      {correctCount} correct
                    </span>
                    <span className="font-display font-bold text-red-300">
                      {incorrectCount} incorrect
                    </span>
                    <span className="font-display text-[#a79b8a]">
                      {completedCount}/{total} positions completed
                    </span>
                  </div>
                  <div className="relative h-2 overflow-hidden rounded-full bg-black/45">
                    <div
                      className="absolute inset-y-0 left-0 rounded-full bg-[#d9b87c]/80 transition-[width] duration-300"
                      style={{
                        width: `${
                          total > 0 ? Math.max(0, Math.min(100, (completedCount / total) * 100)) : 0
                        }%`,
                      }}
                    />
                  </div>
                </div>

                {/* Hint button - no-op with visible "not yet available"
                    message, per the task: no hint source in the schema,
                    so we honor the reference-5 affordance visually but
                    don't fabricate content. */}
                <button
                  type="button"
                  onClick={handleShowHint}
                  className="flex h-11 items-center justify-center gap-2 rounded-full border border-[#d9b87c]/40 bg-black/40 px-5 text-sm font-medium text-[#efd9a7] transition hover:border-[#d9b87c] hover:bg-[#d9b87c]/10"
                >
                  <LightbulbIcon />
                  <span>Show hint</span>
                </button>
                {hintMessage && (
                  <p
                    role="status"
                    className="rounded-full border border-[#a79b8a]/30 bg-black/30 px-3 py-1 text-xs text-[#a79b8a]"
                  >
                    {hintMessage}
                  </p>
                )}

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
                      {currentIdx + (reviewPending ? 0 : 1)} so far.)
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
