from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class OpponentImportRequest(BaseModel):
    lichess_username: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Public Lichess username to import as an opponent profile.",
    )
    chesscom_username: Optional[str] = Field(
        None,
        min_length=1,
        max_length=100,
        description="Public Chess.com username to import as an opponent profile.",
    )
    limit: int = Field(
        100,
        ge=1,
        le=500,
        description="Maximum games per provider to fetch in the background job.",
    )


class OpponentImportStartResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    lichess_username: Optional[str] = None
    chesscom_username: Optional[str] = None
    limit: int


class OpponentImportJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    lichess_username: Optional[str] = None
    chesscom_username: Optional[str] = None
    requested_limit: int
    imported_count: int
    error_message: Optional[str] = None


class WeaknessProfileRequest(BaseModel):
    source_type: Literal["opponent", "user"] = "opponent"
    provider: Optional[Literal["lichess", "chesscom"]] = None
    opponent_username: Optional[str] = Field(None, min_length=1, max_length=100)
    limit: int = Field(
        50,
        ge=1,
        le=200,
        description="Maximum games to analyze from the selected corpus.",
    )


class WeaknessProfileStartResponse(BaseModel):
    job_id: str
    status: Literal["queued"]
    source_type: Literal["opponent", "user"]
    provider: Optional[str] = None
    opponent_username: Optional[str] = None
    limit: int


class WeaknessProfileJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    source_type: Literal["opponent", "user"]
    provider: Optional[str] = None
    opponent_username: Optional[str] = None
    requested_limit: int
    analyzed_games_count: int
    analyzed_moves_count: int
    mistake_count: int
    blunder_count: int
    summary: dict
    error_message: Optional[str] = None


class OpponentTrapResponse(BaseModel):
    # A recurring position the opponent has blundered in across 2+
    # different games. Produced by `services.opponent_traps
    # .compute_opponent_traps` (a read/aggregation over
    # `opponent_game_blunders`, NOT a new job). Empty list when zero
    # groups qualify — an expected, common case for opponents with
    # sparse blunder data, not a failure state.
    position_key: str
    # One representative full FEN from the group (the first row
    # encountered). The frontend uses this to render the board position
    # for the "Traps He's Fallen For" section.
    fen: str
    # Sorted distinct move_san values the opponent played at this
    # position that were classified as mistake or blunder. Multiple
    # distinct moves means the opponent has tried (and failed at) more
    # than one approach here.
    moves: List[str]
    # Worst classification in the group — "blunder" if any row was a
    # blunder, otherwise "mistake".
    classification: Literal["mistake", "blunder"]
    # Number of DISTINCT games in the group (deduped by game_id —
    # the same game blundering twice at the same position counts as 1).
    # Always >= 2 (the qualifying threshold); groups below 2 are
    # filtered out before reaching the response.
    game_count: int
    move_number_min: int
    move_number_max: int
    # Always "position" — the only tier implemented. An opening-family
    # fallback tier is intentionally NOT built (scope creep for this
    # task). If a later task adds it, this field becomes a Literal
    # union; for now it is a single-value Literal.
    tier: Literal["position"]


