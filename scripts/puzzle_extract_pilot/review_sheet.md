# Puzzle-extract pilot -- hand-review sheet

## Pilot report
- WARNING: opening book empty/unavailable; user plies with move_number<=8 excluded as 'book_fallback' (counted below)
- settings: screen MultiPV2 @150000 nodes (det), stage2 MultiPV4 @500000 nodes, Threads=1 Hash=16, nodes-only fresh-token
- engine: Stockfish 19 | path=/home/iaminspiredbro/.local/opt/stockfish/stockfish | sha256=0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19; mode: `rev-det-v1|engine=Stockfish 19|threads=1|hash=16|nodes=150000|multipv=2|classifier=v1:43445a99c86e|book=unloaded`
- run_id=1
- book: services.opening_book.is_book_move (fail-soft, EMPTY -> fallback); fallback excludes user plies with move_number<=8
- fetch: lichess=iaminspiredbro chesscom=iaminspiredbroo max_games=20 per provider (lichess clocks=true)
  - lichess: 20 games fetched
  - chesscom: 20 games fetched
- games skipped (color not derivable): 0
- games screened ok: 40
- user plies: 1222; non-user plies: 1224
- [%clk] presence: lichess 20/20 games, 577 plies; chesscom 20/20 games, 645 plies
- clock base time: 1222 clk plies with known base, 0 unknown (30s fallback in decision)
- clock exclusion grids (user plies with [%clk], n=1222): {0.05: 43, 0.1: 87, 0.15: 144}; fixed 30s: 179
- exclusions (user plies, first-match): {'book_fallback': 282, 'clock': 74, 'time_class': 130}
- included user plies: 736
- screen yield (cp>=100): {0.1: 104, 0.15: 75, 0.2: 55, 0.25: 40}
- candidates pre-volume: 75; post dedupe+cap(top-3/game): 61
- stage2 verified: 61; pass: 24 (39.3%)
- margin yield (eligible=61) at M: {0.05: 31, 0.1: 24, 0.15: 16, 0.2: 10}
- reject reasons: {'margin_lt_0.10': 37}
- best_agrees (stage2 best == screen best): 51/61
- best-move character shares kept(n=24) vs rejected(n=37): capture 33%/19%; check 17%/3%; mate 0%/0%; promotion 0%/0%; quiet 54%/78%
- pv material Δ mean (cp, best-line / played-line) kept: +102 / -27; rejected: +28 / -183
- puzzles/game distribution (kept games only): {1: 11, 2: 5, 3: 1}
- timing: screen total 4516.0s; stage2 total 292.6s
  - game 1 [lichess] https://lichess.org/rslweHTa: screen 172280ms verify 23569ms user_plies 69
  - game 2 [lichess] https://lichess.org/SDxXVF33: screen 96051ms verify 0ms user_plies 34
  - game 3 [lichess] https://lichess.org/3ecTOztQ: screen 102602ms verify 13884ms user_plies 37
  - game 4 [lichess] https://lichess.org/WFAYPpMU: screen 65717ms verify 14391ms user_plies 22
  - game 5 [lichess] https://lichess.org/HrRCQqqS: screen 72562ms verify 4852ms user_plies 26
  - game 6 [lichess] https://lichess.org/WtARkVtS: screen 89157ms verify 17103ms user_plies 32
  - game 7 [lichess] https://lichess.org/piN0UdPH: screen 91385ms verify 3368ms user_plies 28
  - game 8 [lichess] https://lichess.org/nuFq8tmk: screen 106973ms verify 19416ms user_plies 31
  - game 9 [lichess] https://lichess.org/089xf09l: screen 84502ms verify 0ms user_plies 20
  - game 10 [lichess] https://lichess.org/BFbElKXx: screen 123277ms verify 11259ms user_plies 23
  - game 11 [lichess] https://lichess.org/8ZwF2nvP: screen 85641ms verify 9125ms user_plies 13
  - game 12 [lichess] https://lichess.org/QUVZz0Xr: screen 87152ms verify 4775ms user_plies 20
  - game 13 [lichess] https://lichess.org/XdlbeHmA: screen 58659ms verify 0ms user_plies 15
  - game 14 [lichess] https://lichess.org/8cbJ5DRG: screen 147236ms verify 12277ms user_plies 38
  - game 15 [lichess] https://lichess.org/xKJgVItt: screen 192267ms verify 11967ms user_plies 47
  - game 16 [lichess] https://lichess.org/fURrG10x: screen 141681ms verify 0ms user_plies 24
  - game 17 [lichess] https://lichess.org/LgKbO9T3: screen 136644ms verify 5028ms user_plies 34
  - game 18 [lichess] https://lichess.org/DAsZjWUo: screen 57205ms verify 14835ms user_plies 14
  - game 19 [lichess] https://lichess.org/CotneR1y: screen 91638ms verify 8456ms user_plies 19
  - game 20 [lichess] https://lichess.org/m4GV1NXX: screen 114502ms verify 3696ms user_plies 31
  - game 21 [chesscom] https://www.chess.com/game/live/184576022294: screen 221229ms verify 6166ms user_plies 69
  - game 22 [chesscom] https://www.chess.com/game/live/184575814496: screen 88613ms verify 9315ms user_plies 23
  - game 23 [chesscom] https://www.chess.com/game/live/184575572144: screen 107300ms verify 8938ms user_plies 28
  - game 24 [chesscom] https://www.chess.com/game/live/184551359070: screen 152843ms verify 13081ms user_plies 40
  - game 25 [chesscom] https://www.chess.com/game/live/184280245094: screen 40804ms verify 0ms user_plies 11
  - game 26 [chesscom] https://www.chess.com/game/live/184232048814: screen 166997ms verify 0ms user_plies 30
  - game 27 [chesscom] https://www.chess.com/game/live/180003083758: screen 130688ms verify 0ms user_plies 30
  - game 28 [chesscom] https://www.chess.com/game/live/174130588758: screen 13259ms verify 0ms user_plies 2
  - game 29 [chesscom] https://www.chess.com/game/live/174130481932: screen 59421ms verify 4492ms user_plies 14
  - game 30 [chesscom] https://www.chess.com/game/live/174103519292: screen 245728ms verify 8411ms user_plies 44
  - game 31 [chesscom] https://www.chess.com/game/live/173906785604: screen 107223ms verify 13047ms user_plies 25
  - game 32 [chesscom] https://www.chess.com/game/live/173755557612: screen 164510ms verify 12245ms user_plies 62
  - game 33 [chesscom] https://www.chess.com/game/live/173755196038: screen 170712ms verify 7939ms user_plies 59
  - game 34 [chesscom] https://www.chess.com/game/live/173754832234: screen 97267ms verify 14358ms user_plies 25
  - game 35 [chesscom] https://www.chess.com/game/live/173574482590: screen 114475ms verify 5141ms user_plies 31
  - game 36 [chesscom] https://www.chess.com/game/live/173574240936: screen 130259ms verify 2340ms user_plies 34
  - game 37 [chesscom] https://www.chess.com/game/live/173553526042: screen 139125ms verify 0ms user_plies 39
  - game 38 [chesscom] https://www.chess.com/game/live/173553416150: screen 114720ms verify 0ms user_plies 34
  - game 39 [chesscom] https://www.chess.com/game/live/173553365432: screen 70278ms verify 0ms user_plies 23
  - game 40 [chesscom] https://www.chess.com/game/live/173438456408: screen 63450ms verify 9166ms user_plies 22

