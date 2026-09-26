#!/usr/bin/env python3
"""Rebuild all_solutions_fft<N>.csv with true SQNR and an energy column.

all_solutions_*.csv derives SQNR by inverting ((50 - sqnr)/50)**2 - a parabola
with its minimum at 50 dB - so the square root cannot return anything above 50:
a design whose true SQNR is X > 50 comes out as 100 - X, and a numerically exact
design (clamped to 100 dB) appears as 0.00.

all_generations_fft<N>.csv is built from the per-solution result dumps rather
than the objective vector, so it holds the true value. This joins them on the
chromosome, rewrites sqnr_dB, and adds energy_pJ, which was never written even
though it is the primary objective. Originals are untouched.
"""
import csv, glob, os, re, sys

root = sys.argv[1] if len(sys.argv) > 1 else "results"
ISO_NS = 10.0
try:
    from energyObjective import ISO_FREQUENCY_NS
    if ISO_FREQUENCY_NS: ISO_NS = float(ISO_FREQUENCY_NS)
except Exception:
    pass
print(f"energy uses a {ISO_NS} ns clock period\n")

def gene_cols(fn):
    g = [c for c in (fn or []) if re.match(r"s\d+_(mult|add)$", c)]
    return sorted(g, key=lambda c: (int(re.match(r"s(\d+)", c).group(1)), c))
def key_of(row, cols): return "".join((row.get(c) or "").strip() for c in cols)
def num(s):
    try: return float(s)
    except (TypeError, ValueError): return None

sizes = sorted((int(m.group(1)), d) for d in glob.glob(os.path.join(root, "fft_*"))
               if (m := re.match(r"fft_(\d+)$", os.path.basename(d))))
print(f"{'N':>6} {'rows':>6} {'joined':>7} {'sqnr fixed':>11} {'was >50 dB':>11} {'true max dB':>12}")
print("-" * 62)

for n, d in sizes:
    sol, gen = (os.path.join(d, f"all_solutions_fft{n}.csv"),
                os.path.join(d, f"all_generations_fft{n}.csv"))
    if not (os.path.exists(sol) and os.path.exists(gen)):
        print(f"{n:>6} {'-':>6} {'-':>7} {'missing a file':>11}"); continue
    with open(gen) as f:
        gr = csv.DictReader(f); gcols = gene_cols(gr.fieldnames)
        truth = {key_of(r, gcols): v for r in gr
                 if (v := num(r.get("sqnr_dB"))) is not None and v == v}
    with open(sol) as f:
        sr = csv.DictReader(f); scols = gene_cols(sr.fieldnames)
        rows, fields = list(sr), list(sr.fieldnames)
    if "energy_pJ" not in fields:
        fields.insert(fields.index("power_W") + 1 if "power_W" in fields else len(fields), "energy_pJ")
    if "sqnr_dB_asreported" not in fields: fields.append("sqnr_dB_asreported")

    joined = fixed = above = 0; truemax = None
    for r in rows:
        p, c = num(r.get("power_W")), num(r.get("avg_exec_cycles"))
        r["energy_pJ"] = f"{p*c*ISO_NS*1e3:.1f}" if p is not None and c and c > 0 else ""
        r["sqnr_dB_asreported"] = r.get("sqnr_dB", "")
        k = key_of(r, scols)
        if k in truth:
            joined += 1; t = truth[k]; old = num(r.get("sqnr_dB"))
            # 0.05, not 0.005: all_generations stores 4 dp, so a tighter
            # tolerance overwrites a correct full-precision original with
            # a rounded copy (41.4807 -> 41.4800). Only genuine mirroring,
            # which is tens of dB out, should trigger a rewrite.
            if old is None or abs(old - t) > 0.05:
                fixed += 1
                r["sqnr_dB"] = f"{t:.4f}"
            if t > 50: above += 1
            truemax = t if truemax is None else max(truemax, t)
    with open(os.path.join(d, f"all_solutions_fft{n}_fixed.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print(f"{n:>6} {len(rows):>6} {joined:>7} {fixed:>11} {above:>11} "
          f"{(f'{truemax:.2f}' if truemax is not None else '-'):>12}")

print("\nwrote all_solutions_fft<N>_fixed.csv beside each original")
print("columns added: energy_pJ, sqnr_dB_asreported (the old mirrored value)")
