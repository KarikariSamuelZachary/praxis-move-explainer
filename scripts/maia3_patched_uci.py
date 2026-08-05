"""
Wrapper for maia3-uci that adds a `policy` token to the UCI `info` line.

The upstream maia3-uci output (venv/.../site-packages/maia3/uci.py:471)
emits only the standard UCI fields:
    info depth 1 multipv 1 score cp 25 wdl 500 50 450 pv e2e4

The internal ranking is by `torch.topk(softmax(logits), N)` (uci.py:327),
so the actual policy probability of each candidate is known at emit
time but not surfaced. The style-bias re-ranker
(src/services/opponent_style_reranker.py) needs that probability as the
base weight for its weighted-sample bias; without it the reranker
falls back to a geometric rank-decay proxy that's acknowledged as a
coarse stand-in (opponent_style_reranker.py:148-150).

This wrapper monkey-patches `Maia3UCIEngine.cmd_go` at import time to
append `policy {prob}` to each info line:
    info depth 1 multipv 1 score cp 25 wdl 500 50 450 policy 0.234567 pv e2e4

The python-chess parser patch (applied at import in maia_engine.py)
captures the `policy` token into info["policy"], and the engine wrapper
surfaces it on each candidate dict as `candidate["policy"]`. The
re-ranker reads it directly.

Failure modes:
  * If maia3's `cmd_go` signature changes, our patched version breaks.
    detect via src/engines/maia_engine.py:verify_maia3_patch() (one-shot
    self-test at startup, returns False if no candidate has `policy`).
  * If the patched wrapper isn't on disk, the engine falls back to
    upstream `maia3-uci` and the reranker transparently degrades to the
    rank-decay proxy. verify_maia3_patch() catches this too.

Deployment:
  * Default: src/engines/maia_engine.py:resolve_maia3_command() auto-
    detects this wrapper at scripts/maia3_patched_uci.py and uses it
    when MAIA3_COMMAND is unset.
  * Explicit: set MAIA3_COMMAND="python <repo>/scripts/maia3_patched_uci.py
    --model maia3-5m" to bypass the auto-detect.
  * Rollback: set MAIA3_COMMAND="maia3-uci --model maia3-5m" to revert
    to the unpatched upstream.
"""
import sys

import maia3.uci


_original_cmd_go = maia3.uci.Maia3UCIEngine.cmd_go


def _patched_cmd_go(self, line):
    self.ensure_model_loaded()
    move, top_moves = self.score_moves()
    for rank, item in enumerate(top_moves, start=1):
        win, draw, loss = item["wdl"]
        cp = maia3.uci.cp_from_wdl(item["wdl"])
        prob = item.get("policy", 0.0)
        print(
            f"info depth 1 multipv {rank} score cp {cp} wdl {win} {draw} {loss} "
            f"policy {prob:.6f} "
            f"pv {item['move'].uci()}",
            flush=True,
        )
    if "infinite" in line.split():
        self.pending_bestmove = move
        self.pending_search = True
        return
    self.print_bestmove(move)


maia3.uci.Maia3UCIEngine.cmd_go = _patched_cmd_go


if __name__ == "__main__":
    maia3.uci.main()
