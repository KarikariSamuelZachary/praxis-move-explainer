# Gate pre-registration: review N and label parity

Registered before running the final gate on set B. Any change to the formulas
below after this commit invalidates the gate; record a new pre-registration
commit instead.

## Frozen artifacts (SHA-256)

| file | role | sha256 |
|---|---|---|
| `data/gate_set_A.pgn` (tuning) | tuning | `05538f9dda21b50ca6956ebf789ec8f99cf23660573849c8ec346d50360ada5f` |
| `data/gate_set_B.pgn` (final gate) | base gate | `8394f93779132c0fc94d6f5c26022de563fc0b90724c3d6e00219836a8dac8fb` |
| `data/gate_set_B.neutral.thibault.pgn` | primary human (~1750) | `d473e4cb30932d7b81900e8c761171fae57c4e110b93b5192c4958a907a51533` |
| `data/gate_set_B.neutral.chessweeb.pgn` | primary human (~2700) | `8c199af89cb5aea901cb37c0ce142098db30147e3083b2f717999b042ed4e1e1` |
| `data/gate_set_B.neutral.maia1.pgn` | secondary bot-style | `232d1137930590f62a96512ca843fa031105bf448820e7c171152da982ac10ad` |
| `data/gate_sets.json` (manifest) | record | ids, aliases, exclusions, per-file neutral entries |
| `data/gate_used_ids.json` (exclusions) | record | 240 ids, verified `B ∩ used = 0` |

- Split: disjoint opponent pools, even share per opponent; the accounts
  `iaminspiredbro` and `iaminspiredbroo` are aliased to one pool and stay in
  the same set.
- Set B excludes every id in `data/gate_used_ids.json`: the old A/B pools the
  200-ply N sweep and noise floors sampled from, the exact 10-game 200-ply
  sample, and the exact 15-game 300-ply subsample of the intermediate
  `54468c1b` A set.
- Neutral games go entirely into set B as one capped file per source (cap
  120 each = `--per-set`, so no file outnumbers the base B set).
  Human primary set: one authenticated Lichess export per user
  (`--neutral-user thibault ChessWeeb --neutral-max 300`), 300 fetched each:
  `thibault` (human, ~1750 blitz, active; 1 BOT-opponent game and 11 short
  games dropped, 288 kept) and `ChessWeeb` (human streamer GM account,
  ~2700 blitz, archive games; 0 BOT games, 1 short game dropped, 299 kept),
  each truncated to the first 120. `maia1` stays as the secondary bot-style
  reference (BOT account, single style): the legacy 274-game file truncated
  to its first 120 (all 120 involve the BOT side, by design).
  Verified per file: exact-PGN overlap with A = 0, with B = 0, with the other
  neutral files = 0 (by GameId); 0 of the 240 used-id substrings; no A/B
  opponent names among White/Black headers; 0 BOT-title games in the human
  files. `ChessWeeb`'s account is inactive (games are 2024 archive), recorded
  here so staleness cannot be mistaken for fresh sampling.

## Per-file gate (registered before anything runs on B)

- The final-gate formula (8) is evaluated on the base B file **and** on each
  neutral file independently, with the same reference, candidates, seed, and
  CIs. Result is recorded per file: PASS if the file meets (8), FAIL
  otherwise.
- Overall gate verdict = PASS only if base B **and every neutral file**
  PASS. Any single file FAIL fails the gate; no post-hoc pooling or
  file-dropping is allowed (a new pre-registration is required instead).
- Frozen file minima (checked at export, enforced by the script): each
  neutral file holds >= 50 games after filtering (`--neutral-min-games`),
  <= 120 games (`--neutral-file-cap`), and human files hold 0 BOT-title
  games. A source violating any minimum fails at export time and never
  reaches the gate.

## Game-length cap

Cap applies in deterministic mode only; flag off keeps the old behavior
(no ply cap, only the proxy's 2 MiB PGN size bound). When the flag is on,
`review_max_plies()` computes the cap from the container speed:
`max_plies = (240 - serial_LLM) / 2 * NPS / N - 1`. Until
`REVIEW_CONTAINER_NPS` is measured it falls back to `GATE_P99_PLIES = 157`
= p99 of the mainline-ply distribution of A ∪ B (240 games: p50 61, p90
114, p95 134, p99 157, max 177). The frontend surfaces the backend 4xx
detail, e.g. "Game too long for review: 158 plies (max 157)".

## Reference and candidates

- Reference: fresh-token, fixed **1,000,000 nodes**, `Threads=1`, `Hash=16`,
  `MultiPV=2`, Stockfish 19 (`STOCKFISH_PATH`).
- Candidates: N in {60k, 100k, 150k, 300k} plus the shipped old mode
  (`depth=18`, `analysis_time=0.5s`, no fresh token, `MultiPV=2`).

## Formulas

1. Severity ordering: best < excellent < good < inaccuracy < mistake < miss <
   blunder; book/brilliant/great are special and never counted as crossings.
2. Severity-crossing rate: moves where the candidate and reference labels
   differ by >= 2 severity levels, divided by all moves.
3. 95% CI: game-cluster bootstrap (resample whole games with replacement,
   2000 iterations, seed 7).
4. Depth-reached distribution: engine-reported `depth` over all candidate
   evaluations, reported p50/p90/p95.
5. Mate recall and long-mate (>= 6) retention are reported for information
   only; they are **not** N criteria.
6. N fitness: engine seconds `(plies + 1) * N / nps` must fit
   `(240 - serial_LLM) / 2` at `plies = 157`. N target is the container
   old-mode median; the container old-mode p10 is the floor.
7. Paired comparison vs old mode: on the same games, `direct` is the
   severity-crossing rate between the candidate and old-mode labels;
   `ref_diff` is the candidate-vs-reference crossing rate minus the
   old-mode-vs-reference rate (negative = the candidate disagrees with the
   reference less than old mode does). Both get game-cluster bootstrap 95%
   CIs; a CI excluding 0 is the significance test.
8. Final gate (set B): run N against the 1M reference and compare with old
   mode on the same positions. Pass if the crossing-rate point estimate is
   at most the old mode's and the bootstrap CI upper bound is not worse than
   the old mode's by more than the reference's own instability
   (400k-vs-1M crossing rate measured on a 300-ply subsample).

## Evidence files

- `data/measurements/` holds the sweep JSONs (sample ids, budgets, CIs,
  depth distributions, mate reports).
