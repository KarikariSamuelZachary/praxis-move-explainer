"""
Chess.com public API integration.

Fetches a user's recent games by walking the monthly archive index
published at https://api.chess.com/pub/player/{username}/games/archives.

The integration uses only Python's standard library (urllib) so it does
not add a new dependency to requirements.txt.
"""
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List

log = logging.getLogger(__name__)

ARCHIVES_URL = "https://api.chess.com/pub/player/{username}/games/archives"

# Chess.com requires a descriptive User-Agent so they can contact you if
# a client misbehaves. Update the contact address to a real inbox or repo
# URL owned by the project before relying on this in production.
USER_AGENT = "PraxisMove/1.0 (contact: praxis.app.dev@gmail.com)"

REQUEST_TIMEOUT_SECONDS = 15


class ChessComError(Exception):
    """Raised when the Chess.com API fails for a non-404 reason."""


class ChessComUserNotFound(ChessComError):
    """Raised when Chess.com returns 404 for the requested username."""


def _http_get_json(url: str) -> Any:
    """Perform a GET request and decode the JSON body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _fetch_archive_urls(username: str) -> List[str]:
    """Return the list of monthly archive URLs, oldest-first."""
    url = ARCHIVES_URL.format(username=username)
    try:
        data = _http_get_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ChessComUserNotFound(
                f"Chess.com username '{username}' not found"
            ) from exc
        raise ChessComError(
            f"Chess.com archives request failed (HTTP {exc.code})"
        ) from exc
    except urllib.error.URLError as exc:
        raise ChessComError(
            f"Unable to reach Chess.com: {exc.reason}"
        ) from exc

    archives = data.get("archives") or []
    return [str(a) for a in archives]


def _derive_game_result(white: Dict[str, Any], black: Dict[str, Any]) -> str:
    """Map the per-player Chess.com result flags to an overall result string."""
    if white.get("result") == "win":
        return "1-0"
    if black.get("result") == "win":
        return "0-1"
    return "1/2-1/2"


def _summarize_game(game: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw Chess.com game object into the summary shape."""
    white = game.get("white") or {}
    black = game.get("black") or {}
    return {
        "url": game.get("url", ""),
        "pgn": game.get("pgn", ""),
        "white": white,
        "black": black,
        "result": _derive_game_result(white, black),
        "end_time": game.get("end_time", 0),
        "time_class": game.get("time_class", ""),
    }


def fetch_recent_chesscom_games(username: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch a user's most recent Chess.com games, most-recent first.

    Walks the monthly archive index in reverse chronological order and stops
    fetching new months as soon as `limit` games have been collected, so no
    more archive months than are necessary are requested.

    Args:
        username: A Chess.com username (case-insensitive, normalized to lower).
        limit: Maximum number of games to return.

    Returns:
        A list of dicts, most recent first, each containing:
        { url, pgn, white, black, result, end_time, time_class }

    Raises:
        ValueError: If `username` is empty/whitespace.
        ChessComUserNotFound: If Chess.com returns 404 for the username.
        ChessComError: For any other upstream failure.
    """
    username = (username or "").strip().lower()
    if not username:
        raise ValueError("username must not be empty")

    archives = _fetch_archive_urls(username)
    if not archives:
        return []

    games: List[Dict[str, Any]] = []
    # Chess.com lists archives oldest-first; most recent is the last entry.
    for archive_url in reversed(archives):
        if len(games) >= limit:
            break
        try:
            month_data = _http_get_json(archive_url)
        except urllib.error.HTTPError as exc:
            log.warning(
                "Chess.com archive fetch failed (HTTP %s): %s",
                exc.code,
                archive_url,
            )
            continue
        except urllib.error.URLError as exc:
            log.warning("Chess.com archive request failed: %s (%s)", archive_url, exc.reason)
            continue

        for game in month_data.get("games", []) or []:
            games.append(_summarize_game(game))

    games.sort(key=lambda g: g.get("end_time") or 0, reverse=True)
    return games[:limit]