---

## Kept puzzles (24)

### P1: white to move, play Qf4 (game: https://lichess.org/8cbJ5DRG, move 32)
- fen: `5qk1/p4ppp/1p2p3/3rQ1P1/1P1B4/P7/7P/5RK1 w - - 6 32`
- side to move: white
- my played move: Qf5 (e5f5)
- best move: Qf4 (e5f4)
- PV (explanation only, not graded): Qf4 Rf5 Qg3 Rd5 Be3 Qd8 Qf3 Qc7 a4 a6 h4 Rd3 Rf2 a5 b5 h5 gxh6 Qe5 Qxf7+ Kh7
- win-prob before: 0.8971 after (verified): 0.0092
- screen ep_loss/cp_loss: 0.8548 / 1289; verified: 0.8878 / 1416
- uniqueness margin (EP best-second): 0.1178
- best character: quiet; pv material Δ (best/played, cp): +0 / -900
- game URL: https://lichess.org/8cbJ5DRG ply 62
- analysis: https://lichess.org/analysis/5qk1/p4ppp/1p2p3/3rQ1P1/1P1B4/P7/7P/5RK1_w_-_-_6_32

### P2: white to move, play Nxh2 (game: https://lichess.org/LgKbO9T3, move 14)
- fen: `rn2k2r/ppq2pp1/2pbp1p1/6Q1/3P4/2N2NB1/PPP2PPn/R4RK1 w kq - 0 14`
- side to move: white
- my played move: Ne5 (f3e5)
- best move: Nxh2 (f3h2)
- PV (explanation only, not graded): Nxh2 Rh5 Qe3 Nd7 Ne4 Bxg3 Qxg3 O-O-O Qxc7+ Kxc7 Ng3 Rh6 Nf3 Rdh8 Ng5 f5 Nf7
- win-prob before: 0.9444 after (verified): 0.1237
- screen ep_loss/cp_loss: 0.8123 / 973; verified: 0.8207 / 996
- uniqueness margin (EP best-second): 0.6216
- best character: capture; pv material Δ (best/played, cp): -10 / -490
- game URL: https://lichess.org/LgKbO9T3 ply 26
- analysis: https://lichess.org/analysis/rn2k2r/ppq2pp1/2pbp1p1/6Q1/3P4/2N2NB1/PPP2PPn/R4RK1_w_kq_-_0_14

