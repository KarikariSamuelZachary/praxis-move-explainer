import { Chess } from 'chess.js';

import {
  EndgameFailureCategory,
  EndgameHintRequest,
  EndgameHintResponse,
  EndgameMoveRequestPayload,
  EndgameMoveResponse,
  EndgameOpponentReply,
  EndgameOutcome,
  EndgamePlayoutEnding,
  EndgamePlayoutReplyRequest,
  EndgamePlayoutReplyResponse,
  EndgamePosition,
  EndgamePracticeCategory,
  EndgameResolution,
  EndgameStatus,
  EndgameWoodpeckerAttemptPayload,
  EndgameWoodpeckerAttemptResponse,
  EndgameWoodpeckerCountResponse,
  EndgameWoodpeckerQueueEntry,
} from '@/types';

/**
 * Client for the rated Endgame Trainer endpoints (Vercel proxy routes ->
 * FastAPI). The drill is stateless: this module only fetches a position and
 * posts single graded moves; the page/board own the chess.js game.
 */

/**
 * One user move as the board submits it. Deliberately route-agnostic: the
 * board owns the chess.js game and knows the before/after FENs, while the
 * caller owns WHICH endpoint grades the move (rated trainer vs. Woodpecker
 * review) and any route-specific identifiers/timing.
 */
export interface EndgameMoveSubmission {
  fen_before: string;
  move: string;
  fen_after: string;
  /**
   * Hints this drill attempt has revealed, as the client counted them. The
   * board attaches it to every submission; the backend reads it only on a
   * resolving move, where a hinted solve is neutral on the rated surface
   * and a hinted review pass is scheduled as not-clean.
   */
  hints_used: number;
  /**
   * True when this attempt replays an already-recorded drill (the panel's
   * Retry). The board attaches it to every submission of that replay; the
   * backend grades each move for the verdict but writes nothing on
   * resolution -- no rating change, no review capture/FSRS transition.
   */
  retry: boolean;
  /**
   * Every move played since the drill's start position, UCI, oldest first,
   * NOT including the move being submitted. The board attaches it to every
   * submission so the backend can rebuild the game and adjudicate threefold
   * repetition (which a FEN alone cannot express).
   */
  history: string[];
}

export type EndgameSubmitMove<Result> = (
  submission: EndgameMoveSubmission
) => Promise<Result>;

/**
 * The grade payload shared by EndgameMoveResponse (rated trainer) and
 * EndgameWoodpeckerAttemptResponse (review queue). EndgameBoard/panel logic
 * only ever reads these fields; everything else is caller-specific
 * (rating on the trainer, attempt/scheduling on reviews). The review
 * response is deliberately NOT a narrowing: it mirrors the trainer's
 * grader block exactly, minus `rating`.
 */
export interface EndgameGradeResult {
  status: EndgameStatus;
  failure_category?: EndgameFailureCategory | null;
  resolution?: EndgameResolution | null;
  outcome_before?: EndgameOutcome | null;
  outcome_after?: EndgameOutcome | null;
  dtz_before?: number | null;
  dtz_after?: number | null;
  common_mistake?: string | null;
  /** > 0 marks a hint-assisted resolution (the panel renders it). */
  hints_used?: number;
  opponent_reply?: EndgameOpponentReply | null;
}

export class EndgameFetchError extends Error {
  status: number;
  retryable: boolean;

  constructor(message: string, status: number, retryable: boolean) {
    super(message);
    this.name = 'EndgameFetchError';
    this.status = status;
    this.retryable = retryable;
  }
}

/**
 * Shared "serve one drill" reader: the rated trainer's GET /next and
 * practice mode's GET /practice/next return the same EndgamePositionResponse
 * and the same error contract -- 404 means the window/category is empty (not
 * retryable), 429/5xx are transient. Only the URL and the empty-state
 * message differ.
 */
async function fetchEndgamePositionFrom(
  path: string,
  emptyMessage: string
): Promise<EndgamePosition> {
  let response: Response;
  try {
    response = await fetch(path, { cache: 'no-store' });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body.error;
    if (response.status === 404) {
      throw new EndgameFetchError(detail ?? emptyMessage, 404, false);
    }
    throw new EndgameFetchError(
      detail ?? `Could not load a drill (${response.status}).`,
      response.status,
      response.status === 429 || response.status >= 500
    );
  }

  return (await response.json()) as EndgamePosition;
}

export function fetchNextEndgamePosition(): Promise<EndgamePosition> {
  return fetchEndgamePositionFrom(
    '/api/endgames/next',
    'No endgame drills are available right now.'
  );
}

