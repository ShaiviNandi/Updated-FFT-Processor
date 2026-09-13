# Option (a) — Constant-Area Butterfly + Operand Isolation: Implementation & Verification

Follow-on to `AREA_POWER_VARIANCE_ANALYSIS.md`. Everything below was built and run; the measured numbers are from this session, not estimates.

---

## 1. The gated wrapper is verified equivalent

`verilog_sources/butterfly_wrapper_gated.v` is no longer untested for functional equivalence. `tb_equiv.v` drives identical stimulus into the original `butterfly_wrapper` and the operand-isolated `butterfly_wrapper_gated` and compares `X`, `Y` and `output_is_fp8` bit-for-bit.

```
=== butterfly_wrapper vs butterfly_wrapper_gated equivalence ===
phase 1 done: checks=1048576 errors=0      # exhaustive FP4 operand space
phase 2 mp=0 ap=0 done: checks=1298576 errors=0
phase 2 mp=0 ap=1 done: checks=1548576 errors=0
phase 2 mp=1 ap=0 done: checks=1798576 errors=0
phase 2 mp=1 ap=1 done: checks=2048576 errors=0
phase 3 done: checks=2048616 errors=0      # directed corners
PASS: 2048616 vectors, 0 mismatches. Gated wrapper is bit-identical.
```

**2,048,616 vectors, zero mismatches**, covering all four `(mult_prec, add_prec)` combinations, an exhaustive sweep of the FP4-relevant operand space, and directed corners (zeros, ±max normals, −0, min normals, subnormals, saturation). Icarus Verilog 12.0, 5m24s.

**Still outstanding:** it has not been *synthesised*. Expect a small area increase (tens of LUTs for the AND-gate masks) — measure it and report it rather than hiding it. That is the honest cost of the isolation.

Paper sentence you can now write:

> "Functional equivalence between the baseline and operand-isolated butterfly was verified by exhaustive simulation over the FP4 operand space and 10⁶ pseudo-random vectors across all four precision-mode combinations (2.05 × 10⁶ vectors total, zero mismatches)."

## 2. Operand isolation converts an inert objective into a linear one

This is the result that justifies option (a). `tb_activity.v` + `count_toggles.py` drive a realistic per-stage precision schedule (128 butterflies per stage, 8 stages) through both wrappers and attribute every VCD bit-transition to a datapath cone.

**Activity vs. number of FP8 stages** (`activity_sweep.csv`):

| FP8 stages | toggles, ungated | toggles, isolated | reduction |
|---|---|---|---|
| 0 / 8 | 1,124,095 | 431,336 | 61.6 % |
| 1 / 8 | 1,127,080 | 477,874 | 57.6 % |
| 2 / 8 | 1,129,490 | 522,124 | 53.8 % |
| 3 / 8 | 1,132,858 | 567,228 | 49.9 % |
| 4 / 8 | 1,136,594 | 613,320 | 46.0 % |
| 5 / 8 | 1,141,338 | 660,208 | 42.2 % |
| 6 / 8 | 1,144,622 | 705,084 | 38.4 % |
| 7 / 8 | 1,148,738 | 750,580 | 34.7 % |
| 8 / 8 | 1,152,670 | 795,636 | 31.0 % |

Linear fits:

| | slope | R² | dynamic range | total span |
|---|---|---|---|---|
| **ungated** | +3,634 toggles / FP8 stage | 0.994 | 1.025× | **2.5 %** |
| **isolated** | **+45,570 toggles / FP8 stage** | **0.99998** | **1.845×** | **84.5 %** |

Read that carefully: **the ungated architecture varies by 2.5 % in switching activity across the entire 2⁸ chromosome space.** That is not a measurement artefact in your power flow — it is the architecture. A vectorless `report_power` then quantises those 2.5 % down to the three values you observed (0.072/0.073/0.074 W). The objective was inert *by construction*, and no change of power tool would have fixed it.

With operand isolation the same space spans **1.845×** with a per-gene slope 12.5× larger and R² = 0.99998 — monotonic, linear, and resolvable at single-gene granularity. That is a usable NSGA-II objective.