### P3: black to move, play Nc5 (game: https://www.chess.com/game/live/184551359070, move 17)
- fen: `2r2rk1/1p1n1ppp/1q2p3/pP3b2/P1Pp4/1Q2bN2/1B1NB1PP/3R1R1K b - - 1 17`
- side to move: black
- my played move: Nf6 (d7f6)
- best move: Nc5 (d7c5)
- PV (explanation only, not graded): Nc5 Qa3 d3 g4 dxe2 Qxe3 exf1=Q+ Rxf1 Bd3 Rc1 Qd6 Bd4
- win-prob before: 0.8929 after (verified): 0.1362
- screen ep_loss/cp_loss: 0.7134 / 786; verified: 0.7567 / 872
- uniqueness margin (EP best-second): 0.4884
- best character: quiet; pv material Δ (best/played, cp): +0 / +0
- game URL: https://www.chess.com/game/live/184551359070 ply 33
- analysis: https://lichess.org/analysis/2r2rk1/1p1n1ppp/1q2p3/pP3b2/P1Pp4/1Q2bN2/1B1NB1PP/3R1R1K_b_-_-_1_17

### P4: black to move, play Rc8 (game: https://www.chess.com/game/live/173906785604, move 9)
- fen: `1r2kb1r/p1Q1nppp/2n1b3/3qp3/8/5NP1/PP1PPPBP/RNB1K2R b KQk - 0 9`
- side to move: black
- my played move: g6 (g7g6)
- best move: Rc8 (b8c8)
- PV (explanation only, not graded): Rc8 Nc3 Rxc7 Nxd5 Bxd5 O-O Bxf3 Bxf3 Nd4 d3 Nxf3+ exf3 f6 Be3 Rd7 Rac1 Kf7 Rfd1 Nd5 Rc8 Rg8 Rdc1 Be7
- win-prob before: 0.9148 after (verified): 0.1621
- screen ep_loss/cp_loss: 0.7435 / 850; verified: 0.7527 / 885
- uniqueness margin (EP best-second): 0.3785
- best character: quiet; pv material Δ (best/played, cp): +320 / +0
- game URL: https://www.chess.com/game/live/173906785604 ply 17
- analysis: https://lichess.org/analysis/1r2kb1r/p1Q1nppp/2n1b3/3qp3/8/5NP1/PP1PPPBP/RNB1K2R_b_KQk_-_0_9

### P5: black to move, play Qd4+ (game: https://www.chess.com/game/live/184575572144, move 22)
- fen: `r3r3/p2q2pk/1pp1p2p/2p1N3/P3P2Q/3P3P/1PP3P1/R4RK1 b - - 1 22`
- side to move: black
- my played move: Qc7 (d7c7)
- best move: Qd4+ (d7d4)
- PV (explanation only, not graded): Qd4+ Rf2 Qxe5 Raf1 Qxb2 Rf7 Qxc2 Qh5 Rg8 R1f6 Qc1+ Kh2 Qg5 Qxg5 hxg5 Rc7
- win-prob before: 0.4830 after (verified): 0.0282
- screen ep_loss/cp_loss: 0.4419 / 685; verified: 0.4548 / 765
- uniqueness margin (EP best-second): 0.4464
- best character: check; pv material Δ (best/played, cp): +420 / +0
- game URL: https://www.chess.com/game/live/184575572144 ply 43
- analysis: https://lichess.org/analysis/r3r3/p2q2pk/1pp1p2p/2p1N3/P3P2Q/3P3P/1PP3P1/R4RK1_b_-_-_1_22

### P6: white to move, play Nb5+ (game: https://lichess.org/8ZwF2nvP, move 12)
- fen: `r1bq1b1r/ppp3pp/3k4/3Bp3/8/P1N2Q2/1P1P1PPP/n1BK3R w - - 1 12`
- side to move: white
- my played move: Qf7 (f3f7)
- best move: Nb5+ (c3b5)
- PV (explanation only, not graded): Nb5+ Kc5 Bxb7 Bxb7 Qxb7 Qd5
- win-prob before: 0.5125 after (verified): 0.1144
- screen ep_loss/cp_loss: 0.2847 / 401; verified: 0.3981 / 503
- uniqueness margin (EP best-second): 0.2612
- best character: check; pv material Δ (best/played, cp): +100 / +0
- game URL: https://lichess.org/8ZwF2nvP ply 22
- analysis: https://lichess.org/analysis/r1bq1b1r/ppp3pp/3k4/3Bp3/8/P1N2Q2/1P1P1PPP/n1BK3R_w_-_-_1_12

### P7: black to move, play Rd8+ (game: https://www.chess.com/game/live/173755196038, move 43)
- fen: `1r4k1/6p1/Rp5p/1RpK1P2/2P2P2/2r5/8/8 b - - 5 43`
- side to move: black
- my played move: Rf3 (c3f3)
- best move: Rd8+ (b8d8)
- PV (explanation only, not graded): Rd8+ Kc6 Rxc4 f6 Rxf4 Kc7 Rf8 fxg7 R8f6 Raxb6 Rxb6 Kxb6 c4 Rc5 Kxg7 Kb7
- win-prob before: 0.8830 after (verified): 0.5000
- screen ep_loss/cp_loss: 0.3881 / 454; verified: 0.3830 / 443
- uniqueness margin (EP best-second): 0.2012
- best character: check; pv material Δ (best/played, cp): +200 / +0
- game URL: https://www.chess.com/game/live/173755196038 ply 85
- analysis: https://lichess.org/analysis/1r4k1/6p1/Rp5p/1RpK1P2/2P2P2/2r5/8/8_b_-_-_5_43

