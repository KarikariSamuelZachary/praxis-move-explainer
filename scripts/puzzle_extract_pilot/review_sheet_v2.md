# Puzzle-extract pilot -- hand-review sheet (rebuilt read-only)

Source DB settings: `{"screen_nodes": 150000, "stage2_nodes": 500000, "multipv_screen": 2, "multipv_stage2": 4, "threads": 1, "hash_mb": 16, "cand_ep": 0.15, "cand_cp": 100, "margin_M": 0.1, "per_game_cap": 3, "min_move": 8, "engine_version": "Stockfish 19", "stockfish_path": "/home/iaminspiredbro/.local/opt/stockfish/stockfish", "engine_sha256": "0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19", "all`

Kept: 24; near-miss rejects [0.05,0.10): 7; other rejects sampled: 3; clock-excluded screen-passing: 8; book_fallback-excluded screen-passing: 5.

---

## Kept puzzles (24)

### P1: played Qf5, best Qf4 (game: https://lichess.org/8cbJ5DRG, move 32, ply 62)
- fen: `5qk1/p4ppp/1p2p3/3rQ1P1/1P1B4/P7/7P/5RK1 w - - 6 32`
- analysis: https://lichess.org/analysis/5qk1/p4ppp/1p2p3/3rQ1P1/1P1B4/P7/7P/5RK1_w_-_-_6_32
- game URL: https://lichess.org/8cbJ5DRG ply 62
- screen ep_loss/cp_loss: 0.8548 / 1289; verified: 0.8878 / 1416
- margin: 0.1178; best character: quiet; pv material Δ (best/played): +0 / -900
- my rating: 1711; reject: kept

### P2: played Ne5, best Nxh2 (game: https://lichess.org/LgKbO9T3, move 14, ply 26)
- fen: `rn2k2r/ppq2pp1/2pbp1p1/6Q1/3P4/2N2NB1/PPP2PPn/R4RK1 w kq - 0 14`
- analysis: https://lichess.org/analysis/rn2k2r/ppq2pp1/2pbp1p1/6Q1/3P4/2N2NB1/PPP2PPn/R4RK1_w_kq_-_0_14
- game URL: https://lichess.org/LgKbO9T3 ply 26
- screen ep_loss/cp_loss: 0.8123 / 973; verified: 0.8207 / 996
- margin: 0.6216; best character: capture; pv material Δ (best/played): -10 / -490
- my rating: 1697; reject: kept

### P3: played Nf6, best Nc5 (game: https://www.chess.com/game/live/184551359070, move 17, ply 33)
- fen: `2r2rk1/1p1n1ppp/1q2p3/pP3b2/P1Pp4/1Q2bN2/1B1NB1PP/3R1R1K b - - 1 17`
- analysis: https://lichess.org/analysis/2r2rk1/1p1n1ppp/1q2p3/pP3b2/P1Pp4/1Q2bN2/1B1NB1PP/3R1R1K_b_-_-_1_17
- game URL: https://www.chess.com/game/live/184551359070 ply 33
- screen ep_loss/cp_loss: 0.7134 / 786; verified: 0.7567 / 872
- margin: 0.4884; best character: quiet; pv material Δ (best/played): +0 / +0
- my rating: 1532; reject: kept

### P4: played g6, best Rc8 (game: https://www.chess.com/game/live/173906785604, move 9, ply 17)
- fen: `1r2kb1r/p1Q1nppp/2n1b3/3qp3/8/5NP1/PP1PPPBP/RNB1K2R b KQk - 0 9`
- analysis: https://lichess.org/analysis/1r2kb1r/p1Q1nppp/2n1b3/3qp3/8/5NP1/PP1PPPBP/RNB1K2R_b_KQk_-_0_9
- game URL: https://www.chess.com/game/live/173906785604 ply 17
- screen ep_loss/cp_loss: 0.7435 / 850; verified: 0.7527 / 885
- margin: 0.3785; best character: quiet; pv material Δ (best/played): +320 / +0
- my rating: 1524; reject: kept

### P5: played Qc7, best Qd4+ (game: https://www.chess.com/game/live/184575572144, move 22, ply 43)
- fen: `r3r3/p2q2pk/1pp1p2p/2p1N3/P3P2Q/3P3P/1PP3P1/R4RK1 b - - 1 22`
- analysis: https://lichess.org/analysis/r3r3/p2q2pk/1pp1p2p/2p1N3/P3P2Q/3P3P/1PP3P1/R4RK1_b_-_-_1_22
- game URL: https://www.chess.com/game/live/184575572144 ply 43
- screen ep_loss/cp_loss: 0.4419 / 685; verified: 0.4548 / 765
- margin: 0.4464; best character: check; pv material Δ (best/played): +420 / +0
- my rating: 1523; reject: kept