class OpponentProfileResponse(BaseModel):
    provider: Literal["lichess", "chesscom"]
    opponent_username: str
    game_count: int
    rating: int
    # Per-time-class average rating for the opponent, keyed by the
    # provider's time-class label (`rapid`, `blitz`, `bullet`, or
    # `daily` for Lichess's "correspondence"). Computed by averaging
    # the opponent's per-game rating (read from
    # `opponent_games.white_player.rating`/`black_player.rating` filtered
    # to the opponent's side) within each time-class bucket. Values are
    # ints (rounded means); a key is OMITTED iff no game in that class
    # was found for the opponent — callers must treat a missing key as
    # "no games at that speed" rather than "rating 0". Used by the
    # Opponent Preparation page's per-time-class rating row.
    ratings_by_time_class: Optional[Dict[str, int]] = None
    # Playing-style pill derived from the opponent's recency-weighted
    # sacrifice frequency (computed in
    # `services.opponent_style.compute_opening_results`'s same
    # per-game loop — no extra PGN parse). Bins (chosen to match
    # the spec's "Passive / Balanced / Aggressive" labelling):
    #   * < 0.05    -> "Passive"
    #   * 0.05-0.15 -> "Balanced"
    #   * >= 0.15   -> "Aggressive"
    # None iff the corpus had zero opponent moves (empty corpus / every
    # game unparseable) — caller must treat None as "no pill", not
    # "Passive".
    playing_style: Optional[Literal["Passive", "Balanced", "Aggressive"]] = None
    # Opponent's most common time control across imported games
    # (recency-weighted; computed by
    # `services.opponent_style.compute_time_control_distribution`). Used by
    # the Opponent Preparation / Sparring page to prefill the Time Control
    # field when starting a sparring game. None iff the opponent is below
    # the MIN_STYLE_GAMES floor OR no imported game carried a parseable
    # `[TimeControl]` header — callers must treat None as "no prefill"
    # (an open field), never as "no time control".
    preferred_time_control: Optional[str] = None
    # Recency-weighted fraction (0-1) per time-control bucket; named buckets
    # (e.g. "3+2", "10+0", "1+0") in descending weight order, with a single
    # trailing "Other" bucket collecting everything outside the top few.
    # None under the same conditions as `preferred_time_control`.
    time_control_distribution: Optional[Dict[str, float]] = None
    # Per-opening win/loss/draw breakdown keyed by the SAME family labels
    # `opening_family_lean` (in compute_opponent_style) produces — both go
    # through `_analyze_game`'s single `_opening_family(game)` call, so
    # the Opponent Prep page's frequency and results views zip together by
    # key without a remap. Each value is
    # {weighted_count, weighted_wins, weighted_losses, weighted_draws,
    #  win_rate}; win_rate is None iff every game in the bucket was "*"
    #  (unfinished/aborted). NO minimum-sample floor — every bucket with
    # at least one parseable game is shown (the spec: "show every bucket
    # with at least one game, however small. This is deliberate, don't add
    # filtering"). None at the response level iff the opponent had zero
    # parseable PGNs.
    opening_results: Optional[Dict[str, Dict[str, Any]]] = None
    # Openings the opponent has LOST in, projected to the Opponent Prep
    # page's "Openings He Lost Against" panel. Each item is
    # `{name: str, loss_percentage: float, games: int}` where
    # `loss_percentage` is `weighted_losses / (weighted_wins +
    # weighted_losses + weighted_draws)` over the same recency-weighted
    # W/L/D counts `opening_results` exposes (0.0-1.0; multiply by 100
    # for display). Sorted by descending loss_percentage so the panel's
    # "most-lost-against-first" ordering is preserved at the API layer.
    # Empty list when no parseable games (mirrors the opening_results
    # None contract). A bucket is included only if it had at least one
    # decided-or-drawn game — pure-"*" buckets are excluded so the
    # percentage is meaningful (matches `win_rate`'s not-None contract).
    openings_lost_against: List[Dict[str, Any]] = Field(default_factory=list)
    # Recurring-position traps from Stockfish blunder classification.
    # Empty list when zero groups qualify (the common case for opponents
    # with sparse blunder data or before the analysis job has run).
    # Populated by `services.opponent_traps.compute_opponent_traps` via
    # `list_opponent_profiles` — same endpoint, same call, so the frontend
    # gets opening results, time-control breakdown, AND traps in one
    # response without a second round-trip.
    traps: List[OpponentTrapResponse] = Field(default_factory=list)


class OpponentProfileListResponse(BaseModel):
    opponents: list[OpponentProfileResponse]


class OpponentProfileInfoResponse(BaseModel):
    # Display metadata for the opponent — avatar URL + verified badge.
    # Fetched lazily (chess.com has these on their public profile
    # endpoint; Lichess does NOT expose either, so Lichess opponents
    # always get {avatar_url: None, verified: False}). Cached
    # server-side per-process to avoid hammering chess.com on every
    # sparring-page mount.
    provider: Literal["lichess", "chesscom"]
    opponent_username: str
    # chess.com's `avatar` field is a 200x200 PNG by default; on rare
    # accounts it can be missing/null. Frontend falls back to an
    # initials circle when this is None.
    avatar_url: Optional[str] = None
    # chess.com exposes a `verified` boolean on the public profile
    # (lichess has no equivalent). Frontend renders a check badge when
    # True; ignored when False.
    verified: bool = False


class OpponentAnalysisStatusResponse(BaseModel):
    # Opponent identifier
    provider: Literal["lichess", "chesscom"]
    opponent_username: str
    # Job state — idle (never run / nothing to do), running (actively
    # processing), complete (all games analyzed). The frontend polls this
    # for "Analyzing his games… 47/124" and stops polling when
    # status != running.
    status: Literal["idle", "running", "complete"]
    analyzed_games: int
    total_games: int
    started_at: Optional[str] = None
    heartbeat_at: Optional[str] = None


class SparringMoveRequest(BaseModel):
    provider: Literal["lichess", "chesscom"]
    opponent_username: str = Field(..., min_length=1, max_length=100)
    fen: str = Field(..., min_length=1, max_length=200)
    bot_color: Literal["white", "black"]
    catastrophic_loss_cp: int = Field(300, ge=100, le=2000)
    maia_temperature: float = Field(0.15, ge=0, le=2)


class SparringMoveResponse(BaseModel):
    move_uci: str
    move_san: str
    source: Literal["in_book", "playing_naturally", "correcting_blunder"]
    opponent_elo: int
    repertoire_frequency: Optional[int] = None
    cp_loss: int = 0
    best_move_uci: Optional[str] = None
    best_move_san: Optional[str] = None
