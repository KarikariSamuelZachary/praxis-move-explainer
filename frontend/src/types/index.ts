export type MoveClassification =
  | 'book'
  | 'best'
  | 'excellent'
  | 'good'
  | 'inaccuracy'
  | 'mistake'
  | 'blunder';

export interface Puzzle {
  id: string;
  fen: string;
  initialFen?: string;
  moves: string[];
  rating: number;
  themes: string[];
  gameUrl?: string;
  previousMove?: string;
}

export interface PuzzleAttempt {
  puzzleId: string;
  solved: boolean;
  timeSeconds: number;
  attemptNumber: number; // which woodpecker cycle
  date: string;
}

export interface WoodpeckerSession {
  puzzles: Puzzle[];
  currentIndex: number;
  cycle: number;
  solvedCount: number;
  startTime: number;
}

// --- Endgame Trainer (rated mode) ---------------------------------------
// Wire types mirroring src/schemas/endgame_schemas.py exactly.

export type EndgameStatus = 'in_progress' | 'solved' | 'failed';

export type EndgameOutcome = 'win' | 'draw' | 'loss';

export type EndgameFailureCategory =
  | 'threw_away_win'
  | 'blundered_into_loss'
  | 'lost_the_draw'
  | 'ran_out_of_moves';

export type EndgameResolution =
  | 'checkmate'
  | 'stalemate'
  | 'insufficient_material'
  | 'fifty_move_rule'
  | 'promotion';

export interface EndgamePosition {
  id: string;
  fen: string;
  /** Playable stored solution line, USER move first (moves[0] is not a
   * setup move here -- the FEN is already the post-setup position). */
  moves: string[];
  rating: number;
  themes: string[];
  gameUrl?: string | null;
  /** TRUE = tablebase win for the side to move (convert it);
   * FALSE = tablebase draw (hold it). */
  is_winning: boolean;
  topic_id: string;
  topic_name: string;
  topic_category: string;
  source_puzzle_id?: string | null;
}

export interface EndgameOpponentReply {
  move_uci: string;
  move_san: string;
  fen_after: string;
  source: 'tablebase' | 'stockfish';
}

export interface EndgameRatingUpdate {
  old_rating: number;
  new_rating: number;
  change: number;
}

export interface EndgameReviewCapture {
  position_id: string;
  source_puzzle_id?: string | null;
  topic_id: string;
  topic_name: string;
  theme: string;
  fen: string;
  is_winning: boolean;
  failure_category: EndgameFailureCategory;
  resolution?: EndgameResolution | null;
  outcome_before?: EndgameOutcome | null;
  outcome_after?: EndgameOutcome | null;
  source_reason: 'wrong_answer';
  failed_at: string;
}

export interface EndgameMoveResponse {
  position_id: string;
  status: EndgameStatus;
  failure_category?: EndgameFailureCategory | null;
  resolution?: EndgameResolution | null;
  outcome_before?: EndgameOutcome | null;
  outcome_after?: EndgameOutcome | null;
  dtz_before?: number | null;
  dtz_after?: number | null;
  common_mistake?: string | null;
  /** Echoed from the request: > 0 marks a hint-assisted solve, which the
   * panel renders as "Solved (hint used)". */
  hints_used?: number;
  rating?: EndgameRatingUpdate | null;
  review_capture?: EndgameReviewCapture | null;
  opponent_reply?: EndgameOpponentReply | null;
}

export interface EndgameMoveRequestPayload {
  position_id: string;
  fen_before: string;
  move: string;
  fen_after: string;
  /** Hints this drill attempt has revealed (client-owned). Read only on a
   * resolving move: a hint-assisted SOLVED move is neutral in the rated
   * loop; practice carries it for display only. */
  hints_used?: number;
}

/** "Get solution": the single best move for the position on the board.
 * Read-only server-side -- the board highlights the move and the user plays
 * it through the surface's own grading route. */
export interface EndgameHintRequest {
  position_id: string;
  fen: string;
}

export interface EndgameHintResponse {
  position_id: string;
  move_uci: string;
  move_san: string;
  fen_after: string;
  /** "tablebase" whenever local Syzygy or the Lichess fallback can reach
   * the position; "stockfish" only beyond coverage. */
  source: 'tablebase' | 'stockfish';
}

/** One picker row from GET /api/endgames/practice/categories. Only
 * categories that actually have sourced positions are returned, so the
 * picker never renders an empty placeholder. */
export interface EndgamePracticeCategory {
  category: string;
  position_count: number;
}

// --- Endgame Woodpecker review queue ------------------------------------
// Wire types mirroring the EndgameWoodpecker* models in
// src/schemas/endgame_schemas.py (routers/endgame_woodpecker.py). This is a
// separate queue from woodpecker_entries: a card here is created only by a
// FAILED rated drill, and it never appears in the puzzle queue.