### P6: played Qf7, best Nb5+ (game: https://lichess.org/8ZwF2nvP, move 12, ply 22)
- fen: `r1bq1b1r/ppp3pp/3k4/3Bp3/8/P1N2Q2/1P1P1PPP/n1BK3R w - - 1 12`
- analysis: https://lichess.org/analysis/r1bq1b1r/ppp3pp/3k4/3Bp3/8/P1N2Q2/1P1P1PPP/n1BK3R_w_-_-_1_12
- game URL: https://lichess.org/8ZwF2nvP ply 22
- screen ep_loss/cp_loss: 0.2847 / 401; verified: 0.3981 / 503
- margin: 0.2612; best character: check; pv material Δ (best/played): +100 / +0
- my rating: 1289; reject: kept

### P7: played Rf3, best Rd8+ (game: https://www.chess.com/game/live/173755196038, move 43, ply 85)
- fen: `1r4k1/6p1/Rp5p/1RpK1P2/2P2P2/2r5/8/8 b - - 5 43`
- analysis: https://lichess.org/analysis/1r4k1/6p1/Rp5p/1RpK1P2/2P2P2/2r5/8/8_b_-_-_5_43
- game URL: https://www.chess.com/game/live/173755196038 ply 85
- screen ep_loss/cp_loss: 0.3881 / 454; verified: 0.3830 / 443
- margin: 0.2012; best character: check; pv material Δ (best/played): +200 / +0
- my rating: 1540; reject: kept

### P8: played Ng5, best Nd4 (game: https://lichess.org/CotneR1y, move 17, ply 32)
- fen: `r4rk1/pp2nppp/2nR4/q7/2B3b1/BQp2N2/P4PPP/4R1K1 w - - 3 17`
- analysis: https://lichess.org/analysis/r4rk1/pp2nppp/2nR4/q7/2B3b1/BQp2N2/P4PPP/4R1K1_w_-_-_3_17
- game URL: https://lichess.org/CotneR1y ply 32
- screen ep_loss/cp_loss: 0.3813 / 458; verified: 0.3712 / 477
- margin: 0.1113; best character: quiet; pv material Δ (best/played): -220 / -220
- my rating: 1724; reject: kept

### P9: played g4, best Be3 (game: https://lichess.org/8cbJ5DRG, move 15, ply 28)
- fen: `r2q1rk1/ppb2ppp/1np1p3/4Pn2/1P1P4/P1N5/4BPPP/R1BQ1RK1 w - - 0 15`
- analysis: https://lichess.org/analysis/r2q1rk1/ppb2ppp/1np1p3/4Pn2/1P1P4/P1N5/4BPPP/R1BQ1RK1_w_-_-_0_15
- game URL: https://lichess.org/8cbJ5DRG ply 28
- screen ep_loss/cp_loss: 0.2952 / 301; verified: 0.3186 / 324
- margin: 0.2127; best character: quiet; pv material Δ (best/played): +0 / -110
- my rating: 1711; reject: kept

### P10: played Bg5, best Bb5 (game: https://lichess.org/DAsZjWUo, move 11, ply 20)
- fen: `r2qkb1r/5ppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RNBQ1RK1 w kq - 0 11`
- analysis: https://lichess.org/analysis/r2qkb1r/5ppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RNBQ1RK1_w_kq_-_0_11
- game URL: https://lichess.org/DAsZjWUo ply 20
- screen ep_loss/cp_loss: 0.2751 / 524; verified: 0.3157 / 556
- margin: 0.1552; best character: quiet; pv material Δ (best/played): -100 / +0
- my rating: 1712; reject: kept

### P11: played Bxf8, best Rxc8 (game: https://lichess.org/WtARkVtS, move 21, ply 40)
- fen: `r1r2bk1/1p3p1p/pq4pB/3pPp2/3n4/1N3PR1/PP1Q1P1P/2R4K w - - 0 21`
- analysis: https://lichess.org/analysis/r1r2bk1/1p3p1p/pq4pB/3pPp2/3n4/1N3PR1/PP1Q1P1P/2R4K_w_-_-_0_21
- game URL: https://lichess.org/WtARkVtS ply 40
- screen ep_loss/cp_loss: 0.2816 / 245; verified: 0.3142 / 278
- margin: 0.2857; best character: capture; pv material Δ (best/played): +0 / +0
- my rating: 1681; reject: kept

