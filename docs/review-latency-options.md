# Review latency: N, chunking, or a bigger container

## Constraints

- **Game length:** the review route accepts at most `REVIEW_MAX_PLIES = 300`
  (`src/core/analysis_mode.py`); longer PGNs get a 400 before any engine work.
- **Proxy timeout:** `REVIEW_TIMEOUT_MS = 240_000` in
  `frontend/src/app/api/analyze/route.ts` aborts the request at 240s.
- **Headroom:** we size N so engine time is at most **half** the ceiling
  (120s at 300 plies), leaving the other half for UART/HTTP/LLM overhead and
  variance.
- **Determinism:** review uses `Threads=1`, `Hash=16`, a fresh `ucinewgame`
  token per position, and fixed nodes. Threads must stay 1 for reproducibility,
  so the only quality knob is N. Extra cores do not speed up one review; they
  only allow more reviews in parallel.

## The arithmetic

`engine_seconds(N, plies) = (plies + 1) * N / nps`

With the route max of 300 plies and 2x headroom, the budget is
`240 / 2 = 120s`, so `N_max = 120 * nps / 301`.

Run `python scripts/n_ceiling.py --nps <container nps>` for the full table.
At the local p10-derived ~220k nps: 100k fits (36.8s), 300k fits (110.5s),
600k does not (220.9s); max N is ~88k under the 2x rule, ~87k at 300 plies.
The container nps is still unknown; fill it in before fixing N.

## Option A - lower N

Set N to the table's max. Cost: more label churn vs a 1M reference at
positions whose evaluation is within ~a pawn of a classifier boundary; see the
severity-crossing rate from `scripts/nodes_selection.py` (adjacent flips are
cheap, severity crossings are what matter). Mate recall is not the constraint:
100/100 mateIn2/3/4 puzzles found at 60k nodes.

## Option B - chunked review

Split the PGN into chunks of at most `C` plies and review chunks sequentially
on the same engine singleton:

- `engine_seconds = (plies + chunk_overlap) * N / nps`; with chunks the per-
  request latency is `(C + 1) * N / nps`, so the UI can start rendering the
  first chunk while later chunks run.
- Chunk boundaries must overlap by one position (the last position of chunk k
  is the first "before" position of chunk k+1) so the first move of a chunk
  has an eval-before; labels are deterministic per position, so overlapping
  positions produce identical rows and the client can drop duplicates.
- The mode string is per-position, so a chunked response can still echo one
  mode string; keep `MODE_VERSION` in it.
- Cost: implementation complexity, more requests (rate limits are 5/min per
  user on the proxy), and the client must merge/stream partial rows. Benefit:
  N can stay at the quality target for arbitrarily long games and the first
  rows appear much sooner.

## Option C - bigger container

More vCPU does not help a single review (Threads=1). What helps:

- a faster single core (nps scales with clock/IPC), or
- more concurrent reviews (one engine singleton per worker) if the container
  gets more cores; each review still takes the same wall time.

Measure container nps under load and re-run `scripts/n_ceiling.py`. If the
container nps is below the local p10 (~220k), the 2x-headroom max N may fall
below the old-mode p10 nodes (109,657 locally), in which case lower N is not
acceptable and chunking or a bigger container is required.
