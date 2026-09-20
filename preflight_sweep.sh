#!/usr/bin/env bash
# =============================================================================
# preflight_sweep.sh - verify everything the NSGA-II sweep needs, then run and
# time ONE real design end to end.
#
# The sweep is hours long and single-threaded per design. Every failure mode it
# has - a missing testbench, an ungated butterfly, a Vivado path that moved, a
# full /tmp - is cheap to detect now and expensive to discover at hour three.
#
#   ./preflight_sweep.sh [fft_size]        default 256
#
# Exit 0 = safe to launch. Exit 1 = do not launch; the failing check says why.
# =============================================================================
set -uo pipefail

FFT_N="${1:-256}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO" || exit 1

PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

hdr "1. Toolchain"

VIVADO_PATH=$(python3 - <<'PY'
import re,pathlib
m = re.search(r"^VIVADO_PATH\s*=\s*['\"]([^'\"]+)", 
              pathlib.Path('globalVariablesMixedFFT.py').read_text(), re.M)
print(m.group(1) if m else '')
PY
)
if [ -n "$VIVADO_PATH" ] && [ -x "$VIVADO_PATH" ]; then
  ok "vivado at $VIVADO_PATH"
else
  bad "VIVADO_PATH in globalVariablesMixedFFT.py is '$VIVADO_PATH' - not executable"
fi
for t in xvlog xelab xsim; do
  command -v "$t" >/dev/null 2>&1 && ok "$t on PATH" || bad "$t not on PATH (needed for the SAIF pass)"
done
[ -n "${XILINX_VIVADO:-}" ] && [ -f "$XILINX_VIVADO/data/verilog/src/glbl.v" ] \
  && ok "glbl.v found via XILINX_VIVADO" \
  || bad "glbl.v not found - gate-level sim will sit at X and the SAIF will be worthless"

hdr "2. Sources"

[ -f tb/tb_fft_power.v ] && ok "activity testbench present" \
  || bad "tb/tb_fft_power.v missing - the evaluator will refuse every design"
[ -f verilog_sources/butterfly_wrapper_gated.v ] && ok "gated butterfly in verilog_sources" \
  || bad "verilog_sources/butterfly_wrapper_gated.v missing"
if grep -q 'butterfly_wrapper_gated' fft_template_generator.py; then
  ok "generator emits the GATED butterfly"
else
  bad "generator still emits the ungated butterfly - energy will span ~5% and the sweep is wasted"
fi
for f in generate_saif_funcsim.tcl vivado_synthesis_v2.tcl energyObjective.py; do
  [ -f "$f" ] && ok "$f present" || bad "$f missing"
done

hdr "3. Python"

python3 - <<'PY' && ok "imports and objective count agree" || bad "python environment is not ready"
import sys
try:
    import numpy, matplotlib, pymoo
except ImportError as e:
    print(f"    missing module: {e.name}"); sys.exit(1)
from globalVariablesMixedFFT import OBJECTIVES, POWER_TB_FILE, SAIF_FRAMES, POPULATION, GENERATIONS
from energyObjective import ENERGY_OBJECTIVES
if OBJECTIVES != ENERGY_OBJECTIVES:
    print(f"    OBJECTIVES={OBJECTIVES} but energyObjective supplies {ENERGY_OBJECTIVES}")
    sys.exit(1)
print(f"    OBJECTIVES={OBJECTIVES}  POPULATION={POPULATION}  GENERATIONS={GENERATIONS}  SAIF_FRAMES={SAIF_FRAMES}")
PY

hdr "4. Disk"

for d in "$REPO" /tmp; do
  avail_k=$(df -Pk "$d" | awk 'NR==2{print $4}')
  avail_g=$((avail_k / 1048576))
  if [ "$avail_g" -ge 20 ]; then ok "$d has ${avail_g} GB free"
  elif [ "$avail_g" -ge 5 ]; then warn "$d has only ${avail_g} GB free - a full sweep may fill it"
  else bad "$d has only ${avail_g} GB free - too little for a sweep"; fi
done

if [ "$FAIL" -gt 0 ]; then
  printf '\n\033[31m%d check(s) failed. Fix these before running the timing probe.\033[0m\n' "$FAIL"
  exit 1
fi

# -----------------------------------------------------------------------------
hdr "5. Timed single design (FFT-$FFT_N, half FP8) - this is the slow part"
# -----------------------------------------------------------------------------

D="preflight_${FFT_N}"
python3 - "$FFT_N" "$D" <<'PY' || { bad "could not generate the probe design"; exit 1; }
import sys, math
from fft_template_generator import FFTTemplateGenerator
n, name = int(sys.argv[1]), sys.argv[2]
g = FFTTemplateGenerator(n)
stages = int(math.log2(n))
chrom = []
for s in range(stages):
    p = 1 if s < stages // 2 else 0
    chrom += [p, p]
core, top = g.generate_verilog(chrom, f"generated_designs/{name}.v")
print(f"    chromosome {chrom}")
PY

