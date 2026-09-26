'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess, Square } from 'chess.js';
import { Chessboard, type SquareRenderer } from 'react-chessboard';

import PromotionPicker from '@/components/board/PromotionPicker';

import {
  EndgameFetchError,
  EndgameGradeResult,
  EndgameMoveSubmission,
  EndgameSubmitMove,
  moveToUci,
  requestEndgameHint,
  requestEndgamePlayoutReply,
  storedReplyForFen,
  submitEndgameMove,
  uciToMove,
} from '@/lib/endgames';
import {
  EndgameHintResponse,
  EndgameMoveResponse,
  EndgamePlayoutEnding,
  EndgamePosition,
  EndgameStatus,
} from '@/types';

export type EndgameBoardPhase =
  | 'playing'
  | 'submitting'
  | 'opponent'
  | 'resolved'
  // "Play it out": the settled FAILED drill re-opened for a no-stakes
  // continuation. 'playout' = the user may move (the recorded result is not
  // affected); 'playout-reply' = the defender is thinking/replying.
  | 'playout'
  | 'playout-reply'
  | 'error';

export interface EndgameBoardProps<
  Result extends EndgameGradeResult = EndgameMoveResponse,
> {
  position: EndgamePosition;
  /**
   * Which route grades this replay. Defaults to the rated trainer's
   * POST /api/endgames/move; the Woodpecker review tab injects the
   * per-entry attempts endpoint instead. Everything else -- the chess.js
   * state machine, stored-line replies, resolution handling -- is shared,
   * because the interaction contract is identical.
   */
  submitMove?: EndgameSubmitMove<Result>;
  /**
   * Re-open a settled FAILED drill for continued play. Playout moves are
   * applied locally and never submitted for grading; the defender's replies
   * come from the read-only /api/endgames/playout/reply route, which cannot
   * change the already-recorded result. Ignored unless the drill actually
   * failed.
   */
  playout?: boolean;
  /** Fired when the continuation finishes: the game reached a terminal
   *  position (or could not continue). `ending` is the rule that ended it,
   *  or null when the stored line simply ran out. */
  onPlayoutResolved?: (ending: EndgamePlayoutEnding | null) => void;
  /** Hints this drill attempt has revealed so far, as the caller counted
   *  them. Attached to every graded submission; the backend reads it only on
   *  a resolving move (a hinted solve is neutral on the rated surface and
   *  not-clean on the review surface). */
  hintsUsed?: number;
  /** True when the current attempt is a retry of an already-recorded drill
   *  (the panel's Retry). Attached to every submission; a resolving move is
   *  then graded for the verdict but writes nothing. */
  retry?: boolean;
  /** Bumps to request a hint for the current position (the panel's "Hint"):
   *  only the piece to move is highlighted, nothing is played. A counter so
   *  repeat presses are distinct requests. */
  hintRequest?: number;
  /** Bumps to request the immediate best move for the current position (the
   *  panel's "Show move"). The board fetches it and plays it as the user's
   *  move through the normal grading path. A counter so repeat presses are
   *  distinct. */
  solutionRequest?: number;
  /** Fired once a hint or solution move has been fetched. The hint itself
   *  submits nothing: the caller counts it into hintsUsed and the user plays
   *  the move through the normal grading path. The solution path plays the
   *  move itself, so the board counts it before submitting. */
  onHintRevealed?: (hint: EndgameHintResponse) => void;
  onUserMove?: (san: string) => void;
  onOpponentMove?: (san: string) => void;
  onThinkingChange?: (thinking: boolean) => void;
  onDrillResolved?: (result: Result, historySan: string[]) => void;
}

type HighlightSquares = Record<string, React.CSSProperties>;

interface PendingMove {
  fenBefore: string;
  fenAfter: string;
  uci: string;
  san: string;
  from: string;
  to: string;
  /** True once the move is in the tracked drill history (set after the
   *  server accepts it, so the error banner's Retry cannot record twice). */
  recorded?: boolean;
}

interface PendingPromotion {
  from: string;
  to: string;
}

// Same board-ring + wood frame as the Puzzles ChessBoard, so the two drill
// screens read as one product.
const BOARD_FRAME_STYLE: React.CSSProperties = {
  padding: '14px',
  background:
    'linear-gradient(rgba(0,0,0,0.5),rgba(0,0,0,0.5)), url(/walnut-dark.webp)',
  backgroundSize: 'cover',
  backgroundPosition: 'center',
  borderRadius: '6px',
  boxShadow:
    '0 0 0 2px #1a0a02, inset 0 2px 0 rgba(255,200,100,0.12), inset 0 -2px 0 rgba(0,0,0,0.5), 0 12px 40px rgba(0,0,0,0.6)',
};

