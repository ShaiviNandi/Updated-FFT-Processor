#!/usr/bin/env bash
# Measure the widest dynamic-power separation the chromosome space can produce
# at N=256: all-FP4 against all-FP8. If these are under ~0.003 W apart, the
# energy objective is quantised too coarsely to search on.
set -uo pipefail
R="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"; cd "$R" || exit 1
V=/home/digital-1/2025.2/Vivado/bin/vivado
PY=/usr/bin/python3.12
VD=$R/verilog_sources; TB=$R/tb/tb_fft_power.v
mkdir -p reports sim generated_designs

for T in fp4 fp8; do
  D=res_$T
  echo "=== $D : generating ==="
  "$PY" - "$T" <<'PYGEN'
import sys
from fft_template_generator import FFTTemplateGenerator
t = sys.argv[1]
FFTTemplateGenerator(256).generate_verilog([0]*16 if t == 'fp4' else [1]*16,
                                           f"generated_designs/res_{t}.v")
PYGEN
  echo "=== $D : SAIF pass (about 45 s) ==="
  "$V" -mode batch -source ./generate_saif_funcsim.tcl -nojournal \
    -log "reports/${D}_saif.log" -tclargs "$D" \
    "$R/generated_designs/$D.v" "$R/generated_designs/${D}_top.v" "$VD" "$TB" \
    "$R/sim/$D.saif" 256 4 xc7a35tcpg236-1 10.0 >/dev/null 2>&1 \
    && echo "    ok" || echo "    FAILED - see reports/${D}_saif.log"
  echo "=== $D : power pass (about 37 s) ==="
  "$V" -mode batch -source ./vivado_synthesis_v2.tcl -nojournal \
    -log "reports/${D}_pwr.log" -tclargs "$D" "reports/${D}.csv" 10.0 \
    "$R/generated_designs/$D.v" "$R/generated_designs/${D}_top.v" "$VD" \
    xc7a35tcpg236-1 "$R/sim/$D.saif" 1 tb_fft_power/uut >/dev/null 2>&1 \
    && echo "    ok" || echo "    FAILED - see reports/${D}_pwr.log"
done

printf "\n%-6s %10s %10s %8s %8s\n" design dyn_W vecless LUTs fp8mul
for T in fp4 fp8; do
  f=reports/res_$T.csv
  g() { grep -m1 "^$1," "$f" 2>/dev/null | cut -d, -f2; }
  printf "%-6s %10s %10s %8s %8s\n" "$T" "$(g dynamic_power_w)" \
    "$(g dynamic_power_vectorless_w)" "$(g lut_count)" "$(g kept_fp8_mul)"
done

A=$(grep -m1 "^dynamic_power_w," reports/res_fp4.csv 2>/dev/null | cut -d, -f2)
B=$(grep -m1 "^dynamic_power_w," reports/res_fp8.csv 2>/dev/null | cut -d, -f2)
if [ -n "$A" ] && [ -n "$B" ]; then
  awk -v a="$A" -v b="$B" 'BEGIN{
    d = (b > a ? b - a : a - b);
    printf "\nspan = %.3f W over %d quantisation steps (report_power gives 3 dp)\n", d, d*1000 + 0.5;
    if (d >= 0.003) print "VERDICT: enough resolution - the sweep is worth launching.";
    else print "VERDICT: too coarse - raise the analysis clock before launching.";
  }'
fi
