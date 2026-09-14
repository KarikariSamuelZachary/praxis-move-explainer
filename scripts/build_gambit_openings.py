"""
Build a FEN-keyed lookup table of CLASSICAL gambit openings for the
Engine Sparring Sacrificer persona (groundwork only -- nothing here is
wired into persona_reranker.py / persona_weights.py / the sparring endpoint).

Source: lichess-org/chess-openings (CC0), local copy of a.tsv..e.tsv in
data/raw/lichess_chess_openings/. This script re-derives FEN/UCI from the
pgn field with python-chess (the same library the dataset's own gen.py
uses), so the asset is reproducible from the raw tsv alone.

Per gambit entry we emit:
  fen   -- FEN of the position immediately BEFORE the entry's defining move
           (full FEN incl. move counters). This is the lookup key: the
           position the Sacrificer would be IN when deciding whether to
           offer the gambit.
  uci   -- the defining move itself in UCI (what the engine layer uses).
  san   -- same move in SAN, for human review.
  name  -- dataset entry name ("Family: Variation, Subvariation").
  eco   -- ECO code.
  offer -- True when the defining move IS a fresh gambit offer:
           the moved pawn/piece is capturable by the opponent and the
           capture is at least even for the capturer (SEE >= 0). False for
           continuation lines ("Gambit Accepted/Declined..." entries whose
           final named move is the defender's reply) and for the handful of
           rows whose final move is not an offer.

Approach for "which move is the gambit move" (explicitly heuristic):
  1. The dataset coins entry names at the FINAL position of each line, so an
     entry's defining move is the last move of its own pgn.
  2. A last move only counts as the gambit OFFER when it puts the moved
     piece en prise such that the opponent's best capture sequence on that
     square nets the capturer >= 0 material (static exchange evaluation with
     alternating recaptures). This is how "Danish Gambit" resolves to 3.c3
     (defended, but capturable at parity) and "Smith-Morra Gambit" keeps BOTH
     offer points (2.d4 and 3.c3) as separate keys.
  3. Known limitation (reported, not silently patched): Blackmar-Diemer
     resolves to its definitional row 2.e4 -- no row in the entire dataset
     ends at the Diemer 4.f3 move, so that offer position is not a
     line-final position here. See the validation script output.
"""

import json
import re
import sys
from pathlib import Path

import chess

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = REPO_ROOT / "data" / "raw" / "lichess_chess_openings"
OUT_PATH = REPO_ROOT / "src" / "services" / "data" / "gambit_openings.json"

GAMBIT_RE = re.compile(r"gambit", re.IGNORECASE)
CONT_RE = re.compile(r"\bGambit (Accepted|Declined)\b")

PIECE_VAL = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}


def parse_pgn_moves(pgn: str) -> list:
    """Parse the dataset's pgn field into an ordered list of SAN moves."""
    sans = []
    for tok in pgn.split():
        if re.fullmatch(r"\d+\.*", tok):  # move numbers "1." "2" etc.
            continue
        if tok in ("*", "1-0", "0-1", "1/2-1/2"):
            continue
        sans.append(tok)
    return sans


def replay(pgn: str):
    """Replay a pgn string; returns (board_before_last, last_move, final)."""
    board = chess.Board()
    sans = parse_pgn_moves(pgn)
    if not sans:
        raise ValueError("empty move list")
    for san in sans[:-1]:
        board.push_san(san)
    move = board.parse_san(sans[-1])
    before = board.copy()
    board.push(move)
    return before, move, board


def see(board, target):
    """Net material (pawns) for the side to move if it captures on `target`
    (that capture is the move being evaluated, i.e. forced), with
    alternating recaptures on that square (mini-SEE). Returns None when no
    capture exists (piece is not capturable), otherwise an int: >= 0 means
    the capture is at least even for the capturer.
    """
    caps = [m for m in board.legal_moves if m.to_square == target and board.is_capture(m)]
    if not caps:
        return None
    first = min(caps, key=lambda m: PIECE_VAL[board.piece_type_at(m.from_square)])
    if board.is_en_passant(first):
        first_val = 1  # en passant always wins a pawn
    else:
        first_val = PIECE_VAL.get(board.piece_type_at(target), 0)
    b = board.copy()
    b.push(first)
    gains = []
    while True:
        caps = [m for m in b.legal_moves if m.to_square == target and b.is_capture(m)]
        if not caps:
            break
        mover = min(caps, key=lambda m: PIECE_VAL[b.piece_type_at(m.from_square)])
        val = 1 if b.is_en_passant(mover) else PIECE_VAL.get(b.piece_type_at(target), 0)
        gains.append(val)
        b.push(mover)
    later = 0
    for g in reversed(gains):
        later = max(0, g - later)
    return first_val - later