Per-cone breakdown at 4/8 FP8 stages:

| cone | ungated | isolated | reduction |
|---|---|---|---|
| FP8 complex multiply | 353,322 | 176,842 | 49.9 % |
| FP4 complex multiply | 179,532 | 90,223 | 49.7 % |
| FP8 add/sub pair | 278,163 | 145,633 | 47.6 % |
| FP4 add/sub pair | 142,258 | 71,730 | 49.6 % |
| format converters | 63,794 | 0 | 100 % |
| **wrapper total** | **1,136,594** | **613,320** | **46.0 %** |

The converters hit 100 % because this schedule sets `mult_prec == add_prec` for every stage, so the two mixed-precision conversion paths are never selected. Chromosomes where the mult and add genes differ will show a lower converter reduction — worth a sentence in the paper so a reviewer does not read 100 % as too good.

Paper sentence:

> "Operand isolation reduces switching activity in the shared butterfly by 31–62 % depending on the precision schedule, and — more importantly — converts total datapath activity from a quantity that varies by 2.5 % across the design space into one that varies by 84.5 % with R² = 0.99998 against the number of FP8 stages, making dynamic energy a well-conditioned optimisation objective at constant silicon area."

## 3. Flow fixes — `vivado_synthesis_v2.tcl`

Drop-in replacement, same `argv` contract, superset of the old CSV keys, so `objectiveEvaluationFFT.py` keeps working. Six fixes:

| | fix |
|---|---|
| **F1** | `create_clock` now runs **before** `synth_design`, via a generated XDC read with `read_xdc`, plus OOC input/output delays. v1 constrained *after* synthesis, so every published number came from an unconstrained run. |
| **F2** | `opt_design -directive Explore` restored, with a graceful fallback chain and an explicit `opt_design_ran` flag in the CSV so you always know whether a LUT count is pre- or post-opt. |
| **F3** | Optional SAIF argument (`argv 7`). When supplied, `read_saif` is applied and the CSV records `dynamic_power_w`, `static_power_w` and `saif_used` separately. When absent, it prints a loud warning that power is vectorless and must not be used as an objective. |
| **F4** | Netlist forensics: emits `kept_fp8_mul`, `kept_fp4_mul`, `kept_fp8_cmul`, … counts into both the log and the CSV, plus DSP48 primitive count and whether `bf_mult_prec` survived as a net. This is the direct proof of preservation vs. pruning — far stronger than grepping for absence. |
| **F5** | Timing read from `get_timing_paths` worst slack rather than the first `Data Path Delay:` in the summary, and `fmax_mhz` written to the CSV. |
| **F6** | `argv 8 = 0` adds `-max_dsp 0`, which stops the `(* use_dsp = "yes" *)` attributes in `multiplier.v` from burning DSP48E1 macros on 2×2-bit and 4×4-bit significand products. |

New CSV keys: `dynamic_power_w`, `static_power_w`, `saif_used`, `opt_design_ran`, `fmax_mhz`, `dsp48_primitives`, `use_dsp`, `kept_<module>`.

**Not run** — I have no Vivado here. Run it once on a single design and diff the CSV against the v1 output for the same chromosome before you launch a sweep.

## 4. SAIF generation — `generate_saif.tcl`

RTL-level SAIF via `xvlog`/`xelab`/`xsim`, scoped to the DUT subtree, written with `open_saif`/`log_saif`/`run`/`close_saif`. RTL-level is the right choice for a 700-design sweep: it is fast, and the quantity that varies with the chromosome is operand activity, which RTL captures. For the handful of designs that end up in the paper's final table, re-do it post-synthesis so glitch activity is included, and say in the paper which you used for which table.

It assumes your testbench instantiates the DUT as `uut`. If not, edit the `log_saif` scope line — the script tells you so if the SAIF comes out empty. You will need a power testbench (`tb/tb_fft_power.v`) that loads a representative frame and runs the FFT to completion; the existing functional TB is probably close enough to adapt.