### P8: white to move, play Nd4 (game: https://lichess.org/CotneR1y, move 17)
- fen: `r4rk1/pp2nppp/2nR4/q7/2B3b1/BQp2N2/P4PPP/4R1K1 w - - 3 17`
- side to move: white
- my played move: Ng5 (f3g5)
- best move: Nd4 (f3d4)
- PV (explanation only, not graded): Nd4 Bf5 Re3 c2 Nxc2 Bxc2 Qxc2 Nf5 Rh3 h6 Rxc6 bxc6 Bxf8 Rxf8 Bf1 Nd4 Qc4 Rd8 Rd3 c5 Re3 Nf5 Rd3 Rxd3 Qxd3
- win-prob before: 0.4444 after (verified): 0.0732
- screen ep_loss/cp_loss: 0.3813 / 458; verified: 0.3712 / 477
- uniqueness margin (EP best-second): 0.1113
- best character: quiet; pv material Δ (best/played, cp): -220 / -220
- game URL: https://lichess.org/CotneR1y ply 32
- analysis: https://lichess.org/analysis/r4rk1/pp2nppp/2nR4/q7/2B3b1/BQp2N2/P4PPP/4R1K1_w_-_-_3_17

### P9: white to move, play Be3 (game: https://lichess.org/8cbJ5DRG, move 15)
- fen: `r2q1rk1/ppb2ppp/1np1p3/4Pn2/1P1P4/P1N5/4BPPP/R1BQ1RK1 w - - 0 15`
- side to move: white
- my played move: g4 (g2g4)
- best move: Be3 (c1e3)
- PV (explanation only, not graded): Be3 f6 exf6 Qxf6 g3 Rad8 Qc2 Nd5 Nxd5 exd5 Bd3 g6 Bxf5 gxf5 Bd2
- win-prob before: 0.4807 after (verified): 0.1621
- screen ep_loss/cp_loss: 0.2952 / 301; verified: 0.3186 / 324
- uniqueness margin (EP best-second): 0.2127
- best character: quiet; pv material Δ (best/played, cp): +0 / -110
- game URL: https://lichess.org/8cbJ5DRG ply 28
- analysis: https://lichess.org/analysis/r2q1rk1/ppb2ppp/1np1p3/4Pn2/1P1P4/P1N5/4BPPP/R1BQ1RK1_w_-_-_0_15

### P10: white to move, play Bb5 (game: https://lichess.org/DAsZjWUo, move 11)
- fen: `r2qkb1r/5ppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RNBQ1RK1 w kq - 0 11`
- side to move: white
- my played move: Bg5 (c1g5)
- best move: Bb5 (e2b5)
- PV (explanation only, not graded): Bb5 Qd7 Nd4 Ngxe5 Re1 Bd3 Bxc6 Nxc6 Qxd3 Nxd4 Qxd4 Bd6 Qd1
- win-prob before: 0.9645 after (verified): 0.6488
- screen ep_loss/cp_loss: 0.2751 / 524; verified: 0.3157 / 556
- uniqueness margin (EP best-second): 0.1552
- best character: quiet; pv material Δ (best/played, cp): -100 / +0
- game URL: https://lichess.org/DAsZjWUo ply 20
- analysis: https://lichess.org/analysis/r2qkb1r/5ppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RNBQ1RK1_w_kq_-_0_11

### P11: white to move, play Rxc8 (game: https://lichess.org/WtARkVtS, move 21)
- fen: `r1r2bk1/1p3p1p/pq4pB/3pPp2/3n4/1N3PR1/PP1Q1P1P/2R4K w - - 0 21`
- side to move: white
- my played move: Bxf8 (h6f8)
- best move: Rxc8 (c1c8)
- PV (explanation only, not graded): Rxc8 Rxc8
- win-prob before: 0.5888 after (verified): 0.2746
- screen ep_loss/cp_loss: 0.2816 / 245; verified: 0.3142 / 278
- uniqueness margin (EP best-second): 0.2857
- best character: capture; pv material Δ (best/played, cp): +0 / +0
- game URL: https://lichess.org/WtARkVtS ply 40
- analysis: https://lichess.org/analysis/r1r2bk1/1p3p1p/pq4pB/3pPp2/3n4/1N3PR1/PP1Q1P1P/2R4K_w_-_-_0_21

### P12: white to move, play exd5 (game: https://lichess.org/rslweHTa, move 16)
- fen: `r4rk1/4ppbp/p2pb1p1/1p1n4/4P3/4BP2/PPPRB1PP/1K5R w - - 0 16`
- side to move: white
- my played move: Rxd5 (d2d5)
- best move: exd5 (e4d5)
- PV (explanation only, not graded): exd5 Bd7 h4 h5 g4 hxg4 fxg4 Rae8 h5 e6 Bf3 exd5 Bf4 Be5 Bh6 Re6
- win-prob before: 0.5000 after (verified): 0.2457
- screen ep_loss/cp_loss: 0.2501 / 260; verified: 0.2543 / 267
- uniqueness margin (EP best-second): 0.2703
- best character: capture; pv material Δ (best/played, cp): +220 / +150
- game URL: https://lichess.org/rslweHTa ply 30
- analysis: https://lichess.org/analysis/r4rk1/4ppbp/p2pb1p1/1p1n4/4P3/4BP2/PPPRB1PP/1K5R_w_-_-_0_16

