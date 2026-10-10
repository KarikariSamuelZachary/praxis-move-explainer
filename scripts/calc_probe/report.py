"""report.md generation. Reads only stored SQLite rows; engine is never called,
so threshold sensitivity is recomputed from per-ply data."""

from __future__ import annotations

import json
import math
import sqlite3
import statistics

from puzzle_line import BANDS, family_of
from verdicts import build_segments, judge_stage, recompute_final

BAND_LABELS = [f"{lo}+" if hi is None else f"{lo}-{hi}" for lo, hi in BANDS]
FAMILIES = ("mate", "quiet", "endgame", "forcing")
THRESHOLDS = (100, 150, 200, 300)
NS = (2, 3, 4, 5, 6)


def _q(con, sql, args=()):
    return con.execute(sql, args).fetchall()


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    """Exact binomial 95% CI (pure stdlib; n is small here)."""
    if n == 0:
        return (0.0, 1.0)
    if k == 0:
        return (0.0, 1.0 - (alpha / 2) ** (1.0 / n))
    if k == n:
        return ((alpha / 2) ** (1.0 / n), 1.0)

    def tail_ge(p: float) -> float:
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                   for i in range(k, n + 1))

    def tail_le(p: float) -> float:
        return sum(math.comb(n, i) * p ** i * (1 - p) ** (n - i)
                   for i in range(0, k + 1))

    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if tail_ge(mid) > alpha / 2:
            hi = mid
        else:
            lo = mid
    lower = (lo + hi) / 2
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if tail_le(mid) > alpha / 2:
            lo = mid
        else:
            hi = mid
    return (lower, (lo + hi) / 2)


def _puzzle_plies(con, run_id):
    """{puzzle_id: [ply rows]} with parsed stage JSONs, ordered by index."""
    out: dict[str, list[dict]] = {}
    for r in _q(con, "SELECT puzzle_id, reply_index, single_legal, stage1_json,"
                     " stage2_json, fail_sample, final_verdict, margin_cp,"
                     " mate_involved, wdl_margin FROM probe_defender_plies"
                     " WHERE run_id=?", (run_id,)):
        pid, idx, sl, s1, s2 = r[0], r[1], r[2], r[3], r[4]
        out.setdefault(pid, []).append({
            "i": idx, "single": bool(sl),
            "stage1": json.loads(s1) if s1 else None,
            "stage2": json.loads(s2) if s2 else None,
            "fail_sample": r[5], "verdict": r[6], "margin": r[7],
            "mate": r[8], "wdl": r[9]})
    for lst in out.values():
        lst.sort(key=lambda d: d["i"])
    return out


def _segments_at_threshold(plies: list[dict], threshold: int):
    """Recompute verdicts at threshold, rebuild maximal segments.

    Mirrors run policy: Stage 2 decides only where it ran on a Stage 1 pass
    (fail-sample Stage 2 runs do NOT decide; failures stay final).
    Returns (flags, [(start, end, trivial)], margins, mates).
    trivial = every between-reply single-legal (no engine verification).
    """
    flags, margins, mates = [], [], []
    for pl in plies:
        if pl["single"]:
            v, m, mate = "clean", None, 0
        else:
            s1 = pl["stage1"]
            s1v = (s1 or {}).get("judgement", {}).get("verdict")
            stage = pl["stage2"] if (pl["stage2"] is not None
                                     and s1v == "clean") else s1
            j = judge_stage(stage["stored"], stage["pvs"], threshold)
            v, m, mate = j["verdict"], j["margin_cp"], j["mate_involved"]
        flags.append(v == "clean")
        margins.append(m)
        mates.append(mate)
    n_solver = len(plies) + 1
    segs = build_segments(["s"] * n_solver, ["d"] * len(plies), flags)
    out = []
    for s in segs:
        a, b = s["start"], s["end"]
        trivial = all(plies[i]["single"] for i in range(a, b))
        out.append((a, b, trivial))
    return flags, out, margins, mates