### P12: played Rxd5, best exd5 (game: https://lichess.org/rslweHTa, move 16, ply 30)
- fen: `r4rk1/4ppbp/p2pb1p1/1p1n4/4P3/4BP2/PPPRB1PP/1K5R w - - 0 16`
- analysis: https://lichess.org/analysis/r4rk1/4ppbp/p2pb1p1/1p1n4/4P3/4BP2/PPPRB1PP/1K5R_w_-_-_0_16
- game URL: https://lichess.org/rslweHTa ply 30
- screen ep_loss/cp_loss: 0.2501 / 260; verified: 0.2543 / 267
- margin: 0.2703; best character: capture; pv material Δ (best/played): +220 / +150
- my rating: 1310; reject: kept

### P13: played Nf2+, best Nc5 (game: https://www.chess.com/game/live/184551359070, move 20, ply 39)
- fen: `2rr2k1/1p3ppp/1q2p3/pP3b2/P1Ppn3/NQ1BbN2/1B4PP/3R1R1K b - - 7 20`
- analysis: https://lichess.org/analysis/2rr2k1/1p3ppp/1q2p3/pP3b2/P1Ppn3/NQ1BbN2/1B4PP/3R1R1K_b_-_-_7_20
- game URL: https://www.chess.com/game/live/184551359070 ply 39
- screen ep_loss/cp_loss: 0.2820 / 269; verified: 0.2492 / 238
- margin: 0.2151; best character: quiet; pv material Δ (best/played): +10 / +180
- my rating: 1532; reject: kept

### P14: played Ne1, best Be3 (game: https://www.chess.com/game/live/173754832234, move 12, ply 22)
- fen: `2kr1b1r/pbp2pp1/2n2n1p/1Bq5/4p3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 12`
- analysis: https://lichess.org/analysis/2kr1b1r/pbp2pp1/2n2n1p/1Bq5/4p3/2NP1N2/PPP2PPP/R1BQ1RK1_w_-_-_0_12
- game URL: https://www.chess.com/game/live/173754832234 ply 22
- screen ep_loss/cp_loss: 0.3117 / 292; verified: 0.2392 / 222
- margin: 0.1213; best character: quiet; pv material Δ (best/played): +0 / +0
- my rating: 1548; reject: kept

### P15: played Qc4, best Qa5 (game: https://www.chess.com/game/live/173906785604, move 15, ply 29)
- fen: `2r2rk1/pQ3pbp/2n1b1p1/2q5/4N3/6P1/PP1PPPBP/R1B2RK1 b - - 3 15`
- analysis: https://lichess.org/analysis/2r2rk1/pQ3pbp/2n1b1p1/2q5/4N3/6P1/PP1PPPBP/R1B2RK1_b_-_-_3_15
- game URL: https://www.chess.com/game/live/173906785604 ply 29
- screen ep_loss/cp_loss: 0.1916 / 306; verified: 0.2336 / 340
- margin: 0.1151; best character: quiet; pv material Δ (best/played): +0 / -80
- my rating: 1524; reject: kept

### P16: played Bf5, best Bxg2 (game: https://www.chess.com/game/live/173438456408, move 21, ply 41)
- fen: `Q7/2p2kpp/1pP2n1r/1p5q/4N3/2P3Bb/PP3PP1/R3R1K1 b - - 0 21`
- analysis: https://lichess.org/analysis/Q7/2p2kpp/1pP2n1r/1p5q/4N3/2P3Bb/PP3PP1/R3R1K1_b_-_-_0_21
- game URL: https://www.chess.com/game/live/173438456408 ply 41
- screen ep_loss/cp_loss: 0.2610 / 478; verified: 0.2199 / 481
- margin: 0.1826; best character: capture; pv material Δ (best/played): +90 / +220
- my rating: 1555; reject: kept

### P17: played Rxd2, best Nxe7+ (game: https://lichess.org/rslweHTa, move 15, ply 28)
- fen: `r4rk1/4ppbp/p2pbnp1/1p1N4/4P3/4BP2/PPPqB1PP/1K1R3R w - - 0 15`
- analysis: https://lichess.org/analysis/r4rk1/4ppbp/p2pbnp1/1p1N4/4P3/4BP2/PPPqB1PP/1K1R3R_w_-_-_0_15
- game URL: https://lichess.org/rslweHTa ply 28
- screen ep_loss/cp_loss: 0.2010 / 204; verified: 0.2041 / 207
- margin: 0.1915; best character: capture+check; pv material Δ (best/played): +1000 / +900
- my rating: 1310; reject: kept

