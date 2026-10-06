#!/usr/bin/env python3
"""Post-synthesis vs post-route delta per precision track."""

import argparse
import csv as csv_mod
import glob
import os
import sys
import re
import statistics

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))

TRACKS = [
    ("FP16", os.path.join(REPO_ROOT, "fp16_baseline", "synth", "fpga_work_impl")),
    ("FP32", os.path.join(REPO_ROOT, "fp32_baseline", "synth", "fpga_work_impl")),
    ("Mixed", os.path.join(REPO_ROOT, "synth_mixed", "fpga_work_impl")),
]
DEFAULT_OUT = os.path.join(REPO_ROOT, "synth_mixed", "implementation_delta.txt")

N_RE = re.compile(r"_fft_?(\d+)(?:_|\b)")


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_pairs(path):
    d = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv_mod.reader(f):
            if len(r) == 2 and r[0] != "Metric":
                d[r[0]] = r[1]
    return d


def collect(track_dir):
    out = {}
    for p in sorted(glob.glob(os.path.join(track_dir, "*_metrics_impl.csv"))):
        m = N_RE.search(os.path.basename(p))
        if not m:
            continue
        out[int(m.group(1))] = read_pairs(p)
    return out


def render(title, cols, rows, notes=()):
    labels = [c[0] for c in cols]
    units = [c[1] for c in cols]
    body = [[c[2](r) for c in cols] for r in rows]
    widths = [max(len(labels[i]), len(units[i]),
                  max((len(b[i]) for b in body), default=0))
              for i in range(len(cols))]
    L = ["", title,
         "  ".join(l.rjust(w) for l, w in zip(labels, widths)),
         "  ".join(u.rjust(w) for u, w in zip(units, widths)),
         "-" * (sum(widths) + 2 * (len(widths) - 1))]
    for b in body:
        L.append("  ".join(v.rjust(w) for v, w in zip(b, widths)))
    for n in notes:
        L.append("  " + n)
    return L


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
        description="Post-synthesis vs post-route comparison across the three tracks")
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    import socket
    _host = re.sub(r"[^A-Za-z0-9_-]", "", socket.gethostname().split(".")[0]) or "host"
    log_path = os.path.splitext(os.path.abspath(args.out))[0] + f".{_host}.log"
    with _Tee(log_path):
        _run(args)