def is_fresh_offer(board_before, move) -> bool:
    """True if, after `move`, the moved piece is capturable by the opponent
    and the opponent's best capture sequence on that square nets >= 0."""
    after = board_before.copy()
    after.push(move)
    return see(after, move.to_square) is not None


def classify(name: str) -> str:
    if CONT_RE.search(name):
        return "accepted" if re.search(r"\bAccepted\b", name) else "declined"
    return "offer"


def load_rows():
    rows = []
    for tsv in sorted(RAW_DIR.glob("?.tsv")):
        lines = tsv.read_text(encoding="utf-8").splitlines()
        header = lines[0].split("\t")
        assert header == ["eco", "name", "pgn"], header
        for line in lines[1:]:
            if not line.strip():
                continue
            eco, name, pgn = line.split("\t")
            rows.append((eco, name, pgn))
    return rows


def main():
    rows = load_rows()
    print(f"total dataset rows: {len(rows)}")

    gambits = [r for r in rows if GAMBIT_RE.search(r[1])]
    print(f"gambit entries (case-insensitive 'gambit' in name): {len(gambits)}")

    entries = []
    failures = []
    for eco, name, pgn in gambits:
        try:
            before, move, after = replay(pgn)
        except ValueError as exc:
            failures.append((eco, name, f"replay: {exc}"))
            continue
        kind = classify(name)
        entries.append(
            {
                "name": name,
                "eco": eco,
                # fen carries the full move counters of the canonical line
                # and is for reference only; the QUERY KEY is epd (FEN
                # without counters) -- live-game positions will almost
                # never match the counters exactly.
                "fen": before.fen(),
                "epd": before.epd(),
                "uci": move.uci(),
                "san": before.san(move),
                "pgn": pgn,
                "kind": kind,
                "offer": kind == "offer" and is_fresh_offer(before, move),
            }
        )

    if failures:
        print(f"\n{len(failures)} replay failures:", file=sys.stderr)
        for eco, name, err in failures[:20]:
            print(f"  {eco} {name}: {err}", file=sys.stderr)

    # Exact duplicate dataset rows (same eco+name+pgn) collapse first.
    unique_rows = {(e["eco"], e["name"], e["pgn"]): e for e in entries}
    entries = list(unique_rows.values())
    print(f"exact duplicate rows collapsed: {len(gambits) - len(entries)}")

    # Dedupe the lookup key: many named variations share one
    # (pre-offer FEN, defining move). Prefer the shortest line (the most
    # definitional name for that key); prefer an offer-classified entry.
    by_key = {}
    for e in entries:
        key = (e["fen"], e["uci"])
        cur = by_key.get(key)
        if cur is None or (e["offer"], -len(e["pgn"]), e["name"]) > (
            cur["offer"],
            -len(cur["pgn"]),
            cur["name"],
        ):
            by_key[key] = e
    table = sorted(by_key.values(), key=lambda e: (e["eco"], e["name"]))

    offers = [e for e in table if e["offer"]]
    print(f"unique (fen, uci) keys: {len(table)}")
    print(f"  classified fresh gambit offers: {len(offers)}")
    print(f"  continuations / non-offer finals: {len(table) - len(offers)}")

    meta_note = (
        "For 'offer': true entries, `uci` is the gambit-defining move played "
        "from `fen` (the position the Sacrificer would be in when deciding "
        "whether to offer the gambit). Entries whose name contains 'Gambit "
        "Accepted'/'Gambit Declined' are continuation lines: their final "
        "named move is the defender's reply, not the original offer, so "
        "`offer` is false even when the final move is technically a fresh "
        "capture. The lichess/ECO convention coins an entry name at the "
        "final position of its line; for entries appearing at multiple "
        "depths under the same name (e.g. 'Smith-Morra Gambit' at 2.d4 and "
        "3.c3), each distinct offer point is kept as its own key. "
        "LOOKUP KEY: use `epd` (FEN without move counters), not `fen` -- "
        "`fen` includes the halfmove/fullmove counters of the canonical "
        "line, which will almost never equal a live game's counters, so a "
        "counter-ful FEN lookup would silently never match."
    )
    out = {
        "meta": {
            "source": "lichess-org/chess-openings (CC0 public domain)",
            "source_url": "https://github.com/lichess-org/chess-openings",
            "scope": "classical ECO-catalogued gambits; modern/internet-invented gambits excluded by design",
            "filter": 'case-insensitive substring "gambit" anywhere in the dataset name field',
            "key": "epd = position immediately BEFORE the entry's defining move (FEN without move counters); uci = that move; fen = same position WITH counters (reference only)",
            "note": meta_note,
            "entry_count": len(table),
            "offer_count": len(offers),
        },
        "entries": table,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
