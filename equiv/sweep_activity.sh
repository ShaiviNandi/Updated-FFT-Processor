#!/usr/bin/env bash
# sweep_activity.sh -- toggle activity vs number of FP8 stages, gated vs ungated.
# Produces activity_sweep.csv for the paper's energy objective.
set -e
SRC="adder.v multiplier.v precision_converter.v mixed_precision_wrappers.v butterfly_wrapper_gated.v tb_activity.v"
echo "n_fp8_stages,toggles_ungated,toggles_isolated,reduction_pct" > activity_sweep.csv
for f in 0 1 2 3 4 5 6 7 8; do
  iverilog -g2012 -DCHROM_FP8_FRAC=$f -o .sweep.vvp $SRC 2>/dev/null
  ./.sweep.vvp >/dev/null 2>&1
  python3 count_toggles.py activity.vcd \
    | awk -v n=$f '/^ +WRAPPER TOTAL +[0-9,]+/{gsub(",","",$3);gsub(",","",$4);gsub("%","",$5);print n","$3","$4","$5}' \
    >> activity_sweep.csv
done
rm -f .sweep.vvp
cat activity_sweep.csv