### P13: black to move, play Nc5 (game: https://www.chess.com/game/live/184551359070, move 20)
- fen: `2rr2k1/1p3ppp/1q2p3/pP3b2/P1Ppn3/NQ1BbN2/1B4PP/3R1R1K b - - 7 20`
- side to move: black
- my played move: Nf2+ (e4f2)
- best move: Nc5 (e4c5)
- PV (explanation only, not graded): Nc5 Qc2 Nxd3 Rxd3 Qd6 Ne1 Bxd3 Nxd3 e5 c5 Qd5 Rf5 f6 h3 Qe6 Rf3
- win-prob before: 0.7401 after (verified): 0.4909
- screen ep_loss/cp_loss: 0.2820 / 269; verified: 0.2492 / 238
- uniqueness margin (EP best-second): 0.2151
- best character: quiet; pv material Δ (best/played, cp): +10 / +180
- game URL: https://www.chess.com/game/live/184551359070 ply 39
- analysis: https://lichess.org/analysis/2rr2k1/1p3ppp/1q2p3/pP3b2/P1Ppn3/NQ1BbN2/1B4PP/3R1R1K_b_-_-_7_20

### P14: white to move, play Be3 (game: https://www.chess.com/game/live/173754832234, move 12)
- fen: `2kr1b1r/pbp2pp1/2n2n1p/1Bq5/4p3/2NP1N2/PPP2PPP/R1BQ1RK1 w - - 0 12`
- side to move: white
- my played move: Ne1 (f3e1)
- best move: Be3 (c1e3)
- PV (explanation only, not graded): Be3 Qh5 Nd2 exd3 Bxd3 g6 Qxh5 Nxh5 Bc4 Nd4 Rad1 Bg7 Bxf7 Nxc2 Nb3
- win-prob before: 0.7140 after (verified): 0.4749
- screen ep_loss/cp_loss: 0.3117 / 292; verified: 0.2392 / 222
- uniqueness margin (EP best-second): 0.1213
- best character: quiet; pv material Δ (best/played, cp): +0 / +0
- game URL: https://www.chess.com/game/live/173754832234 ply 22
- analysis: https://lichess.org/analysis/2kr1b1r/pbp2pp1/2n2n1p/1Bq5/4p3/2NP1N2/PPP2PPP/R1BQ1RK1_w_-_-_0_12

### P15: black to move, play Qa5 (game: https://www.chess.com/game/live/173906785604, move 15)
- fen: `2r2rk1/pQ3pbp/2n1b1p1/2q5/4N3/6P1/PP1PPPBP/R1B2RK1 b - - 3 15`
- side to move: black
- my played move: Qc4 (c5c4)
- best move: Qa5 (c5a5)
- PV (explanation only, not graded): Qa5 Nc3 Nd4 e3 Rb8 Qe4 Bf5 Qd5 Qxd5 Nxd5 Nc2
- win-prob before: 0.3281 after (verified): 0.0945
- screen ep_loss/cp_loss: 0.1916 / 306; verified: 0.2336 / 340
- uniqueness margin (EP best-second): 0.1151
- best character: quiet; pv material Δ (best/played, cp): +0 / -80
- game URL: https://www.chess.com/game/live/173906785604 ply 29
- analysis: https://lichess.org/analysis/2r2rk1/pQ3pbp/2n1b1p1/2q5/4N3/6P1/PP1PPPBP/R1B2RK1_b_-_-_3_15

### P16: black to move, play Bxg2 (game: https://www.chess.com/game/live/173438456408, move 21)
- fen: `Q7/2p2kpp/1pP2n1r/1p5q/4N3/2P3Bb/PP3PP1/R3R1K1 b - - 0 21`
- side to move: black
- my played move: Bf5 (h3f5)
- best move: Bxg2 (h3g2)
- PV (explanation only, not graded): Bxg2 Nd6+ cxd6 Qb7+ Kg6 Kxg2 Qd5+ f3 Qd2+ Kg1 Rh3 Bf2 Rxf3 Qxb6 Qg5+ Kf1 Ng4 c7 Nxf2 Qxd6+ Kh5
- win-prob before: 0.2565 after (verified): 0.0366
- screen ep_loss/cp_loss: 0.2610 / 478; verified: 0.2199 / 481
- uniqueness margin (EP best-second): 0.1826
- best character: capture; pv material Δ (best/played, cp): +90 / +220
- game URL: https://www.chess.com/game/live/173438456408 ply 41
- analysis: https://lichess.org/analysis/Q7/2p2kpp/1pP2n1r/1p5q/4N3/2P3Bb/PP3PP1/R3R1K1_b_-_-_0_21