### P18: played Qc2, best Bxb5 (game: https://lichess.org/8cbJ5DRG, move 21, ply 40)
- fen: `r2qr1k1/ppb2ppp/1n2p3/1pB5/1P3PP1/P2B4/7P/R2Q1RK1 w - - 2 21`
- analysis: https://lichess.org/analysis/r2qr1k1/ppb2ppp/1n2p3/1pB5/1P3PP1/P2B4/7P/R2Q1RK1_w_-_-_2_21
- game URL: https://lichess.org/8cbJ5DRG ply 40
- screen ep_loss/cp_loss: 0.2111 / 188; verified: 0.2026 / 184
- margin: 0.1763; best character: capture; pv material Δ (best/played): +100 / +100
- my rating: 1711; reject: kept

### P19: played a6, best e5 (game: https://www.chess.com/game/live/174103519292, move 14, ply 27)
- fen: `r3k2r/pp1n1ppp/4p3/3p4/Q2P4/3K4/Pq2PPPP/3R1B1R b kq - 1 14`
- analysis: https://lichess.org/analysis/r3k2r/pp1n1ppp/4p3/3p4/Q2P4/3K4/Pq2PPPP/3R1B1R_b_kq_-_1_14
- game URL: https://www.chess.com/game/live/174103519292 ply 27
- screen ep_loss/cp_loss: 0.1796 / 275; verified: 0.1933 / 296
- margin: 0.1215; best character: quiet; pv material Δ (best/played): -100 / +0
- my rating: 1525; reject: kept

### P20: played Qxe4, best Nxe4 (game: https://lichess.org/nuFq8tmk, move 15, ply 28)
- fen: `2kr3r/p1pb1ppp/2pb1q2/8/3Pp3/2P1Q1N1/PP3PPP/R1B2RK1 w - - 4 15`
- analysis: https://lichess.org/analysis/2kr3r/p1pb1ppp/2pb1q2/8/3Pp3/2P1Q1N1/PP3PPP/R1B2RK1_w_-_-_4_15
- game URL: https://lichess.org/nuFq8tmk ply 28
- screen ep_loss/cp_loss: 0.1836 / 184; verified: 0.1771 / 179
- margin: 0.1038; best character: capture; pv material Δ (best/played): +110 / +100
- my rating: 1369; reject: kept

### P21: played Rh8, best Qf7 (game: https://lichess.org/xKJgVItt, move 20, ply 39)
- fen: `r7/pppq2k1/3p1p2/3Np1pR/2PbP3/P4PP1/1PPQ2P1/2K5 b - - 2 20`
- analysis: https://lichess.org/analysis/r7/pppq2k1/3p1p2/3Np1pR/2PbP3/P4PP1/1PPQ2P1/2K5_b_-_-_2_20
- game URL: https://lichess.org/xKJgVItt ply 39
- screen ep_loss/cp_loss: 0.2000 / 342; verified: 0.1771 / 277
- margin: 0.1313; best character: quiet; pv material Δ (best/played): +0 / -100
- my rating: 1283; reject: kept

### P22: played O-O, best e4 (game: https://www.chess.com/game/live/173438456408, move 9, ply 17)
- fen: `Q1bqk2r/2p2ppp/1pPb1n2/1p2p3/8/2P2N2/PP1P1PPP/RNB2RK1 b k - 1 9`
- analysis: https://lichess.org/analysis/Q1bqk2r/2p2ppp/1pPb1n2/1p2p3/8/2P2N2/PP1P1PPP/RNB2RK1_b_k_-_1_9
- game URL: https://www.chess.com/game/live/173438456408 ply 17
- screen ep_loss/cp_loss: 0.1703 / 277; verified: 0.1564 / 249
- margin: 0.1657; best character: quiet; pv material Δ (best/played): +320 / +0
- my rating: 1555; reject: kept

### P23: played Nd4, best Bb5 (game: https://lichess.org/DAsZjWUo, move 13, ply 24)
- fen: `r3k2r/4qppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RN1Q1RK1 w kq - 0 13`
- analysis: https://lichess.org/analysis/r3k2r/4qppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RN1Q1RK1_w_kq_-_0_13
- game URL: https://lichess.org/DAsZjWUo ply 24
- screen ep_loss/cp_loss: 0.1551 / 155; verified: 0.1450 / 145
- margin: 0.1655; best character: quiet; pv material Δ (best/played): +0 / +0
- my rating: 1712; reject: kept

