#!/usr/bin/env python
"""Build a rating-banded human neutral set from a Lichess database dump.

Streams a (possibly Range-truncated) .pgn.zst prefix, keeps the first
--per-band games per rating band, and freezes one capped file. Bands are
assigned by BOTH players' ratings; only rapid or longer blitz (TimeControl
base >= 180s), at least --min-plies mainline plies.

Database dumps carry no title headers, so BOT exclusion is verified in a
second pass: every distinct player in the buffered candidates is looked up
in bulk (POST /api/users) and games involving BOT-titled accounts -- or
accounts the API no longer returns -- are dropped and backfilled from the
stream. Fails loudly if any band stays unfilled within --max-scan games.

Usage:
  venv/bin/python scripts/build_banded_neutral.py \
      --zst /tmp/opencode/lichess_2026-08.part.zst --month 2026-08 \
      --per-band 40 --out data/gate_set_B.neutral.bands.pgn
"""
import argparse
import hashlib
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

import chess.pgn

try:
    import zstandard
except ImportError:
    zstandard = None

REPO_ROOT = Path(__file__).resolve().parents[1]

BANDS = ((800, 1200), (1200, 1600), (1600, 2000))


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zst", required=True, help="Dump prefix (.pgn.zst).")
    parser.add_argument("--month", required=True, help="Dump month label.")
    parser.add_argument("--per-band", type=int, default=40)
    parser.add_argument("--min-plies", type=int, default=20)
    parser.add_argument("--min-base-seconds", type=int, default=180)
    parser.add_argument("--max-scan", type=int, default=1_000_000)
    parser.add_argument(
        "--out",
        default=str(REPO_ROOT / "data" / "gate_set_B.neutral.bands.pgn"),
    )
    return parser.parse_args()


def base_seconds(timecontrol: str):
    """Base clock seconds from a 'base+inc' TimeControl header."""
    match = re.fullmatch(r"\s*(\d+)\s*\+\s*(\d+)\s*", timecontrol or "")
    if not match:
        return None
    return int(match.group(1))


def band_of(white_elo, black_elo):
    try:
        ratings = (int(white_elo), int(black_elo))
    except (TypeError, ValueError):
        return None
    for low, high in BANDS:
        if low <= ratings[0] < high and low <= ratings[1] < high:
            return f"{low}-{high}"
    return None


def involves_bot(headers) -> bool:
    """True when either side carries a BOT title (API-export headers)."""
    for side in ("White", "Black"):
        if (headers.get(f"{side}Title") or "").strip().lower() == "bot":
            return True
    return False


