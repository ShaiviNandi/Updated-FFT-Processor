#!/usr/bin/env python3
"""Summarise every completed FFT size under results/."""
import csv, glob, os, re, sys, datetime

root = sys.argv[1] if len(sys.argv) > 1 else "results"
sizes = sorted((int(m.group(1)), d) for d in glob.glob(os.path.join(root, "fft_*"))
               if (m := re.match(r"fft_(\d+)$", os.path.basename(d))))
if not sizes:
    print(f"no results/fft_* folders under {root!r}"); sys.exit(0)

def num(s):
    try: return float(s)
    except (TypeError, ValueError): return None

print(f"{'N':>6} {'solns':>6} {'pareto':>7} {'dynP W (min-max)':>20} "
      f"{'LUTs (min-max)':>16} {'SQNR dB (max)':>14} {'written':>12}")
print("-" * 86)

best = {}
for n, d in sizes:
    csvp = os.path.join(d, f"all_solutions_fft{n}.csv")
    summ = os.path.join(d, "summary.txt")
    if not os.path.exists(csvp):
        print(f"{n:>6} {'-':>6} {'-':>7} {'not written yet':>20} {'-':>16} {'-':>14} {'-':>12}")
        continue
    rows = list(csv.DictReader(open(csvp)))
    pw  = [v for v in (num(r.get("power_W"))   for r in rows) if v is not None]
    lut = [v for v in (num(r.get("area_LUTs")) for r in rows) if v is not None and v >= 0]
    sq  = [v for v in (num(r.get("sqnr_dB"))   for r in rows)
           if v is not None and v == v and abs(v) != float("inf")]
    pf  = sum(1 for r in rows if (r.get("on_pareto_front") or "0").strip() == "1")
    when = (datetime.datetime.fromtimestamp(os.path.getmtime(summ)).strftime("%m-%d %H:%M")
            if os.path.exists(summ) else "")
    print(f"{n:>6} {len(rows):>6} {pf:>7} "
          f"{(f'{min(pw):.3f}-{max(pw):.3f}' if pw else '-'):>20} "
          f"{(f'{int(min(lut))}-{int(max(lut))}' if lut else '-'):>16} "
          f"{(f'{max(sq):.2f}' if sq else '-'):>14} {when:>12}")
    cand = [r for r in rows if (r.get("on_pareto_front") or "0").strip() == "1"] or rows
    cand = [r for r in cand if num(r.get("power_W")) is not None]
    if cand: best[n] = min(cand, key=lambda r: num(r["power_W"]))

print("\nLowest-dynamic-power design on each front")
print("-" * 86)
for n, r in best.items():
    g = sorted((k for k in r if re.match(r"s\d+_(mult|add)$", k)),
               key=lambda k: (int(re.match(r"s(\d+)", k).group(1)), k))
    print(f"  N={n:<5} chromosome {''.join((r[x] or '?').strip() for x in g)}  "
          f"dynP {r.get('power_W','?')} W  {r.get('area_LUTs','?')} LUTs  "
          f"SQNR {r.get('sqnr_dB','?')} dB  crit {r.get('crit_delay_ns','?')} ns")

print("\nBest-by-objective blocks")
print("-" * 86)
for n, d in sizes:
    summ = os.path.join(d, "summary.txt")
    if not os.path.exists(summ): continue
    if m := re.search(r"Best Solutions by Objective:.*", open(summ, errors="replace").read(), re.S):
        print(f"\n=== N={n} ===")
        print("\n".join(m.group(0).splitlines()[:26]))
