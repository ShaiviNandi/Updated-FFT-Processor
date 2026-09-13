#!/usr/bin/env python3
# =============================================================================
# diagnose_area_variance.py
#
# Forensic analysis of per-chromosome FPGA/ASIC utilisation spread for the
# mixed-precision (FP4/FP8) FFT processor.
#
# QUESTION IT ANSWERS
#   The RTL (butterfly_wrapper) is a fixed union of both the FP4 and FP8
#   datapaths, so per-chromosome area SHOULD be constant. Vivado nevertheless
#   reports a large LUT spread. This script decides, statistically, which of
#   these is true:
#
#     H1  "gene-proportional"  LUTs scale with the NUMBER of FP8 genes
#                              -> would imply real per-stage hardware (and a
#                                 modelling error, since there is one shared BF)
#     H2  "binary pruning"     LUTs take ~2-3 discrete levels keyed on whether
#                              the FP8 cone is reachable at all (constant
#                              propagation kills it when no stage selects FP8)
#     H3  "LUT<->DSP trading"  LUTs fall as DSPs rise (negative correlation):
#                              tool is re-mapping, not pruning
#     H4  "tool noise"         no structure; spread is run-to-run variance
#
# USAGE
#   python diagnose_area_variance.py results.csv
#   python diagnose_area_variance.py results/fft_256/all_generations_fft256.csv
#   python diagnose_area_variance.py results.csv --chromosome-col chromosome \
#          --lut-col LUTs --dsp-col DSP --power-col Power --sqnr-col SQNR
#
# INPUT FORMATS (auto-detected)
#   (a) one column of chromosome bitstrings, e.g. "1111110111111111",
#       interpreted as [s0_mult, s0_add, s1_mult, s1_add, ...] (interleaved)
#       or, with --gene-layout split, as [all mults..., all adds...]
#   (b) per-gene columns named s0_mult, s0_add, s1_mult, ...
#
# Requires: numpy, scipy (pandas optional).
# =============================================================================

import argparse
import csv
import math
import re
import sys
from collections import OrderedDict

import numpy as np

try:
    from scipy import stats as sps
except ImportError:  # pragma: no cover
    sps = None
    print("WARNING: scipy not found - Spearman / p-values disabled.", file=sys.stderr)


# -----------------------------------------------------------------------------
# column resolution
# -----------------------------------------------------------------------------
CANDIDATES = {
    "lut":   ["luts", "lut", "area_luts", "lut_count", "area", "slice_luts", "clb_luts"],
    "dsp":   ["dsp", "dsps", "dsp_count", "dsp48", "dsp48e1"],
    "power": ["power", "power_w", "total_power_w", "total_power"],
    "sqnr":  ["sqnr", "sqnr_db"],
    "delay": ["crit_delay_ns", "critical_path_delay_ns", "delay", "crit_delay", "wns_ns"],
    "ff":    ["ffs", "ff", "ff_count", "registers"],
    "area":  ["cell_area", "asic_area", "area_um2", "std_cell_area"],
    "chrom": ["chromosome", "chrom", "genome", "config", "bitstring"],
}


def resolve(header, key, override=None):
    if override:
        if override not in header:
            sys.exit(f"ERROR: column '{override}' not in CSV. Have: {list(header)}")
        return override
    low = {h.lower().strip(): h for h in header}
    for c in CANDIDATES[key]:
        if c in low:
            return low[c]
    return None


def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rdr = csv.DictReader(fh)
        rows = [r for r in rdr]
    if not rows:
        sys.exit("ERROR: empty CSV.")
    return rows, list(rows[0].keys())


def to_float(v, default=np.nan):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return default


# -----------------------------------------------------------------------------
# gene extraction
# -----------------------------------------------------------------------------
def gene_columns(header):
    """Find s<i>_mult / s<i>_add style per-gene columns."""
    mult, add = {}, {}
    for h in header:
        m = re.fullmatch(r"s(\d+)_mult", h.strip(), re.I)
        if m:
            mult[int(m.group(1))] = h
        m = re.fullmatch(r"s(\d+)_add", h.strip(), re.I)
        if m:
            add[int(m.group(1))] = h
    return (OrderedDict(sorted(mult.items())), OrderedDict(sorted(add.items())))


