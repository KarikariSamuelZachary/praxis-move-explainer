# Blind Calculation survivor probe — pilot-sf19-42cell

Engine: Stockfish 19 | sha256=0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19 | seed=20261008 | per-cell=42 | workers=2 | nodes=50000/500000 | threshold=150cp | verify_survivors=True | fail_sample_frac=0.1 | check_solver=True (subsample=0.05)
Escalation: Stage 2 runs on Stage 1 passes only; Stage 1 failures are final (+ seeded fail sample). Trivially-forced segments (zero engine-verified replies) reported separately.

## 1. Funnel
- sampled puzzles: 1008
- defender replies analysed: 3488
- single-legal replies (auto-clean, no engine call): 768 (22.0%)
- Stage 1 verdicts (engine plies only): pass=1003 fail=1717
- Stage 2 verification runs on passes: 1003
- flip rate (fraction of Stage 1 PASSES): 146/1003 (14.6%)
- seeded fail sample escalated: 175, rescued by Stage 2: 19 (10.9% rescue rate; failures stay final)
- puzzles with >=1 clean segment: 703/1008 (excl. trivially-forced: 532/1008)
- total clean segments: 837 (non-trivial: 588)

## 2. Segment-length distribution (incl. / excl. trivially-forced)
- incl: L2=408, L3=211, L4=110, L5=83, L6+=25
- excl: L2=276, L3=137, L4=84, L5=67, L6+=24

## 3. Band x N matrix (distinct puzzles with a clean segment of >=N)
Cells: distinct puzzles [exact binomial 95% CI]; sampled = puzzles sampled in band. ⚠ flags bands with <10 sampled puzzles.

### Including trivially-forced segments
| band | N>=2 | N>=3 | N>=4 | N>=5 | N>=6 | sampled |
|---|---|---|---|---|---|---|
| 1200-1400 | 107 [0.56,0.71] | 67 [0.32,0.48] | 35 [0.15,0.28] | 14 [0.05,0.14] | 0 [0.00,0.02] | 168 |
| 1400-1600 | 111 [0.58,0.73] | 63 [0.30,0.45] | 30 [0.12,0.25] | 14 [0.05,0.14] | 4 [0.01,0.06] | 168 |
| 1600-1800 | 118 [0.63,0.77] | 70 [0.34,0.50] | 37 [0.16,0.29] | 23 [0.09,0.20] | 5 [0.01,0.07] | 168 |
| 1800-2000 | 122 [0.65,0.79] | 79 [0.39,0.55] | 41 [0.18,0.32] | 18 [0.06,0.16] | 5 [0.01,0.07] | 168 |
| 2000-2200 | 125 [0.67,0.81] | 78 [0.39,0.54] | 44 [0.20,0.34] | 22 [0.08,0.19] | 4 [0.01,0.06] | 168 |
| 2200+ | 120 [0.64,0.78] | 65 [0.31,0.46] | 31 [0.13,0.25] | 17 [0.06,0.16] | 7 [0.02,0.08] | 168 |

### Excluding trivially-forced segments
| band | N>=2 | N>=3 | N>=4 | N>=5 | N>=6 | sampled |
|---|---|---|---|---|---|---|
| 1200-1400 | 67 [0.32,0.48] | 34 [0.14,0.27] | 19 [0.07,0.17] | 9 [0.02,0.10] | 0 [0.00,0.02] | 168 |
| 1400-1600 | 80 [0.40,0.55] | 45 [0.20,0.34] | 23 [0.09,0.20] | 10 [0.03,0.11] | 3 [0.00,0.05] | 168 |
| 1600-1800 | 86 [0.43,0.59] | 52 [0.24,0.39] | 31 [0.13,0.25] | 19 [0.07,0.17] | 5 [0.01,0.07] | 168 |
| 1800-2000 | 99 [0.51,0.66] | 66 [0.32,0.47] | 37 [0.16,0.29] | 15 [0.05,0.14] | 5 [0.01,0.07] | 168 |
| 2000-2200 | 106 [0.55,0.70] | 60 [0.28,0.43] | 37 [0.16,0.29] | 21 [0.08,0.18] | 4 [0.01,0.06] | 168 |
| 2200+ | 94 [0.48,0.64] | 51 [0.24,0.38] | 28 [0.11,0.23] | 17 [0.06,0.16] | 7 [0.02,0.08] | 168 |

