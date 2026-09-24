#!/usr/bin/env python
"""
Build-time Stockfish pre-warm (game review, sparring, endgame fallback).

Installs the official Stockfish 19 Linux x86-64 release binary into the base
image and points STOCKFISH_PATH at it. Why not apt: Debian trixie (the base
image's distro) ships stockfish 17-1, and distro packages trail upstream
releases by years -- there is no apt path to 19. Why the official binary:
Stockfish 19 ships a single "universal" build that selects the best
instruction set (AVX2/BMI2/AVX-512/...) at runtime, so no per-CPU variant
choice is baked into the image.

Supply-chain pinning: the download URL is pinned to the immutable `sf_19`
tag and verified against a recorded SHA-256 before extraction; the extracted
binary is verified again before install. If the upstream asset were ever
replaced, the build fails here instead of silently baking an unverified
engine. The GPLv3 license text ships next to the binary.

Idempotent: a destination binary whose SHA-256 already matches is left in
place (no download), so repeated local builds are cheap. Mirrors
scripts/prewarm_syzygy.py / prewarm_maia3.py: a build that cannot reach
GitHub fails here instead of the app failing at first review.

Usage (build):
    python scripts/prewarm_stockfish.py

Usage (local dev parity -- then set STOCKFISH_PATH in .env):
    python scripts/prewarm_stockfish.py --dest ~/.local/opt/stockfish
"""
import argparse
import hashlib
import logging
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

RELEASE_TAG = "sf_19"
VERSION = "19"
ASSET = "stockfish-linux-x86-64-universal.tar.gz"
URL = (
    "https://github.com/official-stockfish/Stockfish/releases/download/"
    f"{RELEASE_TAG}/{ASSET}"
)
# SHA-256 recorded 2026-09-23 from the official release asset. The tarball
# hash guards the download; the binary hash guards extraction (a tarball
# with the right outer hash but a swapped member cannot pass both).
TARBALL_SHA256 = "9defc0d4e55d49c65a6d042f3e571a39fcea499ade6dbe741b53b8c65e03611f"
MEMBER = "stockfish/stockfish-linux-x86-64-universal"
BINARY_SHA256 = "0f83d24cc46d2c66c60f16001af5444873bc112b7d028594513426894c12da19"
LICENSE_MEMBER = "stockfish/Copying.txt"

DEFAULT_DEST = Path("/opt/stockfish")

_DOWNLOAD_TIMEOUT_SECONDS = 30.0
_DOWNLOAD_CHUNK = 1 << 20

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_DOWNLOAD_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
        with open(target, "wb") as out:
            while True:
                chunk = response.read(_DOWNLOAD_CHUNK)
                if not chunk:
                    break
                out.write(chunk)


def _verify_version(binary: Path) -> str:
    """Run the installed engine once and return its advertised name."""
    proc = subprocess.run(
        [str(binary)],
        input=b"uci\nquit\n",
        capture_output=True,
        timeout=60,
    )
    for line in proc.stdout.decode(errors="replace").splitlines():
        if line.startswith("id name "):
            name = line[len("id name "):].strip()
            if f"Stockfish {VERSION}" not in name:
                raise RuntimeError(
                    f"installed binary reports {name!r}, expected Stockfish {VERSION}"
                )
            return name
    raise RuntimeError(
        f"engine produced no 'id name' line (exit {proc.returncode}); "
        f"stderr: {proc.stderr.decode(errors='replace')[:500]}"
    )


def install(dest: Path, check: bool = True) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    binary = dest / "stockfish"

    if binary.exists() and _sha256(binary) == BINARY_SHA256:
        log.info("Stockfish %s already present and verified: %s", VERSION, binary)
        if check:
            log.info("engine check: %s", _verify_version(binary))
        return binary

    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / ASSET
        log.info("downloading %s", URL)
        _download(URL, tarball)

        actual = _sha256(tarball)
        if actual != TARBALL_SHA256:
            raise RuntimeError(
                f"tarball SHA-256 mismatch for {URL}\n"
                f"  expected {TARBALL_SHA256}\n  got      {actual}\n"
                "The upstream asset changed; verify it before updating the pin."
            )
        log.info("tarball SHA-256 verified")

        with tarfile.open(tarball, "r:gz") as tar:
            try:
                member = tar.getmember(MEMBER)
            except KeyError as exc:
                raise RuntimeError(f"{MEMBER} not found in {ASSET}") from exc
            with tar.extractfile(member) as src, open(binary, "wb") as out:
                shutil.copyfileobj(src, out)

            license_file = dest / "Copying.txt"
            with tar.extractfile(LICENSE_MEMBER) as src, open(license_file, "wb") as out:
                shutil.copyfileobj(src, out)

    binary.chmod(0o755)
    actual = _sha256(binary)
    if actual != BINARY_SHA256:
        binary.unlink(missing_ok=True)
        raise RuntimeError(
            f"binary SHA-256 mismatch after extraction\n"
            f"  expected {BINARY_SHA256}\n  got      {actual}"
        )

    log.info("installed %s (%s)", binary, _verify_version(binary) if check else "check skipped")
    return binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"install directory (default: {DEFAULT_DEST})",
    )
    parser.add_argument(
        "--no-check",
        action="store_true",
        help="skip running the installed binary (use only when cross-building)",
    )
    args = parser.parse_args()
    install(args.dest, check=not args.no_check)
    return 0


if __name__ == "__main__":
    sys.exit(main())
