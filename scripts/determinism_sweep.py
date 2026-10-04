#!/usr/bin/env python
"""Determinism sweep for the review engine (SF19), runnable in the container.

Answers three questions before any nodes budget (N) is chosen:

  1. Is a fixed-nodes, MultiPV=2 evaluation reproducible?
       warm      -- one engine, sequential evals, NO game token (TT carries
                    across positions, like the batch review loop today)
       fresh     -- a new game token per search (python-chess sends
                    ucinewgame, which clears the TT)
       self      -- fresh repeated (must be identical)
       cross     -- fresh on a second engine process (must be identical)
       warm2     -- warm again, positions in REVERSE order (different TT
                    state; measures warm-vs-warm flakiness)
  2. Do those eval differences change LABELS? Runs the real GameAnalyzer
     (with a fixed-nodes engine injected) over the same games in warm and
     fresh modes and reports the per-class transition matrix, adjacent-band
     flips and severity crossings.
  3. What do mate positions do at 60k/150k/300k nodes (status and distance),
     and what is the |cp| delta distribution excluding mates?

Usage (in the container, politely):
  python scripts/determinism_sweep.py --sf-path /opt/stockfish/stockfish \
      --low-priority --json /tmp/determinism.json

Exit code is always 0 unless the script itself crashes; this is a
measurement, not a pass/fail gate.
"""
import argparse
import json
import math
import os
import statistics
import sys

import chess
import chess.engine
import chess.pgn

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "src"))

from core.game_analyzer import GameAnalyzer  # noqa: E402
from llms.mock_explainer import MockExplainer  # noqa: E402
from schemas.models import Evaluation  # noqa: E402

DEFAULT_GAMES = ["Game 1.txt", "Game 2.pgn", "Game 3.pgn", "Game 4.pgn", "Game 5.pgn"]
MATE_NODE_BUDGETS = (60_000, 150_000, 300_000)

SEVERITY = {
    "best": 0,
    "excellent": 1,
    "good": 2,
    "inaccuracy": 3,
    "mistake": 4,
    "miss": 5,
    "blunder": 6,
}
SPECIAL = {"book", "brilliant", "great"}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sf-path", default=os.getenv("STOCKFISH_PATH", "stockfish"))
    parser.add_argument("--nodes", type=int, default=60_000)
    parser.add_argument("--games", nargs="*", default=DEFAULT_GAMES)
    parser.add_argument("--limit", type=int, default=400, help="Max positions sampled.")
    parser.add_argument("--backstop", type=float, default=30.0)
    parser.add_argument(
        "--low-priority",
        action="store_true",
        help="os.nice(19) so container probes do not perturb serving.",
    )
    parser.add_argument("--json", default=None, help="Write the results as JSON.")
    return parser.parse_args()


def expected_points(cp, rating=1500.0):
    factor = max(0.55, min(1.8, 1.0 + (rating - 1500) * 0.00035))
    k = 0.0045 * factor
    return 1.0 / (1.0 + math.exp(-k * float(cp)))


def ep_band(ep):
    for edge in (0.02, 0.05, 0.10, 0.20):
        if ep <= edge:
            return edge
    return 1.0


def read_games(paths):
    games = []
    for name in paths:
        if not os.path.exists(name):
            print(f"  (skip missing {name})")
            continue
        with open(name, encoding="utf-8", errors="replace") as fh:
            game = chess.pgn.read_game(fh)
        if game is None:
            print(f"  (skip unparseable {name})")
            continue
        games.append((name, game))
    return games


def collect_positions(games, limit):
    positions = []
    for name, game in games:
        board = game.board()
        for move in game.mainline_moves():
            board.push(move)
            if board.legal_moves.count() == 0:
                continue
            positions.append((name, board.copy(stack=True)))
    if len(positions) > limit:
        step = len(positions) / limit
        positions = [positions[int(i * step)] for i in range(limit)]
    return positions


def new_simple(sf_path):
    engine = chess.engine.SimpleEngine.popen_uci(sf_path, timeout=30)
    engine.configure({"Threads": 1, "Hash": 16})
    return engine


def probe(engine, board, nodes, backstop, fresh):
    kwargs = {"game": object()} if fresh else {}
    info = engine.analyse(
        board,
        chess.engine.Limit(nodes=nodes, time=backstop),
        multipv=2,
        **kwargs,
    )
    primary = info[0] if isinstance(info, list) else info
    score = primary["score"].pov(chess.WHITE)
    pv = primary.get("pv", [])
    second = info[1] if isinstance(info, list) and len(info) > 1 else None
    second_cp = None
    if second is not None and second.get("score") is not None:
        second_cp = second["score"].pov(chess.WHITE).score(mate_score=10000)
    return {
        "cp": score.score(mate_score=10000),
        "mate": score.mate(),
        "move": pv[0].uci() if pv else None,
        "second_cp": second_cp,
    }


def eval_diff(a, b):
    return (
        a["cp"] != b["cp"]
        or a["mate"] != b["mate"]
        or a["move"] != b["move"]
        or a["second_cp"] != b["second_cp"]
    )