## 5. Objective vector — `energyObjective.py`

Changes the objective vector from 4 to 3:

```
old:  [ total_power, area_LUTs, sqnr_error², norm_latency ]
new:  [ energy_pj_per_transform, sqnr_error², norm_latency ]
```

`energy = P_dynamic × avg_exec_cycles × T_clk`, at each design's own critical path (or set `ISO_FREQUENCY_NS` for an iso-frequency comparison). Area leaves the objective vector and becomes a hard constraint plus a reported constant — which is what an area-constrained embedded accelerator actually is, and removes the two axes that were carrying noise (`r(SQNR, LUTs) = +0.051, p = 0.17`).

Self-test output, using dynamic power scaled from the measured activity sweep:

```
  all-FP4  (0/8): E =     47705.0 pJ/transform
  mixed    (4/8): E =     86246.4 pJ/transform
  all-FP8  (8/8): E =    131983.9 pJ/transform
  fallback path: dyn=0.073 saif_used=0 degraded=True
  penalty paths OK
ALL SELF-TESTS PASSED
```

A **2.77× spread** in the objective, versus 2.5 % before. It also carries a `degraded` flag so a vectorless run can never be silently mixed into a SAIF-based table, and penalty paths so a failed synthesis is dominated rather than rewarded.

Integration is three edits to `objectiveEvaluationFFT.py` plus `OBJECTIVES = 3` in `globalVariablesMixedFFT.py` — the exact edits are in the module docstring. **Grep for `F[:, 1]`, `area_LUTs` and `pareto_3d` before you run**: the plotting and summary code indexes the objective vector positionally and will silently mislabel the axes otherwise.

**Not run inside a live NSGA-II sweep.** Do 5 generations with a small population and confirm the front is non-degenerate before a full run.

## 6. Correction to the earlier analysis

`CLOCK_PERIOD = 80.0` in `globalVariablesMixedFFT.py` — the repo's synthesis constraint is **80 ns (12.5 MHz)**, not the 10 ns / 100 MHz assumed earlier. Two consequences:

* The 333.33 MHz claim is **26×** away from the code's own target, not 3.3×.
* `meets_timing == 1` for all 722 designs is meaningless: a 35.5 ns path trivially clears an 80 ns constraint. Do not cite it as evidence of timing closure.

`REFERENCE_CLOCK_PERIOD_NS = CLOCK_PERIOD` too, so `norm_latency` is normalised against 80 ns — check whether that is what you intended before re-plotting the latency objective.

## 7. Files

In the repo:

| file | status |
|---|---|
| `verilog_sources/butterfly_wrapper_gated.v` | equivalence-verified, **not synthesised** |
| `vivado_synthesis_v2.tcl` | written, **not run** (no Vivado here) |
| `generate_saif.tcl` | written, **not run**; needs a power TB |
| `energyObjective.py` | self-tested, **not run in a sweep** |
| `equiv/` (`tb_equiv.v`, `tb_activity.v`, `count_toggles.py`, `sweep_activity.sh`, `activity_sweep.csv`) | run; results above |
| `diagnose_area_variance.py` | run against the N=256 log |
| `AREA_POWER_VARIANCE_ANALYSIS.md` | the root-cause analysis |

## 8. Next steps, in order

1. Synthesise one chromosome with `vivado_synthesis_v2.tcl`; diff the CSV against v1. Record the gating area cost. **(30 min, unblocks everything)**
2. Write `tb/tb_fft_power.v` and get one SAIF out of `generate_saif.tcl`. **(half a day)**
3. Confirm `dynamic_power_w` now varies across three hand-picked chromosomes (0/8, 4/8, 8/8 FP8 stages). If it does not, the SAIF scope is wrong — check it before scaling up. **(1 h)**
4. Switch the objective vector; 5-generation smoke sweep; fix the plotting indices. **(half a day)**
5. Full re-sweep for N=256 and N=1024. **(overnight)**
6. Rewrite §4.4 narrative and §4.3 timing numbers in the paper; re-do Pareto sorting on the new objectives.