Derived-exercise count (NOT distinct puzzles): sum over stored segments of (L-N+1) for L>=N, incl. / excl. trivial:

| N | derived (incl) | derived (excl) |
|---|---|---|
| >=2 | 1625 | 1198 |
| >=3 | 788 | 610 |
| >=4 | 359 | 298 |
| >=5 | 141 | 123 |
| >=6 | 33 | 32 |

Stratum populations (band x family x depth):

| band | family | depth | population | sampled |
|---|---|---|---|---|
| 1200-1400 | endgame | 3 | 79184 | 11 |
| 1200-1400 | endgame | 4 | 11017 | 11 |
| 1200-1400 | endgame | 5 | 1650 | 11 |
| 1200-1400 | endgame | 6+ | 310 | 9 |
| 1200-1400 | forcing | 3 | 32266 | 19 |
| 1200-1400 | forcing | 4 | 2859 | 19 |
| 1200-1400 | forcing | 5 | 227 | 4 |
| 1200-1400 | forcing | 6+ | 21 | 0 |
| 1200-1400 | mate | 3 | 32484 | 13 |
| 1200-1400 | mate | 4 | 3510 | 13 |
| 1200-1400 | mate | 5 | 305 | 13 |
| 1200-1400 | mate | 6+ | 28 | 3 |
| 1200-1400 | other | 3 | 24683 | 0 |
| 1200-1400 | other | 4 | 1211 | 0 |
| 1200-1400 | other | 5 | 68 | 0 |
| 1200-1400 | other | 6+ | 3 | 0 |
| 1200-1400 | quiet | 3 | 4083 | 11 |
| 1200-1400 | quiet | 4 | 1760 | 11 |
| 1200-1400 | quiet | 5 | 884 | 10 |
| 1200-1400 | quiet | 6+ | 620 | 10 |
| 1400-1600 | endgame | 3 | 88797 | 11 |
| 1400-1600 | endgame | 4 | 14469 | 11 |
| 1400-1600 | endgame | 5 | 2605 | 10 |
| 1400-1600 | endgame | 6+ | 568 | 10 |
| 1400-1600 | forcing | 3 | 48510 | 13 |
| 1400-1600 | forcing | 4 | 5521 | 13 |
| 1400-1600 | forcing | 5 | 546 | 13 |
| 1400-1600 | forcing | 6+ | 68 | 3 |
| 1400-1600 | mate | 3 | 28241 | 12 |
| 1400-1600 | mate | 4 | 4887 | 12 |
| 1400-1600 | mate | 5 | 392 | 11 |
| 1400-1600 | mate | 6+ | 55 | 7 |
| 1400-1600 | other | 3 | 37974 | 0 |
| 1400-1600 | other | 4 | 2571 | 0 |
| 1400-1600 | other | 5 | 171 | 0 |
| 1400-1600 | other | 6+ | 17 | 0 |
| 1400-1600 | quiet | 3 | 7256 | 11 |
| 1400-1600 | quiet | 4 | 2542 | 11 |
| 1400-1600 | quiet | 5 | 1067 | 10 |
| 1400-1600 | quiet | 6+ | 649 | 10 |
| 1600-1800 | endgame | 3 | 89854 | 11 |
| 1600-1800 | endgame | 4 | 17350 | 11 |
| 1600-1800 | endgame | 5 | 3360 | 10 |
| 1600-1800 | endgame | 6+ | 869 | 10 |
| 1600-1800 | forcing | 3 | 56456 | 11 |
| 1600-1800 | forcing | 4 | 8774 | 11 |
| 1600-1800 | forcing | 5 | 1141 | 10 |
| 1600-1800 | forcing | 6+ | 181 | 10 |
| 1600-1800 | mate | 3 | 23021 | 13 |
| 1600-1800 | mate | 4 | 4944 | 12 |
| 1600-1800 | mate | 5 | 573 | 12 |
| 1600-1800 | mate | 6+ | 99 | 5 |
| 1600-1800 | other | 3 | 49054 | 0 |
| 1600-1800 | other | 4 | 4800 | 0 |
| 1600-1800 | other | 5 | 429 | 0 |
| 1600-1800 | other | 6+ | 48 | 0 |
| 1600-1800 | quiet | 3 | 10860 | 11 |
| 1600-1800 | quiet | 4 | 3664 | 11 |
| 1600-1800 | quiet | 5 | 1421 | 10 |
| 1600-1800 | quiet | 6+ | 894 | 10 |
| 1800-2000 | endgame | 3 | 86468 | 11 |
| 1800-2000 | endgame | 4 | 21016 | 11 |
| 1800-2000 | endgame | 5 | 4867 | 10 |
| 1800-2000 | endgame | 6+ | 1520 | 10 |
| 1800-2000 | forcing | 3 | 54198 | 11 |
| 1800-2000 | forcing | 4 | 12291 | 11 |
| 1800-2000 | forcing | 5 | 1976 | 10 |
| 1800-2000 | forcing | 6+ | 331 | 10 |
| 1800-2000 | mate | 3 | 16741 | 11 |
| 1800-2000 | mate | 4 | 4709 | 11 |
| 1800-2000 | mate | 5 | 777 | 10 |
| 1800-2000 | mate | 6+ | 211 | 10 |
| 1800-2000 | other | 3 | 51475 | 0 |
| 1800-2000 | other | 4 | 6716 | 0 |
| 1800-2000 | other | 5 | 784 | 0 |
| 1800-2000 | other | 6+ | 118 | 0 |
| 1800-2000 | quiet | 3 | 13806 | 11 |
| 1800-2000 | quiet | 4 | 5153 | 11 |
| 1800-2000 | quiet | 5 | 1853 | 10 |
| 1800-2000 | quiet | 6+ | 1240 | 10 |
| 2000-2200 | endgame | 3 | 75496 | 11 |
| 2000-2200 | endgame | 4 | 23222 | 11 |
| 2000-2200 | endgame | 5 | 6383 | 10 |
| 2000-2200 | endgame | 6+ | 2334 | 10 |
| 2000-2200 | forcing | 3 | 45457 | 11 |
| 2000-2200 | forcing | 4 | 14635 | 11 |
| 2000-2200 | forcing | 5 | 3258 | 10 |
| 2000-2200 | forcing | 6+ | 843 | 10 |
| 2000-2200 | mate | 3 | 9890 | 11 |
| 2000-2200 | mate | 4 | 3741 | 11 |
| 2000-2200 | mate | 5 | 827 | 10 |
| 2000-2200 | mate | 6+ | 238 | 10 |
| 2000-2200 | other | 3 | 46384 | 0 |
| 2000-2200 | other | 4 | 8217 | 0 |
| 2000-2200 | other | 5 | 1249 | 0 |
| 2000-2200 | other | 6+ | 210 | 0 |
| 2000-2200 | quiet | 3 | 16563 | 11 |
| 2000-2200 | quiet | 4 | 7082 | 11 |
| 2000-2200 | quiet | 5 | 2787 | 10 |
| 2000-2200 | quiet | 6+ | 1920 | 10 |
| 2200+ | endgame | 3 | 112101 | 11 |
| 2200+ | endgame | 4 | 48836 | 11 |
| 2200+ | endgame | 5 | 19010 | 10 |
| 2200+ | endgame | 6+ | 11295 | 10 |
| 2200+ | forcing | 3 | 59649 | 11 |
| 2200+ | forcing | 4 | 31187 | 11 |
| 2200+ | forcing | 5 | 12144 | 10 |
| 2200+ | forcing | 6+ | 6102 | 10 |
| 2200+ | mate | 3 | 5220 | 11 |
| 2200+ | mate | 4 | 3427 | 11 |
| 2200+ | mate | 5 | 1469 | 10 |
| 2200+ | mate | 6+ | 800 | 10 |
| 2200+ | other | 3 | 70294 | 0 |
| 2200+ | other | 4 | 20152 | 0 |
| 2200+ | other | 5 | 4974 | 0 |
| 2200+ | other | 6+ | 1697 | 0 |
| 2200+ | quiet | 3 | 42181 | 11 |
| 2200+ | quiet | 4 | 25694 | 11 |
| 2200+ | quiet | 5 | 13029 | 10 |
| 2200+ | quiet | 6+ | 12209 | 10 |