def write_report(con: sqlite3.Connection, run_id: str, args, engine_version: str,
                 stats: dict, path: str) -> None:
    try:
        sha = con.execute("SELECT engine_sha256 FROM probe_runs WHERE run_id=?",
                          (run_id,)).fetchall()[0][0]
    except Exception:
        sha = None
    L: list[str] = []
    add = L.append
    puzzles = _q(con, "SELECT puzzle_id, rating, band, family, depth_bucket,"
                      " solver_move_count, status, engine_calls"
                      " FROM probe_puzzles WHERE run_id=?", (run_id,))
    plies = _puzzle_plies(con, run_id)
    n_puzzles = len(puzzles)
    n_plies = sum(len(v) for v in plies.values())
    n_single = sum(1 for v in plies.values() for p in v if p["single"])
    s1_pass = s1_fail = 0
    for v in plies.values():
        for p in v:
            if p["single"]:
                continue
            j = (p["stage1"] or {}).get("judgement", {})
            if j.get("verdict") == "clean":
                s1_pass += 1
            else:
                s1_fail += 1
    s2_on_pass = _q(con, "SELECT COUNT(*) FROM probe_defender_plies WHERE"
                         " run_id=? AND single_legal=0 AND stage2_json IS NOT"
                         " NULL AND fail_sample=0", (run_id,))[0][0]
    flips_on_pass = _q(con, "SELECT COUNT(*) FROM probe_defender_plies WHERE"
                            " run_id=? AND fail_sample=0 AND flipped=1",
                       (run_id,))[0][0]
    n_fail_sample = _q(con, "SELECT COUNT(*) FROM probe_defender_plies WHERE"
                            " run_id=? AND fail_sample=1", (run_id,))[0][0]
    rescued = 0
    for (s2,) in _q(con, "SELECT stage2_json FROM probe_defender_plies WHERE"
                         " run_id=? AND fail_sample=1", (run_id,)):
        if s2 and json.loads(s2).get("judgement", {}).get("verdict") == "clean":
            rescued += 1
    n_seg_puzzles = _q(con, "SELECT COUNT(DISTINCT puzzle_id) FROM"
                             " clean_forced_segments WHERE run_id=?",
                       (run_id,))[0][0]
    n_seg_puzzles_nt = _q(con, "SELECT COUNT(DISTINCT puzzle_id) FROM"
                               " clean_forced_segments WHERE run_id=? AND"
                               " trivially_forced=0", (run_id,))[0][0]
    n_segs = _q(con, "SELECT COUNT(*) FROM clean_forced_segments WHERE run_id=?",
                (run_id,))[0][0]
    n_segs_nt = _q(con, "SELECT COUNT(*) FROM clean_forced_segments WHERE"
                        " run_id=? AND trivially_forced=0", (run_id,))[0][0]

    add(f"# Blind Calculation survivor probe — {run_id}")
    add("")
    add(f"Engine: {engine_version} | sha256={sha} | seed={args.seed} | "
        f"per-cell={args.per_cell} | workers={args.workers} | "
        f"nodes={args.nodes_stage1}/{args.nodes_stage2} | "
        f"threshold={args.threshold}cp | verify_survivors={args.verify_survivors} | "
        f"fail_sample_frac={getattr(args, 'fail_sample_frac', 0)} | "
        f"check_solver={args.check_solver} "
        f"(subsample={getattr(args, 'solver_subsample', 0)})")
    add("Escalation: Stage 2 runs on Stage 1 passes only; Stage 1 failures "
        "are final (+ seeded fail sample). Trivially-forced segments "
        "(zero engine-verified replies) reported separately.")
    add("")
    add("## 1. Funnel")
    add(f"- sampled puzzles: {n_puzzles}")
    add(f"- defender replies analysed: {n_plies}")
    single_pct = (100.0 * n_single / n_plies) if n_plies else 0.0
    add(f"- single-legal replies (auto-clean, no engine call): {n_single} "
        f"({single_pct:.1f}%)")
    add(f"- Stage 1 verdicts (engine plies only): pass={s1_pass} "
        f"fail={s1_fail}")
    add(f"- Stage 2 verification runs on passes: {s2_on_pass}")
    flipr = (100.0 * flips_on_pass / s1_pass) if s1_pass else 0.0
    add(f"- flip rate (fraction of Stage 1 PASSES): {flips_on_pass}/{s1_pass} "
        f"({flipr:.1f}%)")
    resc = (100.0 * rescued / n_fail_sample) if n_fail_sample else 0.0
    add(f"- seeded fail sample escalated: {n_fail_sample}, rescued by Stage 2: "
        f"{rescued} ({resc:.1f}% rescue rate; failures stay final)")
    add(f"- puzzles with >=1 clean segment: {n_seg_puzzles}/{n_puzzles} "
        f"(excl. trivially-forced: {n_seg_puzzles_nt}/{n_puzzles})")
    add(f"- total clean segments: {n_segs} (non-trivial: {n_segs_nt})")
    add("")

    seg_rows = _q(con, "SELECT puzzle_id, solver_move_count, trivially_forced,"
                       " engine_verified_replies FROM clean_forced_segments"
                       " WHERE run_id=?", (run_id,))
    by_puzzle: dict[str, list[int]] = {}
    by_puzzle_nt: dict[str, list[int]] = {}
    for pid, ln, triv, _ in seg_rows:
        by_puzzle.setdefault(pid, []).append(ln)
        if not triv:
            by_puzzle_nt.setdefault(pid, []).append(ln)
    fam_of = {r[0]: r[3] for r in puzzles}
    band_of_map = {r[0]: r[2] for r in puzzles}
    sampled_per_band = {}
    for r in puzzles:
        sampled_per_band[r[2]] = sampled_per_band.get(r[2], 0) + 1

    add("## 2. Segment-length distribution (incl. / excl. trivially-forced)")
    for label, rows in (("incl", seg_rows),
                       ("excl", [r for r in seg_rows if not r[2]])):
        buckets = {"2": 0, "3": 0, "4": 0, "5": 0, "6+": 0}
        for _, ln, _, _ in rows:
            buckets["6+" if ln >= 6 else str(ln)] += 1
        add(f"- {label}: " + ", ".join(f"L{b}={buckets[b]}"
                                       for b in ("2", "3", "4", "5", "6+")))
    add("")

    def matrix(mapping):
        lines = []
        lines.append("| band | " + " | ".join(f"N>={n}" for n in NS) +
                     " | sampled |")
        lines.append("|" + "---|" * (len(NS) + 2))
        for b in BAND_LABELS:
            cells = []
            for n in NS:
                k = sum(1 for pid, lns in mapping.items()
                        if band_of_map.get(pid) == b and max(lns) >= n)
                nn = sampled_per_band.get(b, 0)
                lo, hi = clopper_pearson(k, nn)
                cells.append(f"{k} [{lo:.2f},{hi:.2f}]")
            flag = " ⚠<10" if sampled_per_band.get(b, 0) < 10 else ""
            lines.append(f"| {b} | " + " | ".join(cells) +
                         f" | {sampled_per_band.get(b, 0)}{flag} |")
        return lines

    add("## 3. Band x N matrix (distinct puzzles with a clean segment of >=N)")
    add("Cells: distinct puzzles [exact binomial 95% CI]; sampled = puzzles "
        "sampled in band. ⚠ flags bands with <10 sampled puzzles.")
    add("")
    add("### Including trivially-forced segments")
    for ln in matrix(by_puzzle):
        add(ln)
    add("")
    add("### Excluding trivially-forced segments")
    for ln in matrix(by_puzzle_nt):
        add(ln)
    add("")
    add("Derived-exercise count (NOT distinct puzzles): sum over stored segments "
        "of (L-N+1) for L>=N, incl. / excl. trivial:")
    add("")
    add("| N | derived (incl) | derived (excl) |")
    add("|---|---|---|")
    for n in NS:
        d_all = sum(ln - n + 1 for _, ln, _, _ in seg_rows if ln >= n)
        d_nt = sum(ln - n + 1 for _, ln, triv, _ in seg_rows
                   if ln >= n and not triv)
        add(f"| >={n} | {d_all} | {d_nt} |")
    add("")
    strata = _q(con, "SELECT band, family, depth_bucket, population_count,"
                     " sampled_count FROM probe_strata WHERE run_id=?",
                (run_id,))
    add("Stratum populations (band x family x depth):")
    add("")
    add("| band | family | depth | population | sampled |")
    add("|---|---|---|---|---|")
    for b, f, d, pop, samp in sorted(strata):
        add(f"| {b} | {f} | {d} | {pop} | {samp} |")
    add("")
    add("Population-weighted estimate (N>=2): rate over (band x family) cells "
        "with >=30 sampled puzzles, weighted by cell population. Cells below "
        "30 sampled are DROPPED from the weighting:")
    # distinct (band, family) cells and their sample sizes
    cell_n = {}
    for r in puzzles:
        cell_n[(r[2], r[3])] = cell_n.get((r[2], r[3]), 0) + 1
    dropped = sorted(k for k, v in cell_n.items() if v < 30)
    add(f"- dropped cells (<30 sampled): {dropped if dropped else 'none'}")
    add("")
    add("| band | weighted rate | est. population survivors |")
    add("|---|---|---|")
    for b in BAND_LABELS:
        num = den = 0
        for f in FAMILIES:
            if (b, f) in dropped:
                continue
            pop = sum(p for (bb, ff, dd, p, s) in
                      [(r[0], r[1], r[2], r[3], r[4]) for r in strata]
                      if bb == b and ff == f)
            samp = cell_n.get((b, f), 0)
            surv = sum(1 for pid, lns in by_puzzle.items()
                       if band_of_map.get(pid) == b and fam_of.get(pid) == f
                       and max(lns) >= 2)
            if samp:
                num += pop * surv / samp
                den += pop
        rate = num / den if den else 0.0
        add(f"| {b} | {rate:.3f} | {num:.0f} (of {den}) |")
    add("")

    add("## 4. Threshold sensitivity (recomputed from stored ply data, no engine)")
    add("")
    add("| threshold (cp) | puzzles >=1 (incl/excl trivial) | segments (incl/excl) |")
    add("|---|---|---|")
    for t in THRESHOLDS:
        pz = pz_nt = seg = seg_nt = 0
        for pid, lst in plies.items():
            _, segs, _, _ = _segments_at_threshold(lst, t)
            if segs:
                pz += 1
                seg += len(segs)
            nts = [s for s in segs if not s[2]]
            if nts:
                pz_nt += 1
                seg_nt += len(nts)
        add(f"| {t} | {pz} / {pz_nt} | {seg} / {seg_nt} |")
    add("")

    add("## 5. Mate lines vs non-mate lines")
    mate_ids = {r[0] for r in puzzles if r[3] == "mate"}
    for label, ids in (("mate", mate_ids),
                       ("non-mate", {r[0] for r in puzzles} - mate_ids)):
        sel = {pid: lns for pid, lns in by_puzzle.items() if pid in ids}
        sel_nt = {pid: lns for pid, lns in by_puzzle_nt.items() if pid in ids}
        n_p = len(ids)
        n_s = len(sel)
        rate = (100.0 * n_s / n_p) if n_p else 0.0
        add(f"- {label}: puzzles={n_p} with-segment={n_s} ({rate:.1f}%) "
            f"[excl trivial: {len(sel_nt)}], "
            f"segments={sum(len(v) for v in sel.values())} "
            f"[excl: {sum(len(v) for v in sel_nt.values())}]")
    uniq = _q(con, "SELECT COUNT(*) FROM probe_defender_plies WHERE run_id=? AND"
                   " stage2_json LIKE '%unique_longest_resistance%'", (run_id,))[0][0]
    add(f"- mate-involved replies satisfying unique-longest-resistance: {uniq} "
        f"(substring over stored stage JSONs)")
    dm = _q(con, "SELECT COUNT(*) FROM probe_defender_plies WHERE run_id=? AND"
                 " (stage1_json LIKE '%defender_mates%' OR stage2_json LIKE"
                 " '%defender_mates%')", (run_id,))[0][0]
    add(f"- replies where a line mated FOR the defender: {dm}")
    add("")

    add("## 6. Move-character shares (stored clean segments, incl. trivial)")
    tot = _q(con, "SELECT SUM(checks), SUM(captures), SUM(quiet),"
                  " SUM(promotions), SUM(solver_move_count) FROM"
                  " clean_forced_segments WHERE run_id=?", (run_id,))[0]
    tot = [t or 0 for t in tot]
    denom = tot[4] or 1
    add(f"- overall solver moves: {tot[4]} (checks={tot[0]}, captures={tot[1]}, "
        f"quiet={tot[2]}, promotions={tot[3]})")
    add(f"- shares: checks={tot[0]/denom:.3f} captures={tot[1]/denom:.3f} "
        f"quiet={tot[2]/denom:.3f} promotions={tot[3]/denom:.3f}")
    seg_fam = _q(con, "SELECT themes, SUM(checks), SUM(captures), SUM(quiet),"
                      " SUM(promotions), SUM(solver_move_count) FROM"
                      " clean_forced_segments WHERE run_id=? GROUP BY themes",
                 (run_id,))
    fam_agg: dict[str, list[int]] = {}
    for th, c1, c2, c3, c4, n in seg_fam:
        f = family_of((th or "").split()) or "other"
        a = fam_agg.setdefault(f, [0, 0, 0, 0, 0])
        for j, v in enumerate((c1, c2, c3, c4, n)):
            a[j] += v or 0
    for f in FAMILIES:
        a = fam_agg.get(f, [0, 0, 0, 0, 0])
        d = a[4] or 1
        add(f"- {f}: moves={a[4]} checks={a[0]/d:.3f} captures={a[1]/d:.3f} "
            f"quiet={a[2]/d:.3f} promotions={a[3]/d:.3f}")
    add("")

    add("## 7. Margin distribution (clean non-mate replies, cp)")
    margins = [r[0] for r in _q(con, "SELECT margin_cp FROM probe_defender_plies"
                                     " WHERE run_id=? AND final_verdict='clean'"
                                     " AND mate_involved=0 AND margin_cp IS NOT NULL",
                                (run_id,))]
    if margins:
        qs = statistics.quantiles(sorted(margins), n=4)
        add(f"- n={len(margins)} p25={qs[0]:.0f} median={qs[1]:.0f} p75={qs[2]:.0f} "
            f"min={min(margins)} max={max(margins)}")
    else:
        add("- n=0 (no clean non-mate replies with margins)")
    add("")

    add("## 8. Solver-move check (seeded 5% subsample of puzzles)")
    s_tot = _q(con, "SELECT COUNT(*) FROM probe_solver_plies WHERE run_id=?",
               (run_id,))[0][0]
    s_bad = _q(con, "SELECT COUNT(*) FROM probe_solver_plies WHERE run_id=? AND"
                    " is_best=0 AND alt_mate=0", (run_id,))[0][0]
    s_alt = _q(con, "SELECT COUNT(*) FROM probe_solver_plies WHERE run_id=? AND"
                    " alt_mate=1", (run_id,))[0][0]
    s_puz = _q(con, "SELECT COUNT(DISTINCT puzzle_id) FROM probe_solver_plies"
                    " WHERE run_id=?", (run_id,))[0][0]
    pct = (100.0 * s_bad / s_tot) if s_tot else 0.0
    add(f"- puzzles checked: {s_puz}/{n_puzzles}, solver plies: {s_tot}, stored "
        f"move not best (excl alt_mate): {s_bad} ({pct:.1f}%), alt_mate "
        f"acceptable: {s_alt}")
    add("")

    add("## 9. Throughput")
    try:
        trow = con.execute("SELECT wall_s, sampling_s, engine_phase_s,"
                           " conv_phase_s FROM probe_runs WHERE run_id=?",
                           (run_id,)).fetchall()[0]
    except Exception:
        trow = (None, None, None, None)
    wall = trow[0] if trow[0] is not None else stats.get("wall_s", 0.0)
    samp_s = trow[1] if trow[1] is not None else stats.get("sampling_s", 0.0)
    eng_s = trow[2] if trow[2] is not None else stats.get("engine_phase_s", 0.0)
    conv_s = trow[3] if trow[3] is not None else stats.get("conv_phase_s", 0.0)
    # Per-call records prefer the persisted table (survives resume/crash);
    # fall back to in-memory stats for runs predating probe_calls.
    db_calls = []
    try:
        db_calls = _q(con, "SELECT phase, nodes, ms, reported_nps, t_end FROM"
                           " probe_calls WHERE run_id=? ORDER BY t_end",
                      (run_id,))
    except Exception:
        pass  # runs predating probe_calls: fall back to in-memory stats
    if db_calls:
        clog = [{"phase": ph, "nodes": nn, "ms": mm,
                 "reported_nps": rn, "t_end": te}
                for ph, nn, mm, rn, te in db_calls]
    else:
        clog = sorted(stats.get("call_log", []),
                      key=lambda c: c.get("t_end", 0))
    s1 = [c["ms"] for c in clog if c.get("phase") == "stage1" and c.get("ms")]
    s2 = [c["ms"] for c in clog if c.get("phase") == "stage2" and c.get("ms")]
    solv = [c["ms"] for c in clog if c.get("phase") == "solver" and c.get("ms")]
    calls_row = _q(con, "SELECT SUM(engine_calls) FROM probe_puzzles WHERE run_id=?",
                   (run_id,))[0][0] or 0
    cpp = calls_row / n_puzzles if n_puzzles else 0.0
    pps = n_puzzles / wall if wall else 0.0
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0
    cpu = stats.get("cpu_count")
    add(f"- cores (os.cpu_count): {cpu} | engine workers: {args.workers} "
        f"(1 thread each, Hash={args.hash_mb}MB)")
    db_s = stats.get("db_write_s", 0.0)
    add(f"- phase wall time: sampling={samp_s:.1f}s engine={eng_s:.1f}s "
        f"convergence={conv_s:.1f}s db_writes={db_s:.1f}s (writes serialized)")
    by_phase: dict[str, list] = {}
    for c in clog:
        by_phase.setdefault(c.get("phase", "?"), []).append(c)
    for ph in ("stage1", "stage2", "solver", "conv"):
        entries = by_phase.get(ph, [])
        rep = [c["reported_nps"] for c in entries if c.get("reported_nps")]
        tot_n = sum(c["nodes"] or 0 for c in entries)
        tot_ms = sum(c["ms"] or 0 for c in entries)
        wall_nps = (tot_n / tot_ms * 1000.0) if tot_ms else 0.0
        add(f"- {ph}: n={len(entries)} wall-derived nps={wall_nps:.0f} "
            f"(=sum(nodes)/sum(wall); engine-reported mean={mean(rep):.0f}, "
            f"short searches inflate the reported figure)")
    wins: list[float] = []
    for k in range(0, len(clog), 100):
        win = clog[k:k + 100]
        wn = sum(c["nodes"] or 0 for c in win)
        wm = sum(c["ms"] or 0 for c in win)
        if wm:
            wins.append(wn / wm * 1000.0)
    if len(wins) >= 2:
        drift = 100.0 * (wins[-1] - wins[0]) / wins[0] if wins[0] else 0.0
        spread = (max(wins) - min(wins)) / (sum(wins) / len(wins))
        add(f"- rolling WALL-derived nps per 100 calls ({len(wins)} windows, "
            f"completion order): "
            + ", ".join(f"{w:.0f}" for w in wins))
        add(f"- drift last-vs-first: {drift:+.1f}%; max-min spread/mean: "
            f"{spread:.3f}")
    elif wins:
        add(f"- rolling nps: single window of {len(clog)} calls "
            f"(wall-derived {wins[0]:.0f}); <100 calls so no drift estimate")
    else:
        add("- rolling nps: no calls logged")
    add(f"- wall time: {wall:.1f}s for {n_puzzles} puzzles "
        f"({args.workers} workers)")
    add(f"- engine calls: {calls_row} total, {cpp:.1f}/puzzle")
    add(f"- mean ms/call: stage1={mean(s1):.0f} (n={len(s1)}), "
        f"stage2={mean(s2):.0f} (n={len(s2)}), solver={mean(solv):.0f} "
        f"(n={len(solv)})")
    add(f"- puzzles/sec: {pps:.2f}")
    if pps:
        add(f"- projected 1,000 puzzles: {1000/pps/60:.1f} min")
        add(f"- projected 100,000 puzzles: {100000/pps/3600:.1f} h "
            f"(same settings/workers)")
    add("")

    add("## 10. Final defender-reply failure reasons")
    reasons: dict[str, int] = {}
    for v in plies.values():
        for p in v:
            if p["single"] or p["verdict"] == "clean":
                continue
            s1j = (p["stage1"] or {}).get("judgement", {})
            s2j = (p["stage2"] or {}).get("judgement", {}) if p["stage2"] else {}
            # The deciding stage: stage2 decides only when it ran on a pass.
            if p["stage2"] is not None and s1j.get("verdict") == "clean":
                r = s2j.get("reason", "unknown")
            else:
                r = s1j.get("reason", "unknown")
            reasons[r] = reasons.get(r, 0) + 1
    label = {"disagree": "stored != engine best (disagree)",
             "margin_lt_threshold": "margin below threshold",
             "defender_mates": "defender_mates (stored line bad)",
             "mate_tie_or_unforced": "mate against defender but not unique "
                                     "longest resistance",
             "no_second_line": "no second line", "no_pvs": "no PVs"}
    for r in ("disagree", "margin_lt_threshold", "defender_mates",
              "mate_tie_or_unforced", "no_second_line", "no_pvs", "unknown"):
        if reasons.get(r):
            add(f"- {label.get(r, r)}: {reasons[r]}")
    add("")

    add("## 11. Convergence (Stage-2-clean plies rerun at 2M nodes, MultiPV=4)")
    conv = _q(con, "SELECT verdict, COUNT(*) FROM probe_convergence WHERE"
                   " run_id=? GROUP BY verdict", (run_id,))
    conv_n = sum(c for _, c in conv)
    conv_flip = _q(con, "SELECT COUNT(*) FROM probe_convergence WHERE run_id=?"
                        " AND flipped_to_fail=1", (run_id,))[0][0]
    add(f"- rerun: {conv_n} plies; verdicts: "
        + (", ".join(f"{v}={c}" for v, c in conv) if conv else "none"))
    if conv_n:
        add(f"- flipped to fail at 2M nodes: {conv_flip}/{conv_n} "
            f"({100.0*conv_flip/conv_n:.1f}%)")
    add("")

    with open(path, "w") as fh:
        fh.write("\n".join(L))
