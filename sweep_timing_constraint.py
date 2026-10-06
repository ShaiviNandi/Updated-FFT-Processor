#!/usr/bin/env python3
"""
Find the clock period each design actually closes at
===================================================
Every design in this project reports NEGATIVE slack, because the flow constrains
at POWER_CLOCK_NS = 10 ns while the real paths are 24-36 ns. That is not a
defect: 10 ns is the operating point chosen so report_power has enough
significant figures (at CLOCK_PERIOD = 80 ns dynamic power collapses to
0.006-0.009 W and rounds away), and `critical_path_delay = clock_period - WNS`
is computed FROM that negative slack.

What it costs is the ability to say any design closes timing. This script buys
that back: it bisects the `create_clock` period per design until worst slack
crosses zero, and reports the tightest period that still meets timing with
POSITIVE slack -- measured, not extrapolated.

It also answers a second question for free. The whole project's f_max comes from
extrapolating `10 - WNS` at a constraint missed by 250 %. Comparing the closing
period found here against that extrapolation, across every design, says how much
the extrapolation is worth. One earlier same-chromosome pair put it at 1.9 %;
this measures it properly.

HOW IT WORKS
  For each size it calls the track's own FPGA driver with --no-saif (one Vivado
  pass instead of two, since timing needs no switching activity) and
  --clock-period P, into a throwaway output directory, then reads `wns_ns` from
  the metrics CSV. Bisection: the first probe is the delay already measured at
  10 ns, then the interval is widened or narrowed until it brackets WNS = 0 to
  within --tolerance.

  Nothing existing is modified or overwritten -- every probe writes into
  <work>/p<period>/ under --work-dir, which defaults to a sweep directory of its
  own.

WHAT NOT TO DO WITH THE RESULT
  Do NOT take power or energy from these runs. Changing the constraint changes
  the operating point, so a swept run's dynamic power is not comparable with the
  10 ns numbers in the reports. Timing only. (Energy per transform is
  frequency-invariant, but only when the same power measurement is used for
  both.)

Usage (from the repository root):
    python3 sweep_timing_constraint.py --track mixed --sizes 256
    python3 sweep_timing_constraint.py --track fp16
    python3 sweep_timing_constraint.py --track all --sizes 64 256 1024

Budget: one Vivado synthesis per probe, typically 4-6 probes per design, roughly
60-100 s each. One size on one track is a few minutes; all ten sizes on all
three tracks is several hours. Start with one size.

STATUS: NOT RUN AGAINST VIVADO. The bisection logic is unit-tested against a
  simulated design whose true period is known; the driver invocation reuses the
  same argument names the three drivers already accept.
"""

import argparse
import csv as csv_mod
import glob
import math
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TRACKS = {
    "fp16": {
        "driver": os.path.join("fp16_baseline", "synth", "run_fp16_fpga.py"),
        "label": "FP16 baseline",
    },
    "fp32": {
        "driver": os.path.join("fp32_baseline", "synth", "run_fp32_fpga.py"),
        "label": "FP32 baseline",
    },
    "mixed": {
        "driver": "run_mixed_fpga.py",
        "label": "Mixed FP4/FP8",
    },
}
ALL_SIZES = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]
DEFAULT_WORK = os.path.join(REPO_ROOT, "synth_mixed", "constraint_sweep")
DEFAULT_OUT = os.path.join(REPO_ROOT, "synth_mixed", "closing_period.txt")


def log(msg):
    print(f"[constraint-sweep] {msg}", flush=True)