Population-weighted estimate (N>=2): rate over (band x family) cells with >=30 sampled puzzles, weighted by cell population. Cells below 30 sampled are DROPPED from the weighting:
- dropped cells (<30 sampled): none

| band | weighted rate | est. population survivors |
|---|---|---|
| 1200-1400 | 0.647 | 110790 (of 171208) |
| 1400-1600 | 0.649 | 133818 (of 206173) |
| 1600-1800 | 0.627 | 140007 (of 223461) |
| 1800-2000 | 0.686 | 155784 (of 227157) |
| 2000-2200 | 0.680 | 145959 (of 214676) |
| 2200+ | 0.630 | 254799 (of 404353) |

## 4. Threshold sensitivity (recomputed from stored ply data, no engine)

| threshold (cp) | puzzles >=1 (incl/excl trivial) | segments (incl/excl) |
|---|---|---|
| 100 | 798 / 642 | 943 / 713 |
| 150 | 703 / 532 | 837 / 588 |
| 200 | 685 / 510 | 814 / 559 |
| 300 | 641 / 452 | 754 / 490 |

## 5. Mate lines vs non-mate lines
- mate: puzzles=252 with-segment=248 (98.4%) [excl trivial: 141], segments=289 [excl: 141]
- non-mate: puzzles=756 with-segment=455 (60.2%) [excl trivial: 391], segments=548 [excl: 447]
- mate-involved replies satisfying unique-longest-resistance: 216 (substring over stored stage JSONs)
- replies where a line mated FOR the defender: 0

