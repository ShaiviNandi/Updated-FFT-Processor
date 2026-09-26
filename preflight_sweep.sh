#!/usr/bin/env bash
# preflight_sweep.sh - verify what the NSGA-II sweep needs, then time one design.
#   bash preflight_sweep.sh [fft_size]          default 256
#   PYTHON=/path/to/python3 bash preflight_sweep.sh 256
set -uo pipefail

FFT_N="${1:-256}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO" || exit 1

PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mWARN\033[0m  %s\n' "$*"; }
info() { printf '  \033[36mINFO\033[0m  %s\n' "$*"; }
hdr()  { printf '\n\033[1m%s\033[0m\n' "$*"; }

# 0. /usr/bin/python3 here is 3.6.8 and cannot run pymoo 0.6+. Find one that can.
PY=""
for cand in "${PYTHON:-}" python3.13 python3.12 python3.11 python3 /usr/bin/python3; do
  [ -z "$cand" ] && continue
  res=$(command -v "$cand" 2>/dev/null) || continue
  if "$res" -c "import numpy, matplotlib, pymoo" >/dev/null 2>&1; then PY="$res"; break; fi
done
PY_OK=1
if [ -z "$PY" ]; then PY_OK=0; PY=$(command -v python3 2>/dev/null || echo python3); fi

hdr "1. Toolchain"

# globalVariablesMixedFFT prints a banner at import, so every value is read
# through a tagged sentinel. Capturing bare stdout is what put that banner into
# Vivado's argv as a part number and killed both passes in 2 s.
CFG=$(mktemp)
"$PY" - >"$CFG" 2>/dev/null <<'PYCFG'
try:
    from globalVariablesMixedFFT import (VIVADO_PATH, CLOCK_PERIOD, FPGA_DEVICE,
                                         SAIF_FRAMES, SOLUTION_THREADS,
                                         OBJECTIVES, POPULATION, GENERATIONS)
    from energyObjective import ENERGY_OBJECTIVES
except Exception as e:
    print(f"PFCFG_ERROR={type(e).__name__}: {e}")
else:
    for k, v in (("VIVADO", VIVADO_PATH), ("CLKP", CLOCK_PERIOD),
                 ("PART", FPGA_DEVICE), ("FRAMES", SAIF_FRAMES),
                 ("THREADS", SOLUTION_THREADS), ("OBJ", OBJECTIVES),
                 ("ENOBJ", ENERGY_OBJECTIVES), ("POP", POPULATION),
                 ("GEN", GENERATIONS)):
        print(f"PFCFG_{k}={v}")
PYCFG
cfg() { grep -m1 "^PFCFG_$1=" "$CFG" 2>/dev/null | cut -d= -f2-; }
CFG_ERR=$(cfg ERROR)
VIVADO_BIN=$(cfg VIVADO); CLKP=$(cfg CLKP); PART=$(cfg PART)
FRAMES=$(cfg FRAMES);     THREADS=$(cfg THREADS)
CFG_OBJ=$(cfg OBJ); CFG_ENOBJ=$(cfg ENOBJ); CFG_POP=$(cfg POP); CFG_GEN=$(cfg GEN)
rm -f "$CFG"

if [ -n "$VIVADO_BIN" ] && [ -x "$VIVADO_BIN" ]; then ok "vivado at $VIVADO_BIN"
else bad "VIVADO_PATH is '${VIVADO_BIN:-<unreadable>}' - not executable"; fi
for t in xvlog xelab xsim; do
  command -v "$t" >/dev/null 2>&1 && ok "$t on PATH" || bad "$t not on PATH"
done
if [ -n "${XILINX_VIVADO:-}" ] && [ -f "$XILINX_VIVADO/data/verilog/src/glbl.v" ]; then
  ok "glbl.v found via XILINX_VIVADO"
else bad "glbl.v not found - gate-level sim will sit at X and the SAIF is worthless"; fi

hdr "2. Sources"

[ -f tb/tb_fft_power.v ] && ok "activity testbench present" \
  || bad "tb/tb_fft_power.v missing - the evaluator will refuse every design"
[ -f verilog_sources/butterfly_wrapper_gated.v ] && ok "gated butterfly present" \
  || bad "verilog_sources/butterfly_wrapper_gated.v missing"
grep -q 'butterfly_wrapper_gated' fft_template_generator.py \
  && ok "generator emits the GATED butterfly" \
  || bad "generator still emits the ungated butterfly - the sweep would be wasted"
for f in generate_saif_funcsim.tcl vivado_synthesis_v2.tcl energyObjective.py; do
  [ -f "$f" ] && ok "$f present" || bad "$f missing"
done

hdr "3. Python"

info "interpreter: $PY"
if [ "$PY_OK" -eq 0 ]; then
  bad "no interpreter here can import numpy + matplotlib + pymoo"
  warn "needs pymoo >= 0.6, so Python >= 3.7:"
  warn "  python3.11 -m pip install --user 'pymoo>=0.6' matplotlib numpy"
fi
if [ -n "$CFG_ERR" ]; then
  bad "could not import the project config: $CFG_ERR"
elif [ -n "$CFG_OBJ" ]; then
  [ "$CFG_OBJ" = "$CFG_ENOBJ" ] && ok "OBJECTIVES=$CFG_OBJ matches energyObjective" \
    || bad "OBJECTIVES=$CFG_OBJ but energyObjective supplies $CFG_ENOBJ"
  info "POPULATION=$CFG_POP  GENERATIONS=$CFG_GEN  SAIF_FRAMES=$FRAMES  threads=$THREADS"
fi

hdr "4. Disk"

for d in "$REPO" /tmp; do
  avail_g=$(( $(df -Pk "$d" | awk 'NR==2{print $4}') / 1048576 ))
  if   [ "$avail_g" -ge 20 ]; then ok "$d has ${avail_g} GB free"
  elif [ "$avail_g" -ge 5 ];  then warn "$d has only ${avail_g} GB free"
  else bad "$d has only ${avail_g} GB free - too little for a sweep"; fi
done

for v in CLKP PART FRAMES THREADS; do
  val=${!v}
  case "$val" in
    ""|*[[:space:]]*) bad "config value $v is empty or has whitespace: '${val}'" ;;
  esac
