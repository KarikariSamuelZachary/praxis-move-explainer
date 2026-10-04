#!/usr/bin/env python
"""N-vs-latency table and 240s-ceiling calculation (fill in container nps).

The container nps is the missing input: measure it under load in the deployed
container (Stockfish `info nps`, or determinism_sweep's telemetry) and pass
it here. Results are identical at any N, so only quality and latency trade
off; this script answers "what fits".

Usage:
  python scripts/n_ceiling.py --nps 250000 --plies 80
  python scripts/n_ceiling.py --nps 120000 --plies 80 --llm-seconds 15
"""
import argparse
import math


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nps", type=float, required=True, help="measured nodes/sec")
    parser.add_argument("--plies", type=int, default=80, help="game length in plies")
    parser.add_argument("--ceiling", type=float, default=240.0, help="frontend timeout (s)")
    parser.add_argument(
        "--llm-seconds",
        type=float,
        default=0.0,
        help="worst-case serial LLM time still on the request path",
    )
    parser.add_argument(
        "--nodes",
        nargs="*",
        type=int,
        default=[60_000, 100_000, 150_000, 200_000, 300_000, 400_000, 600_000, 1_000_000],
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print(
        f"nps={args.nps:,.0f}  plies={args.plies}  ceiling={args.ceiling:.0f}s  "
        f"llm={args.llm_seconds:.0f}s"
    )
    print(f"{'N':>10} {'per-eval':>10} {'review':>9} {'headroom':>10} {'max plies':>10} fits")
    for nodes in args.nodes:
        per_eval = nodes / args.nps
        review = (args.plies + 1) * per_eval + args.llm_seconds
        headroom = args.ceiling - review
        max_plies = math.floor(
            (args.ceiling - args.llm_seconds) / per_eval
        ) - 1
        print(
            f"{nodes:>10,} {per_eval:>9.2f}s {review:>8.1f}s "
            f"{headroom:>9.1f}s {max_plies:>10} "
            f"{'yes' if review <= args.ceiling else 'NO'}"
        )
    max_per_eval = (args.ceiling - args.llm_seconds) / max(1, args.plies + 1)
    print(
        f"max per-eval for the ceiling at {args.plies} plies: "
        f"{max_per_eval:.2f}s -> max N {max_per_eval * args.nps:,.0f}"
    )


if __name__ == "__main__":
    main()
