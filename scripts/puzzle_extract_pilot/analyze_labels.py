"""Analyze hand labels against the blind key. Tables only, no recommendations.

Reads labels.csv (entry,keep,code,note) + labeling_key.json and prints:
  (a) keep rate and code counts per group (n + Wilson 95%%);
  (b) keep rate across bins of margin, ep_loss, pv_mat_best, character (Stage 2 only);
  (c) precision/recall of each suggest_* rule vs your codes
      (suggest_T->T, suggest_Q->Q, suggest_A->A, suggest_X->X, suggest_C->C;
      "N/A" suggestions excluded);
  (d) for M in 0.05/0.10/0.15/0.20: labeled K passing vs labeled D passing
      (margin None = mate-pass, counts as pass; entries without margin data
      count as not passing).
Cells with n < 5 are flagged [*].
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from typing import Any, Dict, List

Z = 1.96
RULE2CODE = {"suggest_T": "T", "suggest_Q": "Q", "suggest_A": "A",
             "suggest_X": "X", "suggest_C": "C"}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default="labeling_key.json")
    ap.add_argument("--labels", default="labels.csv")
    return ap.parse_args(argv)


def wilson(k: int, n: int):
    if n == 0:
        return 0.0, 0.0, 0.0
    p = k / n
    d = 1 + Z * Z / n
    c = p + Z * Z / (2 * n)
    m = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n))
    return p, max(0.0, (c - m) / d), min(1.0, (c + m) / d)


def cell(k: int, n: int) -> str:
    p, lo, hi = wilson(k, n)
    flag = " [*]" if n < 5 else ""
    return "%d/%d = %.2f [%.2f,%.2f]%s" % (k, n, p, lo, hi, flag)


def char_label(flags: Dict[str, int]) -> str:
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


def main(argv=None) -> int:
    args = parse_args(argv)
    key = json.load(open(args.key))
    entries = key["entries"]
    labels: Dict[str, Dict[str, str]] = {}
    with open(args.labels, newline="") as f:
        for row in csv.DictReader(f):
            num = (row.get("entry") or "").strip()
            keep = (row.get("keep") or "").strip().upper()
            if num in entries and keep in ("K", "D"):
                labels[num] = {"keep": keep, "code": (row.get("code") or "").strip().upper(),
                               "note": row.get("note") or ""}
    lab = [(n, entries[n], labels[n]) for n in labels]
    print("labeled %d/%d entries" % (len(lab), len(entries)))

    print("\n(a) keep rate + code counts per group")
    print("group | n | keep rate [Wilson95] | T Q A X C (codes)")
    for g in sorted({e["group"] for _, e, _ in lab}):
        rows = [(e, l) for _, e, l in lab if e["group"] == g]
        n = len(rows)
        k = sum(1 for _, l in rows if l["keep"] == "K")
        codes = {c: sum(1 for _, l in rows if l["code"] == c) for c in "TQAXC"}
        print("%s | %d | %s | %s" % (
            g, n, cell(k, n),
            " ".join("%s=%d" % (c, codes[c]) for c in "TQAXC")))

    print("\n(b) keep rate by bins (Stage 2 entries only)")
    st = [(e, l) for _, e, l in lab if e["origin"] == "verify"]
    print("stage2 labeled: %d" % len(st))
    bins = [
        ("margin", lambda e: ("None(mate)" if e["margin_ep"] is None else
                              "[0,.05)" if e["margin_ep"] < 0.05 else
                              "[.05,.10)" if e["margin_ep"] < 0.10 else
                              "[.10,.15)" if e["margin_ep"] < 0.15 else
                              "[.15,.20)" if e["margin_ep"] < 0.20 else "[.20,inf)")),
        ("ep_loss", lambda e: ("<0.12" if (e["ep_loss"] or 0) < 0.12 else
                               "0.12-0.25" if e["ep_loss"] < 0.25 else
                               "0.25-0.5" if e["ep_loss"] < 0.5 else ">=0.5")),
        ("pv_mat_best", lambda e: ("None" if e["pv_mat_best"] is None else
                                   "<0" if e["pv_mat_best"] < 0 else
                                   "0-99" if e["pv_mat_best"] < 100 else
                                   "100-299" if e["pv_mat_best"] < 300 else ">=300")),
        ("char", lambda e: char_label(e["char"])),
    ]
    for name, fn in bins:
        print("-- %s" % name)
        seen: List[str] = []
        for e, _ in st:
            b = fn(e)
            if b not in seen:
                seen.append(b)
        for b in seen:
            rows = [(e, l) for e, l in st if fn(e) == b]
            n = len(rows)
            print("  %s | %s" % (b, cell(sum(1 for _, l in rows if l["keep"] == "K"), n)))

    print("\n(c) suggest rule precision/recall vs codes")
    print("rule -> code | suggested&hit / suggested (prec) | hit / code-n (recall)")
    for rule, code in RULE2CODE.items():
        app = [(e, l) for _, e, l in lab if e["suggestions"][rule]["value"] != "N/A"]
        sug = [(e, l) for e, l in app if e["suggestions"][rule]["value"] is True]
        hit = [(e, l) for e, l in sug if l["code"] == code]
        coden = sum(1 for _, l in app if l["code"] == code)
        ns, nh, nc = len(sug), len(hit), coden
        print("%s -> %s | %s (prec) | %d/%d = %s (recall)%s" % (
            rule, code, cell(nh, ns),
            nh, nc, ("%.2f" % (nh / nc) if nc else "n/a"),
            " [*]" if min(ns, nc) < 5 else ""))

    print("\n(d) margin threshold vs your labels")
    print("M | K pass / K labeled | D pass / D labeled")
    for m in (0.05, 0.10, 0.15, 0.20):
        krows = [(e, l) for _, e, l in lab if l["keep"] == "K"]
        drows = [(e, l) for _, e, l in lab if l["keep"] == "D"]
        kp = sum(1 for e, _ in krows if e["margin_ep"] is None or e["margin_ep"] >= m)
        dp = sum(1 for e, _ in drows if e["margin_ep"] is None or e["margin_ep"] >= m)
        print("%.2f | %d/%d | %d/%d" % (m, kp, len(krows), dp, len(drows)))
    print("note: entries without margin data count as not passing; "
          "margin None (mate-pass) counts as pass.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())