done

if [ "$FAIL" -gt 0 ]; then
  printf '\n\033[31m%d check(s) failed. Fix these first.\033[0m\n' "$FAIL"; exit 1
fi

hdr "5. Timed single design (FFT-$FFT_N, half FP8) - this is the slow part"

info "clock ${CLKP} ns   part ${PART}   frames ${FRAMES}   threads ${THREADS}"

D="preflight_${FFT_N}"
"$PY" - "$FFT_N" "$D" <<'PYGEN' || { bad "could not generate the probe design"; exit 1; }
import sys, math
from fft_template_generator import FFTTemplateGenerator
n, name = int(sys.argv[1]), sys.argv[2]
g = FFTTemplateGenerator(n)
stages = int(math.log2(n))
chrom = []
for s in range(stages):
    p = 1 if s < stages // 2 else 0
    chrom += [p, p]
g.generate_verilog(chrom, f"generated_designs/{name}.v")
sys.stderr.write(f"    chromosome {chrom}\n")
PYGEN

mkdir -p reports sim
VDIR="$REPO/verilog_sources"
CORE="$REPO/generated_designs/${D}.v"
TOP="$REPO/generated_designs/${D}_top.v"
SAIF="$REPO/sim/${D}.saif"
SAIF_LOG="$REPO/reports/${D}_saif.log"
PWR_LOG="$REPO/reports/${D}_pwr.log"
CSV="$REPO/reports/${D}_metrics.csv"

printf '  running SAIF pass ... '
T0=$(date +%s)
"$VIVADO_BIN" -mode batch -source ./generate_saif_funcsim.tcl -nojournal \
  -log "$SAIF_LOG" -tclargs "$D" "$CORE" "$TOP" "$VDIR" \
  "$REPO/tb/tb_fft_power.v" "$SAIF" "$FFT_N" "$FRAMES" "$PART" "$CLKP" >/dev/null 2>&1
