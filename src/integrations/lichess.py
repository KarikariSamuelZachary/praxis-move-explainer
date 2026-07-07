"""
Lichess public API integration.

Fetches a user's recent games from the Lichess game export endpoint
https://lichess.org/api/games/user/{username} which streams NDJSON
(one JSON game object per line) when called with
`Accept: application/x-ndjson` and `pgnInJson=true`.

The integration uses only Python's standard library (urllib) so it does
not add a new dependency to requirements.txt.

The returned dict shape { url, pgn, white, black, result, end_time,
time_class } matches src/integrations/chess_com.py so downstream callers
(e.g. a future shared game picker) can treat the two providers uniformly.
"""
import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List

log = logging.getLogger(__name__)

GAMES_URL = "https://lichess.org/api/games/user/{username}"

# Lichess does not strictly require a User-Agent but requests one for good
# API citizenship; keep the same style as the Chess.com integration.
USER_AGENT = "PraxisMove/1.0 (contact: praxis.app.dev@gmail.com)"

REQUEST_TIMEOUT_SECONDS = 30

# Lichess timestamps (createdAt / lastMoveAt) are Unix milliseconds. To
# match Chess.com's end_time (Unix seconds) we divide by 1000.
MILLIS_PER_SECOND = 1000


class LichessError(Exception):
    """Raised when the Lichess API fails for a non-404 / non-429 reason."""


class LichessUserNotFound(LichessError):
    """Raised when Lichess returns 404 for the requested username."""


class LichessRateLimited(LichessError):
    """Raised when Lichess returns 429. Caller should wait ~60s and retry."""


def _http_get_ndjson(url: str, username: str = "") -> List[Dict[str, Any]]:
    """
    Perform a GET request and parse the body as NDJSON.

    Returns a list of dicts, one per non-blank line. Lines that fail to
    parse as JSON are skipped with a warning so a single malformed record
    cannot poison an otherwise successful stream.

    `username` is only used to produce a clearer 404 error message.
    """
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/x-ndjson",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # Lichess returns 404 for unknown usernames. The body differs
            # depending on which route handled the request — sometimes an
            # HTML error page, sometimes `{"error":"Not found"}` JSON — so
            # we key off the status code only.
            label = username or "requested user"
            raise LichessUserNotFound(
                f"Lichess username '{label}' not found"
            ) from exc
        if exc.code == 429:
            raise LichessRateLimited(
                "Lichess rate limit reached. Wait about a minute before retrying."
            ) from exc
        raise LichessError(
            f"Lichess games request failed (HTTP {exc.code})"
        ) from exc
    except urllib.error.URLError as exc:
        raise LichessError(
            f"Unable to reach Lichess: {exc.reason}"
        ) from exc

    records: List[Dict[str, Any]] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            log.warning("Skipping non-JSON line in Lichess NDJSON stream: %r", line[:200])
    return records


def _summarize_player(player: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a Lichess player object to { username, rating } (Chess.com-ish shape)."""
    user = player.get("user") or {}
    rating = player.get("rating")
    return {
        "username": user.get("name", ""),
        "rating": rating if isinstance(rating, int) else 0,
    }


def _derive_game_result(game: Dict[str, Any]) -> str:
    """
    Derive '1-0' / '0-1' / '1/2-1/2' / 'aborted' from the game object.

    The `winner` field drives decisive results. When there is no winner,
    we check `status` before falling back to a draw: aborted or no-start
    games are reported as "aborted", not as "1/2-1/2", so a genuine draw
    is never conflated with a game that was never completed.
    """
    winner = game.get("winner")
    if winner == "white":
        return "1-0"
    if winner == "black":
        return "0-1"

    status = game.get("status")
    if status in ("aborted", "noStart"):
        return "aborted"

    return "1/2-1/2"


def _summarize_game(game: Dict[str, Any]) -> Dict[str, Any]:
    """Project a raw Lichess game object into the shared summary shape."""
    players = game.get("players") or {}
    white = players.get("white") or {}
    black = players.get("black") or {}

    end_time_ms = game.get("lastMoveAt") or game.get("createdAt") or 0
    end_time_seconds = int(end_time_ms // MILLIS_PER_SECOND) if end_time_ms else 0

    return {
        "url": f"https://lichess.org/{game.get('id', '')}",
        "pgn": game.get("pgn", ""),
        "white": _summarize_player(white),
        "black": _summarize_player(black),
        "result": _derive_game_result(game),
        "end_time": end_time_seconds,
        "time_class": game.get("speed", ""),
    }


def fetch_recent_lichess_games(username: str, limit: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch a user's most recent Lichess games, most-recent first.

    Calls the Lichess game export endpoint with `pgnInJson=true` so each
    streamed game includes its PGN, and `max={limit}` to cap the number of
    games returned upstream. The response is parsed as NDJSON (one JSON
    object per line), not as a single JSON array.

    Args:
        username: A Lichess username (case-sensitive on Lichess — preserved as-is).
        limit: Maximum number of games to return.

    Returns:
        A list of dicts, most recent first, each containing:
        { url, pgn, white, black, result, end_time, time_class }

    Raises:
        ValueError: If `username` is empty/whitespace.
        LichessUserNotFound: If Lichess returns 404 for the username.
        LichessRateLimited: If Lichess returns 429 (caller should wait and retry).
        LichessError: For any other upstream failure.
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")

    url = "{base}?max={limit}&pgnInJson=true".format(
        base=GAMES_URL.format(username=username),
        limit=limit,
    )

    records = _http_get_ndjson(url, username=username)
    games = [_summarize_game(rec) for rec in records]

    games.sort(key=lambda g: g.get("end_time") or 0, reverse=True)
    return games[:limit]