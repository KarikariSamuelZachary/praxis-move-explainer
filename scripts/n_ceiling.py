#!/usr/bin/env python
"""N-vs-latency table at the deterministic review's maximum game length.

Inputs: the container nps (the only unknown). The deterministic review
accepts at most review_max_plies() plies (GATE_P99_PLIES until
REVIEW_CONTAINER_NPS is measured) and the frontend proxy aborts at 240s
(REVIEW_TIMEOUT_MS). We size N so that 2x the engine time still fits in the
240s ceiling, leaving the other half for UART/HTTP/LLM overhead and variance.
Flag off means no ply cap (old behavior), so this table only applies to the
new deterministic mode.

Usage:
  python scripts/n_ceiling.py --nps 250000
  python scripts/n_ceiling.py --nps 120000 --llm-seconds 15
"""
import argparse
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from core.analysis_mode import (  # noqa: E402
    GATE_P99_PLIES,
    REVIEW_HEADROOM,
    REVIEW_TIMEOUT_SECONDS,
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nps", type=float, required=True, help="measured nodes/sec")
    parser.add_argument(
        "--plies",
        type=int,
        default=GATE_P99_PLIES,
        help="game length; defaults to the deterministic cap placeholder",
    )
    parser.add_argument("--ceiling", type=float, default=REVIEW_TIMEOUT_SECONDS, help="proxy abort (s)")
    parser.add_argument(
        "--headroom",
        type=float,
        default=REVIEW_HEADROOM,
        help="required headroom (2 = engine time must be <= ceiling/2)",
    )
    parser.add_argument(
        "--llm-seconds",
        type=float,
        default=0.0,
        help="worst-case serial LLM time still on the request path",
    )
    parser.add_argument(
        "--llm-per-call",
        type=float,
        default=0.0,
        help="per-explanation latency (used when --llm-seconds is 0)",
    )
    parser.add_argument(
        "--llm-calls",
        type=int,
        default=0,
        help="serial explanations per review (mistakes/blunders)",
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
    llm_seconds = args.llm_seconds
    llm_note = "explicit"
    if llm_seconds == 0.0 and args.llm_per_call > 0 and args.llm_calls > 0:
        llm_seconds = args.llm_per_call * args.llm_calls
        llm_note = f"{args.llm_calls} x {args.llm_per_call:.1f}s"
    engine_budget = (args.ceiling - llm_seconds) / args.headroom
    print(
        f"nps={args.nps:,.0f}  plies={args.plies} (deterministic cap)  "
        f"ceiling={args.ceiling:.0f}s  headroom={args.headroom:.1f}x  "
        f"llm={llm_seconds:.0f}s ({llm_note})  "
        f"engine budget={engine_budget:.0f}s"
    )
    print(
        f"{'N':>10} {'per-eval':>10} {'engine':>9} {'x budget':>9} "
        f"{'max plies':>10} fits"
    )
    for nodes in args.nodes:
        per_eval = nodes / args.nps
        engine_time = (args.plies + 1) * per_eval
        ratio = engine_time / engine_budget if engine_budget > 0 else float("inf")
        max_plies = math.floor(engine_budget / per_eval) - 1 if per_eval > 0 else 0
        print(
            f"{nodes:>10,} {per_eval:>9.2f}s {engine_time:>8.1f}s "
            f"{ratio:>8.2f}x {max_plies:>10} "
            f"{'yes' if engine_time <= engine_budget else 'NO'}"
        )
    max_per_eval = engine_budget / max(1, args.plies + 1)
    print(
        f"max per-eval at {args.plies} plies with {args.headroom:.1f}x headroom: "
        f"{max_per_eval:.2f}s -> max N {max_per_eval * args.nps:,.0f}"
    )
    print(
        "options: (a) lower N to the table's max; (b) chunked review (split the "
        "PGN, stream partial rows); (c) bigger container (more nps). "
        "See docs/review-latency-options.md."
    )


if __name__ == "__main__":
    main()
