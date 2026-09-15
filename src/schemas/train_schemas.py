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
        500,
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
    total_games: int = 0
    error_message: Optional[str] = None
    opponent_prep_ready: bool = False
    repertoire_index_status: Literal["queued", "running", "complete", "failed"] = "queued"
    repertoire_indexed_games: int = 0
    repertoire_total_games: int = 0


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
    avatar_url: Optional[str] = None
    verified: bool = False
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
    # The current sparring session's time control, so the style-bias
    # re-ranker can prefer games from the same (or similar) time control
    # when aggregating the opponent's style profile. Accepts a canonical
    # bucket label ("bullet"/"blitz"/"rapid"/"classical"), an "M+I"
    # label ("3+2"), or a raw "base+inc" seconds string. When omitted
    # (or unbucketable) the style layer falls back to recency-only
    # weighting -- existing callers are unaffected.
    time_control: Optional[str] = Field(
        None,
        max_length=20,
        description=(
            "Sparring session time control (bucket label, 'M+I' label, "
            "or 'base+inc' seconds). Optional; when absent the style "
            "layer uses recency-only weighting."
        ),
    )


class SparringMoveResponse(BaseModel):
    move_uci: str
    move_san: str
    source: Literal["in_book", "playing_naturally", "correcting_blunder"]
    opponent_elo: int
    repertoire_frequency: Optional[int] = None
    cp_loss: int = 0
    best_move_uci: Optional[str] = None
    best_move_san: Optional[str] = None


class SparringWarmupRequest(BaseModel):
    # Session-start precomputation: precomputes the opponent's style profile
    # and exploitable traps into the sparring hot-path cache so the FIRST
    # out-of-book Maia move isn't slowed by the corpus-replay cache miss.
    # Same time_control semantics as SparringMoveRequest (the cache key is
    # per-TC, so the warmup must use the exact TC label the moves will use).
    provider: Literal["lichess", "chesscom"]
    opponent_username: str = Field(..., min_length=1, max_length=100)
    time_control: Optional[str] = Field(None, max_length=20)


class SparringWarmupResponse(BaseModel):
    warmed: bool
    already_warm: bool
    opponent_elo: int


# The Engine Sparring persona choices as a schema-layer Literal. REDECLARED
# here rather than importing services.persona_reranker.PersonaType: no module
# under src/schemas/ imports from services/ or engines/ (checked: zero
# existing occurrences) -- the schema layer is pure Pydantic contracts with
# no service dependencies, and importing a service enum would invert that
# layering. Drift between this Literal and PersonaType's values is pinned by
# a test in routers/train_engine_sparring_test.py (get_args == enum values).
PersonaName = Literal["attacker", "sacrificer", "defender", "positional", "gambiter"]


class EngineSparringMoveRequest(BaseModel):
    # Strength-control fields for the Stockfish-based Engine Sparring mode.
    # This is a SEPARATE feature from the Maia-based Opponent Preparation
    # flow (SparringMoveRequest above): there is no provider /
    # opponent_username / time_control here -- the user picks a Stockfish
    # strength and a persona instead of importing an opponent.
    #
    # The valid Elo range is deliberately NOT hardcoded in this schema. It is
    # read at request time from the bundled Stockfish binary's advertised
    # UCI_Elo min/max (via engines.stockfish_engine.configure_strength), so a
    # Stockfish upgrade cannot drift the schema out of sync. Both fields are
    # optional; when both are None (or omitted) the engine plays at full
    # strength.
    #
    # Move-generation fields, mirroring SparringMoveRequest's stateless
    # pattern exactly: the frontend owns the game (chess.js) and sends the
    # CURRENT position's FEN on every request; there is no server-side
    # session. `fen` uses the same length bounds as SparringMoveRequest.fen,
    # and `bot_color` the same Literal, so the two move endpoints accept
    # identically-shaped positions.
    fen: str = Field(..., min_length=1, max_length=200)
    bot_color: Literal["white", "black"]
    # Which persona re-ranks the engine's candidates. The string values match
    # services.persona_reranker.PersonaType exactly (see PersonaName above);
    # an unknown name is rejected by Pydantic at parse time (422) before the
    # endpoint runs -- the service layer's resolve_persona() remains as the
    # loud second gate for non-HTTP callers.
    persona: PersonaName
    target_elo: Optional[int] = Field(
        None,
        description=(
            "Target Elo for Stockfish's UCI_LimitStrength/UCI_Elo limiting. "
            "Valid range is read from the bundled Stockfish binary's "
            "advertised UCI_Elo option (Stockfish 16: 1320-3190)."
        ),
    )
    skill_level: Optional[int] = Field(
        None,
        description=(
            "Coarse Stockfish Skill Level (0-20). Used when target_elo is "
            "omitted, or as a fallback on builds that don't advertise UCI_Elo."
        ),
    )


