#!/usr/bin/env python
"""
Build-time Maia-3 checkpoint pre-warm.

This intentionally performs a throwaway inference, rather than only downloading
the file, so the deploy build fails if Hugging Face is unreachable or the UCI
entrypoint cannot load the 5M checkpoint.
"""
import logging
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from engines.maia_engine import prewarm_maia3_model  # noqa: E402


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def main() -> int:
    if prewarm_maia3_model():
        return 0

    logging.error(
        "Maia-3 pre-warm failed. Confirm the deploy build environment has "
        "outbound HTTPS access to huggingface.co and can persist its cache."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
