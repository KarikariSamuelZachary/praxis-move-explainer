#!/usr/bin/env python
"""
Build-time Syzygy tablebase pre-warm (Endgame Trainer).

Downloads the Syzygy files the trainer needs into data/syzygy/regular/ and
validates them by probing known positions. Mirrors scripts/prewarm_maia3.py:
a deploy build that cannot reach any mirror fails here, instead of the app
failing at first drill.

Coverage: the FULL 3-, 4- and 5-man sets (145 material configurations,
~940 MB on disk) -- the material of every sourced endgame position whose
drill start is <=5 men, including capture/promotion transitions. 6- and
7-man positions (~149 GB and ~16 TB respectively) are deliberately NOT
baked into the image; they are served by the Lichess API fallback in
services/tablebase.py.

Idempotent: files already present are not re-downloaded, and the whole run
is skipped when everything is present and valid. Each file is tried against
every mirror (Lichess first, sesse second) because either mirror can be
down; downloads carry a 30s socket timeout so a stalled connection fails
over instead of hanging the build, and the build fails only when no mirror
can supply a file. Every file's magic header is checked, so an error page
or truncated download can never be baked into the image.
"""
import logging
import sys
import time
import urllib.request
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# Per-file mirror URL templates, tried in order. Lichess splits WDL and DTZ
# into sibling directories; sesse hosts both in one directory.
MIRRORS = (
    "https://tablebase.lichess.ovh/tables/standard/{kind}/{name}",
    "http://tablebase.sesse.net/syzygy/3-4-5/{name}",
)

TABLEBASE_DIR = ROOT_DIR / "data" / "syzygy" / "regular"

# Standard-chess Syzygy magic headers (first 4 bytes of every file).
_MAGIC = {
    ".rtbw": b"\x71\xe8\x23\x5d",
    ".rtbz": b"\xd7\x66\x0c\xa5",
}

# All 145 material configurations of the published 3-4-5 Syzygy set: both
# kings plus 1-3 extra pieces drawn (with repetition) from {Q,R,B,N,P} and
# assigned to either side, canonicalized so mirrored sides collapse to one
# name. This generates exactly the mirrored 3-4-5 file set (verified against
# the tablebase.lichess.ovh / tablebase.sesse.net listing); each name is
# downloaded as .rtbw (WDL) and .rtbz (DTZ).
_PIECE_TYPES = "QRBNP"
_RANK = "KQRBNP"


def _material_signatures():
    import itertools

    def strength(side: str):
        # Published naming orders the side with more pieces first, then the
        # stronger piece (K > Q > R > B > N > P). Rank indices grow weaker,
        # so negate them and sort (len, negated) descending.
        return len(side), [-_RANK.index(p) for p in side]

    names = set()
    for extra in range(1, 4):
        for combo in itertools.combinations_with_replacement(_PIECE_TYPES, extra):
            for side in itertools.product((0, 1), repeat=extra):
                white = "K" + "".join(
                    sorted(
                        (p for p, s in zip(combo, side) if s == 0),
                        key=_PIECE_TYPES.index,
                    )
                )
                black = "K" + "".join(
                    sorted(
                        (p for p, s in zip(combo, side) if s == 1),
                        key=_PIECE_TYPES.index,
                    )
                )
                names.add("v".join(sorted([white, black], key=strength, reverse=True)))
    return sorted(names)


FILES = [
    f"{name}{ext}"
    for name in _material_signatures()
    for ext in (".rtbw", ".rtbz")
]

# (validation fen, expected WDL) -- probed AFTER all files are present.
VALIDATION = [
    ("1K1k4/1P6/8/8/8/8/r7/5R2 w - - 0 1", 2),   # b-file Lucena, WTM: win
    ("K1k5/P7/8/8/8/8/1r6/7R w - - 0 1", 0),     # rook-pawn anti-Lucena: draw
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _mirror_kind(name: str) -> str:
    return "3-4-5-wdl" if name.endswith(".rtbw") else "3-4-5-dtz"


# A stalled mirror must fail the attempt, not hang the build forever:
# urllib's urlretrieve has NO timeout, and a silently dead HTTPS connection
# once froze a real image build for 20+ minutes. urlopen's timeout applies
# to connect and to every blocking read.
_DOWNLOAD_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_CHUNK = 1 << 20


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        with open(target, "wb") as out:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)


def fetch(name: str, target: Path):
    """Download one file, trying every mirror; returns the serving URL or None."""
    for attempt in (1, 2, 3):
        for pattern in MIRRORS:
            url = pattern.format(kind=_mirror_kind(name), name=name)
            try:
                _download(url, target)
                return url
            except Exception as exc:
                target.unlink(missing_ok=True)
                log.warning("mirror failed for %s (%s): %s", name, url, exc)
        time.sleep(5 * attempt)
    return None


def validate(directory: Path) -> bool:
    """Magic-check every file, then probe the two reference positions."""
    import chess
    import chess.syzygy

    for path in sorted(directory.glob("*.rtb*")):
        expected = _MAGIC.get(path.suffix)
        if expected is None:
            continue
        with open(path, "rb") as fh:
            header = fh.read(4)
        if header != expected:
            log.error("invalid magic header in %s: %r", path, header)
            return False

    try:
        tablebase = chess.syzygy.open_tablebase(str(directory))
        for fen, expected_wdl in VALIDATION:
            wdl = tablebase.probe_wdl(chess.Board(fen))
            if wdl != expected_wdl:
                log.error("Probe mismatch for %s: wdl=%s expected %s", fen, wdl, expected_wdl)
                tablebase.close()
                return False
        tablebase.close()
        return True
    except Exception:
        log.exception("Tablebase validation failed for %s", directory)
        return False


def main() -> int:
    TABLEBASE_DIR.mkdir(parents=True, exist_ok=True)

    missing = [name for name in FILES if not (TABLEBASE_DIR / name).exists()]
    if not missing and validate(TABLEBASE_DIR):
        log.info("Syzygy files already present and valid in %s", TABLEBASE_DIR)
        return 0

    for name in missing:
        target = TABLEBASE_DIR / name
        log.info("Fetching %s", name)
        url = fetch(name, target)
        if url is None:
            log.error(
                "Syzygy pre-warm failed for %s: no mirror could supply it. "
                "Check outbound access to tablebase.lichess.ovh / "
                "tablebase.sesse.net from the build host.",
                name,
            )
            return 1
        log.info("Saved %s from %s", target, url)

    if not validate(TABLEBASE_DIR):
        log.error(
            "Syzygy pre-warm downloaded files but validation failed; refusing "
            "to bake them into the image."
        )
        return 1

    log.info("Syzygy pre-warm complete and validated (%d files).", len(FILES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
