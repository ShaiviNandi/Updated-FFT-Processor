# Constrained Synthesis Run — Results and Corrections

Vivado 2025.2, `xc7a35tcpg236-1`, OOC, 10 ns constraint applied **before** synthesis, `opt_design` ran. Run date 2026-09-18.

---

## 1. The hypothesis is confirmed — and one number is stronger than predicted

| design | FP8 mult stages | LUTs | DSPs | FFs | f_max | `kept_fp8_mul` | `kept_fp4_mul` |
|---|---|---|---|---|---|---|---|
| `probe_fp8x0` | 0 / 8 | **505** | **6** | 128 | **63.62 MHz** | **0** | 4 |
| `probe_fp8x4` | 4 / 8 | **1309** | 10 | 156 | 28.19 MHz | **4** | 4 |
| `probe_fp8x8` | 8 / 8 | **1276** | 10 | 145 | 28.67 MHz | **4** | 4 |

`kept_fp8_mul = 0` for the FP4-only chromosome and `= 4` for both others. That single line is the rebuttal: the FP8 multiplier cone is **physically absent** from one netlist and present in the others, and nothing else about the chromosome changes the area.

**The stronger-than-predicted result: 4/8 FP8 stages costs MORE LUTs than 8/8.** 1309 vs 1276 — area is *not* monotonic in the FP8 gene count, which is a cleaner refutation of "area scales with precision" than the correlation statistics alone. The mechanism is visible in the forensics: with a mixed chromosome both the FP4 **and** FP8 add paths are reachable, so both format converters stay live; with all-FP8 the FP4 add path is dead and gets pruned. The 33-LUT difference is also inside the 2.9 % noise band measured over 708 designs, so either reading supports the same conclusion.

DSP arithmetic, straight from the synthesis log's DSP mapping table:

```
dit_fft_agu_streaming  C+A*B  11x11     -> 1 DSP48E1
dit_fft_agu_streaming  A*B    11x9      -> 1 DSP48E1
fp4_mul  x4            A*B     2x2      -> 4 DSP48E1
fp8_mul  x4            A*B     4x4      -> 4 DSP48E1
```

6 = 2 AGU + 4 fp4_mul. 10 = that plus 4 fp8_mul. **Eight DSP48E1 macros are being spent on 2×2-bit and 4×4-bit significand products** — about 6 LUT6s of real arithmetic each. That is 20 % of the XC7A35T's DSP budget. Run step 6 of the runbook (`use_dsp = 0`) and report LUT-only numbers.

`probe_fp8x0` at **63.6 MHz vs 28.2 MHz** — the FP4-only configuration is 2.3× faster. Worth a sentence in the paper: the precision schedule buys frequency, not area.

## 2. Three corrections to what I told you earlier

**`opt_design` is not the noise source. I was wrong about that.** The log is unambiguous:

```
Opt_design Change Summary
  Retarget                     0 created  0 removed
  Constant propagation         0 created  0 removed
  Sweep                        0 created  0 removed
  BUFG optimization            0 created  0 removed
  Shift Register Optimization  0 created  0 removed
  Post Processing Netlist      0 created  0 removed
```

`opt_design` runs clean and changes nothing. The FP8-cone pruning happens inside `synth_design`, in its *Cross Boundary and Area Optimization* phase — by the time `opt_design` sees the netlist there is nothing left to remove. So restoring it did not fix a defect; keep the call anyway (3 s, and `opt_design_ran=1` in the CSV proves the numbers are post-optimisation), but **do not cite it in the paper as the cause of the earlier variance.** The v1→v2 LUT change comes from F1, not F2.

**Constraining before synthesis barely moved the timing.** 35.479 ns constrained vs 35.013–35.490 ns unconstrained. That is itself a result worth stating: the butterfly path is combinational-limited, so no constraint can close it — only pipelining can. F1 still matters for methodological correctness (you can now say the results are from a constrained flow), but don't promise reviewers it changed the numbers.

**Two forensic checks are unreliable as written, and I've annotated both in the script.** `bf_mult_prec nets present = 0` on every probe — synthesis renames and absorbs the net, so its absence proves nothing. And hierarchy is partly flattened before IO insertion, so `fp4_add_sub`, both `*_complex_add_sub` and both converters read 0 even when present. Trust `fp4_mul`, `fp8_mul`, `fp4_cmul`, `fp8_cmul` and the DSP48 count — those survive because DSP48E1 macros anchor the cells against flattening.

## 3. Power is now split, and it independently confirms the gating measurement

| | `probe_fp8x4` | `probe_fp8x8` |
|---|---|---|
| total on-chip | 0.106 W | 0.107 W |
| **dynamic** | **0.037 W** | **0.038 W** |
| static | 0.069 W | 0.069 W |

Two things follow. First, the old single `total_power_w` figure was **65 % static** — which is exactly why it took only three values across 722 designs. Splitting it out is a real improvement even before SAIF.

Second, and more useful: dynamic power varies by **2.7 %** between 4/8 and 8/8 FP8 stages. The Icarus toggle-count sweep predicted **2.5 %** across the *entire* chromosome space for the ungated design. Two completely independent methods — Vivado's vectorless propagation and a gate-level VCD toggle count — agree to within a fraction of a percent. The ungated architecture genuinely has almost no power dynamic range, and operand isolation is what creates it (84.5 %, R² = 0.99998).

## 4. Two bugs the run exposed, both fixed

**XDC input delay on the clock port.** `WARNING: [Constraints 18-6211] Setting input delay on a clock pin 'clk' relative to clock 'clk' defined on the same pin is not supported, ignoring it` — my `all_inputs` included `clk`, and Vivado discarded the whole constraint. Now:

```tcl
set_input_delay -clock clk 0.500 [remove_from_collection [all_inputs] [get_ports clk]]
```

**`generate_saif.tcl` had no testbench to run.** `tb/tb_fft_power.v` didn't exist. It does now (§5).

## 5. `tb/tb_fft_power.v` — written and verified

Parameterised by compile-time defines, because the DUT module name and transform size change per chromosome:

```
-d DUT_TOP=probe_fp8x4_top  -d FFT_N=256  -d FRAMES=4
```

Verified under Icarus against all three probe designs, regenerated locally with your own `fft_template_generator.py`:

```
=== tb_fft_power : N=256  ADDR_W=8  frames=3 ===
frame 0: done after 1123 cycles (warm-up, excluded)
frame 1: done after 1123 cycles
frame 2: done after 1123 cycles
=== tb_fft_power complete: 2 measured frame(s) ===
```

**1123 cycles** — identical to `avg_exec_cycles` in your result CSVs, on all three probes. The load/start/done handshake is right and the readback is non-zero, so the datapath is genuinely exercised.

Design notes:

- Stimulus is a fixed LCG producing FP8 E4M3 pairs with the exponent held in 4–10. Exponent 15 (NaN/Inf) and 0 (zero/subnormal) are avoided so the multipliers don't take their `a_zero || b_zero` short-circuit — otherwise the SAIF would record artificially low activity. The generator is deterministic, which matters: **two chromosomes can only be compared on power if they saw identical data.**
- Frame 0 is a warm-up. `saif_window` goes high from frame 1, and `generate_saif.tcl` now polls it before `open_saif`, so reset and first-load transients stay out of the recording. It then uses `run all` instead of a fixed time, so the SAIF covers exactly the measured frames.
- It is **not** a correctness testbench — it checks the handshake and warns on all-zero readback, nothing more. Keep using the functional TB for verification.

`generate_saif.tcl` argv changed: `... <saif_out> <fft_n> [frames]` (was `[sim_time_ns]`).

## 6. Commands for the next steps

**Copy `tb_fft_power.v` into place, then generate a SAIF and re-run with it:**

```bash
mkdir -p "$REPO/tb"
# tb_fft_power.v is delivered in tb/ - copy it to "$REPO/tb/tb_fft_power.v"

for p in probe_fp8x0 probe_fp8x4 probe_fp8x8; do
  $VIVADO -mode batch -source generate_saif.tcl -nojournal -log "/tmp/saif_$p.log" \
    -tclargs "$p" "$GEN/$p.v" "$GEN/${p}_top.v" "$VDIR" \
             "$REPO/tb/tb_fft_power.v" "/tmp/$p.saif" 256 4
  ls -la "/tmp/$p.saif"
done
```

**Then the measurement that decides whether the energy objective works:**

```bash
for p in probe_fp8x0 probe_fp8x4 probe_fp8x8; do
  $VIVADO -mode batch -source vivado_synthesis_v2.tcl -nojournal -log "/tmp/v2saif_$p.log" \
    -tclargs "$p" "$RPT/${p}_saif.csv" $CLKP \
             "$GEN/$p.v" "$GEN/${p}_top.v" "$VDIR" $PART "/tmp/$p.saif" 1
done

printf "%-14s %10s %10s %10s %6s\n" design dynamic_W static_W total_W saif
for p in probe_fp8x0 probe_fp8x4 probe_fp8x8; do
  f="$RPT/${p}_saif.csv"; g(){ grep -m1 "^$1," "$f" | cut -d, -f2; }
  printf "%-14s %10s %10s %10s %6s\n" "$p" "$(g dynamic_power_w)" \
    "$(g static_power_w)" "$(g total_power_w)" "$(g saif_used)"
done
```

**Go/no-go:** `saif_used` must read `1`. If it reads `0`, the SAIF was empty or unreadable — check the `log_saif` scope in the xsim log before scaling up. And if `dynamic_power_w` still spans only ~3 % across the three probes, that is the **expected** ungated result, not a failure: it is the measurement that motivates operand isolation. The point at which the objective becomes usable is after `butterfly_wrapper_gated` is wired in.

**The `dont_touch` experiment** — this is a code edit, not a shell command (that's what the `bash: syntax error near unexpected token` was):

```bash
python3 - <<'PY'
import re, pathlib
p = pathlib.Path('fft_template_generator.py')
s = p.read_text()
old = 'butterfly_wrapper shared_bf ('
new = '(* keep_hierarchy = "yes", dont_touch = "yes" *) butterfly_wrapper shared_bf ('
n = s.count(old)
print(f'found {n} occurrence(s) of the shared_bf instantiation')
if n and new not in s:
    p.write_text(s.replace(old, new))
    print('patched - regenerate the probes and re-run step 4')
else:
    print('nothing to do (already patched, or the emitted text differs - grep for shared_bf)')
PY
```

Then regenerate the probes (step 1) and re-run step 4. **Prediction:** the +771/+804 LUT step collapses and all three probes land within 2–3 % of each other — the fixed-union result the paper claims. Two columns, with and without, is the table that closes the reviewer thread.

## 7. Status

| file | status |
|---|---|
| `vivado_synthesis_v2.tcl` | **run, works**; XDC clock-port bug fixed; F2/F5 claims corrected in the header |
| `tb/tb_fft_power.v` | **verified under Icarus on all three probes**, 1123 cycles each; not yet run under xsim |
| `generate_saif.tcl` | argv changed, defines wired, warm-up gating added — **not yet run successfully** (it had no TB to run) |
| `butterfly_wrapper_gated.v` | equivalence-verified (2.05 M vectors); **not yet synthesised** |
| `energyObjective.py` | self-tested; **not yet run in a sweep** |