## 6. Move-character shares (stored clean segments, incl. trivial)
- overall solver moves: 2462 (checks=1723, captures=1031, quiet=391, promotions=37)
- shares: checks=0.700 captures=0.419 quiet=0.159 promotions=0.015
- mate: moves=1023 checks=0.936 captures=0.363 quiet=0.048 promotions=0.010
- quiet: moves=358 checks=0.288 captures=0.352 quiet=0.478 promotions=0.020
- endgame: moves=407 checks=0.501 captures=0.359 quiet=0.268 promotions=0.047
- forcing: moves=674 checks=0.680 captures=0.576 quiet=0.092 promotions=0.001

## 7. Margin distribution (clean non-mate replies, cp)
- n=420 p25=239 median=325 p75=442 min=150 max=1557

## 8. Solver-move check (seeded 5% subsample of puzzles)
- puzzles checked: 45/1008, solver plies: 222, stored move not best (excl alt_mate): 0 (0.0%), alt_mate acceptable: 0

## 9. Throughput
- cores (os.cpu_count): 2 | engine workers: 2 (1 thread each, Hash=16MB)
- phase wall time: sampling=368.9s engine=1508.1s convergence=365.5s db_writes=12.6s (writes serialized)
- stage1: n=2720 wall-derived nps=194381 (=sum(nodes)/sum(wall); engine-reported mean=297202, short searches inflate the reported figure)
- stage2: n=1178 wall-derived nps=233221 (=sum(nodes)/sum(wall); engine-reported mean=324258, short searches inflate the reported figure)
- solver: n=222 wall-derived nps=187266 (=sum(nodes)/sum(wall); engine-reported mean=271090, short searches inflate the reported figure)
- conv: n=100 wall-derived nps=241006 (=sum(nodes)/sum(wall); engine-reported mean=332190, short searches inflate the reported figure)
- rolling WALL-derived nps per 100 calls (43 windows, completion order): 283491, 272609, 323685, 284481, 223634, 173960, 259793, 245042, 257482, 230311, 189618, 192590, 247338, 262419, 261767, 277668, 242281, 180449, 186537, 258927, 228590, 223091, 248862, 237631, 198953, 169658, 213958, 243729, 282914, 220709, 265460, 215300, 172182, 211065, 309448, 232762, 265919, 227661, 309022, 174684, 164449, 238582, 244039
- drift last-vs-first: -13.9%; max-min spread/mean: 0.674
- wall time: 2242.6s for 1008 puzzles (2 workers)
- engine calls: 4120 total, 4.1/puzzle
- mean ms/call: stage1=247 (n=2720), stage2=1921 (n=1178), solver=267 (n=222)
- puzzles/sec: 0.45
- projected 1,000 puzzles: 37.1 min
- projected 100,000 puzzles: 61.8 h (same settings/workers)

## 10. Final defender-reply failure reasons
- stored != engine best (disagree): 765
- margin below threshold: 1031
- mate against defender but not unique longest resistance: 67

## 11. Convergence (Stage-2-clean plies rerun at 2M nodes, MultiPV=4)
- rerun: 100 plies; verdicts: clean=98, fail=2
- flipped to fail at 2M nodes: 2/100 (2.0%)