def fnum(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def read_metrics(d):
    """The single *_metrics.csv a one-size probe leaves behind, as a dict."""
    hits = [p for p in glob.glob(os.path.join(d, "*_metrics.csv"))
            if not p.endswith("_driver.csv")]
    if len(hits) != 1:
        return None
    out = {}
    with open(hits[0], newline="", encoding="utf-8") as f:
        for r in csv_mod.reader(f):
            if len(r) == 2 and r[0] != "Metric":
                out[r[0]] = r[1]
    return out


def read_impl_metrics(d):
    """The post-route CSV a probe leaves behind, but only if it routed. Returns
    None when the probe was post-synthesis only, so the caller falls back."""
    hits = glob.glob(os.path.join(d, "*_metrics_impl.csv"))
    if len(hits) != 1:
        return None
    out = {}
    with open(hits[0], newline="", encoding="utf-8") as f:
        for r in csv_mod.reader(f):
            if len(r) == 2 and r[0] != "Metric":
                out[r[0]] = r[1]
    return out if str(out.get("route_ran")) == "1" else None


def probe(track, n, period, work, vivado, timeout, extra):
    """Synthesise once at `period` ns. Returns (wns, crit, fmax, luts) or None."""
    d = os.path.join(work, f"p{period:.2f}")
    os.makedirs(d, exist_ok=True)
    cmd = [sys.executable, os.path.join(REPO_ROOT, TRACKS[track]["driver"]),
           "--sizes", str(n), "--no-saif",
           "--clock-period", f"{period:.3f}",
           "--work-dir", d, "--reports", d,
           "--out", os.path.join(d, "report.txt"),
           "--csv", os.path.join(d, "metrics.csv"),
           "--timeout", str(timeout)]
    if vivado:
        cmd += ["--vivado", vivado]
    cmd += extra
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=REPO_ROOT)
    m = read_metrics(d)
    if m is None:
        tail = (r.stdout or "").strip().splitlines()[-4:]
        log(f"    probe at {period:.2f} ns produced no metrics CSV"
            + ("\n      " + "\n      ".join(tail) if tail else ""))
        return None

    # When --implement is forwarded via --driver-arg, the design was placed and
    # routed and the routed slack is the one that matters. Vivado's own metrics
    # CSV is written by v2 BEFORE place and route, so bisecting on it would find
    # the post-synthesis closing period even though routing had happened. Prefer
    # the routed figures whenever they exist and the design actually routed.
    impl = read_impl_metrics(d)
    if impl is not None:
        return (fnum(impl.get("impl_wns_ns")),
                fnum(impl.get("impl_critical_path_delay_ns")),
                fnum(impl.get("impl_fmax_mhz")),
                fnum(impl.get("impl_lut_count")))
    return (fnum(m.get("wns_ns")), fnum(m.get("critical_path_delay_ns")),
            fnum(m.get("fmax_mhz")), fnum(m.get("lut_count")))


def find_closing_period(track, n, work, args):
    """Bisect until worst slack crosses zero.

    Invariant kept throughout: `fail` is a period with WNS < 0 and `ok` is a
    period with WNS >= 0, so the answer is always bracketed and the loop cannot
    report a period it never actually measured as passing.
    """
    extra = list(args.driver_arg or [])
    os.makedirs(work, exist_ok=True)

    # Probe 1: the delay the 10 ns run already extrapolated. If the
    # extrapolation is any good this is close to the answer.
    first = probe(track, n, args.start, work, args.vivado, args.timeout, extra)
    if first is None:
        return {"N": n, "note": "first probe failed"}
    wns0, crit0, fmax0, luts0 = first
    if crit0 is None:
        return {"N": n, "note": "no critical path in the first probe"}
    log(f"  N={n}: at {args.start} ns, WNS {wns0:+.3f} -> extrapolated "
        f"critical path {crit0:.3f} ns")

    guess = math.ceil(crit0 * 100) / 100.0
    ok, ok_wns, fail = None, None, None
    if wns0 is not None and wns0 >= 0:
        # The start period already meets timing, so it is a valid upper bound.
        ok, ok_wns = args.start, wns0
    # A failing start period is deliberately NOT used as the lower bound. At 10 ns
    # against a ~35 ns path it sits 25 ns below the answer, and bisecting from
    # there burns four probes walking up an interval the first probe has already
    # localised to within a couple of percent.

    probes = 1
    period = guess
    while probes < args.max_probes:
        res = probe(track, n, period, work, args.vivado, args.timeout, extra)
        probes += 1
        if res is None:
            break
        wns, crit, fmax, luts = res
        if wns is None:
            break
        log(f"  N={n}: probe {probes} at {period:.2f} ns -> WNS {wns:+.3f} ns"
            f"  ({'meets' if wns >= 0 else 'misses'})")
        if wns >= 0:
            if ok is None or period < ok:
                ok, ok_wns = period, wns
        else:
            if fail is None or period > fail:
                fail = period

        if ok is not None and fail is not None:
            if ok - fail <= args.tolerance:
                break
            period = (ok + fail) / 2.0
        else:
            # Not yet bracketed. The first probe is the extrapolated delay, which
            # earlier measurement put within ~2 % of the truth, so step in small
            # additive increments rather than scaling: a multiplicative jump
            # overshoots by more than the whole interval of interest and spends
            # probes bisecting back down.
            step = max(2.0 * args.tolerance, 0.01 * period)
            period = (period + step) if ok is None else (period - step)
            if period <= 0:
                break

    row = {"N": n, "extrapolated_crit_ns": crit0, "extrapolated_fmax_mhz": fmax0,
           "luts": luts0, "probes": probes}
    if ok is None:
        row["note"] = (f"no period met timing within {args.max_probes} probes "
                       f"(last tried {period:.2f} ns)")
        return row
    row["closing_period_ns"] = ok
    row["slack_at_closing_ns"] = ok_wns
    row["measured_fmax_mhz"] = 1000.0 / ok
    if crit0:
        row["extrapolation_error_pct"] = 100.0 * (ok - crit0) / crit0
    return row


