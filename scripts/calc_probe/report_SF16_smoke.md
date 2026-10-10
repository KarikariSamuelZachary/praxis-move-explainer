# Blind Calculation survivor probe — probe-20261008-172118

Engine: Stockfish 16 | seed=20261008 | per-cell=2 | workers=4 | nodes=50000/500000 | threshold=150cp | verify_survivors=True | check_solver=True

## 1. Funnel
- sampled puzzles: 48
- defender replies analysed: 120
- single-legal replies (auto-clean, no engine call): 23 (19.2%)
- Stage 1 verdicts (engine plies only): pass=31 fail=66
- escalated to Stage 2: 97
- Stage 1 -> Stage 2 flips: 5/97 (5.2%)
- puzzles with >=1 clean segment: 34/48
- total clean segments: 35

## 2. Segment-length distribution
- length 2: 19
- length 3: 12
- length 4: 4
- length 5: 0
- length 6+: 0

## 3. Band x N matrix (distinct puzzles with a clean segment of >=N)
Each cell: distinct puzzles with segment >=N (sampled in band).

| band | N>=2 | N>=3 | N>=4 | N>=5 | sampled |
|---|---|---|---|---|---|
| 1200-1400 | 6 | 5 | 2 | 0 | 8 |
| 1400-1600 | 4 | 1 | 0 | 0 | 8 |
| 1600-1800 | 7 | 5 | 1 | 0 | 8 |
| 1800-2000 | 5 | 2 | 1 | 0 | 8 |
| 2000-2200 | 6 | 3 | 0 | 0 | 8 |
| 2200+ | 6 | 0 | 0 | 0 | 8 |

Derived-exercise count (NOT distinct puzzles): sum over stored segments of (L-N+1) for L>=N:

| N | derived exercises |
|---|---|
| >=2 | 55 |
| >=3 | 20 |
| >=4 | 4 |
| >=5 | 0 |

Stratum populations (band x family x depth):