### P17: white to move, play Nxe7+ (game: https://lichess.org/rslweHTa, move 15)
- fen: `r4rk1/4ppbp/p2pbnp1/1p1N4/4P3/4BP2/PPPqB1PP/1K1R3R w - - 0 15`
- side to move: white
- my played move: Rxd2 (d1d2)
- best move: Nxe7+ (d5e7)
- PV (explanation only, not graded): Nxe7+ Kh8 Bxd2 Rfe8 Nc6 d5 e5 Nd7 f4 f6 Nd4 Bg8 e6 Bxe6 Nxe6 Rxe6 Bf3 Nb6 h4 Rae8 g4
- win-prob before: 0.7073 after (verified): 0.5032
- screen ep_loss/cp_loss: 0.2010 / 204; verified: 0.2041 / 207
- uniqueness margin (EP best-second): 0.1915
- best character: capture+check; pv material Δ (best/played, cp): +1000 / +900
- game URL: https://lichess.org/rslweHTa ply 28
- analysis: https://lichess.org/analysis/r4rk1/4ppbp/p2pbnp1/1p1N4/4P3/4BP2/PPPqB1PP/1K1R3R_w_-_-_0_15

### P18: white to move, play Bxb5 (game: https://lichess.org/8cbJ5DRG, move 21)
- fen: `r2qr1k1/ppb2ppp/1n2p3/1pB5/1P3PP1/P2B4/7P/R2Q1RK1 w - - 2 21`
- side to move: white
- my played move: Qc2 (d1c2)
- best move: Bxb5 (d3b5)
- PV (explanation only, not graded): Bxb5 Qxd1 Raxd1 Red8 Rxd8+ Rxd8 f5 exf5 Rxf5 g6 Rf1 a6 Be2 Rd2 Rf2 Rd7 Bf3
- win-prob before: 0.4686 after (verified): 0.2660
- screen ep_loss/cp_loss: 0.2111 / 188; verified: 0.2026 / 184
- uniqueness margin (EP best-second): 0.1763
- best character: capture; pv material Δ (best/played, cp): +100 / +100
- game URL: https://lichess.org/8cbJ5DRG ply 40
- analysis: https://lichess.org/analysis/r2qr1k1/ppb2ppp/1n2p3/1pB5/1P3PP1/P2B4/7P/R2Q1RK1_w_-_-_2_21

### P19: black to move, play e5 (game: https://www.chess.com/game/live/174103519292, move 14)
- fen: `r3k2r/pp1n1ppp/4p3/3p4/Q2P4/3K4/Pq2PPPP/3R1B1R b kq - 1 14`
- side to move: black
- my played move: a6 (a7a6)
- best move: e5 (e6e5)
- PV (explanation only, not graded): e5 f3 Rc8 Ke3 Rc4 Qxa7 exd4+ Kf4 O-O e4 d3 Bxd3 Rc6 exd5 Qe5+ Kg4 Nf6+ Kh3 Nxd5
- win-prob before: 0.9040 after (verified): 0.7107
- screen ep_loss/cp_loss: 0.1796 / 275; verified: 0.1933 / 296
- uniqueness margin (EP best-second): 0.1215
- best character: quiet; pv material Δ (best/played, cp): -100 / +0
- game URL: https://www.chess.com/game/live/174103519292 ply 27
- analysis: https://lichess.org/analysis/r3k2r/pp1n1ppp/4p3/3p4/Q2P4/3K4/Pq2PPPP/3R1B1R_b_kq_-_1_14

### P20: white to move, play Nxe4 (game: https://lichess.org/nuFq8tmk, move 15)
- fen: `2kr3r/p1pb1ppp/2pb1q2/8/3Pp3/2P1Q1N1/PP3PPP/R1B2RK1 w - - 4 15`
- side to move: white
- my played move: Qxe4 (e3e4)
- best move: Nxe4 (g3e4)
- PV (explanation only, not graded): Nxe4 Qg6 Nxd6+ Qxd6 Qd3 Kb8 Bg5 Rde8 c4 Ka8 Rad1
- win-prob before: 0.7157 after (verified): 0.5386
- screen ep_loss/cp_loss: 0.1836 / 184; verified: 0.1771 / 179
- uniqueness margin (EP best-second): 0.1038
- best character: capture; pv material Δ (best/played, cp): +110 / +100
- game URL: https://lichess.org/nuFq8tmk ply 28
- analysis: https://lichess.org/analysis/2kr3r/p1pb1ppp/2pb1q2/8/3Pp3/2P1Q1N1/PP3PPP/R1B2RK1_w_-_-_4_15

### P21: black to move, play Qf7 (game: https://lichess.org/xKJgVItt, move 20)
- fen: `r7/pppq2k1/3p1p2/3Np1pR/2PbP3/P4PP1/1PPQ2P1/2K5 b - - 2 20`
- side to move: black
- my played move: Rh8 (a8h8)
- best move: Qf7 (d7f7)
- PV (explanation only, not graded): Qf7 g4
- win-prob before: 0.2929 after (verified): 0.1157
- screen ep_loss/cp_loss: 0.2000 / 342; verified: 0.1771 / 277
- uniqueness margin (EP best-second): 0.1313
- best character: quiet; pv material Δ (best/played, cp): +0 / -100
- game URL: https://lichess.org/xKJgVItt ply 39
- analysis: https://lichess.org/analysis/r7/pppq2k1/3p1p2/3Np1pR/2PbP3/P4PP1/1PPQ2P1/2K5_b_-_-_2_20

