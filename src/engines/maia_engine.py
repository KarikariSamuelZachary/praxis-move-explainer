"""
Maia-3 engine wrapper for human-like chess move generation.

Mirrors the Stockfish wrapper pattern. Maia-3 is a transformer that predicts
human moves at a given Elo; it is NOT a search engine. It ships as a UCI
executable (`maia3-uci`) installed via pip from
https://github.com/CSSLab/maia3 and downloads its checkpoint from Hugging Face
on first use (pre-warmed at deploy time via `scripts/prewarm_maia3.py`).

We run the `maia3-uci --model maia3-5m` entrypoint (smallest, CPU-friendly) as
a single long-lived UCI engine instance configured per-request via UCI options.
"""
import logging
import os
import time
from threading import Lock
from typing import Optional

import chess
import chess.engine

log = logging.getLogger(__name__)


class MaiaUnavailableError(RuntimeError):
    """Raised when Maia-3 inference is attempted but the engine never started.

    Distinct from a generic RuntimeError so callers (e.g. the sparring flow)
    can surface a clear "Maia unavailable" message to the user instead of
    mislabeling the failure as a downstream Stockfish/eval problem.
    """

# Smallest model preset (CPU-friendly). Used as the default.
MAIA3_DEFAULT_MODEL = "maia3-5m"
MAIA3_DEFAULT_ELO = 1500


def resolve_maia3_command() -> list[str]:
    """
    Resolve the UCI launch command for the Maia-3 5M preset.

    Preference order:
      1. MAIA3_COMMAND env var (full pre-split command line).
      2. `maia3-uci --model maia3-5m` on PATH.
      3. `python -m maia3.uci --model maia3-5m` if requested via
         MAIA3_USE_PYTHON_MODULE=1.
    """
    configured = os.getenv("MAIA3_COMMAND")
    if configured:
        import shlex

        return shlex.split(configured)

    if os.getenv("MAIA3_USE_PYTHON_MODULE") == "1":
        import sys

        return [sys.executable, "-m", "maia3.uci", "--model", MAIA3_DEFAULT_MODEL]

    return ["maia3-uci", "--model", MAIA3_DEFAULT_MODEL]


def prewarm_maia3_model() -> bool:
    """
    Pre-download and load the Maia-3 5M checkpoint into the Hugging Face cache.

    Call this during the build/deploy step so a live user's first request
    does not trigger a checkpoint download from Hugging Face. This starts the
    UCI engine and performs one throwaway inference so the checkpoint is both
    downloaded and validated. Returns True if Maia-3 is ready afterwards.
    """
    log.info("Pre-warming Maia-3 5M checkpoint (Hugging Face download)...")
    started = time.time()

    try:
        board = chess.Board()
        with Maia3Engine() as engine:
            engine.best_move(board, elo=MAIA3_DEFAULT_ELO, temperature=0.0)
        log.info(
            "Maia-3 5M checkpoint cached and inference validated after %.1fs",
            time.time() - started,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Maia-3 pre-warm failed (build env may lack outbound HF access): %s",
            exc,
        )
        return False