### P24: played Nxc3, best Nxc3 (game: https://lichess.org/3ecTOztQ, move 22, ply 43)
- fen: `1r3rk1/2pbqpbp/p5p1/2Pp4/2nPn2P/P1N1P1P1/2B3KN/1RB1QR2 b - - 5 22`
- analysis: https://lichess.org/analysis/1r3rk1/2pbqpbp/p5p1/2Pp4/2nPn2P/P1N1P1P1/2B3KN/1RB1QR2_b_-_-_5_22
- game URL: https://lichess.org/3ecTOztQ ply 43
- screen ep_loss/cp_loss: 0.2190 / 276; verified: 0.0000 / 0
- margin: 0.1083; best character: capture; pv material Δ (best/played): +0 / -400
- my rating: 1634; reject: kept

---

## Near-miss rejects, margin in [0.05, 0.10) (7)

### N1: played Bg7, best Qxe5 (game: https://www.chess.com/game/live/173906785604, move 12, ply 23)
- fen: `2r1kb1r/pQ2np1p/2n1b1p1/2q1N3/8/2N3P1/PP1PPPBP/R1B1K2R b KQk - 2 12`
- analysis: https://lichess.org/analysis/2r1kb1r/pQ2np1p/2n1b1p1/2q1N3/8/2N3P1/PP1PPPBP/R1B1K2R_b_KQk_-_2_12
- game URL: https://www.chess.com/game/live/173906785604 ply 23
- screen ep_loss/cp_loss: 0.5053 / 531; verified: 0.5115 / 555
- margin: 0.0966; best character: capture; pv material Δ (best/played): +420 / +0
- my rating: 1524; reject: margin_lt_0.10

### N2: played Bf4, best h3 (game: https://lichess.org/nuFq8tmk, move 19, ply 36)
- fen: `2kr3r/p1pb1pq1/2pb2p1/6Q1/3P4/2P3N1/PP3PPP/R1B2RK1 w - - 4 19`
- analysis: https://lichess.org/analysis/2kr3r/p1pb1pq1/2pb2p1/6Q1/3P4/2P3N1/PP3PPP/R1B2RK1_w_-_-_4_19
- game URL: https://lichess.org/nuFq8tmk ply 36
- screen ep_loss/cp_loss: 0.3235 / 319; verified: 0.3803 / 395
- margin: 0.0871; best character: quiet; pv material Δ (best/played): +0 / -330
- my rating: 1369; reject: margin_lt_0.10

### N3: played Nb5, best f3 (game: https://www.chess.com/game/live/173754832234, move 19, ply 36)
- fen: `2kr3r/pbp2pp1/2nb3p/5q2/8/P1NNB3/1PP1QPPP/R4RK1 w - - 4 19`
- analysis: https://lichess.org/analysis/2kr3r/pbp2pp1/2nb3p/5q2/8/P1NNB3/1PP1QPPP/R4RK1_w_-_-_4_19
- game URL: https://www.chess.com/game/live/173754832234 ply 36
- screen ep_loss/cp_loss: 0.5150 / 697; verified: 0.5322 / 714
- margin: 0.0827; best character: quiet; pv material Δ (best/played): +100 / -1220
- my rating: 1548; reject: margin_lt_0.10

### N4: played Bb7, best cxd4 (game: https://lichess.org/QUVZz0Xr, move 14, ply 27)
- fen: `r1b2rk1/p1p1q1bp/4ppp1/2pp4/3P4/4PNQP/PPPN1PP1/R4RK1 b - - 1 14`
- analysis: https://lichess.org/analysis/r1b2rk1/p1p1q1bp/4ppp1/2pp4/3P4/4PNQP/PPPN1PP1/R4RK1_b_-_-_1_14
- game URL: https://lichess.org/QUVZz0Xr ply 27
- screen ep_loss/cp_loss: 0.1654 / 174; verified: 0.1590 / 166
- margin: 0.0801; best character: capture; pv material Δ (best/played): +0 / +0
- my rating: 1249; reject: margin_lt_0.10

### N5: played Qxc1, best Nxc1 (game: https://lichess.org/WtARkVtS, move 22, ply 42)
- fen: `r4Bk1/1p3p1p/pq4p1/3pPp2/3n4/1N3PR1/PP1Q1P1P/2r4K w - - 0 22`
- analysis: https://lichess.org/analysis/r4Bk1/1p3p1p/pq4p1/3pPp2/3n4/1N3PR1/PP1Q1P1P/2r4K_w_-_-_0_22
- game URL: https://lichess.org/WtARkVtS ply 42
- screen ep_loss/cp_loss: 0.1508 / 177; verified: 0.0922 / 129
- margin: 0.0746; best character: capture; pv material Δ (best/played): +170 / -150
- my rating: 1681; reject: margin_lt_0.10

