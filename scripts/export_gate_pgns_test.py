#!/usr/bin/env python
"""Tests for the gate export's exclusion loader and neutral bot filter.

The loader must fail loudly (SystemExit) on a missing file, bad JSON, an
unrecognized shape, non-id entries, or a set that resolves to empty -- it
must never silently exclude nothing while the flag is passed.

Run with: venv/bin/python scripts/export_gate_pgns_test.py
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from export_gate_pgns import (  # noqa: E402
    _involves_bot,
    load_exclude_ids,
    parse_pgn_text,
)

import chess.pgn  # noqa: E402


def _write_tmp(payload: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    handle.write(payload)
    handle.close()
    return handle.name


def _expect_exit(label, func):
    try:
        func()
    except SystemExit:
        print(f"  [PASS] {label} -> SystemExit")
        return
    raise AssertionError(f"{label} did not fail loudly")


def test_list_shape_ok():
    path = _write_tmp(json.dumps(["a", "b"]))
    assert load_exclude_ids(path) == frozenset({"a", "b"})
    print("  [PASS] JSON list loads")


def test_manifest_shape_ok():
    path = _write_tmp(
        json.dumps({"set_a": {"ids": ["a"]}, "set_b": {"ids": ["b", "c"]}})
    )
    assert load_exclude_ids(path) == frozenset({"a", "b", "c"})
    print("  [PASS] set_a/set_b manifest loads")


def test_union_shapes_ok():
    path = _write_tmp(json.dumps({"excluded_from_b_union": ["x"]}))
    assert load_exclude_ids(path) == frozenset({"x"})
    path = _write_tmp(json.dumps({"excluded_from_b": {"ids": ["y"]}}))
    assert load_exclude_ids(path) == frozenset({"y"})
    print("  [PASS] union shapes load")


def test_real_used_ids_file():
    repo_ids = (
        Path(__file__).resolve().parents[1] / "data" / "gate_used_ids.json"
    )
    if not repo_ids.exists():
        print("  [SKIP] data/gate_used_ids.json not on disk")
        return
    assert len(load_exclude_ids(str(repo_ids))) == 240
    print("  [PASS] real gate_used_ids.json resolves to 240 ids")


def test_empty_list_fails():
    _expect_exit("empty list", lambda: load_exclude_ids(_write_tmp("[]")))


def test_wrong_shapes_fail():
    _expect_exit("empty object", lambda: load_exclude_ids(_write_tmp("{}")))
    _expect_exit(
        "unknown keys",
        lambda: load_exclude_ids(_write_tmp(json.dumps({"nope": [1]}))),
    )
    _expect_exit("JSON scalar", lambda: load_exclude_ids(_write_tmp("42")))
    _expect_exit(
        "bad set_a block",
        lambda: load_exclude_ids(_write_tmp(json.dumps({"set_a": [1]}))),
    )


def test_missing_and_broken_files_fail():
    _expect_exit("missing file", lambda: load_exclude_ids("/nonexistent/x.json"))
    _expect_exit("bad JSON", lambda: load_exclude_ids(_write_tmp("{oops")))


def test_non_id_entries_fail():
    _expect_exit(
        "null entry",
        lambda: load_exclude_ids(_write_tmp(json.dumps(["ok", None]))),
    )
    _expect_exit(
        "empty string",
        lambda: load_exclude_ids(_write_tmp(json.dumps(["ok", ""]))),
    )


def _game_with_titles(white_title=None, black_title=None):
    game = chess.pgn.Game()
    game.headers["White"] = "alice"
    game.headers["Black"] = "bob"
    if white_title:
        game.headers["WhiteTitle"] = white_title
    if black_title:
        game.headers["BlackTitle"] = black_title
    return game


def test_bot_filter():
    assert _involves_bot(_game_with_titles(black_title="BOT")) is True
    assert _involves_bot(_game_with_titles(white_title="bot")) is True
    assert _involves_bot(_game_with_titles(white_title="GM")) is False
    assert _involves_bot(_game_with_titles()) is False
    bot_text = "\n\n".join(str(_game_with_titles(black_title="BOT")) for _ in range(2))
    human_text = "\n\n".join(str(_game_with_titles(white_title="GM")) for _ in range(3))
    text = bot_text + "\n\n" + human_text
    kept, stats = parse_pgn_text(text, 0, exclude_bots=True)
    assert len(kept) == 3 and stats["bot_dropped"] == 2, stats
    kept, _ = parse_pgn_text(text, 0, exclude_bots=False)
    assert len(kept) == 5
    print("  [PASS] BOT-title games dropped only when asked")


def run() -> int:
    print("=== Running gate export loader tests ===")
    tests = [
        test_list_shape_ok,
        test_manifest_shape_ok,
        test_union_shapes_ok,
        test_real_used_ids_file,
        test_empty_list_fails,
        test_wrong_shapes_fail,
        test_missing_and_broken_files_fail,
        test_non_id_entries_fail,
        test_bot_filter,
    ]
    failures = 0
    for test in tests:
        try:
            test()
        except AssertionError as exc:
            failures += 1
            print(f"  [FAIL] {test.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  [FAIL] {test.__name__} raised {type(exc).__name__}: {exc}")
    print(f"  {len(tests) - failures}/{len(tests)} passed")
    return failures


if __name__ == "__main__":
    raise SystemExit(1 if run() else 0)
