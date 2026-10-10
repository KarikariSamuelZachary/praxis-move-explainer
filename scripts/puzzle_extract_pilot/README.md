# Puzzle-extract pilot (offline, read-only)

Finds out whether puzzles extracted from your own games are good, before any
schema work. **No DB writes, no migrations, no routers. `src/` untouched.**

- Fetch: read-only. Lichess is fetched pilot-side with `clocks=true` (same
  endpoint/params as `src/integrations/lichess.py` plus clocks, reusing its
  helpers) so PGNs carry `[%clk]`; Chess.com via its integration as-is.
- Screen: Review per-ply path (`core.game_analyzer`) on a **private**
  engine (never the Review singleton), pinned
  `REVIEW_DETERMINISTIC=1 REVIEW_NODES=150000 MultiPV=2`.
- Engine: the SF19 binary from calc_probe via `STOCKFISH_PATH` (or
  `--stockfish`), used for BOTH screen and Stage 2. Path, version and
  sha256 go in the `runs` table; any other version refuses unless
  `--allow-other-engine`.
- Book: `services.opening_book` read-only when available; when it is
  empty/unavailable (or `--no-book`) user plies with
  `move_number <= --min-move` (default 8) are excluded as `book_fallback`,
  with a WARNING at the top of the report.
- Clock exclusion is relative: remaining `< max(10 s, 10% of base time)`
  from the `[TimeControl]` header (30 s fallback when base unknown).
- Stage 2: nodes-only fresh-token engine (`Threads=1 Hash=16`,
  MultiPV=4 @ 500k + played-position re-eval), same discipline as
  `scripts/calc_probe/engine_probe.py`.
- Outputs: SQLite file (`runs`/`meta`/`games`/`plies`/`verify`) +
  `review_sheet.md` (kept puzzles + 10 random rejects with reasons).

## Run

```bash
STOCKFISH_PATH=/path/to/sf19 python scripts/puzzle_extract_pilot/extract.py \
  --lichess <your_lichess> --chesscom <your_chesscom> \
  --max-games 20 --out extract_pilot.sqlite --sheet review_sheet.md
```

One provider is enough (`--lichess` or `--chesscom`).

### --max-games semantics (per provider)

`--max-games N` caps each provider independently: up to N most-recent
Lichess games AND up to N most-recent Chess.com games (2N total when both
usernames are given). Each provider fetch sorts by `end_time` descending
before the cap, so the cap always keeps the most recent games. Position
dedupe and the top-3-per-game cap apply across the combined set. A run
with `--max-games 20` and both providers therefore screens up to 40 games.

## Notes

- `[%clk]` on a move node is time remaining AFTER that move; v1 excludes a
  user ply when remaining `< max(10 s, 10% base)`. The report also shows
  what exclusion would be at 5%/10%/15% and at fixed 30 s.
- Candidate placeholders: `ep_loss >= 0.15`, `cp_loss >= 100` (no label
  gate); uniqueness placeholder `M = 0.10` EP margin. Yield grids for both
  are in the report. Mate-vs-mate and single-legal positions are excluded.
- Metadata only (never gating): best-move character
  (capture/check/mate/promotion/quiet) + material swing over the first 6
  plies of the best PV and the played-move PV; report shows kept-vs-rejected
  shares. Single-move puzzles only; the PV is stored for explanation, never
  graded.
