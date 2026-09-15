# Spec: Gambit-book "stay in book" nudge (Engine Sparring)

Status: PROPOSAL — not designed as a final build plan; needs one design pass
before implementation. No code exists for this yet.

## Motivation

The gambit-book bypass (`services/gambit_book.py`, wired Sacrificer-only in
`routers/train.py`) fixed gambit *presence*: at the live
`SACRIFICER_OFFER_PROBABILITY = 1.0`, whenever a sparring game sits exactly
on a pre-offer square, Sacrificer plays the book's gambit.

What it cannot fix is gambit **reach**. The data:

* The book has 396 offer points; only 41 are reachable with ≤ 3 plies of
  opponent cooperation. Famous deep gambits sit at squares that need 3+
  *exact* opponent moves (Halloween: `1.e4 e5 2.Nf3 Nc6 3.Nc3 Nf6`).
* The bot's NON-offer moves come from the persona reranker, not the book, so
  the game usually leaves the book line before reaching the next offer
  square. (Verified: at the Italian Game point, the pool is
  Jerome/Rosentreter/Evans — but only if the game walked
  `2.Nf3 Nc6 3.Bc4 Bc5`, where `3.Bc4` was a reranker choice.)
* Color gating is structural: black-offered gambits (Stafford, Budapest,
  Benko, Latvian, Elephant, ...) are unreachable whenever the bot plays
  White, and vice versa. Nothing fixes that except which side the bot plays;
  worth stating in the UI rather than fixing.

## Goal

Make the bot's non-offer moves, when the current position lies INSIDE a
known gambit line, follow that line's next move (probabilistically or
deterministically per configuration), so deeper gambits actually get reached
in live sparring.

## Proposal: a second index + a second bypass stage

**Index.** Build once from `gambit_openings.json` alongside the existing
index: for EVERY entry's stored `pgn` line, walk all prefixes and record

```
normalized 4-field position -> list of (entry, next_move_uci)
```

This is a strict superset of the offer index (the offer index maps a
position to the line's FINAL move; the line index maps every position on the
line to the following move). Same normalization convention
(`" ".join(fen.split()[:4])`), same exact-match semantics, same asset file.

**Pipeline change (endpoint-level only, mirrors today's bypass):**

```
1. offer check      (existing maybe_play_gambit — unchanged)
2. NEW: stay-in-book check
     look up the CURRENT position in the line index
     -> no hit: fall through to the pipeline (unchanged behavior)
     -> hit: pick ONE continuation (selection rule below), roll against
        STAY_IN_BOOK_PROBABILITY, and on success return that move as a
        book move (same response shape as today's gambit_book bypass,
        with its own marker sub-field, e.g. kind="line")
3. pipeline (unchanged)
```

This keeps `rerank_moves()` / `best_persona_move()` untouched (same design
constraint as the current bypass) and reuses the book-move response shape.
Mode B — injecting the book continuation as an extra rerank candidate — was
considered and rejected: the continuation is usually NOT in the engine's
MultiPV candidate list, so it would require plumbing a synthetic candidate
through `compute_style_scores()` + `persona_adjusted_score()` at the
endpoint, duplicating pipeline internals for a move we already decided to
play unconditionally at offer squares. Hard bypass is simpler and matches
the existing gambit_book contract.

**Selection rule (multi-line positions).** Same rule family as the offer
pool: prefer entries with `offer=True` when the continuation IS the offer;
otherwise uniform random among the position's line continuations. Needs the
same explicit tests as the offer pool (only-offers-reachable, uniform pick,
no-roll-on-empty).

**Parameters (to be decided in the design pass):**

* `STAY_IN_BOOK_PROBABILITY` — share of in-book positions that follow the
  line instead of the reranker. 1.0 = the bot plays the whole named line
  every game (maximum gambit reach, minimum surprise); lower values mix
  line-following with engine moves.
* Interaction with the offer rate: the offer check should stay FIRST (an
  offer square consumes the position before line-following applies).
* Whether line-following applies to the offering side only, or both sides
  (following the OPPONENT's known defense lines is a different persona —
  likely Sacrificer-only offering side, like today's gate).

## Risks / open questions

* **Determinism vs variety:** at rate 1.0 with a 1-line position, every game
  plays the same line. Uniform random among multi-line positions mitigates;
  per-session line memory would fix it fully but needs session state
  (explicitly out of scope for this codebase's stateless sparring endpoint).
* **Strength vs character:** named gambit lines can be objectively
  questionable (King's Gambit lines at Stockfish-level opposition lose
  material without compensation). That is the persona's job, but the design
  pass should decide whether line-following is capped by depth or by a
  material-down threshold.
* **Blunder gate:** the current book bypass skips the engine entirely, so
  no safety check applies; same for line-following. Accepted by design
  (the asset is vetted opening theory), but document it in the response
  contract the same way (`gambit_book` marker).
* **Multi-match density:** the raw index has positions with up to 17 named
  entries (all continuations, verified offer=False) — the selection rule
  must never surface continuation moves as "offers" (already enforced) and
  the line index inherits the same requirement.

## Test plan (mirrors the bypass tests)

1. Index integrity: every entry's every prefix indexed; keys 4-field.
2. Forced-success line-follow at a real deep line (e.g. KGA Main Line):
   returns the line's actual next move, one roll.
3. Forced-failure / probability=0 / probability=1 extremes.
4. FEN-counter normalization (same as today).
5. Multi-line position: only legal continuations, uniform reachability.
6. Off-book opponent deviation -> fall-through, pipeline called.
7. Endpoint wiring: spy on the pipeline; zero regression on all suites.

## Explicitly out of scope

* Building Gambiter (this stays Sacrificer-only wiring, parameterized).
* Any change to `rerank_moves()` / `best_persona_move()` signatures.
* Session/game-state tracking (stateless endpoint stays stateless).
* Hand-curated extra lines (Jerome 5.Nxe5+ etc.) — separate data-prep task.