def _run(args):

    data = {name: collect(d) for name, d in TRACKS}
    missing = [(n, d) for n, d in TRACKS if not data[n]]
    rows = []
    for name, _ in TRACKS:
        for n in sorted(data[name]):
            r = dict(data[name][n])
            r["_track"] = name
            r["_N"] = n
            rows.append(r)

    if not rows:
        raise SystemExit(
            "no *_metrics_impl.csv found. Run the drivers with --implement first:\n"
            "  python3 fp16_baseline/synth/run_fp16_fpga.py --implement\n"
            "  python3 fp32_baseline/synth/run_fp32_fpga.py --implement\n"
            "  python3 run_mixed_fpga.py --implement")

    def g(r, k, fmt="{:.2f}", dash="-"):
        v = fnum(r.get(k))
        return dash if v is None else fmt.format(v)

    def yn(r, k):
        s = str(r.get(k))
        return "yes" if s == "1" else ("no" if s == "0" else "?")

    L = ["=" * 78,
         "POST-SYNTHESIS vs POST-ROUTE",
         "=" * 78, "",
         "implementation only -- nothing is re-synthesised in between.", ""]
    if missing:
        L.append("MISSING implementation results for: "
                 + ", ".join(f"{n} ({d})" for n, d in missing))
        L.append("")

    L += render("TABLE 1  DID IMPLEMENTATION COMPLETE?", [
        ("Precision track", "FP16 / FP32 / mixed", lambda r: r["_track"]),
        ("FFT size", "points", lambda r: str(r["_N"])),
        ("Placed", "yes / no", lambda r: yn(r, "place_ran")),
        ("Physically optimised", "yes / no", lambda r: yn(r, "phys_opt_ran")),
        ("Routed", "yes / no", lambda r: yn(r, "route_ran")),
        ("Elapsed", "seconds", lambda r: g(r, "impl_elapsed_s", "{:.0f}")),
        ("Error, if any", "Vivado message", lambda r: (r.get("impl_error") or "").strip('"') or "-"),
    ], rows, notes=[])

    L += render("TABLE 2  CRITICAL PATH AND ACHIEVABLE FREQUENCY", [
        ("Precision track", "FP16 / FP32 / mixed", lambda r: r["_track"]),
        ("FFT size", "points", lambda r: str(r["_N"])),
        ("Critical path, post-synthesis", "ns", lambda r: g(r, "synth_critical_path_delay_ns", "{:.3f}")),
        ("Critical path, post-route", "ns", lambda r: g(r, "impl_critical_path_delay_ns", "{:.3f}")),
        ("Change in critical path", "percent", lambda r: g(r, "delta_crit_pct", "{:+.2f}")),
        ("Maximum frequency, post-synthesis", "MHz", lambda r: g(r, "synth_fmax_mhz")),
        ("Maximum frequency, post-route", "MHz", lambda r: g(r, "impl_fmax_mhz")),
        ("Change in maximum frequency", "percent", lambda r: g(r, "delta_fmax_pct", "{:+.2f}")),
    ], rows, notes=[])

    L += render("TABLE 3  AREA AND DYNAMIC POWER", [
        ("Precision track", "FP16 / FP32 / mixed", lambda r: r["_track"]),
        ("FFT size", "points", lambda r: str(r["_N"])),
        ("Logic LUTs, post-synthesis", "count", lambda r: g(r, "synth_lut_count", "{:.0f}")),
        ("Logic LUTs, post-route", "count", lambda r: g(r, "impl_lut_count", "{:.0f}")),
        ("Change in logic LUTs", "percent", lambda r: g(r, "delta_lut_pct", "{:+.2f}")),
        ("Dynamic power, post-synthesis", "watts", lambda r: g(r, "synth_dynamic_power_w", "{:.4f}")),
        ("Dynamic power, post-route", "watts", lambda r: g(r, "impl_dynamic_power_w", "{:.4f}")),
        ("Change in dynamic power", "percent", lambda r: g(r, "delta_dynamic_power_pct", "{:+.2f}")),
        ("Switching activity annotated", "yes / no", lambda r: yn(r, "saif_used")),
    ], rows, notes=[])

    # ---- the summary that actually decides the question ----
    def spread(track, key):
        vals = [fnum(r.get(key)) for r in rows
                if r["_track"] == track and fnum(r.get(key)) is not None]
        if not vals:
            return None
        return (min(vals), statistics.median(vals), max(vals), len(vals))

    summary = []
    for name, _ in TRACKS:
        for key, label in (("delta_crit_pct", "critical path"),
                           ("delta_fmax_pct", "maximum frequency"),
                           ("delta_lut_pct", "logic LUTs"),
                           ("delta_dynamic_power_pct", "dynamic power")):
            sp = spread(name, key)
            if sp:
                summary.append({"track": name, "what": label,
                                "min": sp[0], "med": sp[1], "max": sp[2], "n": sp[3]})

    if summary:
        L += render("TABLE 4  SPREAD OF THE CHANGE, BY TRACK", [
            ("Precision track", "FP16 / FP32 / mixed", lambda r: r["track"]),
            ("Quantity", "figure of merit", lambda r: r["what"]),
            ("Designs compared", "count", lambda r: str(r["n"])),
            ("Smallest change", "percent", lambda r: "{:+.2f}".format(r["min"])),
            ("Median change", "percent", lambda r: "{:+.2f}".format(r["med"])),
            ("Largest change", "percent", lambda r: "{:+.2f}".format(r["max"])),
        ], summary, notes=[])

        crit = {s["track"]: s["med"] for s in summary if s["what"] == "critical path"}
        if len(crit) >= 2:
            worst = max(crit.values()) - min(crit.values())
            L += ["", "VERDICT INPUT",
                  + ", ".join(f"{k} {v:+.2f} %" for k, v in crit.items()),
                  f"  Spread between tracks: {worst:.2f} percentage points.",
                  "  comparison on post-route numbers."]

    text = "\n".join(L) + "\n"
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print("\n" + text)
    print(f"[impl-delta] table: {args.out}")


if __name__ == "__main__":
    main()
