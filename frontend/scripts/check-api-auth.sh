#!/usr/bin/env bash
# Unauthenticated-request guard for every route handler under
# frontend/src/app/api. Each one must reject anonymous callers with 401
# before parsing, provider spend, backend proxying, or rate limiting.
#
# Backend-only routes (src/routers/*) are not reachable without
# X-Internal-Secret and are covered by the router harnesses instead.
#
# Run against a dev or deployed server:
#   ./scripts/check-api-auth.sh [base-url]   # default http://localhost:3000
set -euo pipefail

BASE="${1:-http://localhost:3000}"
FAIL=0
BODY_FILE="$(mktemp)"
trap 'rm -f "$BODY_FILE"' EXIT

check() {
  local method="$1"
  local path="$2"
  local payload="${3:-}"
  local code

  if [ -n "$payload" ]; then
    code=$(curl -s -o "$BODY_FILE" -w "%{http_code}" --max-time 30 \
      -X "$method" "$BASE$path" \
      -H 'Content-Type: application/json' -d "$payload" || true)
  else
    code=$(curl -s -o "$BODY_FILE" -w "%{http_code}" --max-time 30 \
      -X "$method" "$BASE$path" || true)
  fi

  if [ "$code" = "401" ]; then
    echo "  [PASS] $method $path -> 401"
  else
    echo "  [FAIL] $method $path -> $code (expected 401): $(cat "$BODY_FILE")"
    FAIL=1
  fi
}

echo "=== Checking unauthenticated API access at $BASE ==="

# Review + explanations (provider/engine spend).
check POST /api/analyze '{"pgn":"1. e4 e5"}'
check POST /api/explain '{}'

# Endgames (tablebase/Stockfish + DB).
check POST   /api/endgames/hint '{}'
check POST   /api/endgames/move '{}'
check GET    /api/endgames/next
check POST   /api/endgames/playout/reply '{}'
check GET    /api/endgames/practice/categories
check POST   /api/endgames/practice/move '{}'
check GET    /api/endgames/practice/next
check GET    /api/endgames/recommendation
check POST   /api/endgames/woodpecker/attempts '{}'
check GET    /api/endgames/woodpecker/count
check GET    /api/endgames/woodpecker/queue

# Game imports (external provider fetches).
check GET /api/import/chesscom/test-user
check GET /api/import/lichess/test-user

# Onboarding + user.
check GET  /api/onboarding/skill-level
check POST /api/onboarding/skill-level '{}'
check GET  /api/user/rating

# Puzzles.
check GET  /api/puzzles
check GET  /api/puzzles/test-id
check POST /api/puzzles/rating '{}'

# Repertoires (some hit Lichess Explorer or Stockfish in the backend).
check GET    /api/repertoires
check POST   /api/repertoires '{}'
check GET    /api/repertoires/test-id
check DELETE /api/repertoires/test-id
check GET    /api/repertoires/test-id/gaps
check GET    /api/repertoires/test-id/positions
check POST   /api/repertoires/test-id/positions '{}'
check POST   /api/repertoires/test-id/sessions/start '{}'
check DELETE /api/repertoires/positions/test-id
check POST   /api/repertoires/positions/test-id '{}'
check POST   /api/repertoires/sessions/test-id/complete '{}'
# Include a valid fen so this proves auth runs before query validation.
check GET    "/api/repertoires/suggestions?fen=rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR%20w%20KQkq%20-%200%201"

# Train (Maia/Stockfish/LLM background jobs + heavy DB).
check POST /api/train/engine-sparring-move '{}'
check GET  /api/train/opponent-analysis
check POST /api/train/opponent-import '{}'
check GET  /api/train/opponent-import/test-id
check GET  /api/train/opponent-opening-game
check GET  /api/train/opponent-opening-games
check GET  /api/train/opponent-profile-info
check GET  /api/train/opponents
check POST /api/train/sparring-move '{}'
check POST /api/train/sparring-warmup '{}'

# Woodpecker.
check POST /api/woodpecker/attempts '{}'
check POST /api/woodpecker/entries '{}'
check GET  /api/woodpecker/queue

exit "$FAIL"
