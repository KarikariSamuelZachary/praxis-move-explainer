"""
Test harness for the alternate-line picker in
services/user_puzzle_extract.py (_qualifying_alt_pvs).

Pure data in, PV strings out (no engine, no DB): alternate Stage-2 root
lines qualify only with the same loud/decisive bar as best and a mate or
an eval within ALT_EP_TOL of best.

Run:
    cd src && ../venv/bin/python services/user_puzzle_extract_test.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.user_puzzle_extract import ALT_EP_TOL, _qualifying_alt_pvs

FEN = "4k3/8/8/b7/8/8/4K3/R6b w - - 0 1"
BEST = {"move": "a1a5", "cp": 350.0, "mate": None, "pv_uci": ["a1a5", "e8d7"]}


def _alt(move, cp, mate, pv):
    return {"move": move, "cp": cp, "mate": mate, "pv_uci": pv}


def test_near_equivalent_alt_qualifies():
    pvs = [BEST, _alt("a1h1", 330.0, None, ["a1h1", "e8d7"])]
    out = _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500)
    assert out == ["a1h1 e8d7"], out
    print("[PASS] near-equivalent alternate qualifies")


def test_far_alt_rejected():
    pvs = [BEST, _alt("a1h1", -200.0, None, ["a1h1", "e8d7"])]
    out = _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500)
    assert out == [], out
    print("[PASS] alternate far below best is rejected")


def test_tolerance_boundary():
    # At/past the tolerance edge the alternate is out; safely inside it
    # is in. (Loop finds the first cp whose EP gap reaches ALT_EP_TOL.)
    from core.game_analyzer import expected_points

    edge_cp = 350.0
    while expected_points(350.0, 1500) - expected_points(edge_cp, 1500) < ALT_EP_TOL:
        edge_cp -= 1.0
    pvs = [BEST, _alt("a1h1", edge_cp, None, ["a1h1", "e8d7"])]
    assert _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500) == []
    inside = [BEST, _alt("a1h1", edge_cp + 50.0, None, ["a1h1", "e8d7"])]
    assert _qualifying_alt_pvs(inside, BEST, 350.0, ["a1a5"], FEN, 1500) == ["a1h1 e8d7"]
    print("[PASS] tolerance boundary behaves")


def test_mating_alt_qualifies_regardless_of_eval():
    # Engine announces M4 but the PV shows only the confirmed capture.
    # EP(mate) vs EP(+350) differs by ~0.17, far past ALT_EP_TOL, so only
    # the mate bypass lets this through.
    pvs = [BEST, _alt("a1h1", None, 4, ["a1h1", "e8d7"])]
    out = _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500)
    assert out == ["a1h1 e8d7"], out
    print("[PASS] short-PV mate-scored alternate qualifies via bypass")


def test_double_mate_alt_qualifies():
    # Two mates in one: best M1 plus alternate M1 both solve.
    fen = "8/6Q1/8/5K1k/6rp/8/8/8 w - - 0 1"
    best = {"move": "g7h8", "cp": None, "mate": 1, "pv_uci": ["g7h8"]}
    alt = {"move": "g7h7", "cp": None, "mate": 1, "pv_uci": ["g7h7"]}
    out = _qualifying_alt_pvs([best, alt], best, 10000.0, ["g7h8"], fen, 1500)
    assert out == ["g7h7"], out
    print("[PASS] second mating move qualifies as alternate")


def test_quiet_alt_rejected():
    pvs = [BEST, _alt("e2d3", 345.0, None, ["e2d3", "e8d7"])]
    out = _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500)
    assert out == [], out
    print("[PASS] quiet alternate rejected despite close eval")


def test_dupe_and_junk_skipped():
    pvs = [
        BEST,
        _alt("a1a5", 350.0, None, ["a1a5", "e8d7"]),
        {"move": None, "cp": 0.0, "mate": None, "pv_uci": []},
        _alt("a1h1", None, None, ["a1h1", "e8d7"]),
        _alt("e2e4", 340.0, None, ["e2e4"]),
    ]
    out = _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500)
    assert out == [], out
    print("[PASS] dupe, missing, scoreless, and illegal alts skipped")


def test_pv_mismatch_skipped():
    # PV not starting with the alt move cannot be trusted.
    pvs = [BEST, _alt("a1h1", 345.0, None, ["e8d7", "a1h1"])]
    out = _qualifying_alt_pvs(pvs, BEST, 350.0, ["a1a5"], FEN, 1500)
    assert out == [], out
    print("[PASS] alt PV not starting with the alt move skipped")


if __name__ == "__main__":
    test_near_equivalent_alt_qualifies()
    test_far_alt_rejected()
    test_tolerance_boundary()
    test_mating_alt_qualifies_regardless_of_eval()
    test_double_mate_alt_qualifies()
    test_quiet_alt_rejected()
    test_dupe_and_junk_skipped()
    test_pv_mismatch_skipped()
    print("All assertions passed.")
