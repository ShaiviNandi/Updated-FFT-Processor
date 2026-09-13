#!/usr/bin/env bash
# =============================================================================
# VIVADO_RUNBOOK.sh -- copy-paste command sequence to validate the option-(a)
# flow changes and produce the rebuttal evidence.
#
# Run the STEPS one at a time and read the output. Do NOT run this file
# top-to-bottom unattended the first time: step 3 is a go/no-go gate.
#
# Assumes the repo root is the current directory and Vivado is the path from
# globalVariablesMixedFFT.py. Adjust VIVADO and REPO for your machine.
# =============================================================================

set -u

# ---- config -----------------------------------------------------------------
export REPO="${REPO:-$PWD}"
export VIVADO="${VIVADO:-/home/digital-1/2025.2/Vivado/bin/vivado}"
export PART="xc7a35tcpg236-1"
export VDIR="$REPO/verilog_sources"
export GEN="$REPO/generated_designs"
export RPT="$REPO/reports"
export CLKP="10.0"          # 10 ns test constraint: exposes the real gap.
                            # (globalVariablesMixedFFT.py uses 80.0, which
                            #  every design clears trivially.)
export NOSAIF="/tmp/__no_saif__.saif"   # deliberately absent -> vectorless

mkdir -p "$RPT" "$GEN"

# =============================================================================
echo "### STEP 0  environment sanity"
# =============================================================================
"$VIVADO" -version
which xvlog xelab xsim || echo "NOTE: xsim tools not on PATH - source settings64.sh"
python3 -c "import pymoo, numpy; print('pymoo', pymoo.__version__)"
ls -l "$REPO/vivado_synthesis.tcl" "$REPO/vivado_synthesis_v2.tcl"
cp -n "$REPO/vivado_synthesis.tcl" "$REPO/vivado_synthesis.tcl.v1.bak" && echo "v1 backed up"

# =============================================================================
echo "### STEP 1  generate three probe designs for N=256"
#   fp8x0 : no stage uses FP8         -> expect the PRUNED tier
#   fp8x4 : late 4 stages use FP8     -> expect the UNION tier
#   fp8x8 : every stage uses FP8      -> expect the UNION tier
# =============================================================================
cd "$REPO"
python3 - <<'PY'
from fft_template_generator import FFTTemplateGenerator
g = FFTTemplateGenerator(256)
assert g.get_chromosome_length() == 16, g.get_chromosome_length()
# chromosome is interleaved: [s0_mult, s0_add, s1_mult, s1_add, ...]
probes = {
    'probe_fp8x0': [0, 0] * 8,
    'probe_fp8x4': [0, 0] * 4 + [1, 1] * 4,
    'probe_fp8x8': [1, 1] * 8,
}
for name, chrom in probes.items():
    core, top = g.generate_verilog(chrom, f'generated_designs/{name}.v')
    print(f'{name}: {core}  {top}')
PY

# =============================================================================
echo "### STEP 2  BASELINE: run the OLD tcl on one probe (reference numbers)"
# =============================================================================
"$VIVADO" -mode batch -source "$REPO/vivado_synthesis.tcl" -nojournal -log /tmp/v1_fp8x4.log \
  -tclargs probe_fp8x4 "$RPT/probe_fp8x4_v1.csv" "$CLKP" \
           "$GEN/probe_fp8x4.v" "$GEN/probe_fp8x4_top.v" "$VDIR" "$PART"
cat "$RPT/probe_fp8x4_v1.csv"

# =============================================================================
echo "### STEP 3  GO/NO-GO: run the NEW tcl on the same probe and diff"
#   argv 7 = SAIF path (absent here -> vectorless, with a loud warning)
#   argv 8 = use_dsp (1 = keep the (* use_dsp *) attributes)
# =============================================================================
"$VIVADO" -mode batch -source "$REPO/vivado_synthesis_v2.tcl" -nojournal -log /tmp/v2_fp8x4.log \
  -tclargs probe_fp8x4 "$RPT/probe_fp8x4_v2.csv" "$CLKP" \
           "$GEN/probe_fp8x4.v" "$GEN/probe_fp8x4_top.v" "$VDIR" "$PART" \
           "$NOSAIF" 1
cat "$RPT/probe_fp8x4_v2.csv"

echo "--- v1 vs v2 side by side ---"
diff -y --width=80 "$RPT/probe_fp8x4_v1.csv" "$RPT/probe_fp8x4_v2.csv" || true

echo "--- did opt_design actually run? ---"
grep -E "opt_design ran|CRITICAL" /tmp/v2_fp8x4.log
echo "STOP HERE if opt_design_ran is 0 or Vivado errored. Fix that first."

# =============================================================================
echo "### STEP 4  THE REBUTTAL EVIDENCE: forensics across the three tiers"
# =============================================================================
for p in probe_fp8x0 probe_fp8x4 probe_fp8x8; do
  "$VIVADO" -mode batch -source "$REPO/vivado_synthesis_v2.tcl" -nojournal -log "/tmp/v2_$p.log" \
    -tclargs "$p" "$RPT/${p}_v2.csv" "$CLKP" \
             "$GEN/$p.v" "$GEN/${p}_top.v" "$VDIR" "$PART" "$NOSAIF" 1
done

echo "--- fp8_mul / fp4_mul instances kept, per probe ---"
grep -H "^FORENSIC" /tmp/v2_probe_fp8x*.log | grep -E "fp8_mul|fp4_mul|DSP48"