def genes_from_bitstring(s, layout="interleaved"):
    """Return (mult_bits, add_bits) from a chromosome string."""
    bits = [int(c) for c in re.sub(r"[^01]", "", str(s))]
    if not bits:
        return [], []
    if layout == "split":
        h = len(bits) // 2
        return bits[:h], bits[h:]
    return bits[0::2], bits[1::2]          # interleaved: mult, add, mult, add...


# -----------------------------------------------------------------------------
# statistics helpers
# -----------------------------------------------------------------------------
def pearson(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return (float("nan"), float("nan"), len(x))
    if sps is not None:
        r, p = sps.pearsonr(x, y)
    else:
        r = float(np.corrcoef(x, y)[0, 1]); p = float("nan")
    return (r, p, len(x))


def spearman(x, y):
    if sps is None:
        return (float("nan"), float("nan"))
    x, y = np.asarray(x, float), np.asarray(y, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 3 or np.std(x) == 0 or np.std(y) == 0:
        return (float("nan"), float("nan"))
    r, p = sps.spearmanr(x, y)
    return (float(r), float(p))


def stars(p):
    if not np.isfinite(p):
        return "    "
    if p < 1e-4:
        return "****"
    if p < 1e-3:
        return "*** "
    if p < 1e-2:
        return "**  "
    if p < 5e-2:
        return "*   "
    return "ns  "


def rline(label, r, p, n, extra=""):
    print(f"  {label:<42s} r = {r:+.4f}  p = {p:.3g} {stars(p)} n={n:<5d}{extra}")


def cluster_levels(v, gap_frac=0.04):
    """
    Cheap 1-D clustering: sort, split wherever the gap between consecutive
    values exceeds gap_frac * range. Detects architectural TIERS without
    assuming a cluster count.
    """
    v = np.sort(np.asarray(v, float))
    v = v[np.isfinite(v)]
    if len(v) < 2:
        return [v]
    thr = max(gap_frac * (v[-1] - v[0]), 1e-12)
    groups, cur = [], [v[0]]
    for a, b in zip(v[:-1], v[1:]):
        if b - a > thr:
            groups.append(np.array(cur)); cur = [b]
        else:
            cur.append(b)
    groups.append(np.array(cur))
    return groups


# -----------------------------------------------------------------------------
# main
# -----------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv")
    ap.add_argument("--lut-col");        ap.add_argument("--dsp-col")
    ap.add_argument("--power-col");      ap.add_argument("--sqnr-col")
    ap.add_argument("--area-col");       ap.add_argument("--delay-col")
    ap.add_argument("--ff-col");         ap.add_argument("--chromosome-col")
    ap.add_argument("--gene-layout", choices=["interleaved", "split"],
                    default="interleaved",
                    help="how to split a chromosome bitstring into mult/add genes")
    ap.add_argument("--dedupe", action="store_true", default=True,
                    help="collapse duplicate chromosomes (default on; NSGA logs "
                         "repeat survivors and duplicates inflate significance)")
    ap.add_argument("--keep-duplicates", dest="dedupe", action="store_false")
    ap.add_argument("--noise-tol", type=float, default=0.05,
                    help="within-tier CV below this is called tool noise (default 5%%)")
    args = ap.parse_args()

    rows, header = load_rows(args.csv)

    c_lut   = resolve(header, "lut",   args.lut_col)
    c_dsp   = resolve(header, "dsp",   args.dsp_col)
    c_pow   = resolve(header, "power", args.power_col)
    c_sqnr  = resolve(header, "sqnr",  args.sqnr_col)
    c_area  = resolve(header, "area",  args.area_col)
    c_delay = resolve(header, "delay", args.delay_col)
    c_ff    = resolve(header, "ff",    args.ff_col)
    c_chrom = resolve(header, "chrom", args.chromosome_col)

    if c_lut is None:
        sys.exit(f"ERROR: no LUT column found. Pass --lut-col. Have: {header}")

    mult_cols, add_cols = gene_columns(header)
    mode = "per-gene columns" if mult_cols else ("chromosome string" if c_chrom else None)
    if mode is None:
        sys.exit("ERROR: found neither s<i>_mult/s<i>_add columns nor a chromosome column.")

    # ---- build records -----------------------------------------------------
    recs, seen = [], set()
    for r in rows:
        if mult_cols:
            mb = [int(to_float(r[c], 0)) for c in mult_cols.values()]
            ab = [int(to_float(r[c], 0)) for c in add_cols.values()] if add_cols else []
        else:
            mb, ab = genes_from_bitstring(r[c_chrom], args.gene_layout)
        if not mb:
            continue
        key = (tuple(mb), tuple(ab))
        if args.dedupe and key in seen:
            continue
        seen.add(key)
        recs.append(dict(
            n_fp8_mult=sum(mb), n_fp8_add=sum(ab), n_stages=len(mb),
            frac_fp8_mult=sum(mb) / len(mb),
            any_fp8_mult=1 if sum(mb) else 0,
            any_fp8_add=1 if sum(ab) else 0,
            any_fp8=1 if (sum(mb) or sum(ab)) else 0,
            lut=to_float(r[c_lut]),
            dsp=to_float(r[c_dsp]) if c_dsp else np.nan,
            power=to_float(r[c_pow]) if c_pow else np.nan,
            sqnr=to_float(r[c_sqnr]) if c_sqnr else np.nan,
            area=to_float(r[c_area]) if c_area else np.nan,
            delay=to_float(r[c_delay]) if c_delay else np.nan,
            ff=to_float(r[c_ff]) if c_ff else np.nan,
        ))
    if len(recs) < 4:
        sys.exit("ERROR: fewer than 4 usable designs after parsing/dedupe.")

    col = lambda k: np.array([d[k] for d in recs], float)
    lut = col("lut")

    W = 78
    print("=" * W)
    print(" MIXED-PRECISION FFT :: AREA-VARIANCE FORENSICS")
    print("=" * W)
    print(f" file            : {args.csv}")
    print(f" gene source     : {mode}  (layout={args.gene_layout})")
    print(f" designs         : {len(rows)} rows -> {len(recs)} unique chromosomes"
          f"{' (deduped)' if args.dedupe else ''}")
    print(f" stages / genome : {recs[0]['n_stages']} mult + {len(add_cols) or len(genes_from_bitstring(rows[0].get(c_chrom,''),args.gene_layout)[1])} add")
    print(f" columns         : LUT={c_lut}  DSP={c_dsp}  POWER={c_pow}  "
          f"SQNR={c_sqnr}  DELAY={c_delay}")
    print()

    # ---- 1. dispersion -----------------------------------------------------
    print("-" * W)
    print(" [1] RAW DISPERSION")
    print("-" * W)
    for name, v in (("LUTs", lut), ("DSPs", col("dsp")), ("FFs", col("ff")),
                    ("Power", col("power")), ("Delay(ns)", col("delay")),
                    ("ASIC area", col("area"))):
        v = v[np.isfinite(v)]
        if v.size == 0:
            continue
        cv = np.std(v) / np.mean(v) if np.mean(v) else float("nan")
        print(f"  {name:<11s} n={v.size:<5d} min={v.min():>10.4g}  max={v.max():>10.4g}  "
              f"mean={v.mean():>10.4g}  sd={np.std(v):>9.4g}  CV={100*cv:6.2f}%  "
              f"distinct={len(np.unique(v))}")
    print()
    flat = [n for n, v in (("Power", col("power")), ("ASIC area", col("area")))
            if np.isfinite(v).any() and np.std(v[np.isfinite(v)]) /
            (np.mean(v[np.isfinite(v)]) or 1) < 0.01]
    if flat:
        print(f"  !! {', '.join(flat)} varies < 1% across all designs -> NOT a usable")
        print("     optimisation objective as measured (vectorless estimate / fixed union).")
        print()

    # ---- 2. H1: gene-proportional -----------------------------------------
    print("-" * W)
    print(" [2] H1  gene-proportional scaling  (LUTs ~ number of FP8 genes)")
    print("-" * W)
    for lbl, k in (("LUTs vs #FP8 mult genes", "n_fp8_mult"),
                   ("LUTs vs #FP8 add  genes", "n_fp8_add"),
                   ("LUTs vs frac FP8 mult",   "frac_fp8_mult")):
        r, p, n = pearson(col(k), lut)
        rs, ps = spearman(col(k), lut)
        rline(lbl, r, p, n, f"  (rho={rs:+.3f})")
    print()

    # ---- 3. H2: binary pruning --------------------------------------------
    print("-" * W)
    print(" [3] H2  binary pruning  (LUTs keyed on FP8 cone REACHABILITY)")
    print("-" * W)
    for lbl, k in (("LUTs vs ANY stage uses FP8 mult", "any_fp8_mult"),
                   ("LUTs vs ANY stage uses FP8 add ", "any_fp8_add"),
                   ("LUTs vs ANY FP8 gene at all    ", "any_fp8")):
        r, p, n = pearson(col(k), lut)
        rline(lbl, r, p, n)
    print()
    tiers = cluster_levels(lut)
    print(f"  discrete LUT tiers detected: {len(tiers)}")
    for i, g in enumerate(tiers):
        cv = np.std(g) / np.mean(g) if np.mean(g) else 0.0
        print(f"    tier {i}: n={g.size:<5d} [{g.min():.0f} .. {g.max():.0f}]  "
              f"mean={g.mean():7.1f}  within-tier CV={100*cv:5.2f}%")
    if len(tiers) >= 2:
        steps = [tiers[i+1].mean() - tiers[i].mean() for i in range(len(tiers)-1)]
        print(f"    inter-tier steps: {', '.join(f'{s:+.0f} LUT' for s in steps)}")
    print()

    # ---- 4. within-tier: is residual spread gene-driven or noise? ---------
    print("-" * W)
    print(" [4] WITHIN-TIER RESIDUAL  (does spread survive after conditioning?)")
    print("-" * W)
    for lbl, mask in (("FP8 mult PRESENT", col("any_fp8_mult") == 1),
                      ("FP8 mult ABSENT ", col("any_fp8_mult") == 0)):
        if mask.sum() < 4:
            print(f"  {lbl}: n={int(mask.sum())} - too few to test")
            continue
        sub_l = lut[mask]
        cv = np.std(sub_l) / np.mean(sub_l)
        print(f"  {lbl}: n={int(mask.sum())}  LUT mean={sub_l.mean():.1f}  "
              f"sd={np.std(sub_l):.1f}  CV={100*cv:.2f}%")
        for k, nm in (("n_fp8_mult", "#FP8 mult"), ("n_fp8_add", "#FP8 add")):
            r, p, n = pearson(col(k)[mask], sub_l)
            rline(f"    LUTs vs {nm} | {lbl.strip()}", r, p, n)
        verdict = ("TOOL NOISE" if cv < args.noise_tol else "structured - investigate")
        print(f"    -> residual CV {100*cv:.2f}% : {verdict}")
    print()

    # ---- 5. H3: LUT/DSP trading -------------------------------------------
    print("-" * W)
    print(" [5] H3  LUT<->DSP trading  (negative r => remapping, not pruning)")
    print("-" * W)
    if c_dsp is None:
        print("  no DSP column - cannot test.")
    else:
        dsp = col("dsp")
        r, p, n = pearson(dsp, lut)
        rline("LUTs vs DSPs", r, p, n)
        rd, pd_, nd = pearson(col("n_fp8_mult"), dsp)
        rline("DSPs vs #FP8 mult genes", rd, pd_, nd)
        ra, pa, na = pearson(col("any_fp8_mult"), dsp)
        rline("DSPs vs ANY FP8 mult", ra, pa, na)
        u = np.unique(dsp[np.isfinite(dsp)])
        print(f"  distinct DSP counts: {u.tolist()}")
        if np.isfinite(r):
            if r < -0.3:
                print("  -> NEGATIVE: tool is swapping LUT logic for DSP slices.")
                print("     Area 'savings' are a mapping artefact, not pruned datapath.")
            elif r > 0.3:
                print("  -> POSITIVE: LUTs and DSPs rise TOGETHER. This is NOT LUT/DSP")
                print("     trading; a whole datapath cone enters or leaves the netlist.")
            else:
                print("  -> uncorrelated: DSP mapping is not driving the LUT spread.")
        # per-DSP-level LUT stats: isolates mapping from pruning
        for lvl in u:
            m = dsp == lvl
            if m.sum() >= 3:
                s = lut[m]
                print(f"     DSP={lvl:g}: n={int(m.sum()):<5d} LUT {s.min():.0f}-{s.max():.0f} "
                      f"mean={s.mean():.1f} CV={100*np.std(s)/s.mean():.2f}%")
    print()

    # ---- 6. sanity: does SQNR track the genes? ----------------------------
    if c_sqnr:
        print("-" * W)
        print(" [6] CONTROL  (SQNR must track the genes, else the genome is inert)")
        print("-" * W)
        r, p, n = pearson(col("n_fp8_mult"), col("sqnr"))
        rline("SQNR vs #FP8 mult genes", r, p, n)
        r, p, n = pearson(col("n_fp8_add"), col("sqnr"))
        rline("SQNR vs #FP8 add  genes", r, p, n)
        r, p, n = pearson(lut, col("sqnr"))
        rline("SQNR vs LUTs", r, p, n)
        print()

    # ---- 7. verdict --------------------------------------------------------
    print("=" * W)
    print(" VERDICT")
    print("=" * W)
    r_lin, p_lin, _ = pearson(col("n_fp8_mult"), lut)
    r_bin, p_bin, _ = pearson(col("any_fp8_mult"), lut)
    r_dsp, _, _ = pearson(col("dsp"), lut) if c_dsp else (float("nan"),) * 3
    m1 = col("any_fp8_mult") == 1
    resid_cv = (np.std(lut[m1]) / np.mean(lut[m1])) if m1.sum() >= 4 else float("nan")

    lines = []
    if np.isfinite(r_bin) and abs(r_bin) > 0.6 and (not np.isfinite(r_lin) or abs(r_bin) > abs(r_lin) + 0.2):
        lines.append("H2 BINARY PRUNING is the dominant effect: the binary 'is the FP8 cone")
        lines.append("   reachable at all' indicator explains the spread, the FP8 gene COUNT")
        lines.append("   does not. Constant propagation removes the unreachable datapath;")
        lines.append("   the architecture is otherwise a fixed union. Report area per TIER,")
        lines.append("   not as a continuous function of the chromosome.")
    elif np.isfinite(r_lin) and abs(r_lin) > 0.6:
        lines.append("H1 GENE-PROPORTIONAL: LUTs scale with the FP8 gene count. For a single")
        lines.append("   shared butterfly this should be impossible - check that the generator")
        lines.append("   is not emitting per-stage hardware.")
    if np.isfinite(r_dsp) and r_dsp < -0.3:
        lines.append("H3 LUT<->DSP TRADING is also present: report LUT+DSP jointly, never LUTs alone.")
    if np.isfinite(resid_cv) and resid_cv < args.noise_tol:
        lines.append(f"H4 residual within-tier spread is {100*resid_cv:.1f}% CV = tool noise;")
        lines.append("   quote it as a synthesis-noise band, not as an optimisation result.")
    if not lines:
        lines.append("No dominant hypothesis. Inspect the tier table in [3] by hand.")
    for l in lines:
        print(" " + l)
    print("=" * W)


if __name__ == "__main__":
    main()