### P22: black to move, play e4 (game: https://www.chess.com/game/live/173438456408, move 9)
- fen: `Q1bqk2r/2p2ppp/1pPb1n2/1p2p3/8/2P2N2/PP1P1PPP/RNB2RK1 b k - 1 9`
- side to move: black
- my played move: O-O (e8g8)
- best move: e4 (e5e4)
- PV (explanation only, not graded): e4 d4 exf3 Re1+ Be7 Qa3 Be6 Qa6 O-O Qxb5 fxg2 Bg5 Nd5 Bxe7 Qxe7 Nd2 Nf4 Qe5 Qh4 Re3 Bd5 c4 Bxc6
- win-prob before: 0.2548 after (verified): 0.0984
- screen ep_loss/cp_loss: 0.1703 / 277; verified: 0.1564 / 249
- uniqueness margin (EP best-second): 0.1657
- best character: quiet; pv material Δ (best/played, cp): +320 / +0
- game URL: https://www.chess.com/game/live/173438456408 ply 17
- analysis: https://lichess.org/analysis/Q1bqk2r/2p2ppp/1pPb1n2/1p2p3/8/2P2N2/PP1P1PPP/RNB2RK1_b_k_-_1_9

### P23: white to move, play Bb5 (game: https://lichess.org/DAsZjWUo, move 13)
- fen: `r3k2r/4qppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RN1Q1RK1 w kq - 0 13`
- side to move: white
- my played move: Nd4 (f3d4)
- best move: Bb5 (e2b5)
- PV (explanation only, not graded): Bb5 Qd7 Nd4 Nge7 a4 O-O Na3 h6 Re1 Rfd8 Qd2 Qc8 Bxc6 Nxc6
- win-prob before: 0.7726 after (verified): 0.6276
- screen ep_loss/cp_loss: 0.1551 / 155; verified: 0.1450 / 145
- uniqueness margin (EP best-second): 0.1655
- best character: quiet; pv material Δ (best/played, cp): +0 / +0
- game URL: https://lichess.org/DAsZjWUo ply 24
- analysis: https://lichess.org/analysis/r3k2r/4qppp/1pn1p1n1/3pPb2/1P6/2P2N2/P3BPPP/RN1Q1RK1_w_kq_-_0_13

### P24: black to move, play Nxc3 (game: https://lichess.org/3ecTOztQ, move 22)
- fen: `1r3rk1/2pbqpbp/p5p1/2Pp4/2nPn2P/P1N1P1P1/2B3KN/1RB1QR2 b - - 5 22`
- side to move: black
- my played move: Nxc3 (e4c3)
- best move: Nxc3 (e4c3)
- PV (explanation only, not graded): Nxc3 Qxc3 Rxb1 Bxb1 Rb8 Ba2 Qe4+ Kg1 Bh3 Rf2 h5 Bxc4 dxc4 Qxc4
- win-prob before: 0.8065 after (verified): 0.8676
- screen ep_loss/cp_loss: 0.2190 / 276; verified: 0.0000 / 0
- uniqueness margin (EP best-second): 0.1083
- best character: capture; pv material Δ (best/played, cp): +0 / -400
- game URL: https://lichess.org/3ecTOztQ ply 43
- analysis: https://lichess.org/analysis/1r3rk1/2pbqpbp/p5p1/2Pp4/2nPn2P/P1N1P1P1/2B3KN/1RB1QR2_b_-_-_5_22

---

## Rejected candidates (random 10 of 37, seed=20261008)

### R1: margin_lt_0.10 (game: https://lichess.org/m4GV1NXX, move 18, played Ng4, ep_loss 0.2653)
- fen: `r1r3k1/pp2Bpp1/4pn1p/4Q3/3P4/2P1Pq2/P4P1P/R4RK1 b - - 1 18`
- screen best: Nd5 (f6d5); verified best: f6d5
- rejection reason: margin_lt_0.10
- game URL: https://lichess.org/m4GV1NXX ply 35
- analysis: https://lichess.org/analysis/r1r3k1/pp2Bpp1/4pn1p/4Q3/3P4/2P1Pq2/P4P1P/R4RK1_b_-_-_1_18

### R2: margin_lt_0.10 (game: https://lichess.org/WFAYPpMU, move 11, played g4, ep_loss 0.3265)
- fen: `r1bq1rk1/4ppbp/p1np1np1/1p6/4P3/1NN1B3/PPPQBPPP/2KR3R w - - 0 11`
- screen best: f3 (f2f3); verified best: f2f3
- rejection reason: margin_lt_0.10
- game URL: https://lichess.org/WFAYPpMU ply 20
- analysis: https://lichess.org/analysis/r1bq1rk1/4ppbp/p1np1np1/1p6/4P3/1NN1B3/PPPQBPPP/2KR3R_w_-_-_0_11