/**
 * The practice picker's real options. The backend builds this from the DB
 * (`list_categories(sourced_only=True)`), so categories with no sourced
 * positions are absent rather than rendered empty -- the UI shows exactly
 * what it returns, in the backend's name order.
 */
export async function fetchEndgamePracticeCategories(): Promise<
  EndgamePracticeCategory[]
> {
  let response: Response;
  try {
    response = await fetch('/api/endgames/practice/categories', {
      cache: 'no-store',
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new EndgameFetchError(
      body.detail ??
        body.error ??
        `Could not load practice categories (${response.status}).`,
      response.status,
      response.status === 429 || response.status >= 500
    );
  }

  return (await response.json()) as EndgamePracticeCategory[];
}

/**
 * One random sourced position from a practice category. The picker only
 * offers categories GET /practice/categories reported, so the backend's 400
 * "unknown category" is unreachable from the UI; the 404 (a category that
 * emptied out) is handled like the trainer's empty window.
 */
export function fetchNextEndgamePracticePosition(
  category: string
): Promise<EndgamePosition> {
  return fetchEndgamePositionFrom(
    `/api/endgames/practice/next?category=${encodeURIComponent(category)}`,
    'No practice positions are available in this category right now.'
  );
}

/**
 * Shared per-move grader for the rated trainer and practice mode: identical
 * EndgameMoveRequestPayload and EndgameMoveResponse. Practice writes nothing
 * (its route returns rating/review_capture as null), but it honors the same
 * retry contract so the board's error handling is one implementation, not
 * three.
 */
async function postEndgameMoveTo(
  path: string,
  payload: EndgameMoveRequestPayload
): Promise<EndgameMoveResponse> {
  let response: Response;
  try {
    response = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body.error;
    // Only statuses where the backend guarantees nothing was written are
    // offered as Retry: 503 (tablebase/reply temporarily unavailable; both
    // routes document "nothing was written"), 429 (rejected before the
    // handler) and 502 (the proxy failing to reach the backend). A 500 may
    // have partially processed, and 400/404/409 mean the client's game
    // state disagrees with the drill -- those take the move back instead of
    // re-submitting it (in the rated loop a terminal move re-graded would
    // write the rating twice).
    const retryable =
      response.status === 429 ||
      response.status === 502 ||
      response.status === 503;
    throw new EndgameFetchError(
      detail ?? `The move was not accepted (${response.status}).`,
      response.status,
      retryable
    );
  }

  return (await response.json()) as EndgameMoveResponse;
}

export function submitEndgameMove(
  payload: EndgameMoveRequestPayload
): Promise<EndgameMoveResponse> {
  return postEndgameMoveTo('/api/endgames/move', payload);
}

/** Grade one practice move. Writes nothing: no rating, no Woodpecker capture. */
export function submitEndgamePracticeMove(
  payload: EndgameMoveRequestPayload
): Promise<EndgameMoveResponse> {
  return postEndgameMoveTo('/api/endgames/practice/move', payload);
}

/**
 * The panel's view of the continuation:
 *   * active -- the user is playing it out against the defender;
 *   * done   -- the continuation ended. The board detects the terminal
 *               position itself (the playout is ungraded) and reports how it
 *               ended; `ending` is null when the stored line simply ran out
 *               with no terminal position.
 */
export type EndgamePlayoutStatus =
  | { state: 'active' }
  | { state: 'done'; ending: EndgamePlayoutEnding | null };

/**
 * The defender's reply for a FAILED drill's continuation.
 *
 * Read-only by construction: the backend route never calls the grader, never
 * writes a rating and never captures into the review queue -- the verdict was
 * final before this was ever called. `opponent_reply` is null while the
 * request is still on the stored line (the client applies its own copy via
 * storedReplyForFen) or when the position is already over.
 */
export async function requestEndgamePlayoutReply(
  payload: EndgamePlayoutReplyRequest
): Promise<EndgamePlayoutReplyResponse> {
  let response: Response;
  try {
    response = await fetch('/api/endgames/playout/reply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body.error;
    const retryable =
      response.status === 429 ||
      response.status === 502 ||
      response.status === 503;
    throw new EndgameFetchError(
      detail ?? `The defender could not reply (${response.status}).`,
      response.status,
      retryable
    );
  }

  return (await response.json()) as EndgamePlayoutReplyResponse;
}

/**
 * The single best move for the position on the board ("Get solution").
 *
 * Read-only by construction: the backend route never grades and never
 * writes, so revealing a hint cannot change a recorded outcome. The board
 * highlights the returned move and the user plays it through the surface's
 * own grading route.
 *
 * Latency note: within local Syzygy coverage the answer is ~20-40ms; on
 * 6-7-man material it costs a Lichess child-probe sweep (measured 3-13s for
 * a first call, cached afterwards), so callers keep a busy state and reveal
 * nothing until the move lands.
 */
export async function requestEndgameHint(
  payload: EndgameHintRequest
): Promise<EndgameHintResponse> {
  let response: Response;
  try {
    response = await fetch('/api/endgames/hint', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body.error;
    const retryable =
      response.status === 429 ||
      response.status === 502 ||
      response.status === 503;
    throw new EndgameFetchError(
      detail ?? `Could not get a hint (${response.status}).`,
      response.status,
      retryable
    );
  }

  return (await response.json()) as EndgameHintResponse;
}

/**
 * The stored-line counterpart of the backend's stored_line_reply: replays
 * `line` (the playable solution, USER move first, exactly the `moves` array
 * GET /next returns) from the drill FEN and, when `fenAfter` still sits on
 * that line with an opponent move next, returns that move's UCI.
 *
 * The server returns opponent_reply=null precisely while the client is on
 * the stored line, so the board needs this to keep playing. Positional
 * (4-field FEN) matching, mirroring the backend, so deviation and
 * transposition behave identically on both sides.
 */
export function storedReplyForFen(
  startFen: string,
  line: string[],
  fenAfter: string
): string | null {
  if (!line || line.length < 2) return null;

  let board: Chess;
  try {
    board = new Chess(startFen);
  } catch {
    return null;
  }

  const target = fenKey(fenAfter);

  for (let index = 0; index < line.length; index += 1) {
    let move;
    try {
      move = board.move(uciToMove(line[index]));
    } catch {
      return null;
    }
    if (!move) return null;

    if (fenKey(board.fen()) !== target) continue;

    // `index + 1` line plies have been played; the next stored move is the
    // opponent's exactly when that count is odd and still within range.
    const nextIndex = index + 1;
    if (nextIndex >= line.length || nextIndex % 2 === 0) return null;

    const candidate = line[nextIndex];
    const probe = new Chess(board.fen());
    try {
      if (!probe.move(uciToMove(candidate))) return null;
    } catch {
      return null;
    }
    return candidate;
  }

  return null;
}

/**
 * The endgame review queue (Woodpecker tab): due cards only, each carrying
 * the full drill payload. Separate table and separate endpoints from the
 * puzzle queue -- a missed endgame never appears in GET /api/woodpecker/queue.
 */
export async function fetchEndgameWoodpeckerQueue(): Promise<
  EndgameWoodpeckerQueueEntry[]
> {
  let response: Response;
  try {
    response = await fetch('/api/endgames/woodpecker/queue', {
      cache: 'no-store',
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new EndgameFetchError(
      body.detail ?? body.error ?? `Could not load your reviews (${response.status}).`,
      response.status,
      response.status === 429 || response.status >= 500
    );
  }

  return (await response.json()) as EndgameWoodpeckerQueueEntry[];
}

/**
 * The endgame review due count, for the Woodpecker card's "Reviews Due".
 * Same predicate as the queue: due-now, unmastered cards only.
 */
export async function fetchEndgameWoodpeckerCount(): Promise<number> {
  let response: Response;
  try {
    response = await fetch('/api/endgames/woodpecker/count', {
      cache: 'no-store',
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new EndgameFetchError(
      body.detail ?? body.error ?? `Could not load your reviews (${response.status}).`,
      response.status,
      response.status === 429 || response.status >= 500
    );
  }

  const body = (await response.json()) as EndgameWoodpeckerCountResponse;
  return body.due_count;
}

/**
 * Grade one move of a full-resolution review replay.
 *
 * Same grading, same resolution rule and same stored-line/opponent-reply
 * contract as submitEndgameMove -- the only differences are the route, the
 * entry being reviewed, and the accumulated time. The server owns the FSRS
 * write and derives solved_correctly itself on the resolving move; the
 * client never asserts the verdict here.
 */
export async function submitEndgameWoodpeckerMove(
  payload: EndgameWoodpeckerAttemptPayload
): Promise<EndgameWoodpeckerAttemptResponse> {
  let response: Response;
  try {
    response = await fetch('/api/endgames/woodpecker/attempts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new EndgameFetchError('Could not reach the server.', 0, true);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    const detail = body.detail ?? body.error;
    // Identical retry contract to the trainer's move endpoint: only
    // statuses where the backend promises nothing was written are offered
    // as Retry. A 500 may have partially processed (the FSRS write happens
    // on the resolving request), so it must not be re-submitted blindly.
    const retryable =
      response.status === 429 ||
      response.status === 502 ||
      response.status === 503;
    throw new EndgameFetchError(
      detail ?? `The move was not accepted (${response.status}).`,
      response.status,
      retryable
    );
  }

  return (await response.json()) as EndgameWoodpeckerAttemptResponse;
}

export function fenKey(fen: string): string {
  return fen.split(/\s+/).slice(0, 4).join(' ');
}

export function uciToMove(uci: string): {
  from: string;
  to: string;
  promotion?: string;
} {
  return {
    from: uci.slice(0, 2),
    to: uci.slice(2, 4),
    promotion: uci.length > 4 ? uci[4] : undefined,
  };
}

export function moveToUci(move: {
  from: string;
  to: string;
  promotion?: string;
}): string {
  return `${move.from}${move.to}${move.promotion ?? ''}`;
}

/**
 * Display names for the picker's material categories. Covers all ten CHECK
 * values so a category that gains sourced content renders properly without a
 * code change -- and unknown values still fall back to a readable form
 * rather than a raw key.
 */
const PRACTICE_CATEGORY_LABELS: Record<string, string> = {
  pure_pawn: 'Pure Pawn',
  knight: 'Knight',
  bishop: 'Bishop',
  bishop_bishop: 'Bishop & Bishop',
  bishop_knight: 'Bishop & Knight',
  rook: 'Rook',
  bishop_rook: 'Bishop & Rook',
  knight_rook: 'Knight & Rook',
  queen: 'Queen',
  multi_piece: 'Multi-piece',
};

export function practiceCategoryLabel(category: string): string {
  const known = PRACTICE_CATEGORY_LABELS[category];
  if (known) return known;

  const spaced = category.replace(/_/g, ' ').trim();
  if (!spaced) return category;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/**
 * Presentation for the practice picker's rows: which crop of the generated
 * icon sheet belongs to each material category, and the one-line descriptor
 * under its name ("Opposition · Zugzwang").
 *
 * Covers all ten CHECK values so a category that gains sourced content
 * renders fully without a code change. Combination categories borrow the
 * icon of their first-named piece (the same rule the labels follow), and the
 * seven tiles of the sheet map one-to-one onto the seeded set -- pure pawn
 * takes the king-and-pawn tile, multi-piece the all-pieces banner crop.
 */
const PRACTICE_CATEGORY_META: Record<
  string,
  { icon: string; blurb: string }
> = {
  pure_pawn: { icon: 'king-pawn', blurb: 'Opposition · Zugzwang' },
  knight: { icon: 'knight', blurb: 'Key squares · Technique' },
  bishop: { icon: 'bishop', blurb: 'Good vs bad bishop' },
  bishop_bishop: { icon: 'bishop', blurb: 'Colour complexes' },
  bishop_knight: { icon: 'bishop', blurb: 'Two minors' },
  rook: { icon: 'rook', blurb: 'Lucena · Philidor' },
  bishop_rook: { icon: 'bishop', blurb: 'Rook & bishop' },
  knight_rook: { icon: 'knight', blurb: 'Rook & knight' },
  queen: { icon: 'queen', blurb: 'Endgame fundamentals' },
  multi_piece: { icon: 'multi-piece', blurb: 'Mixed material' },
};

/** The sheet's remaining tile, for a category with no mapping yet. */
const PRACTICE_CATEGORY_FALLBACK_ICON = 'pawn';

/** Public path of a category's thumbnail (see PRACTICE_CATEGORY_META). */
export function practiceCategoryIcon(category: string): string {
  const icon =
    PRACTICE_CATEGORY_META[category]?.icon ?? PRACTICE_CATEGORY_FALLBACK_ICON;
  return `/endgame-categories/${icon}.webp`;
}

/** The category's descriptor line, or null when it has no authored copy. */
export function practiceCategoryBlurb(category: string): string | null {
  return PRACTICE_CATEGORY_META[category]?.blurb ?? null;
}

export function endgameResolutionLabel(resolution: EndgameResolution): string {
  switch (resolution) {
    case 'checkmate':
      return 'Checkmate';
    case 'stalemate':
      return 'Stalemate';
    case 'insufficient_material':
      return 'Insufficient material';
    case 'fifty_move_rule':
      return 'Fifty-move rule';
    case 'threefold_repetition':
      return 'Threefold repetition';
    case 'promotion':
      return 'Promotion';
  }
}
