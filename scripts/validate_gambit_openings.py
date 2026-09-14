"""
Standalone validation for src/services/data/gambit_openings.json.

NOT integrated into the persona_reranker test suite (this asset is not wired
into any module yet). Checks, over every entry:
  1. the stored FEN parses via python-chess,
  2. the stored UCI move is legal in that FEN,
  3. the stored SAN matches the UCI move,
  4. end-to-end: replaying the stored pgn line reproduces both the stored
     pre-move FEN and the stored final position (the stored fen+uci must be
     the "one move before" state of the named line, not an invention),
then prints the total count, per-volume breakdown, and a sample of entries
for manual review.
"""

import json
import re
import sys
from pathlib import Path

import chess

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = REPO_ROOT / "src" / "services" / "data" / "gambit_openings.json"


def parse_pgn_moves(pgn: str) -> list:
    sans = []
    for tok in pgn.split():
        if re.fullmatch(r"\d+\.*", tok):
            continue
        if tok in ("*", "1-0", "0-1", "1/2-1/2"):
            continue
        sans.append(tok)
    return sans


def main() -> int:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]
    meta = data["meta"]
    errors = []

    fen_ok = uci_ok = san_ok = epd_ok = pgn_ok = 0
    for i, e in enumerate(entries):
        try:
            board = chess.Board(e["fen"])
            fen_ok += 1
        except ValueError as exc:
            errors.append(f'#{i} {e["name"]}: bad FEN {e["fen"]!r}: {exc}')
            continue
        try:
            move = chess.Move.from_uci(e["uci"])
            if move not in board.legal_moves:
                errors.append(
                    f'#{i} {e["name"]}: UCI {e["uci"]} illegal in {e["fen"]}'
                )
                continue
            uci_ok += 1
        except ValueError as exc:
            errors.append(f'#{i} {e["name"]}: bad UCI {e["uci"]!r}: {exc}')
            continue
        if e["san"] != board.san(move):
            errors.append(
                f'#{i} {e["name"]}: SAN mismatch stored={e["san"]!r} '
                f'computed={board.san(move)!r}'
            )
            continue
        san_ok += 1
        if e["epd"] != board.epd():
            errors.append(
                f'#{i} {e["name"]}: epd mismatch stored={e["epd"]!r} '
                f'computed={board.epd()!r}'
            )
            continue
        epd_ok += 1

        # End-to-end: the stored line must pass through the stored FEN and
        # end after the stored move.
        sans = parse_pgn_moves(e["pgn"])
        try:
            walk = chess.Board()
            for san in sans[:-1]:
                walk.push_san(san)
            if walk.fen() != e["fen"]:
                errors.append(
                    f'#{i} {e["name"]}: pre-move FEN mismatch\n'
                    f'    stored:   {e["fen"]}\n    replayed: {walk.fen()}'
                )
                continue
            if walk.epd() != e["epd"]:
                errors.append(
                    f'#{i} {e["name"]}: pre-move EPD mismatch '
                    f'stored={e["epd"]!r} replayed={walk.epd()!r}'
                )
                continue
            if walk.san(move) != sans[-1]:
                errors.append(
                    f'#{i} {e["name"]}: last SAN mismatch stored={e["san"]!r} '
                    f'line-final={sans[-1]!r}'
                )
                continue
            walk.push(move)
            expected = chess.Board()
            for san in sans:
                expected.push_san(san)
            if walk.fen() != expected.fen():
                errors.append(f'#{i} {e["name"]}: post-move board mismatch')
                continue
            pgn_ok += 1
        except ValueError as exc:
            errors.append(f'#{i} {e["name"]}: pgn replay failed: {exc}')

    print(f"meta: {meta['entry_count']} entries "
          f"({meta['offer_count']} classified gambit offers)")
    print(f"FEN parsed:            {fen_ok}/{len(entries)}")
    print(f"UCI legal in FEN:      {uci_ok}/{len(entries)}")
    print(f"SAN matches UCI:       {san_ok}/{len(entries)}")
    print(f"epd matches FEN:       {epd_ok}/{len(entries)}")
    print(f"pgn line consistent:   {pgn_ok}/{len(entries)}")

    counts = {}
    for e in entries:
        counts[e["eco"][0]] = counts.get(e["eco"][0], 0) + 1
    print("per ECO volume:", dict(sorted(counts.items())))

    print("\n--- sample entries (one per spread of ECO codes) ---")
    picked, seen_prefix = [], set()
    for e in entries:
        if len(picked) >= 15:
            break
        if e["eco"] not in seen_prefix:
            seen_prefix.add(e["eco"])
            picked.append(e)
    for e in picked:
        print(f'{e["eco"]:4} {e["name"]:60} {e["san"]:6} offer={e["offer"]}')
        print(f'     fen={e["fen"]}')
        print(f'     uci={e["uci"]}  pgn={e["pgn"]}')

    if errors:
        print(f"\nFAILED: {len(errors)} validation errors", file=sys.stderr)
        for err in errors[:20]:
            print("  " + err, file=sys.stderr)
        return 1
    print("\nALL ENTRIES PASSED: FEN valid, UCI legal, SAN consistent, "
          "pgn line reproduces stored fen+uci")
    return 0


if __name__ == "__main__":
    sys.exit(main())