export interface EndgameWoodpeckerQueueEntry {
  id: string;
  user_id: string;
  position_id: string;
  theme: string;
  added_at: string;
  mastered_at?: string | null;
  is_mastered: boolean;
  source_reason?: string | null;
  due: string;
  state: number;
  step?: number | null;
  stability?: number | null;
  difficulty?: number | null;
  reps: number;
  lapses: number;
  last_review?: string | null;
  /** Full drill payload in the exact shape GET /api/endgames/next returns. */
  position: EndgamePosition;
}

/** The FSRS transition written when a review replay resolves. */
export interface EndgameScheduling {
  prior_state: number;
  rating: number;
  new_state: number;
  due: string;
  stability?: number | null;
  difficulty?: number | null;
  step?: number | null;
  reps: number;
  lapses: number;
  is_mastered: boolean;
}

/** The endgame_woodpecker_attempts row written on resolution. */
export interface EndgameAttemptRecord {
  id: string;
  entry_id: string;
  user_id: string;
  solved_correctly: boolean;
  time_taken_ms: number;
  /** Hints revealed during the replay: solved_correctly stays the board
   * verdict, this says how it was reached. */
  hints_used: number;
  resolution?: EndgameResolution | null;
  failure_category?: EndgameFailureCategory | null;
  attempted_at: string;
}

export interface EndgameWoodpeckerAttemptPayload {
  entry_id: string;
  fen_before: string;
  move: string;
  fen_after: string;
  /** Accumulated across the whole replay; read only on the resolving move. */
  time_taken_ms: number;
  /** Hints this replay has revealed: on resolution a hinted pass is
   * scheduled as not-clean (FSRS Again), so the card cannot graduate. */
  hints_used?: number;
}

/** One continuation step of a settled FAILED drill ("Play it out"). */
export interface EndgamePlayoutReplyRequest {
  position_id: string;
  fen_after: string;
}

export interface EndgamePlayoutReplyResponse {
  /** null while the request is still on the stored line (the client applies
   * its own copy of the next stored move) or when the position is already
   * over. The backend route never grades and never writes. */
  opponent_reply?: EndgameOpponentReply | null;
}

/** Fast-forward request for a settled drill's continuation. */
export interface EndgamePlayoutFinishRequest {
  position_id: string;
  fen: string;
}

/** The terminal reasons a fast-forwarded line can reach. */
export type EndgamePlayoutFinishEnding =
  | 'checkmate'
  | 'stalemate'
  | 'insufficient_material'
  | 'fifty_move_rule'
  | 'seventy_five_move_rule'
  | 'fivefold_repetition';

export interface EndgamePlayoutFinishResponse {
  /** Tablebase verdict from the USER's colour, so the panel never has to
   * reason about whose turn the request position is at. Null only when the
   * request position was already terminal. */
  outcome?: EndgameOutcome | null;
  /** A concrete terminal position, when a cheap line existed; null when only
   * the verdict could be produced (6-7-man material costs seconds per ply on
   * the Lichess fallback). */
  fen?: string | null;
  ending?: EndgamePlayoutFinishEnding | null;
  plies: number;
  /** The ordered UCI moves that reached `fen`, for stepping the line with no
   * further requests (the board replays them against the request FEN).
   * Non-empty exactly when a concrete ending came back and the request
   * position was not already terminal; always empty on the verdict path. */
  line: string[];
}

export interface EndgameWoodpeckerAttemptResponse {
  entry_id: string;
  status: EndgameStatus;
  failure_category?: EndgameFailureCategory | null;
  resolution?: EndgameResolution | null;
  outcome_before?: EndgameOutcome | null;
  outcome_after?: EndgameOutcome | null;
  dtz_before?: number | null;
  dtz_after?: number | null;
  common_mistake?: string | null;
  /** Echoed from the request: > 0 means the resolved replay was
   * hint-assisted, which the panel calls out (and which is why the FSRS
   * block above shows the not-clean transition). */
  hints_used?: number;
  /** Absent by design: a review never touches users.endgame_trainer_rating. */
  opponent_reply?: EndgameOpponentReply | null;
  /** Present only on the resolving move. */
  attempt?: EndgameAttemptRecord | null;
  /** Present only on the resolving move. */
  scheduling?: EndgameScheduling | null;
}

export interface ExplanationRequest {
  fen: string;
  move: string;
  moveHistory?: string[];
  classification?: MoveClassification;
  isCorrect?: boolean;
  playerElo?: number;
  puzzleThemes?: string[];
}

export interface ExplanationResponse {
  explanation: string;
  concept: string;
  tip?: string;
}

export interface GameReviewMove {
  fen: string;
  san: string;
  color: 'white' | 'black';
  classification: MoveClassification;
  cp_loss: number;
  eval_cp: number;
  eval_mate?: number | null;
  best_move_san?: string | null;
  best_move_uci?: string | null;
  explanation?: {
    explanation: string;
    concept?: string;
    tip?: string;
  };
}

export interface UserSettings {
  elo: number;
  puzzleRatingMin: number;
  puzzleRatingMax: number;
  themes: string[];
  dailyPuzzleGoal: number;
}