class Maia3Engine:
    """
    Long-lived Maia-3 UCI engine instance.

    Maia-3 is configured per-request via the `Elo` UCI option rather than
    restarting the process. Use the module-level `get_maia3()` accessor to
    obtain the shared singleton; call `close_maia3()` on shutdown.
    """

    MODEL = MAIA3_DEFAULT_MODEL

    def __init__(self, command: Optional[list[str]] = None, elo: int = MAIA3_DEFAULT_ELO):
        self.command = command if command is not None else resolve_maia3_command()
        self.elo = int(elo)
        self.engine: Optional[chess.engine.SimpleEngine] = None
        self._lock = Lock()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    @property
    def started(self) -> bool:
        # The engine handle exists AND the underlying subprocess hasn't
        # terminated. python-chess attaches `transport` to the SimpleEngine
        # once popen_uci() has forked the subprocess; its get_returncode()
        # returns None while alive and an int exit code once terminated.
        # This is the same probe python-chess itself uses in __repr__(), so
        # it's stable across versions even though `transport` isn't strictly
        # public API. A bare `self.engine is not None` check would keep
        # reporting True after a mid-session OOM/SIGKILL — which is exactly
        # the bug we're closing here.
        if self.engine is None:
            return False
        transport = getattr(self.engine, "transport", None)
        if transport is None:
            return False
        try:
            return transport.get_returncode() is None
        except Exception:  # noqa: BLE001
            return False

    def start(self):
        log.info("Starting Maia-3 UCI engine: %s", " ".join(self.command))
        self.engine = chess.engine.SimpleEngine.popen_uci(self.command)
        try:
            self.engine.configure(
                {"Elo": self.elo, "Temperature": 0}
            )
        except chess.engine.EngineError:
            # Some presets expose Elo only; fall back to the --elo flag at popen.
            log.warning("Maia-3 runtime Elo config unsupported; using launch-time Elo.")

    def close(self):
        if self.engine:
            try:
                self.engine.quit()
            except chess.engine.EngineError:
                pass
            self.engine = None

    def mark_dead(self) -> None:
        # Drop the engine handle WITHOUT attempting `quit()` — the
        # subprocess is gone, so poking it via UCI would hang or raise
        # BrokenPipeError. After this `.started` reports False, the health
        # endpoint flips to unavailable, and get_maia3()/start_maia3() can
        # rebuild from a clean slate on the next cold start.
        self.engine = None

    def best_move(
        self,
        board: chess.Board,
        elo: Optional[int] = None,
        temperature: float = 0.0,
    ) -> dict:
        """
        Return Maia-3's suggested move for `board` at the given Elo.

        `temperature=0` selects the argmax human move; a small positive value
        samples from Maia-3's human-move distribution (useful for the sparring
        bot). Returns a dict with uci, san, elo, temperature, and inference_ms.

        Raises MaiaUnavailableError whenever the engine is not usable —
        whether that's because it never started, or because it crashed
        mid-session (OOM, SIGKILL, UCI protocol failure). In the mid-session
        case the singleton is marked dead so the health endpoint flips to
        False and the next request sees a fast typed error instead of
        repeatedly poking the same dead subprocess.
        """
        if not self.engine:
            raise MaiaUnavailableError(
                "Maia-3 engine is not running. The startup attempt failed or "
                "was never made; see the application logs for the underlying "
                "reason (commonly a missing checkpoint, unreachable Hugging "
                "Face at build time, or the `maia3-uci` entrypoint missing "
                "from PATH)."
            )

        try:
            with self._lock:
                return self._best_move_locked(board, elo=elo, temperature=temperature)
        except chess.engine.EngineError as exc:
            # Mid-session failure of some kind. Distinguish two cases so the
            # health endpoint reflects reality: if the subprocess has truly
            # terminated (OOM kill, SIGKILL, broken UCI pipe once it's gone),
            # mark dead so subsequent is_maia_available() reports False and
            # callers fail fast; if the subprocess is still alive (a
            # transient protocol error on a healthy process), do NOT mark
            # dead — a later request may still succeed.
            transport = getattr(self.engine, "transport", None)
            really_dead = (
                transport is None
                or not callable(getattr(transport, "get_returncode", None))
                or transport.get_returncode() is not None
            )
            if really_dead:
                log.error(
                    "Maia-3 subprocess terminated mid-request (returncode=%r): %s",
                    (transport.get_returncode() if transport is not None else None),
                    exc,
                )
                self.mark_dead()
                raise MaiaUnavailableError(
                    "Maia-3 engine became unavailable mid-request — the "
                    "underlying subprocess terminated (e.g. OOM, crash, or "
                    "UCI protocol failure). It has been marked dead; "
                    "subsequent requests will fail fast until the engine is "
                    "restarted (next cold boot via start_maia3())."
                ) from exc
            log.error(
                "Maia-3 raised EngineError but subprocess is still alive "
                "(not marking dead): %s",
                exc,
            )
            raise MaiaUnavailableError(
                "Maia-3 raised an engine error mid-request. The subprocess "
                "is still alive, so it has NOT been marked dead — retry may "
                "succeed. See the application logs for the underlying error."
            ) from exc

    def best_move_candidates(
        self,
        board: chess.Board,
        multipv: int = 5,
        self_elo: Optional[int] = None,
        oppo_elo: Optional[int] = None,
    ) -> list[dict]:
        """
        Return Maia-3's ranked candidate moves for `board` via MultiPV.

        Unlike best_move() (which calls engine.play() and discards the
        subprocess's multipv info lines), this calls engine.analyse() with
        multipv=N to surface the top-N candidate moves by policy
        probability. Each candidate carries the WDL-derived cp score from
        Maia's forward pass over the resulting position.

        Maia-3 is a policy network, not a search engine: the candidates are
        ranked by policy probability at depth 1, the score is WDL-derived
        (cp = win - loss, permille-scale), and the "PV" is a single move,
        not a variation. Treat the output as a ranked candidate list with
        policy + resulting-position WDL — not as search output.

        `self_elo` and `oppo_elo` are wired through the UCI `SelfElo` and
        `OppoElo` setoptions independently. maia3-uci's policy forward pass
        consumes (self_elo, oppo_elo) as the side-to-move / other-side
        identities; for the per-candidate WDL pass the candidate's
        resulting position has the *opponent* to move, so the subprocess
        internally swaps the two values (uci.py:343-346). Net: pass the
        side-to-move player's rating as `self_elo` and the opponent's as
        `oppo_elo`; the wrapper handles the swap.

        Validation rules:
          * Pass BOTH `self_elo` and `oppo_elo`, or NEITHER. Mixed (one
            None, one set) raises ValueError — silently reusing the set
            value for the missing side would be exactly the stale-state
            class of bug we just fixed.
          * When both are None, both default to MAIA3_DEFAULT_ELO (1500),
            which is the maia3-uci out-of-the-box default
            (maia3/uci.py:71: argparse default=1500; advertised in UCI
            as `option name SelfElo type spin default 1500` and the
            matching OppoElo line).
          * When both are provided they must be plain ints in [0, 5000]
            (the UCI advertised range). bool / float / str are rejected
            even if they'd coerce — same strict-int rule as `multipv`.

        Stale-state note (Part A fix): the engine is a long-lived
        singleton shared across calls. The previous implementation
        cached `self.elo` and *skipped* `engine.configure({"Elo": N})`
        when the requested value matched the cache, which meant a call
        with `elo=None` after a prior `elo=2500` call would silently
        replay elo=2500 — verified live to produce bit-identical output
        to the elo=2500 call (cp=-22, wdl 0.447/0.084/0.469) instead of
        the 1500 baseline. We fix this two ways: (1) `None` now resolves
        to the explicit default, not the last-set cache; (2) the locked
        helper always emits `SelfElo`/`OppoElo` setoption lines — no
        "skip if matches cache" branch. The cost is one extra UCI
        round-trip per call (a few ms) which is negligible against the
        multipv forward passes (see the per-multipv cost table below).

        There is deliberately NO `temperature` parameter here.
        Temperature in maia3-uci only enters `sample_from_logits()`,
        which controls the single `bestmove` UCI line; the multipv
        candidates come from `torch.topk(softmax(logits), N)` over the
        unscaled logits, so temperature has zero effect on what
        `analyse(multipv=N)` returns. Verified live: temp=0.0, 3.0 and
        10.0 produce bit-identical candidate lists.

        Cost grows with multipv, but NOT as "N full forward passes". The
        subprocess performs one policy forward pass plus N smaller
        per-candidate WDL evaluations; the WDL pass is NOT a full second
        forward pass (it scores the resulting position for each candidate
        without re-running the full policy network). Empirical timing on
        a warm `maia3-5m` engine at the starting position (10 runs of
        multipv=1, 5 runs each of multipv=5 and 10):

            best_move()                    mean ≈ 204 ms  range [168, 237]
            best_move_candidates(mpv=1)    mean ≈ 118 ms  range [100, 133]
            best_move_candidates(mpv=5)    mean ≈ 202 ms  range [188, 215]
            best_move_candidates(mpv=10)   mean ≈ 354 ms  range [333, 413]

        Decomposition (least-squares fit): a fixed per-call cost of ~90 ms
        (policy forward pass + UCI round-trips + lock) plus ~22-30 ms per
        multipv slot added. So multipv=N ≈ 90 + N·25 ms on this preset.

        Three counterintuitive findings worth recording:

        (1) `best_move()` is NOT faster than `best_move_candidates(mpv=1)`.
            On a warm engine at mpv=1 the candidates path is ~86 ms
            *cheaper* (118 vs 204). Two causes: best_move() issues two UCI
            `configure` round-trips (Elo + Temperature) where the candidates
            path issues one (SelfElo+OppoElo in a single setoption pair);
            and the play() UCI path runs `sample_from_logits` plus the
            bestmove line, where analyse(multipv=N) does topk + per-candidate
            WDL. The earlier claim (prior session) that "play() and
            analyse(multipv=1) have identical cost" because both pass
            `Limit(nodes=1)` was WRONG — verified empirically here.

        (2) The docstring's previous claim of "~25-30s vs ~1s for
            best_move()" was wildly off for the 5M preset. Those numbers
            may have been from the 79M preset (maia3-79m) or a cold
            checkpoint load conflated with inference cost; they don't
            match the warm-engine 5M CPU reality (~0.2-0.35s across the
            multipv range tested).

        (3) Per-candidate overhead is ~22-30 ms, not ~90 ms (the policy
            pass cost). So the previous "second forward pass per candidate
            ... roughly N forward passes in addition to the policy pass"
            phrasing overstated magnitude by ~3-4x while getting the
            qualitative shape right (per-candidate work does scale
            linearly with N).

        Raises MaiaUnavailableError whenever the engine is not usable
        (never started or crashed mid-session), mirroring best_move()'s
        contract. Raises ValueError on invalid `multipv` or Elo args.
        """
        # --- multipv validation (strict int; reject bool/float/str) ---
        if isinstance(multipv, bool) or not isinstance(multipv, int):
            raise ValueError(
                f"multipv must be an integer in [1, 20]; got {multipv!r} "
                f"(type {type(multipv).__name__})"
            )
        multipv_int = multipv
        if not 1 <= multipv_int <= 20:
            raise ValueError(f"multipv must be in [1, 20]; got {multipv_int}")

        # --- Elo validation ---
        # Mixed (one None, one set) is rejected: silently reusing the set
        # value for the missing side would be a stale-state footgun.
        if (self_elo is None) != (oppo_elo is None):
            raise ValueError(
                f"Pass both self_elo and oppo_elo, or neither (both default "
                f"to {MAIA3_DEFAULT_ELO}). Got self_elo={self_elo!r}, "
                f"oppo_elo={oppo_elo!r}."
            )
        if self_elo is None and oppo_elo is None:
            eff_self_elo = MAIA3_DEFAULT_ELO
            eff_oppo_elo = MAIA3_DEFAULT_ELO
        else:
            # Strict int per side, same rule as multipv. bool is a subclass
            # of int but a footgun (True==1); floats would silently truncate
            # via int() and sneak through. Range is [0, 5000] — this matches
            # the maia3-uci advertised UCI spin option exactly:
            #   maia3/uci.py:364-366:
            #     print(f"option name Elo type spin default {self.cfg.elo} min 0 max 5000")
            #     print(f"option name SelfElo type spin default {self.cfg.elo} min 0 max 5000")
            #     print(f"option name OppoElo type spin default {self.cfg.elo} min 0 max 5000")
            # and python-chess exposes the same min/max on the parsed
            # EngineOption (verified: opt.min == 0, opt.max == 5000). Reading
            # this from source rather than the cfg field means a future
            # model preset cannot drift this out from under us silently.
            for _label, _val in (("self_elo", self_elo), ("oppo_elo", oppo_elo)):
                if isinstance(_val, bool) or not isinstance(_val, int):
                    raise ValueError(
                        f"{_label} must be an int in [0, 5000]; got {_val!r} "
                        f"(type {type(_val).__name__})"
                    )
                if not 0 <= _val <= 5000:
                    raise ValueError(f"{_label} must be in [0, 5000]; got {_val}")
            eff_self_elo = self_elo
            eff_oppo_elo = oppo_elo

        if not self.engine:
            raise MaiaUnavailableError(
                "Maia-3 engine is not running. The startup attempt failed or "
                "was never made; see the application logs for the underlying "
                "reason (commonly a missing checkpoint, unreachable Hugging "
                "Face at build time, or the `maia3-uci` entrypoint missing "
                "from PATH)."
            )

        try:
            with self._lock:
                return self._best_move_candidates_locked(
                    board,
                    multipv=multipv_int,
                    self_elo=eff_self_elo,
                    oppo_elo=eff_oppo_elo,
                )
        except chess.engine.EngineError as exc:
            # Same mid-session failure handling as best_move(): distinguish
            # truly-terminated subprocess (mark dead) from a transient
            # protocol error on a live process (leave alive for retry).
            transport = getattr(self.engine, "transport", None)
            really_dead = (
                transport is None
                or not callable(getattr(transport, "get_returncode", None))
                or transport.get_returncode() is not None
            )
            if really_dead:
                log.error(
                    "Maia-3 subprocess terminated mid-request (returncode=%r): %s",
                    (transport.get_returncode() if transport is not None else None),
                    exc,
                )
                self.mark_dead()
                raise MaiaUnavailableError(
                    "Maia-3 engine became unavailable mid-request — the "
                    "underlying subprocess terminated (e.g. OOM, crash, or "
                    "UCI protocol failure). It has been marked dead; "
                    "subsequent requests will fail fast until the engine is "
                    "restarted (next cold boot via start_maia3())."
                ) from exc
            log.error(
                "Maia-3 raised EngineError but subprocess is still alive "
                "(not marking dead): %s",
                exc,
            )
            raise MaiaUnavailableError(
                "Maia-3 raised an engine error mid-request. The subprocess "
                "is still alive, so it has NOT been marked dead — retry may "
                "succeed. See the application logs for the underlying error."
            ) from exc

    def _best_move_candidates_locked(
        self,
        board: chess.Board,
        multipv: int,
        self_elo: int,
        oppo_elo: int,
    ) -> list[dict]:
        # Always reconfigure both Elo channels explicitly — do NOT skip on a
        # cached "last value". The engine subprocess is a long-lived
        # singleton shared across calls (and shared with best_move()), and
        # any cached value can be stale: a prior best_move_candidates() call
        # with different self_elo/oppo_elo, or a best_move() call that wrote
        # the shared `Elo` setoption, would leave the subprocess at a
        # different state than whatever we cached. Emitting both setoption
        # lines unconditionally is one cheap UCI round-trip (a few ms)
        # against multipv calls that cost ~0.1-0.4s on the 5M preset (see
        # the per-multipv cost table in the method docstring above), and
        # it guarantees the subprocess is at exactly the requested values
        # regardless of history. This is the Part A fix.
        self.engine.configure({"SelfElo": self_elo, "OppoElo": oppo_elo})

        # Keep self.elo in sync ONLY for the symmetric case (both sides
        # equal), so best_move()'s `effective_elo != self.elo` heuristic
        # doesn't wrongly skip a needed reconfigure after a symmetric
        # candidates call.
        #
        # Asymmetric case — sentinel + subprocess reset (Part A fix for the
        # best_move() stale-Elo leak):
        #
        # When self_elo != oppo_elo the subprocess is left configured at
        # (self_elo, oppo_elo) asymmetric — a single `self.elo` cannot
        # represent both. We need to make best_move() reconfigure on its
        # next call regardless of which Elo arg (or `None`) the caller
        # passes. Two intertwined concerns:
        #
        # (1) For next-call `best_move(elo=N)` (explicit N):
        #     best_move()'s `effective_elo = int(elo) if elo is not None
        #     else self.elo`; and `engine.configure({"Elo": N})` is only
        #     sent if `effective_elo != self.elo`. So if we leave self.elo
        #     at the prior symmetric value (say 1500) and the next caller
        #     passes elo=1500, the cache check passes, configure is
        #     skipped, and the subprocess silently stays at the asymmetric
        #     (2500, 1100). Verified live — `best_move()` reports
        #     elo=1500 while the subprocess actually produces the
        #     asymmetric WDL (0.932/0.020/0.048 instead of 0.461/0.025/
        #     0.514). Setting self.elo = -1 sentinel (a value that can
        #     never match a real Elo, which maia3-uci advertises as
        #     [0, 5000]) forces the cache check to fail for any real N,
        #     so configure is always re-emitted.
        #
        # (2) For next-call `best_move(elo=None)` (no override):
        #     best_move() falls back to `effective_elo = self.elo` (the
        #     sentinel -1), so `effective_elo != self.elo` evaluates False
        #     and configure is STILL skipped. The sentinel alone is
        #     insufficient for this path — verified live. Without touching
        #     best_move() (which is out of scope per the task brief), the
        #     fix is to reset the subprocess to a symmetric default Elo
        #     AFTER the analyse completes, so the leftover state is
        #     benign and any best_move(elo=None) call sees a usable
        #     symmetric policy at MAIA3_DEFAULT_ELO. The reset is at the
        #     bottom of this function (look for "asymmetric reset
        #     suffix"); it's a single extra `engine.configure` round-trip
        #     (a few ms) against multipv calls that cost ~0.1-0.4s on the
        #     5M preset — negligible.
        #
        # Combined: the sentinel forces explicit-Elo callers to
        # reconfigure, and the subprocess reset makes elo=None callers
        # see the symmetric default. Both paths are correct.
        if self_elo == oppo_elo:
            self.elo = self_elo
        else:
            self.elo = -1  # sentinel; cannot match any real Elo in [0, 5000]
            # (subprocess reset happens near the function's end — see below)

        # NOTE: no Temperature configure here. maia3-uci's multipv path
        # derives candidates from `torch.topk(softmax(logits), N)` over the
        # unscaled logits; temperature only feeds `sample_from_logits()` for
        # the bestmove UCI line, which analyse(multipv=N) discards. Setting
        # it would be a no-op — see best_move_candidates() docstring.

        started = time.time()
        # python-chess manages MultiPV itself: engine.configure({"MultiPV": N})
        # raises "cannot set MultiPV which is automatically managed". The
        # multipv kwarg on analyse() is the supported path and emits the
        # `setoption name MultiPV value N` + `go ...` sequence the subprocess
        # expects.
        infos = self.engine.analyse(
            board,
            chess.engine.Limit(nodes=1),
            multipv=multipv,
        )
        # Surface wall-clock cost via the logger so callers can tune multipv
        # without binding timing into the return shape (spec is a list of
        # candidate dicts only).
        log.debug(
            "Maia-3 analyse(multipv=%d) took %.1f ms",
            multipv, (time.time() - started) * 1000,
        )

        # analyse(multipv=N) always returns a list of info dicts (even for
        # multipv=1, where len == 1). Defensive: tolerate a bare dict for
        # non-multipv code paths that may sneak through.
        if isinstance(infos, dict):
            infos = [infos]

        candidates: list[dict] = []
        for info in infos:
            pv = info.get("pv") or []
            move_uci = pv[0].uci() if pv else ""

            # score: cp from the side-to-move (relative) perspective. Maia's
            # UCI emits `score cp {cp}` from side-to-move; python-chess stores
            # it as PovScore with pov = side-to-move, so .relative.score()
            # returns the raw cp. Mate scores return None from .score(); fall
            # back to a large signed sentinel via .relative.score(mate_score).
            score_cp = None
            pov_score = info.get("score")
            if pov_score is not None:
                try:
                    score_cp = pov_score.relative.score()
                    if score_cp is None:
                        score_cp = pov_score.relative.score(mate_score=100000)
                except Exception:  # noqa: BLE001
                    score_cp = None

            # wdl: win/draw/loss from the side-to-move (relative) perspective,
            # normalized to probabilities summing to ~1.0. PovWdl.relative is
            # the Wdl trio from the side-to-move's POV; python-chess stores
            # the chances as permille ints (wins/draws/losses summing to 1000)
            # and exposes .winning_chance() / .drawing_chance() / .losing_chance()
            # as bound *methods* (not attributes) returning floats in [0, 1].
            # NB: the draw accessor is `drawing_chance`, not `draw_chance`.
            wdl_dict = None
            pov_wdl = info.get("wdl")
            if pov_wdl is not None:
                try:
                    rel = pov_wdl.relative
                    wdl_dict = {
                        "win": round(float(rel.winning_chance()), 4),
                        "draw": round(float(rel.drawing_chance()), 4),
                        "loss": round(float(rel.losing_chance()), 4),
                    }
                except Exception:  # noqa: BLE001
                    wdl_dict = None

            candidates.append({
                "move": move_uci,
                "score": score_cp,
                "wdl": wdl_dict,
            })

        # Asymmetric reset suffix (Part A fix — see the big comment block
        # where self.elo is set above). After analyse() returns we expect the
        # candidates results to be valid; if the call was asymmetric the
        # subprocess is still configured at (self_elo, oppo_elo) asymmetric and
        # self.elo is at the -1 sentinel. We reset the subprocess to
        # MAIA3_DEFAULT_ELO symmetric (1500, 1500) so that any subsequent
        # best_move() call seeing an `elo=None` override (which falls back to
        # self.elo = -1 and therefore SKIPS engine.configure) finds a benign
        # symmetric policy instead of the asymmetric leftover.
        #
        # Reset-failure handling (Part B): this is a non-fatal cleanup.
        # analyse() already succeeded and the candidates list is built, so a
        # reset failure (e.g. the subprocess just died mid-request, or a UCI
        # protocol error on a still-alive process) must NOT mask the
        # candidates result NOR trigger the outer EngineError handler —
        # which would otherwise translate the EngineError to
        # MaiaUnavailableError and discard the valid candidates. We catch the
        # EngineError locally and return the candidates regardless.
        #
        # But a silently-failed reset on a *still-alive* subprocess is a
        # real correctness hazard: the stale asymmetric (self_elo, oppo_elo)
        # and the -1 sentinel both persist with nothing surfacing it. So we
        # LOG AT ERROR (not warning) — matching every other real failure in
        # this file — and we drop the in-memory Elo cache to a value that
        # forces the next best_move() to reconfigure from scratch rather
        # than trust the cache. The cleanest such value is the existing -1
        # sentinel: it already cannot match any real Elo in [0, 5000], so
        # the next `best_move(elo=N)` (any real N) re-emits
        # `engine.configure({"Elo": N})` and the next `best_move(elo=None)`
        # resolves `effective_elo = -1`. That second path is the known
        # cosmetic wart (best_move out of scope): it skips configure and
        # hits a possibly-stale subprocess policy. There is no way to fix
        # that path from here without touching best_move(), so we surface it
        # loudly in the log instead — the engineer investigating the
        # "asymmetric Elo leak" symptom now has a clear trail.
        #
        # Why not retry the reset before giving up? A retry would burn
        # another UCI round-trip on a subprocess that just failed a
        # configure; if the subprocess is genuinely ill (e.g. dying), the
        # retry raises again and we'd have to swallow that too, doubling
        # the silent failure surface. The single-shot + loud-log approach
        # keeps the failure atomic and visible. If a caller sees this error
        # in logs and cares about Elo hygiene, restarting the engine
        # (close_maia3()/start_maia3()) is the deterministic recovery — and
        # the next outer EngineError path will mark_dead and surface that
        # automatically when the subprocess actually terminates.
        #
        # Why not raise from here? Two constraints: (1) we must not discard
        # the already-built candidates result; (2) genuine subprocess death
        # is the outer EngineError handler's job (it inspects
        # transport.get_returncode() and mark_dead+translates cleanly), so
        # raising here would either duplicate that logic or race it.
        # Logging at error + leaving the -1 sentinel in place threads the
        # needle: the candidates result is returned intact, the outer
        # handler still runs untouched for real death, AND the alive-but-
        # stale case is now visible in logs at a severity matching every
        # other real failure in this file.
        if self_elo != oppo_elo:
            try:
                self.engine.configure(
                    {"SelfElo": MAIA3_DEFAULT_ELO, "OppoElo": MAIA3_DEFAULT_ELO}
                )
            except chess.engine.EngineError as exc:
                log.error(
                    "Maia-3 subprocess reset to symmetric Elo failed after "
                    "asymmetric best_move_candidates(): %s. The candidates "
                    "result is still returned. The subprocess appears still "
                    "alive (else the outer EngineError handler would have "
                    "caught it), so Elo state is now STALE: both the "
                    "SelfElo/OppoElo setoption state and the in-memory "
                    "self.elo=-1 sentinel persist. The next best_move() call "
                    "may see a stale asymmetric policy. Restart the engine "
                    "(close_maia3()/start_maia3()) for deterministic Elo "
                    "hygiene. (Genuine subprocess death is handled "
                    "separately by the outer EngineError handler in "
                    "best_move_candidates().)",
                    exc,
                )

        return candidates

    def _best_move_locked(
        self,
        board: chess.Board,
        elo: Optional[int] = None,
        temperature: float = 0.0,
    ) -> dict:
        effective_elo = int(elo) if elo is not None else self.elo
        if effective_elo != self.elo:
            # Elo configure with loud failure handling — same shape of fix
            # as the asymmetric reset suffix in
            # _best_move_candidates_locked() (Part B of the prior task).
            # The old `except EngineError: pass` silently swallowed every
            # configure failure: a still-alive subprocess that rejected the
            # Elo setoption would log nothing, self.elo would stay at the
            # old value (the assignment is AFTER configure, so it never ran
            # — that part was accidentally correct), and play() would
            # proceed against the STALE Elo with no surfacing at all.
            # Distinguish alive-but-error from genuine subprocess death
            # using the same transport.get_returncode() probe the rest of
            # this file uses. For the alive case: log at ERROR and
            # continue — play() will run against stale Elo, which is
            # wrong but at least produces a move, and the log gives the
            # operator a trail. self.elo is NOT updated to effective_elo
            # (it was never applied to the subprocess); it stays at the
            # last successfully-applied value so the next best_move() with
            # the same effective_elo retries the configure rather than
            # wrongly skipping it. For the dead case: mark_dead() and
            # raise MaiaUnavailableError directly — MaiaUnavailableError is
            # RuntimeError (not EngineError), so it propagates past
            # best_move()'s outer `except EngineError` handler to the
            # caller without being re-caught.
            try:
                self.engine.configure({"Elo": effective_elo})
                self.elo = effective_elo
            except chess.engine.EngineError as exc:
                transport = getattr(self.engine, "transport", None)
                really_dead = (
                    transport is None
                    or not callable(getattr(transport, "get_returncode", None))
                    or transport.get_returncode() is not None
                )
                if really_dead:
                    log.error(
                        "Maia-3 subprocess terminated during Elo configure "
                        "(returncode=%r): %s. Marking dead; not attempting "
                        "play().",
                        (transport.get_returncode() if transport is not None else None),
                        exc,
                    )
                    self.mark_dead()
                    raise MaiaUnavailableError(
                        "Maia-3 engine became unavailable while configuring "
                        "Elo — the subprocess terminated. It has been marked "
                        "dead; subsequent requests will fail fast until the "
                        "engine is restarted (next cold boot via "
                        "start_maia3())."
                    ) from exc
                log.error(
                    "Maia-3 Elo configure failed while subprocess is alive "
                    "(not marking dead): %s. play() will run against stale "
                    "Elo=%d instead of requested Elo=%d. self.elo left "
                    "unchanged at %d so the next call retries the configure.",
                    exc, self.elo, effective_elo, self.elo,
                )
                # self.elo deliberately NOT updated — configure failed, the
                # value was never applied to the subprocess.

        try:
            self.engine.configure({"Temperature": temperature})
        except chess.engine.EngineError:
            pass

        started = time.time()
        # Maia-3 is a policy network, not a search engine: one node equals one
        # forward pass.
        result = self.engine.play(
            board,
            limit=chess.engine.Limit(nodes=1),
        )
        inference_ms = (time.time() - started) * 1000

        move_uci = result.move.uci() if result.move else ""
        move_san = board.san(result.move) if result.move else ""

        # python-chess exposes WDL-style info for Maia-3 bestmove; may be
        # absent. The `wdl` info entry is a PovWdl; .relative is the Wdl trio
        # from the side-to-move's POV, and the chance accessors are bound
        # *methods* (NOT attributes) returning floats in [0, 1]. The draw
        # accessor is `drawing_chance`, not `draw_chance`. The previous
        # attribute-style access (`.winning_chance * 1000` etc.) would have
        # raised TypeError on EVERY call and been silently caught by the
        # broad `except Exception` below — so IF the info dict contained a
        # wdl entry this block would have returned wdl=None and masked the
        # failure. Matching the already-correct extraction in
        # _best_move_candidates_locked() (call methods, use the right name,
        # round(float(...), 4)) is necessary so the block is correct when it
        # DOES run.
        #
        # IMPORTANT — verified by live test (Part A step 2): against the
        # unmodified maia3-uci subprocess, `engine.play(board, Limit(nodes=1))`
        # returns `PlayResult(info={})` — an EMPTY info dict — so the `if
        # "wdl" in info` guard below is False and the extraction block is
        # NEVER entered. The production symptom "best_move() returns
        # wdl=None on every request" is NOT caused by the attribute/method
        # bug (that block never executed to raise). The deeper root cause
        # is upstream: maia3-uci's bestmove UCI path emits no `info ... wdl`
        # lines for the single-pv bestmove response, only for
        # `analyse(multipv=N)` (probe: `play().info.keys() == []`,
        # `analyse(multipv=1)[0].keys() == ['depth','multipv','score',
        # 'wdl','pv']`). Fixing the extraction access pattern here is
        # necessary for correctness but is NOT sufficient to populate wdl
        # in production — that requires switching best_move() from play()
        # to analyse(multipv=1) (out of scope per the task brief —
        # "Touch only what's specified in each part"). Flagged here per
        # Part A step 3 ("flag anything else that looks like this same
        # shape of bug"). The symptom is identical to the attribute/method
        # bug the brief identifies (silent wdl=None on every call) but the
        # mechanism is an unreachable code block, not a swallowed exception.
        wdl = None
        info = getattr(result, "info", {}) or {}
        if "wdl" in info and info["wdl"] is not None:
            try:
                rel = info["wdl"].relative
                wdl = {
                    "win": round(float(rel.winning_chance()), 4),
                    "draw": round(float(rel.drawing_chance()), 4),
                    "loss": round(float(rel.losing_chance()), 4),
                }
            except Exception:  # noqa: BLE001
                wdl = None

        return {
            "best_move_uci": move_uci,
            "best_move_san": move_san,
            "elo": effective_elo,
            "temperature": temperature,
            "inference_ms": round(inference_ms, 1),
            "model": self.MODEL,
            "wdl": wdl,
        }