echo "--- the table for the paper ---"
printf "%-16s %8s %6s %6s %10s %12s %12s\n" design LUTs DSPs FFs fmax_MHz kept_fp8_mul kept_fp4_mul
for p in probe_fp8x0 probe_fp8x4 probe_fp8x8; do
  f="$RPT/${p}_v2.csv"
  get() { grep -m1 "^$1," "$f" | cut -d, -f2; }
  printf "%-16s %8s %6s %6s %10.2f %12s %12s\n" "$p" \
    "$(get lut_count)" "$(get dsp_count)" "$(get ff_count)" \
    "$(get fmax_mhz)" "$(get kept_fp8_mul)" "$(get kept_fp4_mul)"
done

# =============================================================================
echo "### STEP 5  PROVE the union: re-run with dont_touch on the shared butterfly"
#   Expectation: the +410 LUT step VANISHES and all three probes land within
#   ~2-3% of each other. That is the fixed-union result the paper claims.
# =============================================================================
cat > /tmp/keep_bf.xdc <<'XDC'
set_property DONT_TOUCH true [get_cells -hier -filter {REF_NAME =~ *butterfly_wrapper*}]
XDC
echo "Add this to the RTL instead (more reliable than a post-synth XDC) --"
echo "in fft_template_generator.py, at the shared_bf instantiation, emit:"
echo '    (* keep_hierarchy = "yes", dont_touch = "yes" *) butterfly_wrapper shared_bf ('
echo "then re-run STEP 4 and compare the LUT column."

# =============================================================================
echo "### STEP 6  the use_dsp experiment (argv 8 = 0 adds -max_dsp 0)"
# =============================================================================
for p in probe_fp8x0 probe_fp8x8; do
  "$VIVADO" -mode batch -source "$REPO/vivado_synthesis_v2.tcl" -nojournal -log "/tmp/v2_nodsp_$p.log" \
    -tclargs "${p}" "$RPT/${p}_nodsp.csv" "$CLKP" \
             "$GEN/$p.v" "$GEN/${p}_top.v" "$VDIR" "$PART" "$NOSAIF" 0
  echo "$p  with DSP: $(grep -m1 '^lut_count,' "$RPT/${p}_v2.csv"    | cut -d, -f2) LUT / $(grep -m1 '^dsp_count,' "$RPT/${p}_v2.csv"    | cut -d, -f2) DSP"
  echo "$p  no  DSP: $(grep -m1 '^lut_count,' "$RPT/${p}_nodsp.csv" | cut -d, -f2) LUT / $(grep -m1 '^dsp_count,' "$RPT/${p}_nodsp.csv" | cut -d, -f2) DSP"
done

# =============================================================================
echo "### STEP 7  log forensics -- where the pruning is announced"
# =============================================================================
echo "--- removed logic (expect hits for fp8x0, none for fp8x8) ---"
grep -nE "Synth 8-(3332|6014|3331)|is unused and will be removed|Unused sequential" \
     /tmp/v2_probe_fp8x0.log | head -20
grep -nE "Synth 8-(3332|6014|3331)" /tmp/v2_probe_fp8x8.log | head -20

echo "--- DSP inference decisions ---"
grep -inE "DSP|multiplier|use_dsp|implemented as" /tmp/v2_probe_fp8x8.log | grep -iE "mul|dsp" | head -20

echo "--- hierarchical utilisation: per-instance attribution ---"
grep -E "cmul|_mul|add_sub|shared_bf|butterfly" /tmp/probe_fp8x8_util_hier.rpt | head -30

echo "--- the worst path in full ---"
sed -n '1,80p' /tmp/probe_fp8x8_timing_worst.rpt

# =============================================================================
echo "### STEP 8  SAIF power (needs tb/tb_fft_power.v first)"
#   Without this, dynamic_power_w stays vectorless and will NOT vary.
# =============================================================================
# "$VIVADO" -mode batch -source "$REPO/generate_saif.tcl" -nojournal \
#   -tclargs probe_fp8x4 "$GEN/probe_fp8x4.v" "$GEN/probe_fp8x4_top.v" \
#            "$VDIR" "$REPO/tb/tb_fft_power.v" /tmp/probe_fp8x4.saif 200000
#
# then re-run with the SAIF as argv 7:
# "$VIVADO" -mode batch -source "$REPO/vivado_synthesis_v2.tcl" -nojournal \
#   -tclargs probe_fp8x4 "$RPT/probe_fp8x4_saif.csv" "$CLKP" \
#            "$GEN/probe_fp8x4.v" "$GEN/probe_fp8x4_top.v" "$VDIR" "$PART" \
#            /tmp/probe_fp8x4.saif 1
# grep -E "dynamic_power_w|static_power_w|saif_used" "$RPT/probe_fp8x4_saif.csv"

# =============================================================================
echo "### STEP 9  simulation-side checks (no Vivado needed)"
# =============================================================================
cd "$REPO/equiv" 2>/dev/null && {
  iverilog -g2012 -o tb_equiv.vvp \
    ../verilog_sources/adder.v ../verilog_sources/multiplier.v \
    ../verilog_sources/precision_converter.v \
    ../verilog_sources/mixed_precision_wrappers.v \
    ../verilog_sources/butterfly_wrapper_gated.v tb_equiv.v && ./tb_equiv.vvp | tail -5
  ./sweep_activity.sh
  cd "$REPO"
}

# =============================================================================
echo "### STEP 10  the statistics for the rebuttal"
# =============================================================================
cd "$REPO"
python3 diagnose_area_variance.py results/fft_256/all_generations_fft256.csv
python3 diagnose_area_variance.py results/fft_1024/all_generations_fft1024.csv
python3 energyObjective.py

echo "### DONE"