const HIGHLIGHT_USER = 'rgba(16, 185, 129, 0.4)';
const HIGHLIGHT_OPPONENT = 'rgba(255, 170, 0, 0.35)';
const HIGHLIGHT_SOLVED = 'rgba(16, 185, 129, 0.6)';
const HIGHLIGHT_FAILED = 'rgba(239, 68, 68, 0.5)';
// Playout moves keep the settled/aftermath framing: a muted neutral instead
// of the live loop's user-emerald and opponent-amber, so the board never
// reads as "the drill is still on".
const HIGHLIGHT_PLAYOUT = 'rgba(217, 184, 124, 0.3)';
// "Hint" highlights just the piece to move, in the same emerald the Puzzles
// board's hint uses. It fades on its own (HINT_FADE_MS).
const HIGHLIGHT_HINT = 'rgba(16, 185, 129, 0.4)';
const HINT_FADE_MS = 4000;
// "Show move" plays the answer, so its trail reads as an assisted move rather
// than a found one: the origin square in light walnut, the destination in
// deep walnut.
const HIGHLIGHT_SOLUTION_FROM = 'rgba(217, 184, 124, 0.6)';
const HIGHLIGHT_SOLUTION_TO = 'rgba(74, 45, 20, 0.8)';

// Puzzles waits 600ms before playing the stored reply; a converted drill
// should feel like the same opponent answering. The wait is measured from
// the USER'S MOVE, not from the grading response: a slow round trip is
// absorbed into the 600ms instead of being added on top of it (the boards
// used to wait the full 600ms again after every response, which doubled the
// perceived reply time). The piece animation is 200ms, so anything at or
// above that still reads naturally.
const OPPONENT_DELAY_MS = 600;

function isMovablePhase(phase: EndgameBoardPhase): boolean {
  return phase === 'playing' || phase === 'playout';
}

function buildHighlight(
  from: string,
  to: string,
  color: string
): HighlightSquares {
  return {
    [from]: { backgroundColor: color },
    [to]: { backgroundColor: color },
  };
}

function buildGame(position: EndgamePosition): Chess {
  return new Chess(position.fen);
}

function getOrientation(position: EndgamePosition): 'white' | 'black' {
  return position.fen.split(/\s+/)[1] === 'b' ? 'black' : 'white';
}

/** First 4 FEN fields (board/turn/castling/ep): the same normalization the
 *  backend's fen_after / history cross-checks use. */
function positionKey(fen: string): string {
  return fen.split(/\s+/).slice(0, 4).join(' ');
}

/**
 * The rule that ended the game on `game`, or null while it is live.
 *
 * Precedence mirrors python-chess's `is_game_over()` chain. chess.js's own
 * `isThreefoldRepetition()` is unusable here: every ply is applied on a
 * fresh Chess built from a FEN, so its position counter never spans the
 * drill. The tracked `positionKeys` (oldest first, current last) are counted
 * instead -- on the third occurrence the draw is taken, the same
 * auto-adjudication the grader applies to the claim.
 */
function detectEnding(
  game: Chess,
  positionKeys: string[]
): EndgamePlayoutEnding | null {
  if (game.isCheckmate()) return 'checkmate';
  if (game.isStalemate()) return 'stalemate';
  if (game.isInsufficientMaterial()) return 'insufficient_material';
  if (game.isDrawByFiftyMoves()) return 'fifty_move_rule';
  const current = positionKeys[positionKeys.length - 1];
  if (positionKeys.filter((key) => key === current).length >= 3) {
    return 'threefold_repetition';
  }
  return null;
}

/**
 * The full-resolution drill board.
 *
 * Deliberately NOT the Puzzles board: there is no stored solution to
 * validate against client-side. Every user move is legal chess, posted to
 * the grading route (rated trainer by default, Woodpecker review when
 * injected) and graded server-side; the position plays out to real
 * resolution (checkmate or a legitimate board draw) against a defender the
 * backend generates. While the move is still on the stored line the server
 * stays silent (opponent_reply=null) and this component applies the stored
 * reply itself via the same positional matcher the backend uses.
 */
export default function EndgameBoard<
  Result extends EndgameGradeResult = EndgameMoveResponse,