### R3: margin_lt_0.10 (game: https://www.chess.com/game/live/173755557612, move 15, played c4, ep_loss 0.2053)
- fen: `2rq1rk1/4bppp/p1npbn2/1p1Np3/4P3/2P1NP2/PP2B1PP/R1BQ1RK1 w - - 5 15`
- screen best: a4 (a2a4); verified best: a2a4
- rejection reason: margin_lt_0.10
- game URL: https://www.chess.com/game/live/173755557612 ply 28
- analysis: https://lichess.org/analysis/2rq1rk1/4bppp/p1npbn2/1p1Np3/4P3/2P1NP2/PP2B1PP/R1BQ1RK1_w_-_-_5_15

### R4: margin_lt_0.10 (game: https://lichess.org/xKJgVItt, move 35, played Bf4, ep_loss 0.2216)
- fen: `8/1p6/p1pp4/4p3/2P1P3/P1PNb1k1/1P2K1P1/8 b - - 1 35`
- screen best: Ba7 (e3a7); verified best: e3b6
- rejection reason: margin_lt_0.10
- game URL: https://lichess.org/xKJgVItt ply 69
- analysis: https://lichess.org/analysis/8/1p6/p1pp4/4p3/2P1P3/P1PNb1k1/1P2K1P1/8_b_-_-_1_35

### R5: margin_lt_0.10 (game: https://lichess.org/nuFq8tmk, move 24, played Qxf6, ep_loss 0.7118)
- fen: `2k4r/p1pb2q1/2p2p1r/6p1/3PNQ2/2P4P/PP3PP1/R3R1K1 w - - 0 24`
- screen best: Qe3 (f4e3); verified best: f4e3
- rejection reason: margin_lt_0.10
- game URL: https://lichess.org/nuFq8tmk ply 46
- analysis: https://lichess.org/analysis/2k4r/p1pb2q1/2p2p1r/6p1/3PNQ2/2P4P/PP3PP1/R3R1K1_w_-_-_0_24

### R6: margin_lt_0.10 (game: https://lichess.org/CotneR1y, move 15, played Rxd6, ep_loss 0.1717)
- fen: `r1bq1rk1/pp2nppp/2np4/8/Q1B5/B1p2N2/P4PPP/3RR1K1 w - - 4 15`
- screen best: Bxd6 (a3d6); verified best: d1d6
- rejection reason: margin_lt_0.10
- game URL: https://lichess.org/CotneR1y ply 28
- analysis: https://lichess.org/analysis/r1bq1rk1/pp2nppp/2np4/8/Q1B5/B1p2N2/P4PPP/3RR1K1_w_-_-_4_15

### R7: margin_lt_0.10 (game: https://www.chess.com/game/live/173574482590, move 14, played Rfe8, ep_loss 0.2900)
- fen: `r4rk1/pp1n1ppp/1q1b4/3PpbB1/P1B5/1P3N2/5PPP/R2QR1K1 b - - 0 14`
- screen best: h6 (h7h6); verified best: a7a6
- rejection reason: margin_lt_0.10
- game URL: https://www.chess.com/game/live/173574482590 ply 27
- analysis: https://lichess.org/analysis/r4rk1/pp1n1ppp/1q1b4/3PpbB1/P1B5/1P3N2/5PPP/R2QR1K1_b_-_-_0_14

### R8: margin_lt_0.10 (game: https://www.chess.com/game/live/184575814496, move 16, played g5, ep_loss 0.3327)
- fen: `3r1b1r/2pk1pp1/2pn1q2/8/Q1bP3p/2P1B2P/PP3PP1/RN2R1K1 b - - 0 16`
- screen best: Bd5 (c4d5); verified best: f6g6
- rejection reason: margin_lt_0.10
- game URL: https://www.chess.com/game/live/184575814496 ply 31
- analysis: https://lichess.org/analysis/3r1b1r/2pk1pp1/2pn1q2/8/Q1bP3p/2P1B2P/PP3PP1/RN2R1K1_b_-_-_0_16

### R9: margin_lt_0.10 (game: https://lichess.org/WtARkVtS, move 29, played f4, ep_loss 0.4724)
- fen: `3r3Q/1p2kp2/p5p1/3p1p2/8/5P1R/PP3PKP/4q3 w - - 3 29`
- screen best: Qh4+ (h8h4); verified best: h8h4
- rejection reason: margin_lt_0.10
- game URL: https://lichess.org/WtARkVtS ply 56
- analysis: https://lichess.org/analysis/3r3Q/1p2kp2/p5p1/3p1p2/8/5P1R/PP3PKP/4q3_w_-_-_3_29

### R10: margin_lt_0.10 (game: https://www.chess.com/game/live/173755196038, move 49, played h4, ep_loss 0.3904)
- fen: `5rk1/6p1/1RP5/7p/1K6/8/8/8 b - - 0 49`
- screen best: Kf7 (g8f7); verified best: g8h7
- rejection reason: margin_lt_0.10
- game URL: https://www.chess.com/game/live/173755196038 ply 97
- analysis: https://lichess.org/analysis/5rk1/6p1/1RP5/7p/1K6/8/8/8_b_-_-_0_49
