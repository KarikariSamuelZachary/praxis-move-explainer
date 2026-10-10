"""Offline pilot: extract single-move puzzles from the user's own games.

NO DB writes, no migrations, no routers. Reads games via the existing
read-only provider integrations (Lichess fetched pilot-side with
``clocks=true`` so ``[%clk]`` is present; ``src/integrations`` untouched),
screens with the Review per-ply path on a PRIVATE engine, verifies with a
nodes-only fresh-token engine (calc_probe parity), and writes a local
SQLite file + hand-review sheet.

Engine: the SF19 binary from calc_probe via STOCKFISH_PATH (or --stockfish).
Path, version and sha256 are recorded in the ``runs`` table; any other
version refuses unless --allow-other-engine.

Usage:
    STOCKFISH_PATH=/path/to/sf19 python scripts/puzzle_extract_pilot/extract.py \
        --lichess <user> --chesscom <user> --max-games 50 \
        --out extract_pilot.sqlite --sheet review_sheet.md
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import chess

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "src"))

from screen import screen_game  # noqa: E402
from store import connect, write_sheet  # noqa: E402
from verify import Stage2Engine, mate_to_cp_equiv  # noqa: E402

EP_GRID = (0.10, 0.15, 0.20, 0.25)
MARGIN_GRID = (0.05, 0.10, 0.15, 0.20)
CLOCK_FRAC_GRID = (0.05, 0.10, 0.15)
CLOCK_FIXED_S = 30.0
CAND_EP = 0.15
CAND_CP = 100
MARGIN_M = 0.10
PER_GAME_CAP = 3
MIN_MOVE = 8


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lichess", default=None, help="Lichess username (mine)")
    ap.add_argument("--chesscom", default=None, help="Chess.com username (mine)")
    ap.add_argument("--max-games", type=int, default=50, help="max games per provider")
    ap.add_argument("--out", default="extract_pilot.sqlite", help="SQLite output path")
    ap.add_argument("--sheet", default="review_sheet.md", help="review sheet path")
    ap.add_argument("--stockfish", default=None, help="SF19 binary path (else STOCKFISH_PATH)")
    ap.add_argument("--allow-other-engine", action="store_true",
                    help="run even when the binary is not Stockfish 19")
    ap.add_argument("--min-move", type=int, default=MIN_MOVE,
                    help="book-fallback: exclude user plies at/before this move number")
    ap.add_argument("--screen-nodes", type=int, default=150000)
    ap.add_argument("--stage2-nodes", type=int, default=500000)
    ap.add_argument("--seed", type=int, default=20261008)
    ap.add_argument("--no-book", action="store_true", help="skip opening-book lookup")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if not args.lichess and not args.chesscom:
        print("error: provide --lichess and/or --chesscom", file=sys.stderr)
        return 2

    # Pin the Review deterministic screen budget (read dynamically by
    # core.analysis_mode at call time, so setting here before use suffices).
    os.environ["REVIEW_DETERMINISTIC"] = "1"
    os.environ["REVIEW_NODES"] = str(args.screen_nodes)

    from core.analysis_mode import current_mode_string, review_nodes  # noqa: E402
    from core.game_analyzer import GameAnalyzer, expected_points, pv_to_san  # noqa: E402
    from engines.stockfish_engine import StockfishEngine  # noqa: E402
    from llms.mock_explainer import MockExplainer  # noqa: E402

    # ---- engine pin: calc_probe SF19, same binary for screen + stage 2 ----
    stockfish_path = args.stockfish or os.getenv("STOCKFISH_PATH") or ""
    if not stockfish_path or not os.path.exists(stockfish_path):
        print("error: set STOCKFISH_PATH to the calc_probe SF19 binary "
              "(or pass --stockfish); got %r" % stockfish_path, file=sys.stderr)
        return 2
    engine_version = probe_engine_version(stockfish_path)
    engine_sha256 = sha256_file(stockfish_path) or ""
    if "stockfish 19" not in (engine_version or "").lower() and not args.allow_other_engine:
        print("error: refusing non-SF19 engine %r (sha256 %s); pass "
              "--allow-other-engine to override" % (engine_version, engine_sha256[:16]),
              file=sys.stderr)
        return 2
    assert review_nodes() == args.screen_nodes, review_nodes()

    # ---- fetch (read-only provider integrations) ----
    # Lichess is fetched pilot-side with clocks=true (src/integrations
    # untouched): same endpoint/params as fetch_recent_lichess_games plus
    # clocks, reusing its private helpers so summarization stays identical.
    fetched: List[Dict[str, Any]] = []
    fetch_errors: Dict[str, str] = {}
    fetch_notes: Dict[str, str] = {}
    if args.lichess:
        try:
            for g in fetch_lichess_with_clocks(args.lichess, limit=args.max_games):
                g["provider"] = "lichess"
                fetched.append(g)
            fetch_notes["lichess"] = "clocks=true"
        except Exception as exc:  # noqa: BLE001 -- report, don't crash
            fetch_errors["lichess"] = str(exc)
    if args.chesscom:
        try:
            from integrations.chess_com import fetch_recent_chesscom_games  # noqa: E402

            for g in fetch_recent_chesscom_games(args.chesscom, limit=args.max_games):
                g["provider"] = "chesscom"
                fetched.append(g)
        except Exception as exc:  # noqa: BLE001
            fetch_errors["chesscom"] = str(exc)
    if not fetched:
        print("no games fetched: %s" % json.dumps(fetch_errors), file=sys.stderr)
        return 1

    book_lookup = None
    book_note = "disabled(--no-book)"
    book_available = False
    if not args.no_book:
        try:
            # Read-only lookup; fails soft to False without a DB pool, and
            # this pilot never calls init_db so no connection is ever opened.
            from services.opening_book import ensure_book_revision, is_book_move  # noqa: E402

            book_lookup = is_book_move
            book_available = ensure_book_revision() is not None
            if book_available:
                book_note = "services.opening_book.is_book_move (fail-soft, loaded)"
            else:
                # Empty book answers False for everything, which would
                # silently disable the book exclusion. Force the fallback.
                book_lookup = None
                book_note = ("services.opening_book.is_book_move (fail-soft, "
                             "EMPTY -> fallback)")
        except Exception as exc:  # noqa: BLE001
            book_lookup = None
            book_note = "unavailable(%s) -> fallback" % exc
    book_fallback = book_lookup is None
    if book_fallback:
        book_note += "; fallback excludes user plies with move_number<=%d" % args.min_move

    # ---- private screen engine (NOT the Review singleton) ----
    screen_engine = StockfishEngine(stockfish_path=stockfish_path)
    screen_engine.start()
    engine_version = screen_engine.name or engine_version
    try:
        mode_str = current_mode_string(engine_version, 2, args.screen_nodes)
    except Exception:  # noqa: BLE001 -- DB-backed book fingerprint is best-effort
        try:
            from core.analysis_mode import classifier_fingerprint  # noqa: E402

            clf = classifier_fingerprint()
        except Exception:  # noqa: BLE001
            clf = "unknown"
        mode_str = "rev-det-v1|engine=%s|threads=1|hash=16|nodes=%d|multipv=2|classifier=%s|book=unavailable" % (
            engine_version, args.screen_nodes, clf)
    analyzer = GameAnalyzer(
        engine=screen_engine, explainer=MockExplainer(), multipv=2, deterministic=True
    )

    try:
        con = connect(args.out)
    except RuntimeError as exc:  # stale pilot schema guard in store.connect
        print("error: %s" % exc, file=sys.stderr)
        return 2
    con.execute("DELETE FROM verify")
    con.execute("DELETE FROM plies")
    con.execute("DELETE FROM games")
    con.execute("DELETE FROM meta")
    settings = {
        "screen_nodes": args.screen_nodes, "stage2_nodes": args.stage2_nodes,
        "multipv_screen": 2, "multipv_stage2": 4, "threads": 1, "hash_mb": 16,
        "cand_ep": CAND_EP, "cand_cp": CAND_CP, "margin_M": MARGIN_M,
        "per_game_cap": PER_GAME_CAP, "min_move": args.min_move,
        "engine_version": engine_version, "stockfish_path": stockfish_path,
        "engine_sha256": engine_sha256,
        "allow_other_engine": bool(args.allow_other_engine),
        "mode": mode_str, "book": book_note, "seed": args.seed,
        "lichess": args.lichess, "chesscom": args.chesscom,
        "max_games": args.max_games, "fetch": fetch_notes,
    }
    con.execute("INSERT INTO meta(key,value) VALUES('settings',?)", (json.dumps(settings),))
    import datetime  # noqa: E402

    run_id = con.execute(
        "INSERT INTO runs(started_at,stockfish_path,engine_version,engine_sha256,"
        "allow_other_engine,settings_json) VALUES(?,?,?,?,?,?)",
        (datetime.datetime.now(datetime.timezone.utc).isoformat(), stockfish_path,
         engine_version, engine_sha256, 1 if args.allow_other_engine else 0,
         json.dumps(settings)),
    ).lastrowid
    con.commit()

    # ---- screen each game ----
    games_rows: List[Dict[str, Any]] = []  # {db_id, summary..., screen...}
    exclusion_total = Counter()
    n_non_user = 0
    n_user_plies = 0
    clk_games = Counter()  # provider -> games with any [%clk]
    clk_plies = Counter()  # provider -> user plies with [%clk]
    games_per_provider = Counter()
    skipped_no_color = 0
    t_screen_all = 0.0
    n_screen_done = 0

    for g in fetched:
        provider = g.get("provider", "")
        my_name = args.lichess if provider == "lichess" else args.chesscom
        color = derive_color(g, my_name or "")
        games_per_provider[provider] += 1
        if color is None:
            skipped_no_color += 1
            continue
        t0 = time.perf_counter()
        try:
            screen = screen_game(
                g.get("pgn", ""),
                user_color=color,
                user_rating=derive_rating(g, color),
                time_class=g.get("time_class", ""),
                analyzer=analyzer,
                book_lookup=book_lookup,
                min_move=args.min_move,
            )
            status, error = "ok", ""
        except Exception as exc:  # noqa: BLE001 -- per-game failure is a row, not a crash
            screen = None
            status, error = "failed", str(exc)[:500]
        screen_ms = (time.perf_counter() - t0) * 1000.0
        t_screen_all += screen_ms / 1000.0
        if screen is not None:
            n_user_plies += screen["n_user_plies"]
            n_non_user += screen["n_non_user"]
            for k, v in screen["exclusion_counts"].items():
                exclusion_total[k] += v
            if screen["clk_plies"] > 0:
                clk_games[provider] += 1
            clk_plies[provider] += screen["clk_plies"]
            for r in screen["rows"]:
                r["mode"] = mode_str
        cur = con.execute(
            "INSERT INTO games(provider,url,user_color,variant,time_class,result,"
            "white_name,black_name,user_rating,n_plies,n_user_plies,n_non_user,"
            "clk_plies,screen_ms,status,error) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (provider, g.get("url", ""), color,
             (screen["variant"] if screen else ""), g.get("time_class", ""),
             g.get("result", ""), str((g.get("white") or {}).get("username", "")),
             str((g.get("black") or {}).get("username", "")),
             derive_rating(g, color),
             (screen["n_plies"] if screen else 0),
             (screen["n_user_plies"] if screen else 0),
             (screen["n_non_user"] if screen else 0),
             (screen["clk_plies"] if screen else 0), screen_ms, status, error),
        )
        db_id = cur.lastrowid
        n_screen_done += 1
        print("[screen] game %d/%d [%s] %s: status=%s user_plies=%d screen_ms=%.0f" % (
            n_screen_done, len(fetched),
            provider, g.get("url", ""), status,
            (screen["n_user_plies"] if screen else 0), screen_ms), flush=True)
        if screen is not None:
            for r in screen["rows"]:
                r["db_ply_id"] = con.execute(
                    "INSERT INTO plies(game_id,ply_index,move_number,fen_before,fen_after,"
                    "position_key,played_san,played_uci,screen_best_san,screen_best_uci,"
                    "screen_second_san,screen_second_uci,screen_best_pv_uci,screen_ep_loss,"
                    "screen_cp_loss,screen_ep_best,screen_eval_cp_mover,classification,"
                    "player_rating,is_book,exclusion,clk_seconds,base_time_s,mode)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (db_id, r["ply_index"], r["move_number"] or 0, r["fen_before"] or "",
                     r["fen_after"] or "", r["position_key"], r["played_san"] or "",
                     r["played_uci"] or "", r["best_san"] or "", r["best_uci"] or "",
                     r["second_san"] or "", r["second_uci"] or "",
                     " ".join(r.get("best_pv_uci") or []),
                     float(r["ep_loss"] or 0.0), int(r["cp_loss"] or 0),
                     float(r["ep_best"] or 0.0), float(r["eval_cp_mover"] or 0.0),
                     r["classification"] or "", int(r["player_rating"] or 1500),
                     1 if r["is_book"] else 0, r["exclusion"] or "",
                     r["clk_seconds"], r["base_time_s"], mode_str),
                ).lastrowid
            games_rows.append({"db_id": db_id, "summary": g, "screen": screen,
                               "color": color, "provider": provider})
    con.commit()
    screen_engine.close()

    # ---- candidates (placeholders; no classification gate) ----
    included = [r for gr in games_rows for r in gr["screen"]["rows"] if not r["exclusion"]]
    yield_ep = {t: sum(1 for r in included if r["ep_loss"] >= t and r["cp_loss"] >= CAND_CP) for t in EP_GRID}
    cand = [r for r in included if r["ep_loss"] >= CAND_EP and r["cp_loss"] >= CAND_CP]
    n_cand_pre = len(cand)

    # ---- volume control: dedupe by position_key, cap top-3/game by ep_loss ----
    seen: Dict[str, Dict[str, Any]] = {}
    for r in sorted(cand, key=lambda r: r["ep_loss"], reverse=True):
        if r["position_key"] not in seen:
            seen[r["position_key"]] = r
    deduped = list(seen.values())
    per_game: Dict[int, List[Dict[str, Any]]] = {}
    for gr in games_rows:
        per_game[gr["db_id"]] = []
    for r in deduped:
        gid = ply_game_id(con, r["db_ply_id"])
        per_game.setdefault(gid, []).append(r)
    capped: List[Dict[str, Any]] = []
    for gid, rs in per_game.items():
        capped.extend(sorted(rs, key=lambda r: r["ep_loss"], reverse=True)[:PER_GAME_CAP])
    n_cand_post = len(capped)

    # ---- stage 2 ----
    stage2 = Stage2Engine(stockfish_path, hash_mb=16)
    verified: List[Dict[str, Any]] = []
    verify_total_ms = 0.0
    game_verify_ms: Dict[int, float] = {}
    try:
        for r in sorted(capped, key=lambda r: r["db_ply_id"]):
            t0 = time.perf_counter()
            board = chess.Board(r["fen_before"])
            legal_count = sum(1 for _ in board.legal_moves)
            pvs, _ = stage2.analyse_before(r["fen_before"], args.stage2_nodes, multipv=4)
            after, _ = stage2.analyse_after(r["fen_after"], args.stage2_nodes)
            ms = (time.perf_counter() - t0) * 1000.0
            verify_total_ms += ms
            gid = ply_game_id(con, r["db_ply_id"])
            game_verify_ms[gid] = game_verify_ms.get(gid, 0.0) + ms

            rating = int(r["player_rating"] or 1500)
            best = pvs[0] if pvs else {"move": None, "cp": None, "mate": None, "pv_uci": []}
            second = pvs[1] if len(pvs) > 1 else None
            best_cp_eq = best["cp"] if best["cp"] is not None else mate_to_cp_equiv(best["mate"])
            second_cp_eq = None
            second_mate = None
            if second is not None:
                second_cp_eq = second["cp"] if second["cp"] is not None else mate_to_cp_equiv(second["mate"])
                second_mate = second["mate"]
            # Played-move loss VERIFIED at stage-2 budget (not copied).
            opp_cp = after["cp"] if after["cp"] is not None else mate_to_cp_equiv(after["mate"])
            played_cp = -opp_cp if opp_cp is not None else None
            ep_best_v = expected_points(best_cp_eq if best_cp_eq is not None else 0.0, rating)
            ep_played_v = expected_points(played_cp if played_cp is not None else 0.0, rating)
            ep_loss_v = max(0.0, ep_best_v - ep_played_v)
            cp_loss_v = max(0, int(round((best_cp_eq or 0.0) - (played_cp or 0.0))))
            if second_cp_eq is None and not (best.get("mate") and best["mate"] > 0):
                margin = None
                reject_pre = "no_second_line"
            elif best.get("mate") is not None and best["mate"] > 0 and second_mate is not None and second_mate > 0:
                margin = None
                reject_pre = "mate_mate"
            elif best.get("mate") is not None and best["mate"] > 0:
                margin = None  # best mates, second does not: unique by construction
                reject_pre = ""
            else:
                margin = max(0.0, expected_points(best_cp_eq or 0.0, rating) - expected_points(second_cp_eq or 0.0, rating))
                reject_pre = ""
            if legal_count <= 1:
                reject = "single_legal"
            elif reject_pre:
                reject = reject_pre
            elif margin is not None and margin < MARGIN_M:
                reject = "margin_lt_%.2f" % MARGIN_M
            else:
                reject = ""
            pv_uci = best.get("pv_uci") or []
            try:
                pv_san = " ".join(pv_to_san(board, pv_uci))
            except Exception:  # noqa: BLE001
                pv_san = ""
            best_san = san_of(board, best.get("move"))
            # Metadata only (no gating): best-move character + material swing
            # at the end of the first 6 plies of the best PV and of the
            # played-move PV ([played] + opponent reply line).
            flags = best_move_flags(board, best.get("move"), bool(best.get("mate") and best["mate"] > 0))
            after_pv = (after.get("pv_uci") or [])[:5]
            mat_best = pv_material_delta(r["fen_before"], pv_uci[:6])
            mat_played = pv_material_delta(r["fen_before"], [r["played_uci"]] + after_pv)
            rec = {
                "ply": r, "game_id": gid, "legal_count": legal_count,
                "stage2_best_uci": best.get("move") or "", "stage2_best_san": best_san,
                "stage2_best_cp": best["cp"], "stage2_best_mate": best["mate"],
                "stage2_second_uci": (second or {}).get("move") or "",
                "stage2_second_cp": second_cp_eq, "stage2_second_mate": second_mate,
                "stage2_pv_uci": " ".join(pv_uci), "stage2_pv_san": pv_san,
                "played_cp_verified": played_cp, "ep_best_verified": ep_best_v,
                "ep_played_verified": ep_played_v, "ep_loss_verified": ep_loss_v,
                "cp_loss_verified": cp_loss_v, "margin_ep": margin,
                "best_agrees": 1 if (best.get("move") and best.get("move") == r["best_uci"]) else 0,
                "stage2_ms": ms, "reject": reject,
                "flags": flags, "mat_best": mat_best, "mat_played": mat_played,
            }
            verified.append(rec)
            print("[verify] %d/%d ply %s (%s): reject=%s margin=%s" % (
                len(verified), len(capped), r["played_san"], r["fen_before"][:40],
                reject or "KEEP", ("%.4f" % margin) if margin is not None else "mate"),
                flush=True)
            con.execute(
                "INSERT INTO verify(ply_id,stage2_best_uci,stage2_best_san,stage2_best_cp,"
                "stage2_best_mate,stage2_second_uci,stage2_second_cp,stage2_second_mate,"
                "stage2_pv_uci,stage2_pv_san,played_cp_verified,ep_best_verified,"
                "ep_played_verified,ep_loss_verified,cp_loss_verified,margin_ep,"
                "legal_count,best_agrees,stage2_ms,reject,kept,"
                "best_is_capture,best_is_check,best_is_mate,best_is_promotion,"
                "best_is_quiet,pv_mat_best,pv_mat_played)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (r["db_ply_id"], rec["stage2_best_uci"], best_san, best["cp"], best["mate"],
                 rec["stage2_second_uci"], second_cp_eq, second_mate,
                 rec["stage2_pv_uci"], pv_san, played_cp, ep_best_v, ep_played_v,
                 ep_loss_v, cp_loss_v, margin,
                 legal_count, rec["best_agrees"], ms, reject, 1 if not reject else 0,
                 flags["capture"], flags["check"], flags["mate"], flags["promotion"],
                 flags["quiet"], mat_best, mat_played),
            )
    finally:
        stage2.close()
    for gid, ms in game_verify_ms.items():
        con.execute("UPDATE games SET verify_ms=? WHERE id=?", (ms, gid))
    con.commit()

    kept = [v for v in verified if not v["reject"]]
    rejected = [v for v in verified if v["reject"]]
    reject_counts = Counter(v["reject"] for v in rejected)
    eligible_for_margin = [v for v in verified if v["reject"] in ("",) or v["reject"].startswith("margin_lt")]
    margin_yield = {}
    for m in MARGIN_GRID:
        margin_yield[m] = sum(1 for v in eligible_for_margin if v["margin_ep"] is None or v["margin_ep"] >= m)
    per_game_kept = Counter(v["game_id"] for v in kept)
    dist = Counter(per_game_kept.values())

    # ---- clock grids: what exclusion WOULD be at 5%/10%/15% and fixed 30s ----
    clk_rows = [r for gr in games_rows for r in gr["screen"]["rows"] if r["clk_seconds"] is not None]
    n_clk_base_known = sum(1 for r in clk_rows if r["base_time_s"])
    n_clk_base_unknown = len(clk_rows) - n_clk_base_known
    clock_grid = {
        f: sum(1 for r in clk_rows
               if r["base_time_s"] and r["clk_seconds"] < max(10.0, f * r["base_time_s"]))
        for f in CLOCK_FRAC_GRID
    }
    clock_fixed = sum(1 for r in clk_rows if r["clk_seconds"] < CLOCK_FIXED_S)

    # ---- best-move character + material shares, kept vs rejected ----
    CHAR_KEYS = ("capture", "check", "mate", "promotion", "quiet")
    char_shares = {
        k: (sum(v["flags"][k] for v in kept) / len(kept) if kept else 0.0,
            sum(v["flags"][k] for v in rejected) / len(rejected) if rejected else 0.0)
        for k in CHAR_KEYS
    }
    mat_means = {
        "best": (sum(v["mat_best"] for v in kept) / len(kept) if kept else 0.0,
                 sum(v["mat_best"] for v in rejected) / len(rejected) if rejected else 0.0),
        "played": (sum(v["mat_played"] for v in kept) / len(kept) if kept else 0.0,
                   sum(v["mat_played"] for v in rejected) / len(rejected) if rejected else 0.0),
    }

    # ---- report ----
    L: List[str] = []
    def emit(s=""):
        L.append(s + "\n")
    emit("## Pilot report")
    if book_fallback:
        emit("- WARNING: opening book %s; user plies with move_number<=%d "
             "excluded as 'book_fallback' (counted below)" % (
                 "disabled via --no-book" if args.no_book else "empty/unavailable",
                 args.min_move))
    emit("- settings: screen MultiPV2 @%d nodes (det), stage2 MultiPV4 @%d nodes, Threads=1 Hash=16, nodes-only fresh-token" % (args.screen_nodes, args.stage2_nodes))
    emit("- engine: %s | path=%s | sha256=%s%s; mode: `%s`" % (
        engine_version, stockfish_path, engine_sha256,
        " (override --allow-other-engine)" if args.allow_other_engine else "", mode_str))
    emit("- run_id=%d" % run_id)
    emit("- book: %s" % book_note)
    emit("- fetch: lichess=%s chesscom=%s max_games=%d per provider%s" % (
        args.lichess, args.chesscom, args.max_games,
        " (lichess %s)" % fetch_notes.get("lichess", "default") if args.lichess else ""))
    for p in ("lichess", "chesscom"):
        emit("  - %s: %d games fetched%s" % (p, games_per_provider.get(p, 0), (" ERROR " + fetch_errors[p]) if p in fetch_errors else ""))
    emit("- games skipped (color not derivable): %d" % skipped_no_color)
    emit("- games screened ok: %d" % len(games_rows))
    emit("- user plies: %d; non-user plies: %d" % (n_user_plies, n_non_user))
    emit("- [%%clk] presence: lichess %d/%d games, %d plies; chesscom %d/%d games, %d plies"
         % (clk_games.get("lichess", 0), games_per_provider.get("lichess", 0), clk_plies.get("lichess", 0),
            clk_games.get("chesscom", 0), games_per_provider.get("chesscom", 0), clk_plies.get("chesscom", 0)))
    emit("- clock base time: %d clk plies with known base, %d unknown (30s fallback in decision)"
         % (n_clk_base_known, n_clk_base_unknown))
    emit("- clock exclusion grids (user plies with [%%clk], n=%d): %s; fixed 30s: %d"
         % (len(clk_rows),
            {f: clock_grid[f] for f in CLOCK_FRAC_GRID}, clock_fixed))
    emit("- exclusions (user plies, first-match): %s" % (dict(exclusion_total) or "{}"))
    emit("- included user plies: %d" % len(included))
    emit("- screen yield (cp>=%d): %s" % (CAND_CP, {t: yield_ep[t] for t in EP_GRID}))
    emit("- candidates pre-volume: %d; post dedupe+cap(top-%d/game): %d" % (n_cand_pre, PER_GAME_CAP, n_cand_post))
    emit("- stage2 verified: %d; pass: %d (%.1f%%)" % (len(verified), len(kept), 100.0 * len(kept) / len(verified) if verified else 0.0))
    emit("- margin yield (eligible=%d) at M: %s" % (len(eligible_for_margin), {m: margin_yield[m] for m in MARGIN_GRID}))
    emit("- reject reasons: %s" % (dict(reject_counts) or "{}"))
    emit("- best_agrees (stage2 best == screen best): %d/%d" % (sum(v["best_agrees"] for v in verified), len(verified)))
    emit("- best-move character shares kept(n=%d) vs rejected(n=%d): %s" % (
        len(kept), len(rejected),
        "; ".join("%s %.0f%%/%.0f%%" % (k, 100 * char_shares[k][0], 100 * char_shares[k][1]) for k in CHAR_KEYS)))
    emit("- pv material Δ mean (cp, best-line / played-line) kept: %+.0f / %+.0f; rejected: %+.0f / %+.0f" % (
        mat_means["best"][0], mat_means["played"][0], mat_means["best"][1], mat_means["played"][1]))
    emit("- puzzles/game distribution (kept games only): %s" % (dict(sorted(dist.items())) or "{}"))
    emit("- timing: screen total %.1fs; stage2 total %.1fs" % (t_screen_all, verify_total_ms / 1000.0))
    for gr in sorted(games_rows, key=lambda g: g["db_id"]):
        row = con.execute("SELECT screen_ms,verify_ms,n_user_plies FROM games WHERE id=?", (gr["db_id"],)).fetchone()
        emit("  - game %d [%s] %s: screen %.0fms verify %.0fms user_plies %d" % (gr["db_id"], gr["provider"], gr["summary"].get("url", ""), row[0], row[1], row[2]))
    report_text = L

    kept_sheet = []
    for v in sorted(kept, key=lambda v: v["ep_loss_verified"], reverse=True):
        r, gid = v["ply"], v["game_id"]
        url = con.execute("SELECT url FROM games WHERE id=?", (gid,)).fetchone()[0]
        kept_sheet.append({
            "fen_before": r["fen_before"], "side_to_move": r["color"],
            "played_san": r["played_san"], "played_uci": r["played_uci"],
            "best_san": v["stage2_best_san"], "best_uci": v["stage2_best_uci"],
            "pv_san": v["stage2_pv_san"], "ep_best_verified": v["ep_best_verified"],
            "ep_played_verified": v["ep_played_verified"],
            "screen_ep_loss": r["ep_loss"], "screen_cp_loss": r["cp_loss"],
            "ep_loss_verified": v["ep_loss_verified"], "cp_loss_verified": v["cp_loss_verified"],
            "margin_ep": v["margin_ep"], "url": url,
            "ply_index": r["ply_index"], "move_number": r["move_number"],
            "best_char": char_label(v["flags"]), "pv_mat_best": v["mat_best"],
            "pv_mat_played": v["mat_played"],
        })
    rej_sheet = []
    for v in verified:
        if not v["reject"]:
            continue
        r, gid = v["ply"], v["game_id"]
        url = con.execute("SELECT url FROM games WHERE id=?", (gid,)).fetchone()[0]
        rej_sheet.append({
            "reject": v["reject"], "url": url, "move_number": r["move_number"],
            "ply_index": r["ply_index"],
            "played_san": r["played_san"], "screen_ep_loss": r["ep_loss"],
            "fen_before": r["fen_before"], "screen_best_san": r["best_san"] or "",
            "screen_best_uci": r["best_uci"] or "", "stage2_best_uci": v["stage2_best_uci"],
        })
    write_sheet(args.sheet, report_lines=report_text, kept=kept_sheet, rejected=rej_sheet, seed=args.seed)
    con.execute("INSERT INTO meta(key,value) VALUES('report',?)", ("".join(report_text),))
    con.commit()
    con.close()

    print("".join(report_text), end="")
    print("kept=%d verified=%d sqlite=%s sheet=%s" % (len(kept), len(verified), args.out, args.sheet))
    return 0


def fetch_lichess_with_clocks(username: str, limit: int) -> List[Dict[str, Any]]:
    """Pilot-only Lichess fetch: identical to fetch_recent_lichess_games but
    with ``clocks=true`` so PGNs carry ``[%clk]`` comments. Reuses the
    integration's private helpers; ``src/integrations`` is untouched."""
    from integrations import lichess as lich_mod  # noqa: E402

    username = (username or "").strip()
    if not username:
        raise ValueError("username must not be empty")
    url = "{base}?max={limit}&pgnInJson=true&opening=true&clocks=true".format(
        base=lich_mod.GAMES_URL.format(username=username),
        limit=limit,
    )
    records = lich_mod._http_get_ndjson(url, username=username)
    games = [lich_mod._summarize_game(rec) for rec in records]
    games.sort(key=lambda g: g.get("end_time") or 0, reverse=True)
    return games[:limit]