### N6: played Nxd6+, best Ne1 (game: https://www.chess.com/game/live/173754832234, move 20, ply 38)
- fen: `2krr3/pbp2pp1/2nb3p/1N3q2/8/P2NB3/1PP1QPPP/R4RK1 w - - 6 20`
- analysis: https://lichess.org/analysis/2krr3/pbp2pp1/2nb3p/1N3q2/8/P2NB3/1PP1QPPP/R4RK1_w_-_-_6_20
- game URL: https://www.chess.com/game/live/173754832234 ply 38
- screen ep_loss/cp_loss: 0.2561 / 296; verified: 0.2839 / 388
- margin: 0.0600; best character: quiet; pv material Δ (best/played): +0 / +10
- my rating: 1548; reject: margin_lt_0.10

### N7: played c4, best a4 (game: https://www.chess.com/game/live/173755557612, move 15, ply 28)
- fen: `2rq1rk1/4bppp/p1npbn2/1p1Np3/4P3/2P1NP2/PP2B1PP/R1BQ1RK1 w - - 5 15`
- analysis: https://lichess.org/analysis/2rq1rk1/4bppp/p1npbn2/1p1Np3/4P3/2P1NP2/PP2B1PP/R1BQ1RK1_w_-_-_5_15
- game URL: https://www.chess.com/game/live/173755557612 ply 28
- screen ep_loss/cp_loss: 0.2053 / 189; verified: 0.1780 / 161
- margin: 0.0586; best character: quiet; pv material Δ (best/played): +10 / +10
- my rating: 1532; reject: margin_lt_0.10

---

## Other rejects sample (3, seed=20261008)

### R1: played Nc3, best Kb1 (game: https://lichess.org/BFbElKXx, move 12, ply 22)
- fen: `r1q1k1nr/1b1pbppp/2n1p3/1N6/4P3/4BP2/PPPQ2PP/2KR1B1R w kq - 1 12`
- analysis: https://lichess.org/analysis/r1q1k1nr/1b1pbppp/2n1p3/1N6/4P3/4BP2/PPPQ2PP/2KR1B1R_w_kq_-_1_12
- game URL: https://lichess.org/BFbElKXx ply 22
- screen ep_loss/cp_loss: 0.2433 / 287; verified: 0.2718 / 314
- margin: 0.0127; best character: quiet; pv material Δ (best/played): +0 / +0
- my rating: 1711; reject: margin_lt_0.10

### R2: played g5, best Qxc5 (game: https://lichess.org/3ecTOztQ, move 24, ply 47)
- fen: `5rk1/2pbqpbp/p5p1/2Pp4/2nP3P/P1Q1P1P1/6KN/1BB2R2 b - - 0 24`
- analysis: https://lichess.org/analysis/5rk1/2pbqpbp/p5p1/2Pp4/2nP3P/P1Q1P1P1/6KN/1BB2R2_b_-_-_0_24
- game URL: https://lichess.org/3ecTOztQ ply 47
- screen ep_loss/cp_loss: 0.3847 / 391; verified: 0.3687 / 392
- margin: 0.0392; best character: capture; pv material Δ (best/played): +100 / +0
- my rating: 1634; reject: margin_lt_0.10

### R3: played Rxd6, best Rxd6 (game: https://lichess.org/CotneR1y, move 15, ply 28)
- fen: `r1bq1rk1/pp2nppp/2np4/8/Q1B5/B1p2N2/P4PPP/3RR1K1 w - - 4 15`
- analysis: https://lichess.org/analysis/r1bq1rk1/pp2nppp/2np4/8/Q1B5/B1p2N2/P4PPP/3RR1K1_w_-_-_4_15
- game URL: https://lichess.org/CotneR1y ply 28
- screen ep_loss/cp_loss: 0.1717 / 173; verified: 0.0000 / 0
- margin: 0.0264; best character: capture; pv material Δ (best/played): +100 / -230
- my rating: 1724; reject: margin_lt_0.10

---

## Clock-excluded, passed candidate screen (8, no Stage 2)

### C1: played Qc3, screen best Nxe5 (game: https://www.chess.com/game/live/173754832234, move 23, ply 44) [clock]
- fen: `2k1r3/pbp2pp1/2n3rp/4q3/8/P2NBP2/1PPQ2PP/R4RK1 w - - 1 23`
- analysis: https://lichess.org/analysis/2k1r3/pbp2pp1/2n3rp/4q3/8/P2NBP2/1PPQ2PP/R4RK1_w_-_-_1_23
- game URL: https://www.chess.com/game/live/173754832234 ply 44
- screen ep_loss/cp_loss: 0.9061 / 1320; margin: (none, no Stage 2)
- best character (screen): capture; pv material Δ: +700
- my rating: 1548; exclusion: clock; clock: 5.9s / base 180.0s

