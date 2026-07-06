import pandas as pd
import numpy as np
import os

# -----------------------------------------------------------------------------
# CSV files to process
# -----------------------------------------------------------------------------

files = [
    "./results/fft_2/all_solutions_fft2.csv",
    "./results/fft_4/all_solutions_fft4.csv",
    "./results/fft_8/all_solutions_fft8.csv",
    "./results/fft_16/all_solutions_fft16.csv",
    "./results/fft_32/all_solutions_fft32.csv",
    "./results/fft_64/all_solutions_fft64.csv",      # Fixed path
    "./results/fft_128/all_solutions_fft128.csv",
    "./results/fft_256/all_solutions_fft256.csv",
    "./results/fft_512/all_solutions_fft512.csv",
    "./results/fft_1024/all_solutions_fft1024.csv",
]

mixed_precision_results = []

print("=" * 70)
print("Extracting Best Balanced Mixed-Precision Designs")
print("=" * 70)

for f in files:

    if not os.path.exists(f):
        print(f"[SKIP] File not found: {f}")
        continue

    print(f"\nProcessing {f}")

    df = pd.read_csv(f)

    # -------------------------------------------------------------------------
    # Required columns
    # -------------------------------------------------------------------------

    required_cols = [
        "power_W",
        "area_LUTs",
        "crit_delay_ns",
        "sqnr_dB",
    ]

    missing = [c for c in required_cols if c not in df.columns]

    if missing:
        print(f"Missing columns: {missing}")
        continue

    # -------------------------------------------------------------------------
    # Identify chromosome columns
    #
    # FIX: The secondary sort key used to be the raw string "mult"/"add",
    # which sorts alphabetically ("add" < "mult") and silently transposed
    # every stage's two bits when they differed (e.g. stage bits (1,0)
    # got written out as "01" instead of "10"). This is invisible whenever
    # mult == add for a stage, which is why it went unnoticed until a
    # stage with distinct bits showed up. We now force "mult" to always
    # sort before "add", matching the convention fft_template_generator.py
    # uses when it decodes the chromosome (mult bit first, add bit second).
    # -------------------------------------------------------------------------

    chrom_cols = sorted(
        [
            c
            for c in df.columns
            if c.startswith("s") and ("_mult" in c or "_add" in c)
        ],
        key=lambda x: (
            int(x.split("_")[0][1:]),
            0 if x.split("_")[1] == "mult" else 1,
        ),
    )

    # -------------------------------------------------------------------------
    # Filter by SQNR and timing
    # -------------------------------------------------------------------------

    mask = df["sqnr_dB"] >= 15

    if "meets_timing" in df.columns:
        mask &= df["meets_timing"] == 1

    filtered = df[mask].copy()

    if filtered.empty:
        print("No design satisfies SQNR/timing constraints.")
        continue

    # -------------------------------------------------------------------------
    # Mixed precision check
    # -------------------------------------------------------------------------

    def is_mixed(row):
        vals = [row[c] for c in chrom_cols]
        return (0 in vals) and (1 in vals)

    mixed_df = filtered[filtered.apply(is_mixed, axis=1)].copy()

    if not mixed_df.empty:
        target_df = mixed_df
        config_type = "Mixed Precision"
    else:
        target_df = filtered
        config_type = "Fallback"

    # -------------------------------------------------------------------------
    # Normalization
    # -------------------------------------------------------------------------

    metrics_min = [
        "power_W",
        "area_LUTs",
        "crit_delay_ns",
    ]

    metric_max = "sqnr_dB"

    for m in metrics_min:

        mn = target_df[m].min()
        mx = target_df[m].max()

        if mn == mx:
            target_df[f"norm_{m}"] = 0.5
        else:
            target_df[f"norm_{m}"] = (
                target_df[m] - mn
            ) / (mx - mn)

    mn = target_df[metric_max].min()
    mx = target_df[metric_max].max()

    if mn == mx:
        target_df[f"norm_{metric_max}"] = 0.5
    else:
        target_df[f"norm_{metric_max}"] = (
            target_df[metric_max] - mn
        ) / (mx - mn)

    # -------------------------------------------------------------------------
    # Balanced score
    # Lower is better
    # -------------------------------------------------------------------------

    target_df["balance_score"] = np.sqrt(
        target_df["norm_power_W"] ** 2
        + target_df["norm_area_LUTs"] ** 2
        + target_df["norm_crit_delay_ns"] ** 2
        + (1 - target_df["norm_sqnr_dB"]) ** 2
    )

    # -------------------------------------------------------------------------
    # Pick best solution
    # -------------------------------------------------------------------------

    best = target_df.loc[target_df["balance_score"].idxmin()].copy()

    chromosome = "".join(
        str(int(best[c]))
        for c in chrom_cols
    )

    best["chromosome"] = chromosome
    best["config_type"] = config_type

    mixed_precision_results.append(best)

# =============================================================================
# Final Summary
# =============================================================================

print("\n" + "=" * 70)

if len(mixed_precision_results) == 0:
    print("No valid solutions found.")
    exit()

summary_df = pd.DataFrame(mixed_precision_results)

display_cols = [
    "fft_size",
    "solution_id",
    "chromosome",
    "config_type",
    "power_W",
    "area_LUTs",
    "crit_delay_ns",
    "sqnr_dB",
    "balance_score",
]

display_cols = [c for c in display_cols if c in summary_df.columns]

summary_df = summary_df.sort_values("fft_size")

print(summary_df[display_cols].to_string(index=False))

summary_df[display_cols].to_csv(
    "best_mixed_precision_solutions.csv",
    index=False,
)

print("\nSaved summary to:")
print("best_mixed_precision_solutions.csv")
print("=" * 70)