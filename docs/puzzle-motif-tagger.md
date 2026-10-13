# Motif tagger: scope spec (do not build until the 200-game import lands)

## Non-goals

- Acceptance stays exactly as is. The tagger runs only on already
  accepted puzzles and writes labels; it can never keep or reject.
- No copying from lichess-org/puzzler (verified AGPL-3.0). Reimplement
  from the published theme definitions and standard chess geometry.
- The 200-game import comes first: it shows whether variety or volume is
  the real bottleneck, and it supplies the validation corpus.

## Label set (v1, precision-first)

Allowed to stay untagged: a wrong "fork" tag is worse than no tag, so
every detector abstains unless its geometric conditions hold crisply.
Multi-label: a puzzle can carry several motifs.

1. Mate patterns, read off the final position (mate in 1 delivered):
   back-rank mate, smothered mate, Anastasia-style nets only if they
   reduce to exact square sets. Start with back-rank + smothered; add
   more patterns only with per-pattern precision numbers.
2. Promotion (already known: best move promotes).
3. Fork: after the best move, one of our pieces attacks 2+ enemy pieces
   (king excluded from the count unless it forces decisive gain),
   verified on the resulting board, not by piece-movement heuristics.
4. Pin / skewer on the first move: best move creates absolute pin or
   skewer along a line (rook, bishop, queen), confirmed by
   attack-map before/after, with the pinned piece unable to move without
   exposing a more valuable piece or the king.
5. Discovered attack: best move uncovers a line piece onto a valuable
   target or check; detect by comparing sliding attacks before/after
   while the moved piece itself also creates a threat.
6. Hanging piece: best move captures an undefended piece (attackers > 0,
   defenders == 0 before the capture).
7. Trapped piece: reuse the existing find_piece_trap logic.

Explicitly deferred: deflection, attraction, clearance, interference
(combinational, need search beyond static geometry).

## Detection inputs

FEN before + best move + stored PV only (the same inputs as
puzzle_quality.py, no engine calls). New module
`src/services/puzzle_motifs.py`, pure functions, colocated
`*_test.py` with fixed FEN fixtures per motif (positive + negative).

## Storage and serving

- Migration: `user_puzzles.motifs TEXT[] NOT NULL DEFAULT '{}'` (or
  JSONB). Backfilled by re-extraction reusing rows; entries untouched.
- Queue payload: add `motifs: string[]`; deck filter by motif
  (multi-select chips restrict to puzzles carrying all selected motifs).
- Theme (mate/promotion/trap/material) stays the primary tag; motifs
  are the secondary faceted layer.

## Validation (required before merging)

- Corpus: the Lichess puzzle database dump (CC0, ships with theme
  tags). It is the only labeled ground truth available.
- Offset handling: in their CSV the first move belongs to the opponent,
  so the solution line starts with the opponent's reply; align indices
  before comparing predicted motifs to their tags.
- Report per-motif precision and recall on a held-out sample. Merge bar:
  precision >= 0.90 per shipped motif; recall is informational (drives
  the v2 list, never loosens v1 detectors).
- Theme-name mapping table (theirs to ours) lives in the validation
  script, not in the tagger.

## Open question for the data

If the 200-game distribution shows 80%+ material/capture with almost no
mates or promotions, the bottleneck is the mistake mix (your games, not
the filter), and the tagger's realistic job is subdividing captures
(fork/pin/hanging-piece) rather than finding exotic mates.