>({
  position,
  submitMove,
  playout = false,
  onPlayoutResolved,
  hintsUsed = 0,
  retry = false,
  hintRequest = 0,
  solutionRequest = 0,
  onHintRevealed,
  onUserMove,
  onOpponentMove,
  onThinkingChange,
  onDrillResolved,
}: EndgameBoardProps<Result>) {
  const initialGame = useMemo(() => buildGame(position), [position]);
  const [game, setGame] = useState<Chess>(initialGame);
  const [phase, setPhase] = useState<EndgameBoardPhase>('playing');
  const [selectedSquare, setSelectedSquare] = useState<string | null>(null);
  const [pendingPromotion, setPendingPromotion] =
    useState<PendingPromotion | null>(null);
  const [highlightSquares, setHighlightSquares] = useState<HighlightSquares>({});
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [canRetry, setCanRetry] = useState(false);
  const [playoutError, setPlayoutError] = useState<string | null>(null);
  const [hintError, setHintError] = useState<string | null>(null);
  const [hintLoading, setHintLoading] = useState(false);
  // The piece-only hint highlight, kept apart from the move highlights so its
  // fade can never wipe the user's played-move tint.
  const [hintFromSquare, setHintFromSquare] = useState<string | null>(null);

  const gameRef = useRef<Chess>(initialGame);
  const phaseRef = useRef<EndgameBoardPhase>('playing');
  const pendingMoveRef = useRef<PendingMove | null>(null);
  const opponentTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // performance.now() when the current user move was accepted. The opponent
  // delay is measured from here so a slow grading round trip counts toward
  // it (see OPPONENT_DELAY_MS).
  const userMoveAtRef = useRef(0);
  // The last graded verdict, kept so playout only opens on a genuine failed
  // drill, plus the one-shot guard so the continuation starts once.
  const resolvedStatusRef = useRef<EndgameStatus | null>(null);
  const playoutStartedRef = useRef(false);

  // The drill's played moves and position keys. Tracked here because every
  // ply is applied on a fresh Chess built from a FEN, so chess.js's own
  // history and position counter never span the drill. UCI goes to the
  // server for threefold adjudication; the keys let the playout detect
  // repetition client-side; SAN backs onDrillResolved's move list.
  const playedUciRef = useRef<string[]>([]);
  const playedSanRef = useRef<string[]>([]);
  const positionKeysRef = useRef<string[]>([positionKey(position.fen)]);

  // Prop callbacks live in refs so the async grading flow never closes over
  // a stale render's handlers.
  const onUserMoveRef = useRef(onUserMove);
  const onOpponentMoveRef = useRef(onOpponentMove);
  const onThinkingChangeRef = useRef(onThinkingChange);
  const onDrillResolvedRef = useRef(onDrillResolved);
  const onPlayoutResolvedRef = useRef(onPlayoutResolved);
  const submitMoveRef = useRef<EndgameSubmitMove<Result> | null>(null);
  const hintsUsedRef = useRef(hintsUsed);
  const retryRef = useRef(retry);
  const hintHandledRef = useRef(0);
  const solutionHandledRef = useRef(0);
  const hintInFlightRef = useRef(false);
  const hintTimeoutRef = useRef<number | null>(null);
  const onHintRevealedRef = useRef(onHintRevealed);
  useEffect(() => {
    onUserMoveRef.current = onUserMove;
    onOpponentMoveRef.current = onOpponentMove;
    onThinkingChangeRef.current = onThinkingChange;
    onDrillResolvedRef.current = onDrillResolved;
    onPlayoutResolvedRef.current = onPlayoutResolved;
    onHintRevealedRef.current = onHintRevealed;
    hintsUsedRef.current = hintsUsed;
    retryRef.current = retry;
    submitMoveRef.current = submitMove ?? null;
  });

  // The rated trainer endpoint, and its response shape, are the default.
  // Callers that widen Result must inject their own submitter (the review
  // page does); the cast only exists so an injected submitter can hand the
  // resolved response to onDrillResolved with its full type.
  const defaultSubmitMove = useCallback<EndgameSubmitMove<EndgameMoveResponse>>(
    (submission: EndgameMoveSubmission) =>
      submitEndgameMove({ position_id: position.id, ...submission }),
    [position.id]
  );

  const updatePhase = useCallback((next: EndgameBoardPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const setBoard = useCallback((nextGame: Chess) => {
    gameRef.current = nextGame;
    setGame(nextGame);
  }, []);

  const recordMove = useCallback(
    (move: { uci: string; san: string; fenAfter: string }) => {
      playedUciRef.current = [...playedUciRef.current, move.uci];
      playedSanRef.current = [...playedSanRef.current, move.san];
      positionKeysRef.current = [
        ...positionKeysRef.current,
        positionKey(move.fenAfter),
      ];
    },
    []
  );

  const clearOpponentTimer = useCallback(() => {
    if (opponentTimerRef.current) {
      clearTimeout(opponentTimerRef.current);
      opponentTimerRef.current = null;
    }
  }, []);

  // What remains of OPPONENT_DELAY_MS after the user's move until now. The
  // round trip (grading, and for generated replies the tablebase work) is
  // part of the wait, so only the remainder is held back here; an instant
  // response still gets the full, natural pause.
  const remainingOpponentDelay = useCallback((): number => {
    const elapsed = performance.now() - userMoveAtRef.current;
    return Math.max(0, OPPONENT_DELAY_MS - elapsed);
  }, []);

  useEffect(() => clearOpponentTimer, [clearOpponentTimer]);

  // The single resolution path for a graded drill: stop the clock, record
  // the verdict, and hand it to the page with the drill's full move list.
  const finishResolved = useCallback(
    (result: Result) => {
      clearOpponentTimer();
      resolvedStatusRef.current = result.status;
      updatePhase('resolved');
      onThinkingChangeRef.current?.(false);
      onDrillResolvedRef.current?.(result, [...playedSanRef.current]);
    },
    [clearOpponentTimer, updatePhase]
  );

  const applyOpponentReply = useCallback(
    (replyUci: string, fenBefore: string, resolvedResult?: Result) => {
      updatePhase('opponent');
      clearOpponentTimer();
      opponentTimerRef.current = setTimeout(() => {
        opponentTimerRef.current = null;
        const replyGame = new Chess(fenBefore);
        let applied;
        try {
          applied = replyGame.move(uciToMove(replyUci));
        } catch {
          applied = null;
        }
        if (!applied) {
          // A resolving reply that could not be replayed is dropped and the
          // verdict still lands; otherwise stay playable.
          if (resolvedResult) {
            finishResolved(resolvedResult);
            return;
          }
          updatePhase('playing');
          onThinkingChangeRef.current?.(false);
          return;
        }
        setBoard(replyGame);
        setHighlightSquares(
          buildHighlight(
            applied.from,
            applied.to,
            resolvedResult
              ? resolvedResult.status === 'solved'
                ? HIGHLIGHT_SOLVED
                : HIGHLIGHT_FAILED
              : HIGHLIGHT_OPPONENT
          )
        );
        onOpponentMoveRef.current?.(applied.san);
        recordMove({
          uci: replyUci,
          san: applied.san,
          fenAfter: replyGame.fen(),
        });
        if (resolvedResult) {
          // The defender's own move ended the game (fifty-move clock on the
          // second mover's halfmove, stalemate, ...): show it on the board,
          // then resolve with the server's verdict.
          finishResolved(resolvedResult);
          return;
        }
        updatePhase('playing');
        onThinkingChangeRef.current?.(false);
      }, remainingOpponentDelay());
    },
    [
      clearOpponentTimer,
      finishResolved,
      recordMove,
      remainingOpponentDelay,
      setBoard,
      updatePhase,
    ]
  );

  // --- "Play it out": the settled FAILED drill's continuation -------------
  // Nothing in this section is graded or recorded: the verdict already
  // happened, so the board just keeps playing with the shared defender
  // generator. The framing stays on the failed/settled side throughout.

  const finishPlayout = useCallback(
    (ending: EndgamePlayoutEnding | null) => {
      clearOpponentTimer();
      onThinkingChangeRef.current?.(false);
      updatePhase('resolved');
      onPlayoutResolvedRef.current?.(ending);
    },
    [clearOpponentTimer, updatePhase]
  );

  const applyPlayoutReply = useCallback(
    (replyUci: string, fenBefore: string) => {
      clearOpponentTimer();
      opponentTimerRef.current = setTimeout(() => {
        opponentTimerRef.current = null;
        const replyGame = new Chess(fenBefore);
        let applied;
        try {
          applied = replyGame.move(uciToMove(replyUci));
        } catch {
          applied = null;
        }
        if (!applied) {
          finishPlayout(null);
          return;
        }
        setBoard(replyGame);
        setHighlightSquares(
          buildHighlight(applied.from, applied.to, HIGHLIGHT_PLAYOUT)
        );
        onThinkingChangeRef.current?.(false);
        recordMove({
          uci: replyUci,
          san: applied.san,
          fenAfter: replyGame.fen(),
        });
        const ending = detectEnding(replyGame, positionKeysRef.current);
        if (ending) {
          finishPlayout(ending);
          return;
        }
        updatePhase('playout');
      }, remainingOpponentDelay());
    },
    [
      clearOpponentTimer,
      finishPlayout,
      recordMove,
      remainingOpponentDelay,
      setBoard,
      updatePhase,
    ]
  );

  const requestPlayoutReply = useCallback(
    async (fen: string) => {
      setPlayoutError(null);
      // Locked and "thinking" while the defender's reply is fetched: the
      // board is only interactive on the user's turns.
      updatePhase('playout-reply');
      onThinkingChangeRef.current?.(true);
      try {
        const response = await requestEndgamePlayoutReply({
          position_id: position.id,
          fen_after: fen,
        });
        // Same stored-line contract as a graded replay: null means the
        // client already holds the next stored move and applies it itself.
        const replyUci =
          response.opponent_reply?.move_uci ??
          storedReplyForFen(position.fen, position.moves, fen);
        if (!replyUci) {
          finishPlayout(null);
          return;
        }
        applyPlayoutReply(replyUci, fen);
      } catch (error) {
        const fetchError =
          error instanceof EndgameFetchError
            ? error
            : new EndgameFetchError('The defender could not reply.', 0, true);
        setPlayoutError(fetchError.message);
        onThinkingChangeRef.current?.(false);
      }
    },
    [applyPlayoutReply, finishPlayout, position.fen, position.id, position.moves, updatePhase]
  );

  useEffect(() => {
    if (!playout) {
      playoutStartedRef.current = false;
      return;
    }
    if (playoutStartedRef.current) return;
    if (phaseRef.current !== 'resolved' || resolvedStatusRef.current !== 'failed') {
      return;
    }
    playoutStartedRef.current = true;

    // The failed move left the defender to move -- unless that move already
    // ended the game, in which case the continuation is over before it
    // starts (with the same ending the verdict reported).
    const settled = gameRef.current;
    const settledEnding = detectEnding(settled, positionKeysRef.current);
    if (settledEnding) {
      finishPlayout(settledEnding);
      return;
    }
    void requestPlayoutReply(settled.fen());
  }, [playout, finishPlayout, requestPlayoutReply]);

  const clearHintTimeout = useCallback(() => {
    if (hintTimeoutRef.current !== null) {
      window.clearTimeout(hintTimeoutRef.current);
      hintTimeoutRef.current = null;
    }
  }, []);

  const clearHint = useCallback(() => {
    clearHintTimeout();
    setHintFromSquare(null);
  }, [clearHintTimeout]);

  useEffect(() => clearHint, [clearHint]);

  const requestHintMove = useCallback(async () => {
    // A hint only means anything for the live drill on the user's turn:
    // during the defender's think (or a playout) the best move would answer
    // for the wrong side, and a settled drill has nothing left to reveal.
    if (phaseRef.current !== 'playing') return;
    if (hintInFlightRef.current) return;
    hintInFlightRef.current = true;
    const fenBefore = gameRef.current.fen();
    setHintError(null);
    setHintLoading(true);
    try {
      const hint = await requestEndgameHint({
        position_id: position.id,
        fen: fenBefore,
      });
      // A slow answer (6-7-man material costs a Lichess probe sweep) can
      // land after the board has moved on: a hint for a position that is no
      // longer on the board is never shown and never counted.
      if (
        phaseRef.current !== 'playing' ||
        gameRef.current.fen() !== fenBefore
      ) {
        return;
      }
      // Puzzles-style: only the piece to move is highlighted, and it fades
      // on its own. The destination is deliberately withheld.
      const { from } = uciToMove(hint.move_uci);
      clearHintTimeout();
      setHintFromSquare(from);
      hintTimeoutRef.current = window.setTimeout(() => {
        hintTimeoutRef.current = null;
        setHintFromSquare(null);
      }, HINT_FADE_MS);
      onHintRevealedRef.current?.(hint);
    } catch (error) {
      const fetchError =
        error instanceof EndgameFetchError
          ? error
          : new EndgameFetchError('Could not get a hint.', 0, true);
      setHintError(fetchError.message);
    } finally {
      setHintLoading(false);
      hintInFlightRef.current = false;
    }
  }, [clearHintTimeout, position.id]);

  useEffect(() => {
    if (!hintRequest || hintRequest === hintHandledRef.current) {
      return;
    }
    hintHandledRef.current = hintRequest;
    void requestHintMove();
  }, [hintRequest, requestHintMove]);

  const postPendingMove = useCallback(async () => {
    const pending = pendingMoveRef.current;
    if (!pending) return;

    updatePhase('submitting');
    onThinkingChangeRef.current?.(true);
    setErrorMessage(null);

    try {
      // The cast is safe by contract: this fallback is only reachable when
      // the caller did not inject a submitter, i.e. when Result is the
      // default EndgameMoveResponse (the rated trainer page).
      const submit: EndgameSubmitMove<Result> =
        submitMoveRef.current ??
        (defaultSubmitMove as unknown as EndgameSubmitMove<Result>);
      const result = await submit({
        fen_before: pending.fenBefore,
        move: pending.uci,
        fen_after: pending.fenAfter,
        hints_used: hintsUsedRef.current,
        retry: retryRef.current,
        history: [...playedUciRef.current],
      });

      // The server accepted the move: it is now part of the drill and counts
      // toward repetition. Recorded once, so the error banner's Retry
      // re-submitting the same pending move cannot double-count.
      if (!pending.recorded) {
        recordMove({
          uci: pending.uci,
          san: pending.san,
          fenAfter: pending.fenAfter,
        });
        pending.recorded = true;
      }

      setCanRetry(false);
      onUserMoveRef.current?.(pending.san);

      if (result.status === 'in_progress') {
        // Within the stored line the server deliberately answers with no
        // move (the client owns the line); past it the server generated one.
        const replyUci =
          result.opponent_reply?.move_uci ??
          storedReplyForFen(position.fen, position.moves, pending.fenAfter);

        if (!replyUci) {
          // Unreachable per the endpoint contract; stay playable rather
          // than freezing the drill.
          updatePhase('playing');
          onThinkingChangeRef.current?.(false);
          return;
        }

        applyOpponentReply(replyUci, pending.fenAfter);
        return;
      }

      // A resolved result can still carry the defender's final move when
      // that move is what ended the game (the route adjudicates it so a
      // stalemated user is not stuck with no move to submit). Play it, then
      // show the verdict.
      if (result.opponent_reply?.move_uci) {
        applyOpponentReply(
          result.opponent_reply.move_uci,
          pending.fenAfter,
          result
        );
        return;
      }

      // solved | failed -- the drill resolved on the user's move.
      setHighlightSquares(
        buildHighlight(
          pending.from,
          pending.to,
          result.status === 'solved' ? HIGHLIGHT_SOLVED : HIGHLIGHT_FAILED
        )
      );
      finishResolved(result);
    } catch (error) {
      const fetchError =
        error instanceof EndgameFetchError
          ? error
          : new EndgameFetchError('The move was not accepted.', 0, true);

      if (fetchError.retryable) {
        // Keep the move on the board (it may still be graded on retry) and
        // let the user retry or undo it.
        setErrorMessage(
          `${fetchError.message} Your move is still on the board.`
        );
        setCanRetry(true);
        updatePhase('error');
        onThinkingChangeRef.current?.(false);
        return;
      }

      // 400/404/409: the client's game state disagrees with the drill, so
      // the move was never graded. Take it back and stay playable.
      const reverted = new Chess(pending.fenBefore);
      setBoard(reverted);
      pendingMoveRef.current = null;
      setHighlightSquares({});
      setErrorMessage('That move could not be verified. Play another.');
      setCanRetry(false);
      updatePhase('playing');
      onThinkingChangeRef.current?.(false);
    }
  }, [
    applyOpponentReply,
    defaultSubmitMove,
    finishResolved,
    position,
    recordMove,
    setBoard,
    updatePhase,
  ]);

  const submitUserMove = useCallback(
    (from: string, to: string, promotion?: string, assisted = false) => {
      const inPlayout = phaseRef.current === 'playout';
      if (!inPlayout && phaseRef.current !== 'playing') return;
      // The hint described the position before this move; it is stale now.
      clearHint();

      const fenBefore = gameRef.current.fen();
      const nextGame = new Chess(fenBefore);
      let move;
      try {
        move = nextGame.move({ from, to, promotion });
      } catch {
        return;
      }
      if (!move) return;

      // The opponent-delay clock runs from the user's move, not the
      // response; see remainingOpponentDelay().
      userMoveAtRef.current = performance.now();

      if (inPlayout) {
        // Settled-drill continuation: applied locally, never graded, and no
        // move/opponent callbacks fire -- the recorded stats stay frozen at
        // the failure. The defender answers from the read-only route.
        setBoard(nextGame);
        setSelectedSquare(null);
        setHighlightSquares(buildHighlight(move.from, move.to, HIGHLIGHT_PLAYOUT));
        recordMove({
          uci: moveToUci(move),
          san: move.san,
          fenAfter: nextGame.fen(),
        });
        const ending = detectEnding(nextGame, positionKeysRef.current);
        if (ending) {
          finishPlayout(ending);
          return;
        }
        void requestPlayoutReply(nextGame.fen());
        return;
      }

      pendingMoveRef.current = {
        fenBefore,
        fenAfter: nextGame.fen(),
        uci: moveToUci(move),
        san: move.san,
        from: move.from,
        to: move.to,
      };

      setBoard(nextGame);
      setSelectedSquare(null);
      // An assisted move (played by "Show move") gets the walnut trail; a
      // move the user found keeps the emerald one.
      setHighlightSquares(
        assisted
          ? {
              [move.from]: { backgroundColor: HIGHLIGHT_SOLUTION_FROM },
              [move.to]: { backgroundColor: HIGHLIGHT_SOLUTION_TO },
            }
          : buildHighlight(move.from, move.to, HIGHLIGHT_USER)
      );
      void postPendingMove();
    },
    [
      clearHint,
      finishPlayout,
      postPendingMove,
      recordMove,
      requestPlayoutReply,
      setBoard,
    ]
  );

  // "Show move": fetch the immediate best move for the position and play it
  // as the user's move, through the normal grading path. The answer was
  // revealed rather than found, so the hint count is bumped BEFORE the
  // submission goes out: the payload carries it, and the backend neutralizes
  // a solution-assisted solve exactly like a hint-assisted one.
  const requestSolutionMove = useCallback(async () => {
    if (phaseRef.current !== 'playing') return;
    if (hintInFlightRef.current) return;
    hintInFlightRef.current = true;
    const fenBefore = gameRef.current.fen();
    setHintError(null);
    setHintLoading(true);
    try {
      const hint = await requestEndgameHint({
        position_id: position.id,
        fen: fenBefore,
      });
      // Same stale-answer guard as the hint: a move for a position that is no
      // longer on the board is never played and never counted.
      if (
        phaseRef.current !== 'playing' ||
        gameRef.current.fen() !== fenBefore
      ) {
        return;
      }
      hintsUsedRef.current += 1;
      onHintRevealedRef.current?.(hint);
      const { from, to, promotion } = uciToMove(hint.move_uci);
      submitUserMove(from, to, promotion, true);
    } catch (error) {
      const fetchError =
        error instanceof EndgameFetchError
          ? error
          : new EndgameFetchError('Could not get the solution move.', 0, true);
      setHintError(fetchError.message);
    } finally {
      setHintLoading(false);
      hintInFlightRef.current = false;
    }
  }, [position.id, submitUserMove]);

  useEffect(() => {
    if (!solutionRequest || solutionRequest === solutionHandledRef.current) {
      return;
    }
    solutionHandledRef.current = solutionRequest;
    void requestSolutionMove();
  }, [solutionRequest, requestSolutionMove]);

  const undoPendingMove = useCallback(() => {
    const pending = pendingMoveRef.current;
    if (!pending) return;
    clearOpponentTimer();
    setBoard(new Chess(pending.fenBefore));
    pendingMoveRef.current = null;
    setHighlightSquares({});
    setErrorMessage(null);
    setCanRetry(false);
    updatePhase('playing');
    onThinkingChangeRef.current?.(false);
  }, [clearOpponentTimer, setBoard, updatePhase]);

  const dismissError = useCallback(() => {
    setErrorMessage(null);
    setCanRetry(false);
  }, []);

  const onDrop = useCallback(
    (sourceSquare: string, targetSquare: string, pieceType: string) => {
      if (!isMovablePhase(phaseRef.current)) return false;

      const legalMove = gameRef.current
        .moves({ square: sourceSquare as Square, verbose: true })
        .some((move) => move.to === targetSquare);
      if (!legalMove) return false;

      // react-chessboard v5 pieceType is color-prefixed ("wP"), so test the
      // piece letter (chess.js's click path hands us a bare "p").
      if (
        pieceType[pieceType.length - 1].toLowerCase() === 'p' &&
        (targetSquare[1] === '8' || targetSquare[1] === '1')
      ) {
        setPendingPromotion({ from: sourceSquare, to: targetSquare });
        return false;
      }

      submitUserMove(sourceSquare, targetSquare);
      return true;
    },
    [submitUserMove]
  );

  const onSquareClick = useCallback(
    ({ square }: { piece: { pieceType: string } | null; square: string }) => {
      if (!isMovablePhase(phaseRef.current)) {
        setSelectedSquare(null);
        return;
      }

      const clickedPiece = gameRef.current.get(square as Square);
      const isOwnPiece = clickedPiece?.color === gameRef.current.turn();

      if (!selectedSquare) {
        setSelectedSquare(isOwnPiece ? square : null);
        return;
      }
      if (selectedSquare === square) {
        setSelectedSquare(null);
        return;
      }

      const sourcePiece = gameRef.current.get(selectedSquare as Square);
      const legalMove = gameRef.current
        .moves({ square: selectedSquare as Square, verbose: true })
        .some((move) => move.to === square);

      if (legalMove && sourcePiece) {
        if (
          sourcePiece.type === 'p' &&
          (square[1] === '8' || square[1] === '1')
        ) {
          setPendingPromotion({ from: selectedSquare, to: square });
          return;
        }
        submitUserMove(selectedSquare, square);
        return;
      }

      setSelectedSquare(isOwnPiece ? square : null);
    },
    [selectedSquare, submitUserMove]
  );

  const onPromotionPieceSelect = useCallback(
    (piece?: string) => {
      if (piece && pendingPromotion) {
        submitUserMove(pendingPromotion.from, pendingPromotion.to, piece);
      }
      setPendingPromotion(null);
    },
    [pendingPromotion, submitUserMove]
  );

  const displayedSquareStyles = useMemo(() => {
    return {
      // The piece-only hint sits under the move highlights: a played move
      // always wins the square.
      ...(hintFromSquare
        ? { [hintFromSquare]: { backgroundColor: HIGHLIGHT_HINT } }
        : {}),
      ...highlightSquares,
      ...(selectedSquare
        ? {
            [selectedSquare]: {
              ...highlightSquares[selectedSquare],
              backgroundColor: 'rgba(255, 170, 0, 0.35)',
            },
          }
        : {}),
    };
  }, [highlightSquares, hintFromSquare, selectedSquare]);

  const hintSquares = useMemo<Record<string, 'dot' | 'ring'>>(() => {
    if (!selectedSquare || !isMovablePhase(phase)) return {};
    try {
      const legalMoves = game.moves({
        square: selectedSquare as Square,
        verbose: true,
      });
      const hints: Record<string, 'dot' | 'ring'> = {};
      for (const move of legalMoves) {
        hints[move.to] = game.get(move.to as Square) ? 'ring' : 'dot';
      }
      return hints;
    } catch {
      return {};
    }
  }, [game, phase, selectedSquare]);

  const squareRenderer = useCallback<SquareRenderer>(
    ({ square, children }) => {
      const hint = hintSquares[square];
      const squareStyle = displayedSquareStyles[square];
      return (
        <div className="relative h-full w-full" style={squareStyle}>
          {hint === 'dot' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[30%] w-[30%] -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/25" />
          )}
          {hint === 'ring' && (
            <div className="pointer-events-none absolute left-1/2 top-1/2 h-[88%] w-[88%] -translate-x-1/2 -translate-y-1/2 rounded-full border-[6px] border-black/25" />
          )}
          {children}
        </div>
      );
    },
    [displayedSquareStyles, hintSquares]
  );

  const interactive = isMovablePhase(phase);
  // Settled-drill continuation: the board ring stays on the FAILED color
  // while a playout is running, so the unlocked board can never read as
  // "the drill is live again".
  const playoutRing = phase === 'playout' || phase === 'playout-reply';

  return (
    <div>
      <div
        style={
          playoutRing
            ? {
                ...BOARD_FRAME_STYLE,
                boxShadow: `${BOARD_FRAME_STYLE.boxShadow}, 0 0 0 3px rgba(239, 68, 68, 0.4)`,
              }
            : BOARD_FRAME_STYLE
        }
      >
        <div className="relative">
          <div
            style={{
              position: 'absolute',
              inset: 0,
              backgroundImage: 'url("/wood-texture.webp")',
              backgroundSize: 'cover',
              opacity: 0.08,
              pointerEvents: 'none',
              mixBlendMode: 'multiply' as React.CSSProperties['mixBlendMode'],
            }}
          />
          <Chessboard
            options={{
              position: game.fen(),
              boardOrientation: getOrientation(position),
              squareStyles: displayedSquareStyles,
              darkSquareStyle: {
                backgroundImage: 'url(/walnut-dark.webp)',
                backgroundSize: '110% 110%',
                backgroundPosition: 'center',
              },
              lightSquareStyle: {
                backgroundImage: 'url(/walnut-light.webp)',
                backgroundSize: '110% 110%',
                backgroundPosition: 'center',
              },
              darkSquareNotationStyle: { color: '#f0e0c0' },
              lightSquareNotationStyle: { color: '#3a2410' },
              boardStyle: {
                width: '100%',
                borderRadius: '8px',
                boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
              },
              animationDurationInMs: 200,
              allowDragging: interactive,
              canDragPiece: ({ piece, square }) => {
                if (!square || !isMovablePhase(phaseRef.current)) return false;
                return piece.pieceType[0] === gameRef.current.turn();
              },
              onSquareClick,
              squareRenderer,
              onPieceDrop: ({ piece, sourceSquare, targetSquare }) => {
                if (!targetSquare) return false;
                return onDrop(sourceSquare, targetSquare, piece.pieceType);
              },
            }}
          />

          {pendingPromotion && (
            <PromotionPicker
              turn={game.turn()}
              onSelect={onPromotionPieceSelect}
              onCancel={() => setPendingPromotion(null)}
            />
          )}
        </div>
      </div>

      {errorMessage && (
        <div
          role="alert"
          className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-2.5 text-sm text-amber-100"
        >
          <span>{errorMessage}</span>
          <div className="flex shrink-0 items-center gap-2">
            {canRetry && (
              <button
                type="button"
                onClick={() => void postPendingMove()}
                className="rounded-md border border-amber-200/40 px-2.5 py-1 text-xs font-semibold text-amber-50 transition hover:bg-amber-300/10"
              >
                Retry
              </button>
            )}
            {canRetry ? (
              <button
                type="button"
                onClick={undoPendingMove}
                className="rounded-md border border-amber-200/40 px-2.5 py-1 text-xs font-semibold text-amber-50 transition hover:bg-amber-300/10"
              >
                Undo move
              </button>
            ) : (
              <button
                type="button"
                aria-label="Dismiss"
                onClick={dismissError}
                className="text-lg leading-none text-amber-100/70 transition hover:text-amber-100"
              >
                ×
              </button>
            )}
          </div>
        </div>
      )}
      {playoutError && (
        <div
          role="alert"
          className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-2.5 text-sm text-amber-100"
        >
          <span>{playoutError}</span>
          <button
            type="button"
            onClick={() => void requestPlayoutReply(gameRef.current.fen())}
            className="shrink-0 rounded-md border border-amber-200/40 px-2.5 py-1 text-xs font-semibold text-amber-50 transition hover:bg-amber-300/10"
          >
            Retry
          </button>
        </div>
      )}
      {hintLoading && (
        <div
          role="status"
          className="mt-3 flex items-center gap-3 rounded-xl border border-sky-300/30 bg-sky-400/10 px-4 py-2.5 text-sm text-sky-100"
        >
          <span className="h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-sky-200/60 border-t-transparent" />
          Finding the best move for this position…
        </div>
      )}
      {hintError && !hintLoading && (
        <div
          role="alert"
          className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-amber-300/30 bg-amber-400/10 px-4 py-2.5 text-sm text-amber-100"
        >
          <span>{hintError}</span>
          <button
            type="button"
            aria-label="Dismiss"
            onClick={() => setHintError(null)}
            className="shrink-0 text-lg leading-none text-amber-100/70 transition hover:text-amber-100"
          >
            ×
          </button>
        </div>
      )}
    </div>
  );
}
