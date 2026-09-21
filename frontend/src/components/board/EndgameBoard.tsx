'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Chess, Square } from 'chess.js';
import { Chessboard, type SquareRenderer } from 'react-chessboard';

import {
  EndgameFetchError,
  EndgameGradeResult,
  EndgameMoveSubmission,
  EndgamePlayoutEnding,
  EndgamePlayoutResolution,
  EndgamePlayoutStep,
  EndgameSubmitMove,
  finishEndgamePlayout,
  moveToUci,
  playoutEndingFromFinish,
  requestEndgameHint,
  requestEndgamePlayoutReply,
  storedReplyForFen,
  submitEndgameMove,
  uciToMove,
} from '@/lib/endgames';
import {
  EndgameHintResponse,
  EndgameMoveResponse,
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
  // affected); 'playout-reply' = the defender is thinking/replying;
  // 'playout-stepping' = a fast-forwarded line is being walked one move at a
  // time ("Next move" / "Skip to end" drive it, never the user's pieces).
  | 'playout'
  | 'playout-reply'
  | 'playout-stepping'
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
  /**
   * Bumps to request a fast-forward of the position the continuation is at
   * (the panel's "Skip to final result" / "See the final verdict"). A counter
   * so repeat presses are distinct requests.
   */
  playoutSkipRequest?: number;
  /** Plies played in the continuation so far (reported as 0 when it starts),
   *  which drives the soft exit prompt. */
  onPlayoutProgress?: (plies: number) => void;
  /** Fired when the continuation finishes: a reached ending, or the tablebase
   *  verdict when no cheap concrete line existed. */
  onPlayoutResolved?: (resolution: EndgamePlayoutResolution) => void;
  /** Bumps to advance one ply of a fast-forwarded line (the stepper's "Next
   *  move"). A counter so repeat presses are distinct requests. */
  playoutStepRequest?: number;
  /** Mirrors the stepper for the panel: how many plies of the returned line
   *  are on the board and what comes next; null when no line is being
   *  stepped. */
  onPlayoutStepping?: (step: EndgamePlayoutStep | null) => void;
  /** Hints this drill attempt has revealed so far, as the caller counted
   *  them. Attached to every graded submission; the backend reads it only on
   *  a resolving move (a hinted solve is neutral on the rated surface and
   *  not-clean on the review surface). */
  hintsUsed?: number;
  /** Bumps to request the single best move for the current position (the
   *  panel's "Get solution"). A counter so repeat presses are distinct. */
  hintRequest?: number;
  /** Fired once a hint has been fetched and highlighted. The hint itself
   *  submits nothing: the caller counts it into hintsUsed and the user
   *  plays the move through the normal grading path. */
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
// "Get solution" highlights the piece and its destination in a cool sky
// tint: deliberately unlike the user's emerald, the opponent's amber, the
// settled gold and the solved/failed green/red, because a hint is advisory
// information rather than a played move.
const HIGHLIGHT_HINT = 'rgba(56, 189, 248, 0.45)';

// Puzzles waits 600ms before the stored reply; a converted drill should
// feel like the same opponent answering.
const OPPONENT_DELAY_MS = 600;

function isMovablePhase(phase: EndgameBoardPhase): boolean {
  return phase === 'playing' || phase === 'playout';
}

/**
 * The playout's terminal ending, in the same vocabulary the backend grades
 * (checkmate / stalemate / insufficient material / fifty-move rule), with
 * 'draw' covering the repetition rules. Null when the game is not over.
 */
function playoutEnding(game: Chess): EndgamePlayoutEnding | null {
  if (game.isCheckmate()) return 'checkmate';
  if (game.isStalemate()) return 'stalemate';
  if (game.isInsufficientMaterial()) return 'insufficient_material';
  if (game.isDrawByFiftyMoves()) return 'fifty_move_rule';
  if (game.isDraw()) return 'draw';
  return null;
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

/** SAN of `uci` in `fen`, for the stepper's "next move" label. Null when the
 *  move cannot be applied -- the line is server-generated, so this is purely
 *  defensive. */
function sanForMove(fen: string, uci: string): string | null {
  try {
    const board = new Chess(fen);
    const move = board.move(uciToMove(uci));
    return move ? move.san : null;
  } catch {
    return null;
  }
}

function getOrientation(position: EndgamePosition): 'white' | 'black' {
  return position.fen.split(/\s+/)[1] === 'b' ? 'black' : 'white';
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
  playoutSkipRequest = 0,
  playoutStepRequest = 0,
  onPlayoutProgress,
  onPlayoutResolved,
  onPlayoutStepping,
  hintsUsed = 0,
  hintRequest = 0,
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

  const gameRef = useRef<Chess>(initialGame);
  const phaseRef = useRef<EndgameBoardPhase>('playing');
  const pendingMoveRef = useRef<PendingMove | null>(null);
  const opponentTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // The last graded verdict, kept so playout only opens on a genuine failed
  // drill, plus the one-shot guard so the continuation starts once.
  const resolvedStatusRef = useRef<EndgameStatus | null>(null);
  const playoutStartedRef = useRef(false);

  // Prop callbacks live in refs so the async grading flow never closes over
  // a stale render's handlers.
  const onUserMoveRef = useRef(onUserMove);
  const onOpponentMoveRef = useRef(onOpponentMove);
  const onThinkingChangeRef = useRef(onThinkingChange);
  const onDrillResolvedRef = useRef(onDrillResolved);
  const onPlayoutResolvedRef = useRef(onPlayoutResolved);
  const onPlayoutProgressRef = useRef(onPlayoutProgress);
  const onPlayoutSteppingRef = useRef(onPlayoutStepping);
  const submitMoveRef = useRef<EndgameSubmitMove<Result> | null>(null);
  // Invalidates in-flight playout work whenever the continuation is skipped
  // or superseded, so a reply can never land on a board that has moved on.
  const playoutRunRef = useRef(0);
  const playoutPliesRef = useRef(0);
  const skipHandledRef = useRef(0);
  const skipInFlightRef = useRef(false);
  // The fast-forwarded line being stepped, board-owned: the moves, the
  // cursor, the server's final FEN and the ending to report when it ends.
  const steppingRef = useRef<{
    line: string[];
    index: number;
    finalFen: string;
    ending: EndgamePlayoutEnding | null;
  } | null>(null);
  const stepHandledRef = useRef(0);
  const hintsUsedRef = useRef(hintsUsed);
  const hintHandledRef = useRef(0);
  const hintInFlightRef = useRef(false);
  const onHintRevealedRef = useRef(onHintRevealed);
  useEffect(() => {
    onUserMoveRef.current = onUserMove;
    onOpponentMoveRef.current = onOpponentMove;
    onThinkingChangeRef.current = onThinkingChange;
    onDrillResolvedRef.current = onDrillResolved;
    onPlayoutResolvedRef.current = onPlayoutResolved;
    onPlayoutProgressRef.current = onPlayoutProgress;
    onPlayoutSteppingRef.current = onPlayoutStepping;
    onHintRevealedRef.current = onHintRevealed;
    hintsUsedRef.current = hintsUsed;
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

  const clearOpponentTimer = useCallback(() => {
    if (opponentTimerRef.current) {
      clearTimeout(opponentTimerRef.current);
      opponentTimerRef.current = null;
    }
  }, []);

  useEffect(() => clearOpponentTimer, [clearOpponentTimer]);

  const applyOpponentReply = useCallback(
    (replyUci: string, fenBefore: string) => {
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
          updatePhase('playing');
          onThinkingChangeRef.current?.(false);
          return;
        }
        setBoard(replyGame);
        setHighlightSquares(
          buildHighlight(applied.from, applied.to, HIGHLIGHT_OPPONENT)
        );
        onOpponentMoveRef.current?.(applied.san);
        updatePhase('playing');
        onThinkingChangeRef.current?.(false);
      }, OPPONENT_DELAY_MS);
    },
    [clearOpponentTimer, setBoard, updatePhase]
  );

  // --- "Play it out": the settled FAILED drill's continuation -------------
  // Nothing in this section is graded or recorded: the verdict already
  // happened, so the board just keeps playing with the shared defender
  // generator. The framing stays on the failed/settled side throughout.

  const finishPlayout = useCallback(
    (game: Chess) => {
      clearOpponentTimer();
      onThinkingChangeRef.current?.(false);
      updatePhase('resolved');
      onPlayoutResolvedRef.current?.({
        kind: 'ending',
        ending: playoutEnding(game),
      });
    },
    [clearOpponentTimer, updatePhase]
  );

  // --- Stepping a fast-forwarded line ------------------------------------
  // /finish returns the whole locally-covered line in one response, so the
  // user can walk it at their own pace with no further requests. The board
  // owns the cursor; the panel mirrors it through onPlayoutStepping.

  const completeStepping = useCallback(
    (fullyStepped: boolean) => {
      const stepping = steppingRef.current;
      steppingRef.current = null;
      onPlayoutSteppingRef.current?.(null);
      clearOpponentTimer();
      onThinkingChangeRef.current?.(false);
      if (stepping) {
        // The server's FEN is the authority for the line's end. A fully
        // stepped line keeps its last move highlighted; the "Skip to end"
        // escape hatch lands on a clean board, exactly like the old instant
        // jump did.
        setBoard(new Chess(stepping.finalFen));
        const last = stepping.line[stepping.line.length - 1];
        if (fullyStepped && last) {
          const { from, to } = uciToMove(last);
          setHighlightSquares(buildHighlight(from, to, HIGHLIGHT_PLAYOUT));
        } else {
          setHighlightSquares({});
        }
      }
      updatePhase('resolved');
      onPlayoutResolvedRef.current?.({
        kind: 'ending',
        ending: stepping?.ending ?? null,
      });
    },
    [clearOpponentTimer, setBoard, updatePhase]
  );

  const enterStepping = useCallback(
    (line: string[], finalFen: string, ending: EndgamePlayoutEnding | null) => {
      steppingRef.current = { line, index: 0, finalFen, ending };
      updatePhase('playout-stepping');
      onPlayoutSteppingRef.current?.({
        shown: 0,
        total: line.length,
        next_move_san: sanForMove(gameRef.current.fen(), line[0]),
      });
    },
    [updatePhase]
  );

  const stepPlayoutLine = useCallback(() => {
    const stepping = steppingRef.current;
    if (!stepping || phaseRef.current !== 'playout-stepping') return;
    const nextGame = new Chess(gameRef.current.fen());
    let applied;
    try {
      applied = nextGame.move(uciToMove(stepping.line[stepping.index]));
    } catch {
      applied = null;
    }
    if (!applied) {
      // Defensive: a line that cannot be replayed client-side lands on the
      // server's end position rather than dead-ending the stepper.
      completeStepping(false);
      return;
    }
    setBoard(nextGame);
    setHighlightSquares(
      buildHighlight(applied.from, applied.to, HIGHLIGHT_PLAYOUT)
    );
    stepping.index += 1;
    if (stepping.index >= stepping.line.length) {
      completeStepping(true);
      return;
    }
    onPlayoutSteppingRef.current?.({
      shown: stepping.index,
      total: stepping.line.length,
      next_move_san: sanForMove(nextGame.fen(), stepping.line[stepping.index]),
    });
  }, [completeStepping, setBoard]);

  const bumpPlayoutPlies = useCallback(() => {
    playoutPliesRef.current += 1;
    onPlayoutProgressRef.current?.(playoutPliesRef.current);
  }, []);

  const applyPlayoutReply = useCallback(
    (replyUci: string, fenBefore: string) => {
      const run = playoutRunRef.current;
      clearOpponentTimer();
      opponentTimerRef.current = setTimeout(() => {
        opponentTimerRef.current = null;
        if (run !== playoutRunRef.current) return;
        const replyGame = new Chess(fenBefore);
        let applied;
        try {
          applied = replyGame.move(uciToMove(replyUci));
        } catch {
          applied = null;
        }
        if (!applied) {
          finishPlayout(gameRef.current);
          return;
        }
        setBoard(replyGame);
        setHighlightSquares(
          buildHighlight(applied.from, applied.to, HIGHLIGHT_PLAYOUT)
        );
        bumpPlayoutPlies();
        onThinkingChangeRef.current?.(false);
        if (replyGame.isGameOver()) {
          finishPlayout(replyGame);
          return;
        }
        updatePhase('playout');
      }, OPPONENT_DELAY_MS);
    },
    [bumpPlayoutPlies, clearOpponentTimer, finishPlayout, setBoard, updatePhase]
  );

  const requestPlayoutReply = useCallback(
    async (fen: string) => {
      const run = playoutRunRef.current;
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
        if (run !== playoutRunRef.current) return;
        // Same stored-line contract as a graded replay: null means the
        // client already holds the next stored move and applies it itself.
        const replyUci =
          response.opponent_reply?.move_uci ??
          storedReplyForFen(position.fen, position.moves, fen);
        if (!replyUci) {
          finishPlayout(gameRef.current);
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
    playoutPliesRef.current = 0;
    onPlayoutProgressRef.current?.(0);

    // The failed move left the defender to move -- unless that move already
    // ended the game, in which case the continuation is over before it
    // starts.
    const settled = gameRef.current;
    if (settled.isGameOver()) {
      finishPlayout(settled);
      return;
    }
    void requestPlayoutReply(settled.fen());
  }, [playout, finishPlayout, requestPlayoutReply]);

  const skipToFinalResult = useCallback(async () => {
    // Only a settled failure can be fast-forwarded (the buttons only render
    // there, but the request arrives as a prop).
    if (resolvedStatusRef.current !== 'failed') return;
    // Already stepping a returned line: "skip" now means jump to the end of
    // that line, which is already in hand -- no second network call.
    if (steppingRef.current) {
      completeStepping(false);
      return;
    }
    const current = gameRef.current;
    if (current.isGameOver()) {
      finishPlayout(current);
      return;
    }

    const before = phaseRef.current;
    // Invalidates any in-flight playout reply: it must not land on a board
    // that has already jumped to the ending.
    const run = ++playoutRunRef.current;
    skipInFlightRef.current = true;
    setPlayoutError(null);
    updatePhase('playout-reply');
    onThinkingChangeRef.current?.(true);
    try {
      const response = await finishEndgamePlayout({
        position_id: position.id,
        fen: current.fen(),
      });
      if (run !== playoutRunRef.current) return;
      onThinkingChangeRef.current?.(false);

      if (response.fen && response.ending) {
        const ending = playoutEndingFromFinish(response.ending);
        // Defensive `?? []`: a backend that predates the stepper returns no
        // line, and the instant jump below is exactly its old behavior.
        const line = response.line ?? [];
        if (line.length > 0) {
          // Locally-covered material: the whole line came back, so the user
          // steps it move by move. The board stays where the request was
          // made until the first "Next move".
          enterStepping(line, response.fen, ending);
          return;
        }
        // No line to step (the request position was already terminal): the
        // instant jump is still the right behavior.
        setBoard(new Chess(response.fen));
        setHighlightSquares({});
        updatePhase('resolved');
        onPlayoutResolvedRef.current?.({ kind: 'ending', ending });
        return;
      }

      if (response.outcome) {
        // Beyond the local files: the verdict is the result and the board
        // stays where it is.
        updatePhase('resolved');
        onPlayoutResolvedRef.current?.({
          kind: 'verdict',
          outcome: response.outcome,
        });
        return;
      }

      updatePhase('resolved');
      onPlayoutResolvedRef.current?.({ kind: 'ending', ending: null });
    } catch (error) {
      if (run !== playoutRunRef.current) return;
      const fetchError =
        error instanceof EndgameFetchError
          ? error
          : new EndgameFetchError(
              'Could not fast-forward the position.',
              0,
              true
            );
      setPlayoutError(fetchError.message);
      onThinkingChangeRef.current?.(false);
      // Put the board back where it was; if the defender was to move, the
      // reply request that the skip invalidated has to be re-issued.
      updatePhase(before);
      if (before === 'playout-reply') {
        void requestPlayoutReply(gameRef.current.fen());
      }
    } finally {
      skipInFlightRef.current = false;
    }
  }, [
    completeStepping,
    enterStepping,
    finishPlayout,
    position.id,
    requestPlayoutReply,
    setBoard,
    updatePhase,
  ]);

  useEffect(() => {
    if (!playoutSkipRequest || playoutSkipRequest === skipHandledRef.current) {
      return;
    }
    skipHandledRef.current = playoutSkipRequest;
    // A skip already resolving owns the request; consume the duplicate press.
    if (skipInFlightRef.current) return;
    void skipToFinalResult();
  }, [playoutSkipRequest, skipToFinalResult]);

  useEffect(() => {
    if (!playoutStepRequest || playoutStepRequest === stepHandledRef.current) {
      return;
    }
    stepHandledRef.current = playoutStepRequest;
    stepPlayoutLine();
  }, [playoutStepRequest, stepPlayoutLine]);

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
      const { from, to } = uciToMove(hint.move_uci);
      setHighlightSquares(buildHighlight(from, to, HIGHLIGHT_HINT));
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
  }, [position.id]);

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
      });

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

      // solved | failed -- the drill resolved on the board.
      clearOpponentTimer();
      resolvedStatusRef.current = result.status;
      setHighlightSquares(
        buildHighlight(
          pending.from,
          pending.to,
          result.status === 'solved' ? HIGHLIGHT_SOLVED : HIGHLIGHT_FAILED
        )
      );
      updatePhase('resolved');
      onThinkingChangeRef.current?.(false);
      onDrillResolvedRef.current?.(result, gameRef.current.history());
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
    clearOpponentTimer,
    defaultSubmitMove,
    position,
    setBoard,
    updatePhase,
  ]);

  const submitUserMove = useCallback(
    (from: string, to: string, promotion?: string) => {
      const inPlayout = phaseRef.current === 'playout';
      if (!inPlayout && phaseRef.current !== 'playing') return;

      const fenBefore = gameRef.current.fen();
      const nextGame = new Chess(fenBefore);
      let move;
      try {
        move = nextGame.move({ from, to, promotion });
      } catch {
        return;
      }
      if (!move) return;

      if (inPlayout) {
        // Settled-drill continuation: applied locally, never graded, and no
        // move/opponent callbacks fire -- the recorded stats stay frozen at
        // the failure. The defender answers from the read-only route.
        setBoard(nextGame);
        setSelectedSquare(null);
        setHighlightSquares(buildHighlight(move.from, move.to, HIGHLIGHT_PLAYOUT));
        bumpPlayoutPlies();
        if (nextGame.isGameOver()) {
          finishPlayout(nextGame);
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
      setHighlightSquares(buildHighlight(move.from, move.to, HIGHLIGHT_USER));
      void postPendingMove();
    },
    [bumpPlayoutPlies, finishPlayout, postPendingMove, requestPlayoutReply, setBoard]
  );

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
    if (!selectedSquare) return highlightSquares;
    return {
      ...highlightSquares,
      [selectedSquare]: {
        ...highlightSquares[selectedSquare],
        backgroundColor: 'rgba(255, 170, 0, 0.35)',
      },
    };
  }, [highlightSquares, selectedSquare]);

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
  const playoutRing =
    phase === 'playout' ||
    phase === 'playout-reply' ||
    phase === 'playout-stepping';

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
            <div className="absolute inset-0 z-10 flex items-center justify-center rounded-lg bg-black/60 backdrop-blur-sm">
              <div className="rounded-xl border border-zinc-700 bg-zinc-800 p-4 shadow-2xl">
                <h3 className="mb-4 text-center font-medium text-white">
                  Promote to
                </h3>
                <div className="flex gap-2">
                  {['q', 'r', 'b', 'n'].map((piece) => (
                    <button
                      key={piece}
                      type="button"
                      onClick={() => onPromotionPieceSelect(piece)}
                      className="flex h-14 w-14 items-center justify-center rounded-lg bg-zinc-700 pb-2 text-4xl transition-colors hover:bg-emerald-600"
                    >
                      {game.turn() === 'w'
                        ? piece === 'q'
                          ? '♕'
                          : piece === 'r'
                            ? '♖'
                            : piece === 'b'
                              ? '♗'
                              : '♘'
                        : piece === 'q'
                          ? '♛'
                          : piece === 'r'
                            ? '♜'
                            : piece === 'b'
                              ? '♝'
                              : '♞'}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => setPendingPromotion(null)}
                  className="mt-4 w-full rounded-lg bg-zinc-700/50 py-2 text-sm text-zinc-300 transition-colors hover:bg-zinc-700"
                >
                  Cancel
                </button>
              </div>
            </div>
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
