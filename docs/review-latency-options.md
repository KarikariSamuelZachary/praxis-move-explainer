# Review latency: N, chunking, or a bigger container

## Constraints

- **Game length cap:** `REVIEW_MAX_PLIES = 157` in
  `src/core/analysis_mode.py` — the p99 of the frozen gate sets
  (`data/gate_sets.json`: A `05538f9d…`, B `8394f937…`). About 1% of gate
  games are rejected. The cap is enforced in `POST /api/review` **before the
  deterministic flag is read, so it applies with the flag off too**. The
  previous limit was none: only the proxy's 2 MiB PGN size bound existed.
- **Proxy timeout:** `REVIEW_TIMEOUT_MS = 240_000` in
  `frontend/src/app/api/analyze/route.ts` aborts the request at 240s.
- **Headroom:** N must fit with 2x headroom, i.e. engine time <=
  `(240 - serial LLM) / 2` seconds. Before the explain flip, review still
  generates explanations serially on the request path; assume 5 mistake/
  blunder explanations x 2s = 10s, so the engine budget is 115s.
- **Determinism:** review uses `Threads=1`, `Hash=16`, a fresh `ucinewgame`
  token per position, and fixed nodes. Threads must stay 1 for
  reproducibility, so N is the only quality knob. Extra cores do not speed up
  one review; they only allow more reviews in parallel.

## The arithmetic

`engine_seconds(N, plies) = (plies + 1) * N / nps`

At the route cap of 157 plies and 2x headroom, `N_max = 115 * nps / 158`.
Run `python scripts/n_ceiling.py --nps <container nps> --llm-per-call 2
--llm-calls 5` for the table. At the local p10-derived ~220k nps:

| N | per-eval | engine | x budget | fits |
|---|---|---|---|---|
| 60k | 0.27s | 43.1s | 0.37x | yes |
| 100k | 0.45s | 71.8s | 0.62x | yes |
| 150k | 0.68s | 107.7s | 0.94x | yes |
| 200k | 0.91s | 143.6s | 1.25x | NO |

Max N is ~160k. The container nps is still unknown; fill it in before fixing N.

## N decision rule

- **Target:** the container-measured **old-mode median** nodes per position.
- **Floor:** the container-measured **old-mode p10**. N must not be below it.
- **Fitness:** N must fit the 240s ceiling at 157 plies with 2x headroom.
- **Quality metric:** severity-crossing rate vs the 1M-node fresh reference,
  with a game-cluster bootstrap 95% CI; compare against the old mode's own
  crossing rate. Mate recall (including mates >= 6) is reported for
  information only and is **not** an N criterion: 60k already finds all
  mateIn2/3/4 puzzles, while deep mates are missed at every N below 300k
  anyway.

If the container nps comes back **under ~150k**, 150k no longer fits and the
chunked design below is the likely path.

## Option A - lower N

Set N to the table's max. Cost: more label churn vs the 1M reference at
positions whose evaluation is near a classifier boundary; read the
severity-crossing rate and its CI, not raw agreement.

## Option B - chunked / progressive review (likely path if nps < 150k)

Split the PGN into chunks of at most `C` plies (e.g. 40) and review them
sequentially on the same engine singleton:

- **API:** `POST /api/review/chunk` takes the same body plus
  `{chunk_start_ply, chunk_end_ply}` (or an opaque chunk cursor) and returns
  the rows for that window. The existing `/api/review` becomes a thin loop
  over chunks for callers that want one response.
- **Overlap:** each chunk includes one position before its first move, so the
  first move has an eval-before. Deterministic mode makes overlapping
  positions produce identical rows; the client drops duplicates by ply.
- **Latency:** first rows arrive after `(C + 1) * N / nps` instead of
  `(plies + 1) * N / nps`; at 220k nps and N=300k, a 40-ply chunk is ~56s
  versus ~216s for a 157-ply game. The client can render rows progressively.
- **Quality:** N can stay at the full quality target for any game length;
  chunking changes latency, not labels, because every position is still a
  fresh-token fixed-N search with the same mode string.
- **Mode string:** positions carry the same `rev-det-v1|...` fingerprint, so
  a chunk response can echo one mode string for stale-gate detection.
- **Failure/retry:** a failed chunk is retried alone; already-rendered chunks
  stay valid. The proxy's 5/min rate limit must be raised or the loop must
  run server-side (one upstream request).
- **Cost:** more implementation complexity and a client-side merge. This is
  the preferred option when the container cannot fit the full game.

## Option C - bigger container

More vCPU does not help a single review (Threads=1). What helps:

- a faster single core (nps scales with clock/IPC), or
- more concurrent reviews (one engine singleton per worker) if the container
  gets more cores; each review still takes the same wall time.

Measure container nps under load and re-run `scripts/n_ceiling.py`. If the
container nps is below the local p10 (~220k), the 2x-headroom max N may fall
below the old-mode p10 (109,657 locally), in which case lower N is not
acceptable and chunking or a bigger container is required.
