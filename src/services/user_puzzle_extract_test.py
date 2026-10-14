"""
Test harness for incremental collection in services/user_puzzle_extract.py.

collect_unseen walks newest-first pages with a stub fetcher (no network,
no DB): only never-stored URLs are collected, so repeat imports cost no
engine time.

Run:
    cd src && ../venv/bin/python services/user_puzzle_extract_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.user_puzzle_extract import collect_unseen


def _page(*urls):
    return [{"url": u, "end_time": 1} for u in urls]


def _fetcher(pages):
    calls = []

    def fetch_page(cursor):
        idx = 0 if cursor is None else cursor
        calls.append(idx)
        if idx >= len(pages):
            return [], None
        nxt = idx + 1 if idx + 1 < len(pages) else None
        return pages[idx], nxt

    return fetch_page, calls


def test_collects_unseen_newest_first():
    fetch_page, _ = _fetcher([_page("c", "b"), _page("a")])
    collected, pulled = collect_unseen(fetch_page, {"a"}, 2, 6)
    assert [g["url"] for g in collected] == ["c", "b"], collected
    assert pulled == 3
    print("[PASS] unseen collected newest-first, seen skipped")


def test_stops_at_fully_seen_page():
    fetch_page, calls = _fetcher([_page("d"), _page("c", "b"), _page("a")])
    collected, pulled = collect_unseen(fetch_page, {"c", "b", "a"}, 1, 6)
    assert [g["url"] for g in collected] == ["d"], collected
    assert pulled == 3 and calls == [0, 1], (pulled, calls)
    print("[PASS] fully-seen page stops the walk")


def test_fully_seen_first_page_continues():
    # Everything newest already stored, older backlog untouched: the walk
    # must continue past the fully-seen first page.
    fetch_page, calls = _fetcher([_page("e"), _page("d", "c")])
    collected, _ = collect_unseen(fetch_page, {"e"}, 1, 6)
    assert [g["url"] for g in collected] == ["d", "c"], collected
    assert calls == [0, 1], calls
    print("[PASS] fully-seen first page does not end the walk")


def test_empty_page_ends():
    full = _page(*[f"g{i}" for i in range(10)])
    fetch_page, calls = _fetcher([full, []])
    collected, pulled = collect_unseen(fetch_page, set(), 10, 6)
    assert len(collected) == 10 and pulled == 10
    assert calls == [0, 1], calls
    print("[PASS] empty page ends the walk at end of history")


def test_short_page_ends():
    fetch_page, calls = _fetcher([_page("c", "b"), _page("a")])
    collected, _ = collect_unseen(fetch_page, set(), 10, 6)
    assert [g["url"] for g in collected] == ["c", "b"]
    assert calls == [0], calls
    print("[PASS] short page ends the walk")


def test_unit_cap_bounds():
    pages = [_page(*[f"g{i}-{j}" for j in range(3)]) for i in range(10)]
    fetch_page, calls = _fetcher(pages)
    collected, pulled = collect_unseen(fetch_page, set(), 3, 4)
    assert pulled == 12 and len(collected) == 12
    assert calls == [0, 1, 2, 3], calls
    print("[PASS] unit cap bounds the walk")


def test_dedupes_and_skips_url_less():
    fetch_page, _ = _fetcher([_page("a", "a", ""), _page("a")])
    collected, _ = collect_unseen(fetch_page, set(), 10, 6)
    assert [g["url"] for g in collected] == ["a"], collected
    print("[PASS] cross-page dupes and url-less games skipped")


if __name__ == "__main__":
    test_collects_unseen_newest_first()
    test_stops_at_fully_seen_page()
    test_fully_seen_first_page_continues()
    test_empty_page_ends()
    test_short_page_ends()
    test_unit_cap_bounds()
    test_dedupes_and_skips_url_less()
    print("All assertions passed.")