RC1=$?; T1=$(date +%s); SAIF_S=$((T1-T0))
[ $RC1 -eq 0 ] && printf '%ds\n' "$SAIF_S" || printf 'FAILED after %ds\n' "$SAIF_S"

printf '  running power pass ... '
"$VIVADO_BIN" -mode batch -source ./vivado_synthesis_v2.tcl -nojournal \
  -log "$PWR_LOG" -tclargs "$D" "$CSV" "$CLKP" "$CORE" "$TOP" "$VDIR" \
  "$PART" "$SAIF" 1 "tb_fft_power/uut" >/dev/null 2>&1
RC2=$?; T2=$(date +%s); PWR_S=$((T2-T1))
[ $RC2 -eq 0 ] && printf '%ds\n' "$PWR_S" || printf 'FAILED after %ds\n' "$PWR_S"

hdr "6. Gates"

g() { grep -m1 "^$1," "$CSV" 2>/dev/null | cut -d, -f2; }

if [ $RC1 -ne 0 ] || [ $RC2 -ne 0 ]; then
  bad "a Vivado pass exited non-zero"
  for L in "$SAIF_LOG" "$PWR_LOG"; do
    [ -f "$L" ] || continue
    printf '\n  --- first errors in %s ---\n' "$(basename "$L")"
    grep -m5 -E '^(ERROR|CRITICAL WARNING)' "$L" | sed 's/^/  /' || true
  done
else
  [ -s "$SAIF" ] && ok "SAIF written ($(du -h "$SAIF" | cut -f1))" || bad "SAIF empty or missing"
  A=$(grep -m1 "Synth Design complete" "$SAIF_LOG" | grep -oE '[0-9a-f]+$')
  B=$(grep -m1 "Synth Design complete" "$PWR_LOG"  | grep -oE '[0-9a-f]+$')
  { [ -n "$A" ] && [ "$A" = "$B" ]; } && ok "synth checksums match ($A)" \
    || bad "checksum mismatch: saif=$A power=$B"
  [ "$(g saif_used)" = "1" ] \
    && ok "SAIF applied (dyn $(g dynamic_power_vectorless_w) -> $(g dynamic_power_w) W)" \
    || bad "saif_used=$(g saif_used) - power is a vectorless guess"
  COV=$(grep -m1 "% of nets annotated" "$PWR_LOG" | grep -oE '^[0-9]+')
  [ -n "$COV" ] && info "${COV}% of nets annotated"
  info "LUTs $(g lut_count)  DSP $(g dsp_count)  fmax $(g fmax_mhz) MHz  fp8_mul kept $(g kept_fp8_mul)"
fi

hdr "7. Sweep estimate"

PER=$((SAIF_S + PWR_S))
for UNIQ in 200 500 722; do
  HRS=$(awk -v u="$UNIQ" -v s="$PER" -v t="$THREADS" 'BEGIN{printf "%.1f", u*s/t/3600}')
  printf '  %4d unique designs / %s threads  ->  %s h\n' "$UNIQ" "$THREADS" "$HRS"
done
printf '\n  %ds per design (%ds SAIF + %ds power).\n' "$PER" "$SAIF_S" "$PWR_S"
[ "$PER" -gt 420 ] && warn "over 7 min per design - consider SAIF_FRAMES=2"

if [ "$FAIL" -gt 0 ]; then
  printf '\n\033[31m%d check(s) failed - do not launch.\033[0m\n' "$FAIL"; exit 1
fi
printf '\n\033[32mAll %d checks passed. Launch with:\033[0m\n' "$PASS"
printf '  nohup %s runMixedFFTOptimization.py --mode single --fft-size %s > sweep_%s.log 2>&1 &\n' "$PY" "$FFT_N" "$FFT_N"
printf '  tail -f sweep_%s.log\n' "$FFT_N"