class GambitBookMove(BaseModel):
    # Identifying metadata for a move played straight out of the classical
    # gambit book (services/gambit_book.py) instead of by the persona
    # reranker. Populated ONLY on gambit-book bypass moves (Sacrificer, at
    # the configured offer rate); null on every reranker-selected move.
    # `uci` repeats move_uci so the field stays standalone-parseable (name +
    # eco alone can be ambiguous: the same opening name exists at several
    # line depths in the book).
    name: str
    eco: str
    uci: str


class EngineSparringMoveResponse(BaseModel):
    # The persona's chosen move plus the transparency numbers the Sparring UI
    # shows. Where SparringMoveResponse surfaces `cp_loss` (how much the
    # safety check's blunder gate cost the chosen move vs the engine's best),
    # this response surfaces the persona rerank directly:
    #   * engine_score_cp  -- the chosen move's RAW engine score (suggest()'s
    #     score_cp, side-to-move POV, mate coerced to +/-10000);
    #   * engine_norm_cp   -- that score minus the engine's best candidate's
    #     score (<= 0; 0 == the persona played the engine's own best move);
    #   * persona_final_cp -- the persona-adjusted ranking number the move
    #     was actually selected by (norm + 100*bias, trust-decayed,
    #     demotion-floored at -75) -- the "what did style cost/gain"
    #     figure.
    #   * best_move_uci/best_move_san -- the engine's own top choice, present
    #     ONLY when it differs from the chosen move (null when the persona
    #     picked the engine's actual best, same spirit as
    #     SparringMoveResponse's optional best_move fields).
    #   * gambit_book -- populated ONLY when the move came from the classical
    #     gambit book (services/gambit_book.py) instead of the reranker
    #     (currently Sacrificer-only, at the configured offer rate
    #     services.gambit_book.SACRIFICER_OFFER_PROBABILITY -- live value 1.0,
    #     i.e. a book offer at EVERY exact pre-offer square). On such moves the
    #     engine is BYPASSED for the move, so the numeric fields are the
    #     documented "not computed" sentinels -- engine_score_cp=0 (int is
    #     kept so the existing frontend contract is unchanged),
    #     engine_norm_cp=0.0, persona_final_cp=0.0 -- and best_move_* are
    #     null (no engine-best comparison exists). Consumers MUST check
    #     gambit_book rather than inferring anything from the numeric fields
    #     on book moves: a 0 there means "engine not consulted", not
    #     "perfectly equal move".
    move_uci: str
    move_san: str
    persona: PersonaName
    engine_score_cp: int
    engine_norm_cp: float
    persona_final_cp: float
    best_move_uci: Optional[str] = None
    best_move_san: Optional[str] = None
    gambit_book: Optional[GambitBookMove] = None


class OpponentDataClearResponse(BaseModel):
    # Result of DELETE /api/train/opponent-data — wipes the sparring
    # feature's persisted state so the user isn't paying DB storage for
    # opponent PGNs / analysis they're done with.  Two scopes:
    #   * scope="all": cleared every opponent_* row for the user
    #   * scope="opponent": cleared one (provider, opponent_username)
    # provider / opponent_username are populated only when scope="opponent".
    #
    # Counts are DIRECT deletes issued by the single transaction.  Child
    # rows removed by ON DELETE CASCADE are NOT counted here — the parent
    # count is the user-facing signal.  Cascade-removed children of
    # opponent_games: opponent_repertoire_moves, opponent_game_analysis,
    # opponent_game_blunders.  Cascade-removed children of
    # weakness_profile_jobs: weakness_profile_moves.
    scope: Literal["all", "opponent"]
    provider: Optional[Literal["lichess", "chesscom"]] = None
    opponent_username: Optional[str] = None
    opponent_games_deleted: int
    opponent_analysis_jobs_deleted: int
    opponent_import_jobs_deleted: int
    weakness_profile_jobs_deleted: int
