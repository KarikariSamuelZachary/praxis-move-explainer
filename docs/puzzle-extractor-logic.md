# Puzzle extractor: how it works (handoff brief)

Personal tactics mined from the user's own games. Pipeline lives in
`src/services/user_puzzle_extract.py`, pure board/PV analysis in
`src/services/puzzle_quality.py`, serving/grading in
`src/routers/user_puzzles.py`, decks UI in
`frontend/src/app/(app)/puzzle-extractor/page.tsx`.
Tests: `src/services/puzzle_quality_test.py` (26),
`src/routers/user_puzzles_test.py` (8).

## 1. Fetch and store games

Job-based (FastAPI BackgroundTasks, own DB connection, heartbeat on the
job row so stale `running` jobs are reclaimable). Providers: Lichess
(with clocks, paged via `before`) and Chess.com (monthly archives),
read-only. Fetching is incremental: pages walk newest-first and only
game URLs never stored for that user are collected (fully-seen page,
short page, and unit caps bound the walk), so repeat imports cost
network only, never engine time; the job limit caps processed games
newest-first. One running job per user (409 otherwise) plus 10 starts
per day. Games persist into `user_games`
(upsert on user + provider + URL). Recent import yielded ~2 puzzles from
a 20-game pull, so current yield is roughly 0.1 puzzles per game.

## 2. Stage-1 screen (cheap, per ply)

Private deterministic Stockfish, MultiPV=2 at 50k nodes, only the user's
own moves reviewed. Exclusions before any search: non-standard variants,
bullet time controls, opening-book moves (or move number <= 8 fallback),
positions played with under 10% of base time left (10s floor).
Candidate gates: expected-points loss >= 0.15 AND centipawn loss >= 100,
no label gate. Volume control: dedupe by position key, keep top 3 per
game by EP loss.

## 3. Stage-2 verification (expensive, per candidate)

Fresh-nodes engine, MultiPV=4 at 500k nodes, Threads=1 Hash=16.
Position re-analysed before the mistake and after it. Rejects:
- only one legal move, no engine line
- deeper search now prefers the move the user actually played
- mistake rechecked below 0.15 EP / 100 cp at depth
- best and second lines both mate for the solver
- no second line to compare against
EP margin of best over second line is computed and stored but has NO
accept threshold anymore (informational only).

## 4. Quality layer (the current acceptance rule)

`build_quiz_line()` walks the stored best PV (cap 6 plies) and truncates
at the first decisive solver move: checkmate, promotion, or a capture
that still leaves the solver ahead by a pawn once the next PV ply lands
as a legal reply. That reply is verification only and is never served.
Without an early payoff the line continues only while forcing
(2+ checks/captures in a 4-ply window), collapses to the single best
move otherwise, and trailing opponent plies are always trimmed, so every
quiz ends on the solver's move.

`decide()` then requires BOTH:
- loud opener: the best move gives check, captures, mates, or promotes
  (a quiet best move is rejected as `quiet_first_move`, even when the PV
  finds a tactic later)
- decisive ending: mate, promotion, or confirmed capture, else
  `no_concrete_ending` (announced mates the line cannot deliver are
  `mate_too_deep`)

Theme follows the ending: mate, promotion, capture-that-also-traps
(trap detection: enemy minor+ attacked, at most 1 safe escape, winning
capture exists, not already hanging before the move), else material.
Trap or forcing motifs alone never accept. A promotion counts even when
the promoted piece is recaptured immediately.

## 5. Storage, serving, practice

Kept puzzles store FEN, mistake, best move, full PV, losses, margin,
theme, and game link (source account + provider joined from
`user_games`). One queue entry per user per puzzle; re-extraction reuses
rows. The frontend groups due cards into per-source deck cards
(repertoire-style, heading + due count only) and solves one deck at a
time against the stored line (server-side grading, one wrong move fails
the card, FSRS scheduling, bad-puzzle feedback excludes). Removing a
source deck deletes that source's imported games along with its puzzles,
so extracting the account again re-mines it instead of silently skipping
every game as already-seen; an unscoped clear wipes all extractor
artefacts but keeps the game corpus.

## 6. Where good puzzles currently die (levers for more yield)

1. Mistake gates (0.15 EP + 100 cp at both stages) exclude small
   inaccuracies entirely; only clear mistakes become puzzles.
2. Bullet, low-clock, book, and variant positions are never screened.
3. `quiet_first_move` kills quiet brilliancies and scholar's-type
   mate-in-N (quiet threat first, mate later).
4. Captures need a legal reply in the PV plus pawn-ahead advantage after
   it; equal trades and unconfirmed wins die.
5. Mates the engine announces beyond 4, or delivered past the 6-ply cap,
   die as too deep.
6. Traps only count when paired with a capture ending on the best move;
   a trap set up mid-line is invisible to the theme.
7. Margin over the second line is stored but not enforced; uniqueness
   beyond "best != played" is unexploited.
8. Grading is all-or-nothing on the full line; near-misses retire cards
   the same as blunders.