| band | family | depth | population | sampled |
|---|---|---|---|---|
| 1200-1400 | endgame | 3 | 79184 | 1 |
| 1200-1400 | endgame | 4 | 11017 | 1 |
| 1200-1400 | endgame | 5 | 1650 | 0 |
| 1200-1400 | endgame | 6+ | 310 | 0 |
| 1200-1400 | forcing | 3 | 32266 | 1 |
| 1200-1400 | forcing | 4 | 2859 | 1 |
| 1200-1400 | forcing | 5 | 227 | 0 |
| 1200-1400 | forcing | 6+ | 21 | 0 |
| 1200-1400 | mate | 3 | 32484 | 1 |
| 1200-1400 | mate | 4 | 3510 | 1 |
| 1200-1400 | mate | 5 | 305 | 0 |
| 1200-1400 | mate | 6+ | 28 | 0 |
| 1200-1400 | other | 3 | 24683 | 0 |
| 1200-1400 | other | 4 | 1211 | 0 |
| 1200-1400 | other | 5 | 68 | 0 |
| 1200-1400 | other | 6+ | 3 | 0 |
| 1200-1400 | quiet | 3 | 4083 | 1 |
| 1200-1400 | quiet | 4 | 1760 | 1 |
| 1200-1400 | quiet | 5 | 884 | 0 |
| 1200-1400 | quiet | 6+ | 620 | 0 |
| 1400-1600 | endgame | 3 | 88797 | 1 |
| 1400-1600 | endgame | 4 | 14469 | 1 |
| 1400-1600 | endgame | 5 | 2605 | 0 |
| 1400-1600 | endgame | 6+ | 568 | 0 |
| 1400-1600 | forcing | 3 | 48510 | 1 |
| 1400-1600 | forcing | 4 | 5521 | 1 |
| 1400-1600 | forcing | 5 | 546 | 0 |
| 1400-1600 | forcing | 6+ | 68 | 0 |
| 1400-1600 | mate | 3 | 28241 | 1 |
| 1400-1600 | mate | 4 | 4887 | 1 |
| 1400-1600 | mate | 5 | 392 | 0 |
| 1400-1600 | mate | 6+ | 55 | 0 |
| 1400-1600 | other | 3 | 37974 | 0 |
| 1400-1600 | other | 4 | 2571 | 0 |
| 1400-1600 | other | 5 | 171 | 0 |
| 1400-1600 | other | 6+ | 17 | 0 |
| 1400-1600 | quiet | 3 | 7256 | 1 |
| 1400-1600 | quiet | 4 | 2542 | 1 |
| 1400-1600 | quiet | 5 | 1067 | 0 |
| 1400-1600 | quiet | 6+ | 649 | 0 |
| 1600-1800 | endgame | 3 | 89854 | 1 |
| 1600-1800 | endgame | 4 | 17350 | 1 |
| 1600-1800 | endgame | 5 | 3360 | 0 |
| 1600-1800 | endgame | 6+ | 869 | 0 |
| 1600-1800 | forcing | 3 | 56456 | 1 |
| 1600-1800 | forcing | 4 | 8774 | 1 |
| 1600-1800 | forcing | 5 | 1141 | 0 |
| 1600-1800 | forcing | 6+ | 181 | 0 |
| 1600-1800 | mate | 3 | 23021 | 1 |
| 1600-1800 | mate | 4 | 4944 | 1 |
| 1600-1800 | mate | 5 | 573 | 0 |
| 1600-1800 | mate | 6+ | 99 | 0 |
| 1600-1800 | other | 3 | 49054 | 0 |
| 1600-1800 | other | 4 | 4800 | 0 |
| 1600-1800 | other | 5 | 429 | 0 |
| 1600-1800 | other | 6+ | 48 | 0 |
| 1600-1800 | quiet | 3 | 10860 | 1 |
| 1600-1800 | quiet | 4 | 3664 | 1 |
| 1600-1800 | quiet | 5 | 1421 | 0 |
| 1600-1800 | quiet | 6+ | 894 | 0 |
| 1800-2000 | endgame | 3 | 86468 | 1 |
| 1800-2000 | endgame | 4 | 21016 | 1 |
| 1800-2000 | endgame | 5 | 4867 | 0 |
| 1800-2000 | endgame | 6+ | 1520 | 0 |
| 1800-2000 | forcing | 3 | 54198 | 1 |
| 1800-2000 | forcing | 4 | 12291 | 1 |
| 1800-2000 | forcing | 5 | 1976 | 0 |
| 1800-2000 | forcing | 6+ | 331 | 0 |
| 1800-2000 | mate | 3 | 16741 | 1 |
| 1800-2000 | mate | 4 | 4709 | 1 |
| 1800-2000 | mate | 5 | 777 | 0 |
| 1800-2000 | mate | 6+ | 211 | 0 |
| 1800-2000 | other | 3 | 51475 | 0 |
| 1800-2000 | other | 4 | 6716 | 0 |
| 1800-2000 | other | 5 | 784 | 0 |
| 1800-2000 | other | 6+ | 118 | 0 |
| 1800-2000 | quiet | 3 | 13806 | 1 |
| 1800-2000 | quiet | 4 | 5153 | 1 |
| 1800-2000 | quiet | 5 | 1853 | 0 |
| 1800-2000 | quiet | 6+ | 1240 | 0 |
| 2000-2200 | endgame | 3 | 75496 | 1 |
| 2000-2200 | endgame | 4 | 23222 | 1 |
| 2000-2200 | endgame | 5 | 6383 | 0 |
| 2000-2200 | endgame | 6+ | 2334 | 0 |
| 2000-2200 | forcing | 3 | 45457 | 1 |
| 2000-2200 | forcing | 4 | 14635 | 1 |
| 2000-2200 | forcing | 5 | 3258 | 0 |
| 2000-2200 | forcing | 6+ | 843 | 0 |
| 2000-2200 | mate | 3 | 9890 | 1 |
| 2000-2200 | mate | 4 | 3741 | 1 |
| 2000-2200 | mate | 5 | 827 | 0 |
| 2000-2200 | mate | 6+ | 238 | 0 |
| 2000-2200 | other | 3 | 46384 | 0 |
| 2000-2200 | other | 4 | 8217 | 0 |
| 2000-2200 | other | 5 | 1249 | 0 |
| 2000-2200 | other | 6+ | 210 | 0 |
| 2000-2200 | quiet | 3 | 16563 | 1 |
| 2000-2200 | quiet | 4 | 7082 | 1 |
| 2000-2200 | quiet | 5 | 2787 | 0 |
| 2000-2200 | quiet | 6+ | 1920 | 0 |
| 2200+ | endgame | 3 | 112101 | 1 |
| 2200+ | endgame | 4 | 48836 | 1 |
| 2200+ | endgame | 5 | 19010 | 0 |
| 2200+ | endgame | 6+ | 11295 | 0 |
| 2200+ | forcing | 3 | 59649 | 1 |
| 2200+ | forcing | 4 | 31187 | 1 |
| 2200+ | forcing | 5 | 12144 | 0 |
| 2200+ | forcing | 6+ | 6102 | 0 |
| 2200+ | mate | 3 | 5220 | 1 |
| 2200+ | mate | 4 | 3427 | 1 |
| 2200+ | mate | 5 | 1469 | 0 |
| 2200+ | mate | 6+ | 800 | 0 |
| 2200+ | other | 3 | 70294 | 0 |
| 2200+ | other | 4 | 20152 | 0 |
| 2200+ | other | 5 | 4974 | 0 |
| 2200+ | other | 6+ | 1697 | 0 |
| 2200+ | quiet | 3 | 42181 | 1 |
| 2200+ | quiet | 4 | 25694 | 1 |
| 2200+ | quiet | 5 | 13029 | 0 |
| 2200+ | quiet | 6+ | 12209 | 0 |

