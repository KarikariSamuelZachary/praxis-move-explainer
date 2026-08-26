# 2026-08 reranker investigation — archived diagnostics

Test-only measurement/diagnostic scripts from the August 2026 reranker
investigation. **None of these touch production code** — they import the
production `rerank_candidates` / `pick_repertoire_move` /
`pick_near_repertoire_moves` / `compute_opponent_style` / traps functions
verbatim and measure them, mostly over the cached held-out positions
produced by `scripts/heldout_replay_accuracy.py` (see that script's
docstring for the canonical methodology: chronological 80/20 build/held-out
split, session-local temp-table build-set isolation, book-status + ply-depth
bucketting).

They are kept for the historical record and for reproducing the numbers that
motivated the shipped changes (game_length removed, castle disabled). They
are NOT wired into any production path and are not maintained.

Runtime prerequisites: `.env` with `DATABASE_URL` (the opponent tables must
be populated), and typically a cached positions JSON (e.g.
`/tmp/opencode/heldout_positions.json`) produced by `heldout_replay_accuracy.py`.
Several scripts also need Maia (the patched UCI wrapper) available.

| Script | Question it answered | Headline finding |
|---|---|---|
| `candidate_injection_prototype.py` | Does injecting retrieved moves as independent candidates (instead of only reranking Maia's list) recover the multipv promotion-ceiling gap? | **No.** Near-book retrieval is recall-rich but precision-poor: injected moves win only ~9–16% of the reachable ceiling while introducing thousands of false positives (net ≈ −32pp at any weight that actually promotes). Exact-book (in_book) injection is ~100% precise but that slice is tiny and already served by mirror-mode. Same verdict as multipv/near_book: real mechanism, negligible practical effect. |
| `castle_rebuilt_diagnostic.py` | Net delta of the REBUILT narrow castle indicator (fires only on literal O-O/O-O-O), after the coarse indicator was narrowed. | **STALE** — kept for reference only. Measured net **−0.44pp** (5 helped / 18 hurt; in_book −0.61pp): a strict improvement over the old −1.22pp but still net-negative, so castle was left disabled. Will not produce a meaningful measurement against current code: `castle_multiplier` is pinned to 1.0 while `CASTLE_BIAS_ENABLED = False`. |
| `confirm_neutralization.py` | After neutralizing castle + game_length, how often does each signal actually fire (`bias_breakdown.signals_applied`)? | Confirmed castle and game_length fired 0× across all held-out positions while the other signals still fired. Historical — trivially true now (game_length deleted, castle disabled). |
| `heldout_ablation.py` | Isolate the near-book layer (style_only vs full) and the pre-existing style stack (raw vs style_only) on held-out top-1. | Both the style stack and the near-book layer net to roughly neutral on top-1; neither is the source of meaningful gain or loss. |
| `length_castle_diagnostic.py` | Isolated net delta of the game_length forcing-move signal and the castle signal, separately. | **STALE** — kept for reference only. game_length measured **−0.78pp** net; castle (coarse indicator) measured **−1.22pp** net (16 helped / 52 hurt; in_book −4.88pp). These two numbers drove the shipped removal/disable. Reads `length_multiplier` / `length_indicator` / `length_centered` / `average_game_length`, which no longer exist — **will not run against current code without modification.** |
| `multipv_latency.py` | Full-pipeline latency (Maia + reranker) vs `multipv`, capturing the reranker's per-candidate cost the Maia-only estimate missed. | multipv=10 costs ~+63% median / ~+111% p95 over multipv=5; multipv=8 ~+40%. Supported keeping multipv=5. |
| `self_consistency.py` | Empirical prediction ceiling: how consistently does the opponent play the same move from the same position across their own games? | iaminspiredbroo 94.65% (coverage 16.2%), samuel4real 86.59% (coverage 13.4%). Sets an upper bound on any predictor's top-1. |
| `setup_net_effect.py` | Net top-1 effect of the `setup_signature` bias (help vs hurt vs neutral) on held-out positions. | Near-neutral (+0.14pp); setup was not implicated in the net-negative signals and was left unchanged. |
| `setup_signal_diagnostic.py` | Does the setup_signature structural-similarity signal actually discriminate, and how often does it fire? | Real but weak signal; firing pattern is sparse and its net top-1 effect is near-neutral. |

**Stale (referenced removed fields / obsolete measurement):**
- `length_castle_diagnostic.py` — reads `length_multiplier` / `length_indicator`
  / `length_centered` / `average_game_length`, all removed when the game_length
  signal was deleted (2026-08-23). Will not run without modification.
- `castle_rebuilt_diagnostic.py` — does not reference removed fields and will
  not crash, but its measurement is moot: `castle_multiplier` is now pinned to
  1.0 because `CASTLE_BIAS_ENABLED = False`, so it can no longer measure the
  castle signal.

Kept active (not archived) because they are reusable measurement
infrastructure rather than one-off diagnostics:
- `scripts/heldout_replay_accuracy.py` — the canonical held-out evaluation.
- `scripts/import_opponent.py` — generic Chess.com opponent import.
