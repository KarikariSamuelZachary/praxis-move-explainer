# Gate pre-registration: review N and label parity

Registered before running the final gate on set B. Any change to the formulas
below after this commit invalidates the gate; record a new pre-registration
commit instead.

## Frozen artifacts (SHA-256)

| file | sha256 |
|---|---|
| `data/gate_set_A.pgn` (tuning) | `05538f9dda21b50ca6956ebf789ec8f99cf23660573849c8ec346d50360ada5f` |
| `data/gate_set_B.pgn` (final gate) | `8394f93779132c0fc94d6f5c26022de563fc0b90724c3d6e00219836a8dac8fb` |
| `data/gate_set_B.neutral.pgn` | pending (Lichess token or dump; hash added when frozen) |
| `data/gate_sets.json` (manifest) | committed; contains ids, aliases, exclusions |
| `data/gate_used_ids.json` (exclusions) | committed; 240 ids, verified `B ∩ used = 0` |

- Split: disjoint opponent pools, even share per opponent; the accounts
  `iaminspiredbro` and `iaminspiredbroo` are aliased to one pool and stay in
  the same set.
- Set B excludes every id in `data/gate_used_ids.json`: the old A/B pools the
  200-ply N sweep and noise floors sampled from, the exact 10-game 200-ply
  sample, and the exact 15-game 300-ply subsample of the intermediate
  `54468c1b` A set.
- Neutral games (when present) go entirely into B as a separate file.

## Game-length cap

`REVIEW_MAX_PLIES = 157` = p99 of the mainline-ply distribution of A ∪ B
(240 games: p50 61, p90 114, p95 134, p99 157, max 177). The cap is enforced
in `POST /api/review` before the deterministic flag is read, so it applies
with the flag off too. Previous limit: none (only the proxy's 2 MiB PGN size
bound). The frontend surfaces the backend 4xx detail, e.g. "Game too long for
review: 158 plies (max 157)".

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
7. Final gate (set B): run N against the 1M reference and compare with old
   mode on the same positions. Pass if the crossing-rate point estimate is
   at most the old mode's and the bootstrap CI upper bound is not worse than
   the old mode's by more than the reference's own instability
   (400k-vs-1M crossing rate measured on a 300-ply subsample).

## Evidence files

- `data/measurements/` holds the sweep JSONs (sample ids, budgets, CIs,
  depth distributions, mate reports).