### C2: played Re8, screen best Qd6+ (game: https://www.chess.com/game/live/184551359070, move 34, ply 67) [clock]
- fen: `3q2k1/5p1p/6p1/pP1N1p2/P1P5/2Q4P/6PK/4r3 b - - 3 34`
- analysis: https://lichess.org/analysis/3q2k1/5p1p/6p1/pP1N1p2/P1P5/2Q4P/6PK/4r3_b_-_-_3_34
- game URL: https://www.chess.com/game/live/184551359070 ply 67
- screen ep_loss/cp_loss: 0.8381 / 1069; margin: (none, no Stage 2)
- best character (screen): check; pv material Δ: +0
- my rating: 1532; exclusion: clock; clock: 7.7s / base 180.0s

### C3: played Nc4, screen best Rc7 (game: https://www.chess.com/game/live/174103519292, move 28, ply 55) [clock]
- fen: `2r3k1/1p4pp/pn1qp3/5p2/3PpP2/1Q4P1/P2R1K1P/5B2 b - - 6 28`
- analysis: https://lichess.org/analysis/2r3k1/1p4pp/pn1qp3/5p2/3PpP2/1Q4P1/P2R1K1P/5B2_b_-_-_6_28
- game URL: https://www.chess.com/game/live/174103519292 ply 55
- screen ep_loss/cp_loss: 0.7676 / 934; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: +0
- my rating: 1525; exclusion: clock; clock: 13.4s / base 180.0s

### C4: played Qd4+, screen best Qd4+ (game: https://www.chess.com/game/live/174103519292, move 44, ply 87) [clock]
- fen: `2B4k/1p3Qpp/p2q4/5p2/5P2/4p1P1/PKR4P/8 b - - 13 44`
- analysis: https://lichess.org/analysis/2B4k/1p3Qpp/p2q4/5p2/5P2/4p1P1/PKR4P/8_b_-_-_13_44
- game URL: https://www.chess.com/game/live/174103519292 ply 87
- screen ep_loss/cp_loss: 0.4749 / 806; margin: (none, no Stage 2)
- best character (screen): check; pv material Δ: +0
- my rating: 1525; exclusion: clock; clock: 12.3s / base 180.0s

### C5: played b6, screen best Rxc4 (game: https://www.chess.com/game/live/184551359070, move 27, ply 53) [clock]
- fen: `2r1r1k1/1p3p1p/3q2p1/pP3p2/P1PB4/5Q2/2N3PP/3R3K b - - 4 27`
- analysis: https://lichess.org/analysis/2r1r1k1/1p3p1p/3q2p1/pP3p2/P1PB4/5Q2/2N3PP/3R3K_b_-_-_4_27
- game URL: https://www.chess.com/game/live/184551359070 ply 53
- screen ep_loss/cp_loss: 0.4103 / 448; margin: (none, no Stage 2)
- best character (screen): capture; pv material Δ: +200
- my rating: 1532; exclusion: clock; clock: 6.0s / base 180.0s

### C6: played Qd8, screen best Qd4 (game: https://www.chess.com/game/live/184551359070, move 33, ply 65) [clock]
- fen: `6k1/5p1p/1q4p1/pP1N1p2/P1P5/5Q1P/6PK/4r3 b - - 1 33`
- analysis: https://lichess.org/analysis/6k1/5p1p/1q4p1/pP1N1p2/P1P5/5Q1P/6PK/4r3_b_-_-_1_33
- game URL: https://www.chess.com/game/live/184551359070 ply 65
- screen ep_loss/cp_loss: 0.3655 / 415; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: +0
- my rating: 1532; exclusion: clock; clock: 7.3s / base 180.0s

### C7: played Bxd4, screen best Bh4 (game: https://www.chess.com/game/live/184551359070, move 24, ply 47) [clock]
- fen: `2rr2k1/1p3p1p/1q4p1/pP3p2/P1PN4/N2Q4/1B3bPP/3R3K b - - 0 24`
- analysis: https://lichess.org/analysis/2rr2k1/1p3p1p/1q4p1/pP3p2/P1PN4/N2Q4/1B3bPP/3R3K_b_-_-_0_24
- game URL: https://www.chess.com/game/live/184551359070 ply 47
- screen ep_loss/cp_loss: 0.2730 / 250; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: +0
- my rating: 1532; exclusion: clock; clock: 13.8s / base 180.0s

