#!/usr/bin/env python
"""
Build-time Syzygy tablebase pre-warm (Endgame Trainer).

Downloads the Syzygy files the trainer needs into data/syzygy/regular/ and
validates them by probing known positions. Mirrors scripts/prewarm_maia3.py:
a deploy build that cannot reach any mirror fails here, instead of the app
failing at first drill.

Coverage: the FULL 3- and 4-man sets plus the 5-man KRPvKR family
(rook-and-pawn vs rook) -- every material the seeded content and its
capture/promotion transitions can reach. ~34 MB on disk. 6- and 7-man
positions are deliberately NOT baked into the image; they are served by the
Lichess API fallback in services/tablebase.py (e.g. the KQRvKR continuation
after a Lucena promotion).

Idempotent: files already present are not re-downloaded, and the whole run
is skipped when everything is present and valid. Each file is tried against
every mirror (Lichess first, sesse second) because either mirror can be
down; the build fails only when no mirror can supply a file. Every file's
magic header is checked, so an error page or truncated download can never
be baked into the image.
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

# All 3-man and 4-man tables, plus the 5-man KRPvKR family (rook-and-pawn
# vs rook) -- the material of every seeded Lucena Position variant.
FILES = [
    "KBBvK.rtbw",
    "KBBvK.rtbz",
    "KBNvK.rtbw",
    "KBNvK.rtbz",
    "KBPvK.rtbw",
    "KBPvK.rtbz",
    "KBvK.rtbw",
    "KBvK.rtbz",
    "KBvKB.rtbw",
    "KBvKB.rtbz",
    "KBvKN.rtbw",
    "KBvKN.rtbz",
    "KBvKP.rtbw",
    "KBvKP.rtbz",
    "KNNvK.rtbw",
    "KNNvK.rtbz",
    "KNPvK.rtbw",
    "KNPvK.rtbz",
    "KNvK.rtbw",
    "KNvK.rtbz",
    "KNvKN.rtbw",
    "KNvKN.rtbz",
    "KNvKP.rtbw",
    "KNvKP.rtbz",
    "KPPvK.rtbw",
    "KPPvK.rtbz",
    "KPvK.rtbw",
    "KPvK.rtbz",
    "KPvKP.rtbw",
    "KPvKP.rtbz",
    "KQBvK.rtbw",
    "KQBvK.rtbz",
    "KQNvK.rtbw",
    "KQNvK.rtbz",
    "KQPvK.rtbw",
    "KQPvK.rtbz",
    "KQQvK.rtbw",
    "KQQvK.rtbz",
    "KQRvK.rtbw",
    "KQRvK.rtbz",
    "KQvK.rtbw",
    "KQvK.rtbz",
    "KQvKB.rtbw",
    "KQvKB.rtbz",
    "KQvKN.rtbw",
    "KQvKN.rtbz",
    "KQvKP.rtbw",
    "KQvKP.rtbz",
    "KQvKQ.rtbw",
    "KQvKQ.rtbz",
    "KQvKR.rtbw",
    "KQvKR.rtbz",
    "KRBvK.rtbw",
    "KRBvK.rtbz",
    "KRNvK.rtbw",
    "KRNvK.rtbz",
    "KRPvK.rtbw",
    "KRPvK.rtbz",
    "KRPvKR.rtbw",
    "KRPvKR.rtbz",
    "KRRvK.rtbw",
    "KRRvK.rtbz",
    "KRvK.rtbw",
    "KRvK.rtbz",
    "KRvKB.rtbw",
    "KRvKB.rtbz",
    "KRvKN.rtbw",
    "KRvKN.rtbz",
    "KRvKP.rtbw",
    "KRvKP.rtbz",
    "KRvKR.rtbw",
    "KRvKR.rtbz",
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


def fetch(name: str, target: Path):
    """Download one file, trying every mirror; returns the serving URL or None."""
    for attempt in (1, 2, 3):
        for pattern in MIRRORS:
            url = pattern.format(kind=_mirror_kind(name), name=name)
            try:
                urllib.request.urlretrieve(url, target)
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