def fetch_titles(usernames):
    """Bulk Lichess user lookup -> {lowercase name: title or None}.

    Database dumps carry no title headers, so BOT exclusion is verified
    against the API instead. One POST handles ~100 ids; missing accounts
    come back absent from the response.
    """
    names = sorted({name.lower() for name in usernames})
    titles = {}
    for offset in range(0, len(names), 100):
        chunk = ",".join(names[offset : offset + 100])
        request = urllib.request.Request(
            "https://lichess.org/api/users",
            data=chunk.encode("utf-8"),
            headers={"Content-Type": "text/plain", "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            for user in json.loads(response.read().decode("utf-8")):
                titles[user["id"]] = user.get("title")
    return titles


TAG_RE = re.compile(r'^\[(\w+) "(.*)"\]\s*$')
MOVE_NO_RE = re.compile(r"(?:^|\s)(\d+)\.(?!\.)")
RESULT_RE = re.compile(r"\s*(1-0|0-1|1/2-1/2|\*)\s*$")


def scan_blocks(path):
    """Yield (headers, movetext) per game using fast line scanning.

    Header-only prefiltering avoids full move parsing for the ~95% of games
    outside the bands; kept candidates are validated with chess.pgn later.
    """
    if zstandard is None:
        raise SystemExit("zstandard module is missing (venv: pip install zstandard)")
    decompressor = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh:
        reader = decompressor.stream_reader(fh)
        text = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
        headers: dict = {}
        movetext: list = []
        in_tags = True
        for line in text:
            stripped = line.strip()
            if in_tags:
                if not stripped:
                    in_tags = False
                elif stripped.startswith("["):
                    match = TAG_RE.match(stripped)
                    if match:
                        headers[match.group(1)] = match.group(2)
                continue
            if not stripped:
                if headers or movetext:
                    yield headers, " ".join(movetext)
                headers, movetext, in_tags = {}, [], True
                continue
            if stripped.startswith("["):
                # Missing blank line: previous game had no movetext.
                if headers or movetext:
                    yield headers, " ".join(movetext)
                headers, movetext, in_tags = {}, [], True
                match = TAG_RE.match(stripped)
                if match:
                    headers[match.group(1)] = match.group(2)
                continue
            movetext.append(stripped)
        if headers or movetext:
            yield headers, " ".join(movetext)


def estimate_plies(movetext: str):
    """Mainline plies from move numbers; exact on clean dump movetext."""
    numbers = [int(m.group(1)) for m in MOVE_NO_RE.finditer(movetext)]
    if not numbers:
        return 0
    white_moves = numbers[-1]
    body = RESULT_RE.sub("", movetext).rstrip()
    if body.endswith("..."):
        return white_moves * 2
    tail = re.search(r"(\d+)\.\s*(\S+)(?:\s+(\S+))?\s*$", body)
    if tail and int(tail.group(1)) == white_moves:
        return white_moves * 2 if tail.group(3) else white_moves * 2 - 1
    return white_moves * 2


def main():
    args = parse_args()
    wanted = {f"{low}-{high}": args.per_band for low, high in BANDS}
    buffer = {key: [] for key in wanted}
    buf_cap = args.per_band * 2  # slack for BOT/unverified drops
    scanned = short_dropped = fast_dropped = 0

    for headers, movetext in scan_blocks(args.zst):
        scanned += 1
        if scanned > args.max_scan:
            break
        if involves_bot(headers):
            continue
        if (headers.get("Variant", "Standard") != "Standard"
                or headers.get("Event", "").lower().startswith("casual")):
            fast_dropped += 1
            continue
        base = base_seconds(headers.get("TimeControl", ""))
        if base is None or base < args.min_base_seconds:
            fast_dropped += 1
            continue
        key = band_of(headers.get("WhiteElo"), headers.get("BlackElo"))
        if key is None or len(buffer[key]) >= buf_cap:
            continue
        if estimate_plies(movetext) < args.min_plies:
            short_dropped += 1
            continue
        buffer[key].append(
            (headers.get("White", ""), headers.get("Black", ""), dict(headers), movetext)
        )
        if scanned % 500 == 0 and all(
            len(buffer[band]) >= wanted[band] for band in wanted
        ):
            break

    players = {name for games in buffer.values() for entry in games for name in entry[:2]}
    try:
        titles = fetch_titles(players)
    except Exception as exc:
        raise SystemExit(f"bulk title lookup failed; refusing to freeze an "
                         f"unverified file: {exc}") from exc
    kept = {}
    bot_dropped = unverified_dropped = invalid_dropped = 0
    for key in sorted(wanted):
        clean = []
        for white, black, headers, movetext in buffer[key]:
            pair = {white.lower(), black.lower()}
            if not pair <= set(titles):
                unverified_dropped += 1
                continue
            if any((titles[name] or "").upper() == "BOT" for name in pair):
                bot_dropped += 1
                continue
            block = "".join(f'[{tag} "{headers[tag]}"]\n' for tag in headers)
            game = chess.pgn.read_game(io.StringIO(block + "\n" + movetext + "\n"))
            if game is None or sum(1 for _ in game.mainline_moves()) < args.min_plies:
                invalid_dropped += 1
                continue
            clean.append(str(game))
        kept[key] = clean[: args.per_band]

    unfilled = {key: len(kept[key]) for key in wanted if len(kept[key]) < wanted[key]}
    if unfilled:
        raise SystemExit(
            f"bands unfilled within max-scan {args.max_scan} "
            f"(scanned={scanned}): {unfilled}; fetch a larger prefix"
        )

    ordered = [pgn for key in sorted(wanted) for pgn in kept[key]]
    out = Path(args.out)
    with open(out, "w", encoding="utf-8") as fh:
        for pgn in ordered:
            fh.write(pgn.strip())
            fh.write("\n\n")
    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    sidecar = {
        "source": f"lichess database dump {args.month} (prefix, stream order)",
        "bands": {key: len(kept[key]) for key in sorted(wanted)},
        "games": len(ordered),
        "scanned": scanned,
        "bot_check": "bulk POST /api/users titles; BOT-titled or API-absent accounts dropped; kept games re-parsed with chess.pgn",
        "bot_dropped": bot_dropped,
        "unverified_dropped": unverified_dropped,
        "invalid_dropped": invalid_dropped,
        "short_dropped": short_dropped,
        "other_dropped": fast_dropped,
        "min_plies": args.min_plies,
        "min_base_seconds": args.min_base_seconds,
        "file": str(out),
        "sha256": digest,
    }
    Path(str(out) + ".json").write_text(json.dumps(sidecar, indent=2))
    print(f"scanned={scanned}")
    print(f"bands={sidecar['bands']} bot_dropped={bot_dropped} "
          f"unverified_dropped={unverified_dropped} short_dropped={short_dropped}")
    print(f"wrote {out} sha256={digest}")


if __name__ == "__main__":
    main()