### C8: played g6, screen best Qc5 (game: https://www.chess.com/game/live/184551359070, move 23, ply 45) [clock]
- fen: `2rr2k1/1p3ppp/1q6/pP3p2/P1Pp4/N2Q1N2/1B3bPP/3R3K b - - 1 23`
- analysis: https://lichess.org/analysis/2rr2k1/1p3ppp/1q6/pP3p2/P1Pp4/N2Q1N2/1B3bPP/3R3K_b_-_-_1_23
- game URL: https://www.chess.com/game/live/184551359070 ply 45
- screen ep_loss/cp_loss: 0.2333 / 209; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: +0
- my rating: 1532; exclusion: clock; clock: 14.6s / base 180.0s

---

## Book_fallback-excluded, passed candidate screen (5, no Stage 2)

### B1: played Qh4, screen best Nc6 (game: https://www.chess.com/game/live/174130588758, move 2, ply 3) [book_fallback]
- fen: `rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2`
- analysis: https://lichess.org/analysis/rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R_b_KQkq_-_1_2
- game URL: https://www.chess.com/game/live/174130588758 ply 3
- screen ep_loss/cp_loss: 0.4472 / 855; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: +0
- my rating: 1516; exclusion: book_fallback; clock: 181.7s / base 180.0s

### B2: played Nbd2, screen best Qa4+ (game: https://lichess.org/WtARkVtS, move 8, ply 14) [book_fallback]
- fen: `rn2kbnr/pp3ppp/1q2p3/2ppP3/3P2b1/2PB1N2/PP3PPP/RNBQ1RK1 w kq - 0 8`
- analysis: https://lichess.org/analysis/rn2kbnr/pp3ppp/1q2p3/2ppP3/3P2b1/2PB1N2/PP3PPP/RNBQ1RK1_w_kq_-_0_8
- game URL: https://lichess.org/WtARkVtS ply 14
- screen ep_loss/cp_loss: 0.3360 / 691; margin: (none, no Stage 2)
- best character (screen): check; pv material Δ: +330
- my rating: 1681; exclusion: book_fallback; clock: 537.0s / base 600.0s

### B3: played Nf6, screen best Nce7 (game: https://www.chess.com/game/live/173438456408, move 7, ply 13) [book_fallback]
- fen: `Q1bqk1nr/1pp2ppp/2nb4/1p1Pp3/8/2P2N2/PP1P1PPP/RNB1K2R b KQk - 0 7`
- analysis: https://lichess.org/analysis/Q1bqk1nr/1pp2ppp/2nb4/1p1Pp3/8/2P2N2/PP1P1PPP/RNB1K2R_b_KQk_-_0_7
- game URL: https://www.chess.com/game/live/173438456408 ply 13
- screen ep_loss/cp_loss: 0.2829 / 357; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: -430
- my rating: 1555; exclusion: book_fallback; clock: 152.4s / base 180.0s

### B4: played Bd6, screen best dxe4 (game: https://www.chess.com/game/live/173438456408, move 4, ply 7) [book_fallback]
- fen: `r1bqkbnr/ppp2ppp/2n5/1B1pp3/4P3/2P2N2/PP1P1PPP/RNBQK2R b KQkq - 1 4`
- analysis: https://lichess.org/analysis/r1bqkbnr/ppp2ppp/2n5/1B1pp3/4P3/2P2N2/PP1P1PPP/RNBQK2R_b_KQkq_-_1_4
- game URL: https://www.chess.com/game/live/173438456408 ply 7
- screen ep_loss/cp_loss: 0.1921 / 170; margin: (none, no Stage 2)
- best character (screen): capture; pv material Δ: +0
- my rating: 1555; exclusion: book_fallback; clock: 165.3s / base 180.0s

### B5: played Ne7, screen best Nf6 (game: https://www.chess.com/game/live/173574482590, move 7, ply 13) [book_fallback]
- fen: `rn1qkbnr/pp3ppp/4b3/3Np3/P1p1P3/8/1P3PPP/R1BQKBNR b KQkq - 0 7`
- analysis: https://lichess.org/analysis/rn1qkbnr/pp3ppp/4b3/3Np3/P1p1P3/8/1P3PPP/R1BQKBNR_b_KQkq_-_0_7
- game URL: https://www.chess.com/game/live/173574482590 ply 13
- screen ep_loss/cp_loss: 0.1773 / 160; margin: (none, no Stage 2)
- best character (screen): quiet; pv material Δ: +0
- my rating: 1556; exclusion: book_fallback; clock: 156.4s / base 180.0s