def render(rows, args):
    def g(r, k, fmt="{:.3f}", dash="-"):
        v = r.get(k)
        return dash if v is None else fmt.format(v)

    COLS = [
        ("Precision track", "FP16 / FP32 / mixed", lambda r: r["_track"]),
        ("FFT size", "points", lambda r: str(r["N"])),
        ("Closing clock period", "ns, meets timing",
         lambda r: g(r, "closing_period_ns", "{:.2f}")),
        ("Worst slack at that period", "ns, positive",
         lambda r: g(r, "slack_at_closing_ns", "{:+.3f}")),
        ("Measured maximum frequency", "MHz",
         lambda r: g(r, "measured_fmax_mhz", "{:.2f}")),
        ("Extrapolated critical path", "ns, from the 10 ns run",
         lambda r: g(r, "extrapolated_crit_ns")),
        ("Extrapolated maximum frequency", "MHz",
         lambda r: g(r, "extrapolated_fmax_mhz", "{:.2f}")),
        ("Extrapolation error", "percent",
         lambda r: g(r, "extrapolation_error_pct", "{:+.2f}")),
        ("Synthesis runs used", "count", lambda r: str(r.get("probes", "-"))),
    ]
    labels = [c[0] for c in COLS]
    units = [c[1] for c in COLS]
    body = [[c[2](r) for c in COLS] for r in rows]
    widths = [max(len(labels[i]), len(units[i]),
                  max((len(b[i]) for b in body), default=0))
              for i in range(len(COLS))]

    L = ["=" * 78,
         "CLOCK PERIOD EACH DESIGN ACTUALLY CLOSES AT",
         "=" * 78, "",
         "The reports constrain at 10 ns, which every design misses, so every",
         "worst-slack figure there is negative and every frequency is an",
         "extrapolation of (10 ns - worst slack). This table bisects the constraint",
         "until worst slack crosses zero, so the closing period is measured and its",
         "slack is positive.",
         "",
         "Stage: whichever the probes ran. Forward --driver-arg --implement to",
         "bisect on POST-ROUTE slack; without it the bisection uses the",
         "post-synthesis estimate, which on this device has been measured 4-16 %",
         "pessimistic and non-uniformly so across the three tracks.",
         "",
         "Timing only. Do not take power or energy from these runs: changing the",
         "constraint changes the operating point, so their dynamic power is not",
         "comparable with the 10 ns figures in the reports.",
         "",
         "  ".join(l.rjust(w) for l, w in zip(labels, widths)),
         "  ".join(u.rjust(w) for u, w in zip(units, widths)),
         "-" * (sum(widths) + 2 * (len(widths) - 1))]
    for b in body:
        L.append("  ".join(v.rjust(w) for v, w in zip(b, widths)))

    errs = [r["extrapolation_error_pct"] for r in rows
            if r.get("extrapolation_error_pct") is not None]
    if errs:
        errs_sorted = sorted(errs)
        med = errs_sorted[len(errs_sorted) // 2]
        L += ["",
              "WAS THE EXTRAPOLATION SOUND?",
              f"  {len(errs)} design(s) compared.",
              f"  Error of (10 ns - worst slack) against the measured closing period:",
              f"    smallest {min(errs):+.2f} %   median {med:+.2f} %   largest {max(errs):+.2f} %",
              "  A small spread means the extrapolated frequencies in the reports are",
              "  sound and can be quoted with this as their uncertainty. A large or",
              "  one-sided error means the reported frequencies need replacing with",
              "  the measured column above."]
    notes = [r for r in rows if r.get("note")]
    if notes:
        L.append("")
        for r in notes:
            L.append(f"NOTE  {r['_track']} N={r['N']}: {r['note']}")

    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + text)
    log(f"table: {args.out}")

    csv_path = os.path.splitext(args.out)[0] + ".csv"
    fields = ["_track", "N", "closing_period_ns", "slack_at_closing_ns",
              "measured_fmax_mhz", "extrapolated_crit_ns",
              "extrapolated_fmax_mhz", "extrapolation_error_pct", "luts",
              "probes", "note"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv_mod.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    log(f"csv  : {csv_path}")


# ---------------------------------------------------------------------------
class _Tee:
    """Duplicate everything printed to a log file beside the report.

    Terminal scrollback is not a record: a run whose output is only on screen is
    lost the moment the window closes, and several results in this project were.
    Every script writes its own console log next to its outputs so the full run,
    warnings included, survives without anyone having to copy text out.
    """

    def __init__(self, path):
        self.path = path
        self.stream = None
        self.stdout = None

    def __enter__(self):
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            self.stream = open(self.path, "w", encoding="utf-8")
        except OSError:
            return self          # logging must never break the run
        self.stdout = sys.stdout
        sys.stdout = self
        return self

    def __exit__(self, *exc):
        if self.stdout is not None:
            sys.stdout = self.stdout
        if self.stream is not None:
            try:
                self.stream.write(f"\n[log written to {self.path}]\n")
                self.stream.close()
            except OSError:
                pass
        return False

    def write(self, s):
        if self.stdout is not None:
            self.stdout.write(s)
        if self.stream is not None:
            try:
                self.stream.write(s)
            except OSError:
                pass

    def flush(self):
        for t in (self.stdout, self.stream):
            try:
                if t is not None:
                    t.flush()
            except OSError:
                pass



def main():
    ap = argparse.ArgumentParser(
        description="Bisect the clock constraint until worst slack crosses zero")
    ap.add_argument("--track", choices=list(TRACKS) + ["all"], default="mixed")
    ap.add_argument("--sizes", type=int, nargs="*", default=[256],
                    help="FFT sizes to sweep (default 256; 'all ten' is hours)")
    ap.add_argument("--start", type=float, default=10.0,
                    help="first probe, in ns. Default 10, matching the reports, "
                         "so its extrapolated delay seeds the bisection.")
    ap.add_argument("--tolerance", type=float, default=0.25,
                    help="stop once the passing and failing periods are this "
                         "close, in ns (default 0.25)")
    ap.add_argument("--max-probes", type=int, default=7,
                    help="synthesis runs per design (default 7)")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--vivado", default=None,
                    help="passed through; omit to use the driver's own default")
    ap.add_argument("--driver-arg", action="append",
                    help="extra argument forwarded to the driver, repeatable "
                         "(e.g. --driver-arg --use-dsp --driver-arg 0)")
    ap.add_argument("--work-dir", default=DEFAULT_WORK)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    # Hostname in the filename, deliberately: these logs are committed so that
    # digital-1's results can be read from any machine, and two machines writing
    # the same path is what turned a pull into an add/add merge conflict.
    import socket
    _host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname().split(".")[0]) or "host"
    log_path = os.path.splitext(os.path.abspath(args.out))[0] + f".{_host}.log"
    with _Tee(log_path):
        _run(args)


def _run(args):

    tracks = list(TRACKS) if args.track == "all" else [args.track]
    for t in tracks:
        p = os.path.join(REPO_ROOT, TRACKS[t]["driver"])
        if not os.path.isfile(p):
            raise SystemExit(f"{t} driver not found at {p}")

    rows = []
    for t in tracks:
        log(f"=== {TRACKS[t]['label']} ===")
        for n in args.sizes:
            work = os.path.join(args.work_dir, t, f"n{n}")
            r = find_closing_period(t, n, work, args)
            r["_track"] = TRACKS[t]["label"]
            if r.get("closing_period_ns"):
                log(f"  N={n}: CLOSES at {r['closing_period_ns']:.2f} ns "
                    f"(slack {r['slack_at_closing_ns']:+.3f} ns, "
                    f"{r['measured_fmax_mhz']:.2f} MHz) in {r['probes']} runs")
            rows.append(r)
    if rows:
        render(rows, args)


if __name__ == "__main__":
    main()
