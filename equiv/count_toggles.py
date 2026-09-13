#!/usr/bin/env python3
"""
count_toggles.py -- attribute VCD signal transitions to datapath cones.

Reads the VCD produced by tb_activity.v and reports, per wrapper instance
(u_ref = ungated original, u_gat = operand-isolated), the total number of
bit transitions inside:

    the FP8 multiply cone   (cmul_fp8 / fp8_mul / fp8_add_sub under it)
    the FP4 multiply cone   (cmul_fp4 / fp4_mul / fp4_add_sub under it)
    the FP8 add/sub pair
    the FP4 add/sub pair
    the format converters

Bit transitions are a direct proxy for switching-node count, which is what
CV^2f dynamic power is proportional to. The ratio u_gat/u_ref is the
activity reduction that operand isolation buys, independent of any tool's
power model.

Usage:  python3 count_toggles.py activity.vcd
"""

import re
import sys
from collections import defaultdict


def parse(path):
    """Return {full_scope_path: transition_count} counting per-bit changes."""
    ids = {}            # vcd id -> list of full names
    width = {}          # vcd id -> width
    last = {}           # vcd id -> last value string
    toggles = defaultdict(int)

    scope = []
    in_defs = True

    with open(path, "r", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue

            if in_defs:
                if line.startswith("$scope"):
                    parts = line.split()
                    if len(parts) >= 3:
                        scope.append(parts[2])
                    continue
                if line.startswith("$upscope"):
                    if scope:
                        scope.pop()
                    continue
                if line.startswith("$var"):
                    # $var wire 8 ! signame [7:0] $end
                    p = line.split()
                    try:
                        w = int(p[2]); vid = p[3]; nm = p[4]
                    except (IndexError, ValueError):
                        continue
                    full = ".".join(scope + [nm])
                    ids.setdefault(vid, []).append(full)
                    width[vid] = w
                    continue
                if line.startswith("$enddefinitions"):
                    in_defs = False
                    continue
                continue

            # value change section
            c = line[0]
            if c in "01xXzZ" and len(line) >= 2:
                val, vid = line[0], line[1:]
                _acc(toggles, ids, last, vid, val, 1)
            elif c in "bB":
                m = line.split()
                if len(m) < 2:
                    continue
                val, vid = m[0][1:], m[1]
                _acc(toggles, ids, last, vid, val, width.get(vid, len(val)))
            elif c in "rR":
                continue
            # '#' timestamps and everything else ignored

    return toggles


def _acc(toggles, ids, last, vid, val, w):
    prev = last.get(vid)
    last[vid] = val
    if prev is None:
        return
    if prev == val:
        return
    # count differing bits, right-aligned, treating missing high bits as 0
    a = prev.rjust(max(len(prev), len(val)), "0")
    b = val.rjust(max(len(prev), len(val)), "0")
    n = sum(1 for x, y in zip(a, b) if x != y)
    if n == 0:
        n = 1
    for full in ids.get(vid, ()):
        toggles[full] += n


CONES = [
    ("FP8 complex multiply", re.compile(r"\bcmul_fp8\b")),
    ("FP4 complex multiply", re.compile(r"\bcmul_fp4\b")),
    ("FP8 add/sub pair",     re.compile(r"\b(add_fp8|sub_fp8)\b")),
    ("FP4 add/sub pair",     re.compile(r"\b(add_fp4|sub_fp4)\b")),
    ("format converters",    re.compile(r"\bconv_wb(84|48)\b")),
]

INSTANCES = [("u_ref  (ungated)", "u_ref"), ("u_gat  (isolated)", "u_gat")]


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: count_toggles.py activity.vcd")
    tg = parse(sys.argv[1])
    if not tg:
        sys.exit("no value changes parsed - is this a VCD with $dumpvars(0, ...)?")

    print("=" * 74)
    print(" TOGGLE ACTIVITY BY DATAPATH CONE   (bit transitions, VCD-derived)")
    print("=" * 74)

    totals = {}
    per_cone = {}
    for label, inst in INSTANCES:
        sub = {k: v for k, v in tg.items() if f".{inst}." in f".{k}."}
        totals[inst] = sum(sub.values())
        per_cone[inst] = {}
        for cname, rx in CONES:
            per_cone[inst][cname] = sum(v for k, v in sub.items() if rx.search(k))

    hdr = f"  {'cone':<24s}{'ungated':>14s}{'isolated':>14s}{'reduction':>12s}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for cname, _ in CONES:
        a = per_cone["u_ref"][cname]
        b = per_cone["u_gat"][cname]
        red = (1 - b / a) * 100 if a else float("nan")
        print(f"  {cname:<24s}{a:>14,d}{b:>14,d}{red:>11.1f}%")
    a, b = totals["u_ref"], totals["u_gat"]
    print("  " + "-" * (len(hdr) - 2))
    red = (1 - b / a) * 100 if a else float("nan")
    print(f"  {'WRAPPER TOTAL':<24s}{a:>14,d}{b:>14,d}{red:>11.1f}%")
    print()
    print("  Dynamic power scales with switched capacitance, i.e. with these")
    print("  transition counts. Quote the WRAPPER TOTAL reduction as the")
    print("  activity saving; use a SAIF-driven report_power for the watts.")
    print("=" * 74)


if __name__ == "__main__":
    main()