Population-weighted estimate per band: rate_cell = survivors_cell / sampled_cell over (band x family) cells, weighted by cell population. N>=2 survivors:

| band | weighted rate | est. population survivors |
|---|---|---|
| 1200-1400 | 0.793 | 135835 (of 171208) |
| 1400-1600 | 0.554 | 114117 (of 206173) |
| 1600-1800 | 0.751 | 167744 (of 223461) |
| 1800-2000 | 0.651 | 147976 (of 227157) |
| 2000-2200 | 0.684 | 146782 (of 214676) |
| 2200+ | 0.648 | 262176 (of 404353) |

## 4. Threshold sensitivity (recomputed from stored ply data, no engine)

| threshold (cp) | puzzles >=1 segment | segments |
|---|---|---|
| 100 | 35 | 37 |
| 150 | 34 | 35 |
| 200 | 30 | 30 |
| 300 | 27 | 27 |

## 5. Mate lines vs non-mate lines
- mate: puzzles=12 with-segment=11 (91.7%), segments=11
- non-mate: puzzles=36 with-segment=23 (63.9%), segments=24
- mate-involved replies satisfying unique-longest-resistance: 4 (substring over stored stage JSONs)
- replies where a line mated FOR the defender: 0

## 6. Move-character shares (stored clean segments)
- overall solver moves: 90 (checks=55, captures=40, quiet=16, promotions=0)
- shares: checks=0.611 captures=0.444 quiet=0.178 promotions=0.000
- mate: moves=32 checks=1.000 captures=0.344 quiet=0.000 promotions=0.000
- quiet: moves=18 checks=0.222 captures=0.389 quiet=0.556 promotions=0.000
- endgame: moves=17 checks=0.471 captures=0.471 quiet=0.235 promotions=0.000
- forcing: moves=23 checks=0.478 captures=0.609 quiet=0.087 promotions=0.000

## 7. Margin distribution (clean non-mate replies, cp)
- n=20 p25=186 median=240 p75=368 min=158 max=674

## 8. Solver-move check
- solver plies checked: 168, stored move not best (excl alt_mate): 0 (0.0%), alt_mate acceptable: 1

## 9. Throughput
- wall time: 73.4s for 48 puzzles (4 workers)
- engine calls: 362 total, 7.5/puzzle
- mean ms/call: stage1=0 (n=0), stage2=0 (n=0), solver=0 (n=0)
- puzzles/sec: 0.65
- projected 1,000 puzzles: 25.5 min
- projected 100,000 puzzles: 42.5 h (same settings/workers)