class NodeEngine:
    """Duck-typed engine for GameAnalyzer: fixed-nodes MultiPV=2 evaluations."""

    def __init__(self, sf_path, nodes, fresh, backstop):
        self.simple = new_simple(sf_path)
        self.nodes = nodes
        self.fresh = fresh
        self.backstop = backstop

    def evaluate(self, board, depth_limit=None, pov=None, time_limit=None, multipv=1):
        kwargs = {"game": object()} if self.fresh else {}
        info = self.simple.analyse(
            board,
            chess.engine.Limit(nodes=self.nodes, time=self.backstop),
            multipv=max(1, int(multipv)),
            **kwargs,
        )
        primary = info[0] if isinstance(info, list) else info
        view = pov if pov is not None else board.turn
        score = primary.get("score")
        mate = None
        cp = 0.0
        if score:
            normalized = score.pov(view)
            if normalized.is_mate():
                mate = normalized.mate()
                cp = 10000.0 if (mate or 0) > 0 else -10000.0
            else:
                cp = normalized.score() or 0
        second_cp = None
        second_move_uci = None
        second_move_san = None
        second_pv_uci = []
        if isinstance(info, list) and len(info) > 1 and info[1].get("score"):
            second_cp = (
                info[1]["score"].pov(view).score(mate_score=10000)
            )
            second_pv = info[1].get("pv") or []
            if second_pv:
                second_move_uci = second_pv[0].uci()
                second_move_san = board.san(second_pv[0])
                second_pv_uci = [m.uci() for m in second_pv]
        pv = primary.get("pv", [])
        return Evaluation(
            score_cp=cp,
            best_move_uci=pv[0].uci() if pv else "",
            best_move_san=board.san(pv[0]) if pv else "(none)",
            mate=mate,
            second_best_cp=second_cp,
            principal_variation_uci=[m.uci() for m in pv],
            second_best_move_uci=second_move_uci,
            second_best_move_san=second_move_san,
            second_best_pv_uci=second_pv_uci,
            depth=primary.get("depth"),
            nodes=primary.get("nodes"),
            nps=primary.get("nps"),
        )

    def close(self):
        self.simple.quit()


def labels_for(games, sf_path, nodes, fresh, backstop, reverse=False):
    engine = NodeEngine(sf_path, nodes, fresh, backstop)
    analyzer = GameAnalyzer(
        engine=engine, explainer=MockExplainer(), book_lookup=None, multipv=2
    )
    per_game = {}
    ordered = list(reversed(games)) if reverse else games
    try:
        for name, game in ordered:
            pgn = str(game)
            rows = analyzer.analyze_full_game(pgn, include_explanations=False)[1:]
            per_game[name] = [row["classification"] for row in rows]
    finally:
        engine.close()
    return per_game


def compare_labels(a, b):
    matrix = {}
    adjacent = 0
    crossings = 0
    special_changes = 0
    total = 0
    for name in a:
        if name not in b or len(a[name]) != len(b[name]):
            continue
        for left, right in zip(a[name], b[name]):
            total += 1
            matrix[(left, right)] = matrix.get((left, right), 0) + 1
            if left == right:
                continue
            if left in SPECIAL or right in SPECIAL:
                special_changes += 1
                continue
            delta = abs(SEVERITY[left] - SEVERITY[right])
            if delta == 1:
                adjacent += 1
            elif delta >= 2:
                crossings += 1
    return {
        "moves": total,
        "changed": sum(count for (l, r), count in matrix.items() if l != r),
        "adjacent_flips": adjacent,
        "severity_crossings": crossings,
        "special_changes": special_changes,
        "matrix": {f"{l}->{r}": count for (l, r), count in sorted(matrix.items())},
    }


def quantiles(values):
    if not values:
        return {}
    ordered = sorted(values)
    def pick(q):
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]
    return {
        "p50": pick(0.50),
        "p90": pick(0.90),
        "p99": pick(0.99),
        "max": ordered[-1],
    }