# --- Module-level singleton (lifecycle managed by FastAPI startup/shutdown) ---

# The singleton. `None` means "not started". A non-`None` value with
# `.started == False` would be a known-failed boot (leftover state is bad —
# see below); `start_maia3()` must never _leave_ such a half-constructed
# instance here, because `get_maia3()` would then return it and the runtime
# check `if _maia3 is None` would wrongly believe Maia is up. On any start
# failure we ensure `_maia3` is reset back to `None` so the failure is loud
# and retriable, not silently swallowed.
_maia3: Optional[Maia3Engine] = None


def start_maia3() -> Maia3Engine:
    """Start the singleton Maia-3 engine or raise on failure.

    This function is safe to call from startup: it never leaves the module
    in a "looks-started-but-is-broken" state. If `Maia3Engine.start()`
    raises, the module global is rolled back to `None` before propagating
    so a later `is_maia_available()` returns False — no false positive.
    """
    global _maia3
    if _maia3 is not None and _maia3.started:
        return _maia3

    instance = Maia3Engine()
    instance.start()  # raises on failure BEFORE we publish the global
    _maia3 = instance
    return _maia3


def get_maia3() -> Maia3Engine:
    """Return the shared Maia-3 engine, raising MaiaUnavailableError if absent.

    Unlike the previous implementation, this never silently retries a
    failed boot at request time (a request-time retry would mask the root
    cause with a generic `popen_uci` failure and a 30s+ tail latency). If
    the boot-time `start_maia3()` failed, callers see a clear, typed error
    immediately. If Maia has simply never been started (e.g. a unit test
    hitting the debug endpoint without invoking startup), we attempt to
    start it once — that path keeps the debug endpoint self-sufficient.
    """
    if _maia3 is None:
        try:
            return start_maia3()
        except Exception as exc:
            raise MaiaUnavailableError(
                "Maia-3 engine is not running and start-on-demand failed."
            ) from exc
    if _maia3 is not None and not _maia3.started:
        # Defensive: should not happen given start_maia3() roll-back, but
        # guard against direct monkey-patching of `_maia3` in tests.
        raise MaiaUnavailableError(
            "Maia-3 singleton is present but the underlying UCI process is "
            "not running. This indicates a crashed/closed engine."
        )
    return _maia3


def is_maia_available() -> bool:
    """Cheap, side-effect-free health check used by startup logging and the
    /api/debug/maia-health endpoint. True only when the singleton exists
    AND the underlying UCI subprocess is actually live."""
    return _maia3 is not None and _maia3.started


def close_maia3():
    global _maia3
    if _maia3 is not None:
        _maia3.close()
        _maia3 = None