def probe_engine_version(path: str) -> str:
    """UCI id name of a stockfish binary (throwaway process)."""
    import chess.engine  # noqa: E402

    eng = chess.engine.SimpleEngine.popen_uci(path, timeout=120)
    try:
        return eng.id.get("name", "unknown")
    finally:
        try:
            eng.quit()
        except Exception:  # noqa: BLE001
            pass


def sha256_file(path: str) -> Optional[str]:
    """Hex sha256 of a file, None on error."""
    import hashlib  # noqa: E402

    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


PIECE_VALUES_CP = {
    chess.PAWN: 100, chess.KNIGHT: 320, chess.BISHOP: 330,
    chess.ROOK: 500, chess.QUEEN: 900, chess.KING: 0,
}


def best_move_flags(board_before: "chess.Board", best_uci: Optional[str], is_mate: bool) -> Dict[str, int]:
    """Metadata-only character flags for the verified best move."""
    flags = {"capture": 0, "check": 0, "mate": 0, "promotion": 0, "quiet": 0}
    if not best_uci:
        return flags
    try:
        move = chess.Move.from_uci(best_uci)
    except ValueError:
        return flags
    if move not in board_before.legal_moves:
        return flags
    probe = board_before.copy()
    flags["capture"] = 1 if probe.is_capture(move) else 0
    flags["promotion"] = 1 if move.promotion else 0
    probe.push(move)
    flags["check"] = 1 if probe.is_check() else 0
    flags["mate"] = 1 if is_mate else 0
    if not flags["capture"] and not flags["check"]:
        flags["quiet"] = 1
    return flags