def main():
    args = parse_args()
    if args.low_priority:
        os.nice(19)
        print("  (running at nice 19)")

    games = read_games(args.games)
    positions = collect_positions(games, args.limit)
    print(f"engine={args.sf_path} nodes={args.nodes} positions={len(positions)}")

    engine = new_simple(args.sf_path)
    warm = [probe(engine, b, args.nodes, args.backstop, fresh=False) for _, b in positions]
    fresh1 = [probe(engine, b, args.nodes, args.backstop, fresh=True) for _, b in positions]
    fresh2 = [probe(engine, b, args.nodes, args.backstop, fresh=True) for _, b in positions]
    engine.quit()

    engine = new_simple(args.sf_path)
    warm_rev = [
        probe(engine, b, args.nodes, args.backstop, fresh=False)
        for _, b in reversed(positions)
    ]
    engine.quit()
    warm_rev = list(reversed(warm_rev))

    engine = new_simple(args.sf_path)
    cross = [probe(engine, b, args.nodes, args.backstop, fresh=True) for _, b in positions]
    engine.quit()

    warm_diff = [i for i in range(len(positions)) if eval_diff(warm[i], fresh1[i])]
    self_diff = [i for i in range(len(positions)) if eval_diff(fresh1[i], fresh2[i])]
    cross_diff = [i for i in range(len(positions)) if eval_diff(fresh1[i], cross[i])]
    warm2_diff = [i for i in range(len(positions)) if eval_diff(warm[i], warm_rev[i])]

    # Union: a position can be mate in warm but not fresh at low budgets
    # (the 60k TT-state flips), and the breakout should show both directions.
    mate_idx = [
        i
        for i in range(len(positions))
        if fresh1[i]["mate"] is not None or warm[i]["mate"] is not None
    ]
    rep_idx = [i for i, (_, b) in enumerate(positions) if b.is_repetition(3)]
    non_mate_deltas = [
        abs(warm[i]["cp"] - fresh1[i]["cp"])
        for i in range(len(positions))
        if warm[i]["mate"] is None and fresh1[i]["mate"] is None
    ]
    band_crossings = [
        i
        for i in warm_diff
        if ep_band(expected_points(warm[i]["cp"])) != ep_band(expected_points(fresh1[i]["cp"]))
    ]

    print(f"  warm vs fresh:      {len(warm_diff)}/{len(positions)} positions differ")
    print(f"  warm vs warm (rev): {len(warm2_diff)}/{len(positions)} positions differ")
    print(f"  fresh vs fresh:     {len(self_diff)}/{len(positions)}")
    print(f"  cross-process:      {len(cross_diff)}/{len(positions)}")
    print(f"  EP-band crossings (position proxy): {len(band_crossings)}")
    print(f"  non-mate |cp| delta: {quantiles(non_mate_deltas)}")
    print(f"  mate positions: {len(mate_idx)} | claimable repetitions: {len(rep_idx)}")

    labels_warm = labels_for(games, args.sf_path, args.nodes, fresh=False, backstop=args.backstop)
    labels_fresh = labels_for(games, args.sf_path, args.nodes, fresh=True, backstop=args.backstop)
    labels_warm_rev = labels_for(
        games, args.sf_path, args.nodes, fresh=False, backstop=args.backstop, reverse=True
    )
    label_vs_fresh = compare_labels(labels_warm, labels_fresh)
    label_warm_vs_warm = compare_labels(labels_warm, labels_warm_rev)
    print(
        "  move labels warm vs fresh: "
        f"{label_vs_fresh['changed']}/{label_vs_fresh['moves']} changed "
        f"(adjacent {label_vs_fresh['adjacent_flips']}, "
        f"crossings {label_vs_fresh['severity_crossings']}, "
        f"special {label_vs_fresh['special_changes']})"
    )
    print(
        "  move labels warm vs warm(rev): "
        f"{label_warm_vs_warm['changed']}/{label_warm_vs_warm['moves']} changed "
        f"(adjacent {label_warm_vs_warm['adjacent_flips']}, "
        f"crossings {label_warm_vs_warm['severity_crossings']}, "
        f"special {label_warm_vs_warm['special_changes']})"
    )

    mate_report = []
    engine = new_simple(args.sf_path)
    for nodes in MATE_NODE_BUDGETS:
        for i in mate_idx:
            board = positions[i][1]
            w = probe(engine, board, nodes, args.backstop, fresh=False)
            f = probe(engine, board, nodes, args.backstop, fresh=True)
            mate_report.append(
                {
                    "game": positions[i][0],
                    "nodes": nodes,
                    "warm_cp": w["cp"],
                    "warm_mate": w["mate"],
                    "fresh_cp": f["cp"],
                    "fresh_mate": f["mate"],
                }
            )
    engine.quit()
    disagree = [
        row
        for row in mate_report
        if row["nodes"] == MATE_NODE_BUDGETS[0]
        and (row["warm_mate"] != row["fresh_mate"])
    ]
    print(
        f"  mate breakout: {len(mate_idx)} positions x {len(MATE_NODE_BUDGETS)} budgets; "
        f"warm/fresh mate disagreement at {MATE_NODE_BUDGETS[0]}: {len(disagree)}"
    )
    for row in mate_report[:6]:
        print(
            f"    {row['game']} @{row['nodes']}: warm cp={row['warm_cp']} "
            f"mate={row['warm_mate']} | fresh cp={row['fresh_cp']} mate={row['fresh_mate']}"
        )

    results = {
        "engine": args.sf_path,
        "nodes": args.nodes,
        "positions": len(positions),
        "warm_vs_fresh": len(warm_diff),
        "warm_vs_warm_reversed": len(warm2_diff),
        "fresh_vs_fresh": len(self_diff),
        "cross_process": len(cross_diff),
        "ep_band_crossings": len(band_crossings),
        "non_mate_delta": quantiles(non_mate_deltas),
        "mate_positions": len(mate_idx),
        "claimable_repetitions": len(rep_idx),
        "labels_warm_vs_fresh": label_vs_fresh,
        "labels_warm_vs_warm_reversed": label_warm_vs_warm,
        "mate_breakout": mate_report,
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print(f"  wrote {args.json}")


if __name__ == "__main__":
    main()