mkdir -p reports sim
VDIR="$REPO/verilog_sources"
CORE="$REPO/generated_designs/${D}.v"
TOP="$REPO/generated_designs/${D}_top.v"
SAIF="$REPO/sim/${D}.saif"
CLKP=$(python3 -c "from globalVariablesMixedFFT import CLOCK_PERIOD; print(CLOCK_PERIOD)")
PART=$(python3 -c "from globalVariablesMixedFFT import FPGA_DEVICE; print(FPGA_DEVICE)")
FRAMES=$(python3 -c "from globalVariablesMixedFFT import SAIF_FRAMES; print(SAIF_FRAMES)")

printf '  running SAIF pass ... '
T0=$(date +%s)
"$VIVADO_PATH" -mode batch -source ./generate_saif_funcsim.tcl -nojournal \
  -log "reports/${D}_saif.log" -tclargs \
  "$D" "$CORE" "$TOP" "$VDIR" "$REPO/tb/tb_fft_power.v" "$SAIF" \
  "$FFT_N" "$FRAMES" "$PART" "$CLKP" >/dev/null 2>&1
RC1=$?; T1=$(date +%s); SAIF_S=$((T1-T0))
[ $RC1 -eq 0 ] && printf '%ds\n' "$SAIF_S" || printf 'FAILED after %ds\n' "$SAIF_S"

printf '  running power pass ... '
"$VIVADO_PATH" -mode batch -source ./vivado_synthesis_v2.tcl -nojournal \
  -log "reports/${D}_pwr.log" -tclargs \
  "$D" "reports/${D}_metrics.csv" "$CLKP" "$CORE" "$TOP" "$VDIR" "$PART" \
  "$SAIF" 1 "tb_fft_power/uut" >/dev/null 2>&1
RC2=$?; T2=$(date +%s); PWR_S=$((T2-T1))
[ $RC2 -eq 0 ] && printf '%ds\n' "$PWR_S" || printf 'FAILED after %ds\n' "$PWR_S"

hdr "6. Gates"

CSV="reports/${D}_metrics.csv"
g() { grep -m1 "^$1," "$CSV" 2>/dev/null | cut -d, -f2; }

if [ $RC1 -ne 0 ] || [ $RC2 -ne 0 ]; then
  bad "a Vivado pass exited non-zero - see reports/${D}_saif.log and reports/${D}_pwr.log"
else
  [ -s "$SAIF" ] && ok "SAIF written ($(du -h "$SAIF" | cut -f1))" || bad "SAIF is empty or missing"

  A=$(grep -m1 "Synth Design complete" "reports/${D}_saif.log" | grep -oE '[0-9a-f]+$')
  B=$(grep -m1 "Synth Design complete" "reports/${D}_pwr.log"  | grep -oE '[0-9a-f]+$')
  [ -n "$A" ] && [ "$A" = "$B" ] && ok "synth checksums match ($A)" \
    || bad "checksum mismatch: saif=$A power=$B - the SAIF describes a different netlist"

  [ "$(g saif_used)" = "1" ] && ok "SAIF applied (dyn $(g dynamic_power_vectorless_w) -> $(g dynamic_power_w) W)" \
    || bad "saif_used=$(g saif_used) - power is a vectorless guess and will not vary"

  COV=$(grep -m1 "% of nets annotated" "reports/${D}_pwr.log" | grep -oE '^[0-9]+')
  [ -n "$COV" ] && printf '  \033[36mINFO\033[0m  %s\n' "${COV}% of nets annotated"
  printf '  \033[36mINFO\033[0m  %s\n' "LUTs $(g lut_count)  DSP $(g dsp_count)  fmax $(g fmax_mhz) MHz  fp8_mul kept $(g kept_fp8_mul)"
fi

hdr "7. Sweep estimate"

PER=$((SAIF_S + PWR_S))
THREADS=$(python3 -c "from globalVariablesMixedFFT import SOLUTION_THREADS; print(SOLUTION_THREADS)")
for UNIQ in 200 500 722; do
  HRS=$(python3 -c "print(f'{$UNIQ * $PER / $THREADS / 3600:.1f}')")
  printf '  %4d unique designs / %s threads  ->  %s h\n' "$UNIQ" "$THREADS" "$HRS"
done
printf '\n  %ds per design (%ds SAIF + %ds power).\n' "$PER" "$SAIF_S" "$PWR_S"
if [ "$PER" -gt 420 ]; then
  warn "over 7 min per design. Consider SAIF_FRAMES=2 in globalVariablesMixedFFT.py"
  warn "(halves the netlist simulation; frame 0 stays the warm-up, frame 1 is measured)."
fi

if [ "$FAIL" -gt 0 ]; then
  printf '\n\033[31m%d check(s) failed - do not launch the sweep.\033[0m\n' "$FAIL"; exit 1
fi
printf '\n\033[32mAll %d checks passed. Launch with:\033[0m\n' "$PASS"
printf '  nohup python3 runMixedFFTOptimization.py --mode single --fft-size %s > sweep_%s.log 2>&1 &\n' "$FFT_N" "$FFT_N"
printf '  tail -f sweep_%s.log\n' "$FFT_N"