def char_label(flags: Dict[str, int]) -> str:
    """Short human label for hand review (metadata only)."""
    if flags.get("mate"):
        return "mate"
    if flags.get("promotion"):
        return "promotion"
    if flags.get("capture") and flags.get("check"):
        return "capture+check"
    if flags.get("capture"):
        return "capture"
    if flags.get("check"):
        return "check"
    return "quiet"


def pv_material_delta(fen_before: str, line_uci: List[str]) -> Optional[int]:
    """Material-balance change (cp, side-to-move POV) after the first 6 plies
    of a UCI line. None when the line is empty."""
    if not line_uci:
        return None

    def balance(bd: "chess.Board", me: bool) -> int:
        mine = sum(len(bd.pieces(t, me)) * v for t, v in PIECE_VALUES_CP.items())
        theirs = sum(len(bd.pieces(t, not me)) * v for t, v in PIECE_VALUES_CP.items())
        return mine - theirs

    try:
        board = chess.Board(fen_before)
    except ValueError:
        return None
    me = board.turn
    start = balance(board, me)
    for uci in line_uci[:6]:
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            break
        if move not in board.legal_moves:
            break
        board.push(move)
    return balance(board, me) - start


def derive_color(summary: Dict[str, Any], username: str) -> Optional[str]:
    """My color from the provider summary usernames (case-insensitive)."""
    want = (username or "").strip().lower()
    if not want:
        return None
    w = str(((summary.get("white") or {}).get("username")) or "").strip().lower()
    b = str(((summary.get("black") or {}).get("username")) or "").strip().lower()
    if w == want:
        return "white"
    if b == want:
        return "black"
    return None


def derive_rating(summary: Dict[str, Any], color: str) -> int:
    """My rating: PGN header first (screen.py), else summary, else 1500.

    The PGN-header path lives in screen_game via analyzer._ratings_from_headers;
    here we only provide the summary fallback for the games row. The per-ply
    player_rating stored in plies is authoritative.
    """
    side = (summary.get(color) or {})
    try:
        return int(side.get("rating") or 1500)
    except (TypeError, ValueError):
        return 1500


def ply_game_id(con, db_ply_id: int) -> int:
    row = con.execute("SELECT game_id FROM plies WHERE id=?", (db_ply_id,)).fetchone()
    return int(row[0])


def san_of(board, uci: Optional[str]) -> str:
    if not uci:
        return ""
    try:
        return board.san(chess.Move.from_uci(uci))
    except Exception:  # noqa: BLE001
        return ""


if __name__ == "__main__":
    raise SystemExit(main())
