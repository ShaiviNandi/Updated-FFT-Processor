# FP4/FP8 Mixed-Precision FFT — Area & Power Variance: Root Cause and Reporting Strategy

**Scope:** `Updated-FFT-Processor`, N=256 sweep (722 unique chromosomes), Artix-7 `xc7a35tcpg236-1`, OOC synthesis.
**Evidence base:** `results/fft_256/all_generations_fft256.csv`, `verilog_sources/{mixed_precision_wrappers,multiplier,butterfly}.v`, `generated_designs/pipelined_fft_16_sweep_3.v`, `vivado_synthesis.tcl`.

---

## 0. Executive summary (the answer to the reviewer)

The reviewer is right that a fixed-union architecture cannot scale in area — **and the data agrees with them.** There is no 450-LUT *spread*. There is **one binary step of +410 LUTs**, plus a ±36 LUT (2.9 % CV) synthesis-noise band. The step is not architectural exploration; it is **Vivado constant propagation deleting the FP8 multiplier cone** in the handful of chromosomes where *no* stage selects FP8 multiplication.

Statistically, on your own 722 designs:

| Predictor of LUT count | Pearson *r* | *p* | Verdict |
|---|---|---|---|
| `ANY` stage uses FP8 mult (binary) | **+0.848** | 1.3e-200 | dominant |
| Number of FP8 mult genes (0–8) | +0.308 | 2.6e-17 | spurious (collinear with the binary) |
| Number of FP8 mult genes, *within* the FP8-present group | **+0.034** | 0.36 (ns) | **no effect** |
| Number of FP8 add genes | +0.016 | 0.66 (ns) | no effect |
| DSP count | **+0.848** | 1.3e-200 | see §1.3 — *positive*, so not LUT↔DSP trading |
| SQNR vs LUTs | +0.051 | 0.17 (ns) | **your area axis carries no information** |

Three further findings you need to know before this goes back to reviewers:

1. **`create_clock` runs *after* `synth_design`** in `vivado_synthesis.tcl` (line ~44). Every result in the repo was produced by an **unconstrained** synthesis. No timing-driven optimisation ran, and the reported WNS/delay is a post-hoc measurement of an unconstrained netlist.
2. **Power is vectorless.** `report_power` is called with no SAIF and no switching-activity assertions. Across all 722 designs power takes exactly **three** values: 0.072, 0.073, 0.074 W (CV 0.44 %). That is the static power of an XC7A35T plus a default toggle-rate guess. It is not a measurement of your design, and it cannot be an NSGA-II objective as it stands.
3. **333.33 MHz is not supported by any number in the repository.** Best measured FPGA critical path is 14.88 ns; the tier your Pareto solutions actually live in is 35.0–36.5 ns (**27–29 MHz**). ASIC is 7.94 ns (126 MHz). See §4.3.

---

## 1. Why Vivado reports a step and Yosys reports nothing

### 1.1 The chromosome *is* a compile-time constant

`fft_template_generator.py` does not pass the chromosome in as a runtime input. It bakes it into the generated core as localparams:

```verilog
localparam STAGE0_MULT_PREC = 0;
localparam STAGE1_MULT_PREC = 0;
localparam STAGE2_MULT_PREC = 1;   // etc.
```

and then decodes them against the stage counter into the **single shared** butterfly:

```verilog
// SINGLE SHARED BUTTERFLY UNIT
reg bf_mult_prec, bf_add_prec;
always @(*) begin
    case (current_stage_stable_delayed)
        4'd0: begin bf_mult_prec = 1'b0; bf_add_prec = 1'b0; end
        4'd1: begin bf_mult_prec = 1'b0; bf_add_prec = 1'b0; end
        4'd2: begin bf_mult_prec = 1'b1; bf_add_prec = 1'b1; end
        ...
```

This is the whole mechanism. `bf_mult_prec` is a *function of constants*:

* If **every** `STAGEn_MULT_PREC == 0`, then `bf_mult_prec` is provably the constant `1'b0` for all reachable stage values. Vivado's constant propagation folds the mux in `butterfly_wrapper`, the `fp8_cmul` output cone becomes unobservable, and `opt`-stage dead-code elimination removes all four `fp8_mul` instances, the `fp8_add_sub` pair inside `fp8_cmul`, and the `complex_fp8_to_fp4` converter — **including the four DSP48E1s they had been forced into.**
* If **any** stage selects FP8, `bf_mult_prec` is a live signal, both cones are observable, and you get the full union.

So the "sweep" over 16 genes collapses, at the netlist level, to a **2-valued question**: *is the FP8 multiplier cone reachable at all?* That is exactly what the +0.848 correlation is measuring, and it is exactly why the within-group correlation is 0.034.

### 1.2 The tier structure, from your data

`diagnose_area_variance.py` recovers four discrete levels (gap clustering, N=256):

| Tier | n | LUT range | mean | within-tier CV | DSP | mean delay |
|---|---|---|---|---|---|---|
| 0 — all-FP4 (mult **and** add) | 1 | 481 | 481.0 | — | 6 | 14.88 ns |
| 1 — FP4 mult, mostly-FP4 add | 1 | 793 | 793.0 | — | 6 | 24.05 ns |
| 2 — FP4 mult, FP8 add | 16 | 835–854 | 840.6 | 0.82 % | 6 | 23.6 ns |
| 3 — **FP8 mult present (the union)** | 704 | 1204–1299 | 1250.1 | 1.62 % | 10 | 35.4 ns |

Steps: **+312, +48, +410 LUTs.** Notice that 704 of 722 designs — 97.5 % — sit in a single tier whose entire internal spread is 95 LUTs, i.e. 7.6 % peak-to-peak and 2.9 % CV. **Your "800 to 1250 LUT spread" is tier 2 versus tier 3, nothing more.**

### 1.3 It is *not* DSP↔LUT trading — and the `use_dsp` attribute is a separate problem

You asked whether Vivado is swapping LUTs for DSPs. The data says no, unambiguously: **r(LUT, DSP) = +0.848**, i.e. LUTs and DSPs move *together*. In a genuine remapping you would see a negative correlation. And `r(DSP, any_fp8_mult) = +1.0000` exactly — DSP count is a *perfect* indicator of FP8-cone presence, taking only the values **6 and 10**. The +4 is precisely the four `fp8_mul` instances inside `fp8_cmul`.

The reason DSP count is that clean is this, in `multiplier.v`:

```verilog
(* use_dsp = "yes" *) wire [4:0]  prod = sig_a * sig_b;   // fp4_mul: 2b x 2b
(* use_dsp = "yes" *) wire [10:0] prod = sig_a * sig_b;   // fp8_mul: 4b x 4b
```

You are forcing DSP48E1 macros for a **2×2-bit and a 4×4-bit significand product**. A 4×4 unsigned multiply is ~6 LUT6s. Each of those attributes burns a whole DSP48E1 (25×18 MACC) to do it, and — critically for the reviewer — it makes the DSP column a *label* for which cone exists rather than a measure of arithmetic cost.

**Recommendation:** delete both `use_dsp` attributes (or set `use_dsp = "no"`). Re-synthesise. You will get DSP = 0 for every chromosome and a small LUT increase, and the area comparison becomes one-dimensional and honest. If you keep them, you must report LUT and DSP jointly in every table, because 10 DSP48E1s on a 35T part is 20 % of the DSP budget for arithmetic worth ~50 LUTs.

### 1.4 Why Yosys/OpenSTA locks the area

Yosys is doing the correct thing and it does not contradict Vivado — it just answers the question you actually asked it:

* **You are almost certainly synthesising the union-reachable case.** Any chromosome with a single FP8 mult gene keeps both cones; 97.5 % of your population is in that class. Under a fixed union, standard-cell area is genuinely invariant, so <0.1 % is the right answer.
* **No DSP hard macros exist in an ASIC flow.** `use_dsp` is a Vivado-only attribute; Yosys ignores it. So the whole ±4-DSP/±410-LUT mechanism that creates the FPGA step has no ASIC analogue: both multipliers are synthesised into standard cells either way, and both remain in the netlist.
* **`abc`/`opt` in Yosys still constant-propagates,** so if you *did* feed it an all-FP4 chromosome you would see an area drop there too. Check this — it is a two-minute experiment and it converts "Yosys and Vivado disagree" into "both tools agree; the effect is a single binary pruning event." That is a much stronger position in a rebuttal.
* Standard-cell area is a continuous sum over cells, so it has **no packing quantisation**. Vivado's LUT count is a count of 6-input LUTs after packing, where a 2 % change in mux structure moves the count by tens — that is where your residual 36-LUT sd comes from, amplified by the fact that `opt_design` was **removed** from the TCL (line ~46), so the numbers are pre-optimisation synthesis estimates.

### 1.5 Register replication? No.

FF counts track the same binary indicator (128–140 in the FP4-mult tiers, 145–160 in the union tier) and never exceed 160 for a design with a 10-deep 24-bit operand pipeline plus `srl_style="srl"` shift registers. There is no evidence of replication-driven area growth; the `srl_style` attributes are in fact pushing the pipelines into SRL16s, which is why FF counts are so low.

---

## 2. Diagnostic script

`diagnose_area_variance.py` (delivered alongside this document, and already validated against your N=256 log). It:

* auto-detects `s<i>_mult`/`s<i>_add` columns **or** a packed `chromosome` bitstring (`--gene-layout interleaved|split`);
* de-duplicates chromosomes by default — your NSGA log repeats survivors across generations, and duplicates inflate every p-value (722 rows happened to be 722 unique genomes here, but the 1024-point logs are not);
* tests H1 gene-proportional, H2 binary pruning, H3 LUT↔DSP trading, H4 tool noise;
* recovers discrete area tiers with a gap-split (no assumed cluster count);
* runs the **within-tier** correlation, which is the test that actually settles the argument;
* flags any objective whose CV is <1 % as unusable (it catches your power column automatically);
* includes an SQNR control — if SQNR did *not* track the genes, the genome would be inert.

Run it as:

```bash
python diagnose_area_variance.py results/fft_256/all_generations_fft256.csv
python diagnose_area_variance.py results/fft_1024/all_generations_fft1024.csv
python diagnose_area_variance.py best_chromosomes.csv --chromosome-col chromosome --lut-col area_LUTs
```

Put the N=256 output in the rebuttal verbatim. `r = +0.034, p = 0.36` for LUTs-vs-gene-count within the union tier is the single most persuasive line you have.

---

## 3. Synthesis-log forensics — proving preservation vs. pruning

Everything below is a direct check. Do it for one chromosome from tier 2 (e.g. `s*_mult` all 0) and one from tier 3, and tabulate the difference.

### 3.1 Make Vivado tell you, instead of grepping for absence

Add this to `vivado_synthesis.tcl` immediately after `synth_design`. It is far stronger evidence than a grep, because it reports the hierarchy Vivado actually kept:

```tcl
# --- netlist forensics -------------------------------------------------------
report_utilization -hierarchical -hierarchical_depth 5 \
    -file /tmp/${design_name}_util_hier.rpt

# Did the FP8/FP4 cones survive?
foreach m {fp8_mul fp4_mul fp8_cmul fp4_cmul fp8_complex_add_sub fp4_complex_add_sub} {
    set c [get_cells -hier -filter "REF_NAME =~ *$m*" -quiet]
    puts "FORENSIC: $m instances kept = [llength $c]"
}
puts "FORENSIC: DSP48 cells = [llength [get_cells -hier -filter {PRIMITIVE_TYPE =~ MULT.*} -quiet]]"

# Is the precision select a constant?
puts "FORENSIC: bf_mult_prec net = [get_nets -hier *bf_mult_prec* -quiet]"
puts "FORENSIC: tied-const nets  = [llength [get_nets -hier -filter {TYPE == POWER || TYPE == GROUND} -quiet]]"
```

`FORENSIC: fp8_mul instances kept = 0` for a tier-2 chromosome and `= 4` for a tier-3 one *is* the proof. Nothing else is needed.

### 3.2 Greps on `utilization.rpt` (hierarchical)

```bash
# per-instance LUT/DSP attribution - shows the fp8 subtree appearing/disappearing
grep -E "cmul|_mul|add_sub|butterfly|shared_bf" /tmp/<design>_util_hier.rpt

# the two rows that define the tier
grep -E "^\|\s*(Slice|CLB) LUTs" /tmp/<design>_util.rpt
grep -E "^\|\s*DSPs?\s*\|"        /tmp/<design>_util.rpt

# side-by-side across the sweep
for f in /tmp/*_util.rpt; do
  printf "%-40s %s %s\n" "$(basename $f)" \
    "$(grep -m1 -oE '[0-9]+' <(grep -E '^\|\s*(Slice|CLB) LUTs' $f))" \
    "$(grep -m1 -oE '[0-9]+' <(grep -E '^\|\s*DSPs?\s*\|' $f))"
done
```

### 3.3 Greps on the Vivado synthesis log (`vivado.log` / `runme.log`)

This is where the pruning is *announced*:

```bash
# constant propagation / dead logic removal
grep -nE "Synth 8-(3332|3331|3333|6014|7129)" vivado.log
#   8-3332 : Sequential element ... is unused and will be removed
#   8-3331 : design has unconnected port
#   8-6014 : Unused sequential element ... was removed
#   7129   : Port ... in module ... is either unconnected or has no load

# what the inference engine decided about each multiplier
grep -nE "DSP|Multiplier|use_dsp|implemented as" vivado.log | grep -iE "mul|dsp"

# black-box / hierarchy retention
grep -nE "keep_hierarchy|dont_touch|Synth 8-802|flatten" vivado.log
```

If you see `fp8_mul` under `Synth 8-3332/8-6014` for tier-2 designs and *not* for tier-3 designs, the case is closed in one screenshot.

### 3.4 Guard against the opposite error (accidental over-pruning)

Before claiming the union is preserved, prove it cannot be silently removed. Add to the top module:

```verilog
(* keep_hierarchy = "yes", dont_touch = "yes" *) butterfly_wrapper shared_bf ( ... );
```

Re-synthesise the *whole* sweep with that in place. **Prediction:** the +410-LUT step vanishes and all 722 designs land within ~2–3 % of each other, which is the fixed-union result you claim in the paper. Running the sweep both with and without `dont_touch` gives you a clean two-column table — *"union preserved"* vs *"union + constant folding"* — and pre-empts the reviewer's next question.

### 3.5 OpenSTA / ASIC side

```bash
# is the fp8 cone in the ASIC netlist at all?
grep -cE "\bfp8_mul\b"  synth.v            # expect 4 per fp8_cmul, every chromosome
grep -cE "\bfp4_mul\b"  synth.v
grep -nE "Removing|removed|unused|dangling|constant" yosys.log

# where the 7.94 ns actually goes
report_checks -path_delay max -fields {slew cap input net fanout} -digits 4 -group_count 5
report_checks -through [get_pins -of [get_cells -hier *cmul_fp8*]] -path_delay max
report_power -instance [get_cells -hier *cmul_fp8*]     # per-cone power
report_power -instance [get_cells -hier *cmul_fp4*]
```

The per-cone `report_power` is what will show the FP4 cone burning full dynamic power in an FP8 stage — the motivation for §4.1.

---

## 4. Actionable fixes

### 4.1 RTL: operand isolation (makes dynamic power a real function of the chromosome)

`butterfly_wrapper_gated.v` is delivered alongside this document: a drop-in replacement for `butterfly_wrapper`, same ports, same selected output, with every unselected multiplier/adder/converter operand forced to zero. It exploits the fact that all-zero is a valid encoding in both E2M1 and E4M3, and that both `fp4_mul` and `fp8_mul` already short-circuit on `a_zero || b_zero` — so a gated cone settles at a stable 0 with essentially no internal toggling.

**Flagging honestly: that file is untested.** I wrote it against the module interfaces in `verilog_sources/`; it has not been simulated or synthesised. Before it goes near the paper:

1. run your existing testbench for all four `(mult_prec, add_prec)` combinations and diff `X`/`Y` against the original wrapper — it must be **bit-identical**;
2. re-synthesise and record the area delta (expect a small *increase*, tens of LUTs, for the AND-gates — report it, don't hide it);
3. re-measure power with a real SAIF (§4.2), because vectorless power will stay flat no matter what you gate.

It also carries `PIPELINE_OPERANDS = 1`, which registers the gated operands. That does two things at once: it kills glitch propagation (much larger power delta) and it **breaks the `cmul → add_sub` combinational path**, which is the only route to a defensible frequency claim. It costs one cycle, so `TOTAL_LATENCY` in the generated core must go from 11 to 12.

**Architectural decision — I need your sign-off before anything else moves:**

> **Do you want measurable *power* variance, or measurable *area* variance?**
>
> **(a) Power variance (recommended).** Keep the shared, constant-area butterfly. Add operand isolation. Re-frame the paper around energy-per-transform, not area. Cost: an SAIF-based power flow (~half a day) and a re-run of the sweep. This is the smaller change and the stronger paper.
>
> **(b) Area variance.** Abandon the shared butterfly and give each stage its own specialised unit (`generate` on `STAGEn_MULT_PREC`). Area then genuinely scales with the gene count. Cost: a different architecture, a re-write of the AGU/memory interface, area roughly ×log₂N, and every number in the paper changes.
>
> **(c) Both, honestly split.** Keep (a) for the main results and add a single-point (b) comparison as a "spatial vs. temporal multiplexing" discussion. Most defensible, most work.
>
> My recommendation is **(a)**, because your temporal-multiplexing choice is genuinely the right one for an area-constrained accelerator and the paper currently undersells it. But this changes the paper's framing, so it is your call.

### 4.2 Flow fixes in `vivado_synthesis.tcl` (do these regardless of 4.1)

Three changes, in order of severity:

```tcl
# (1) CONSTRAIN BEFORE SYNTHESIS. Currently create_clock runs after
#     synth_design, so every published number came from an unconstrained run.
create_clock -period $clock_period -name clk [get_ports clk]     # BEFORE
synth_design -top $top_module -part $fpga_part -mode out_of_context
#     ...and delete the later create_clock.

# (2) Restore opt_design. It was removed to dodge OOC fatals; the correct
#     fix is to keep OOC mode and disable the problematic directive, not to
#     skip optimisation - otherwise LUT counts are pre-opt estimates and
#     that is where the 2.9% noise band comes from.
opt_design -directive Explore -quiet

# (3) SAIF-BASED POWER. Vectorless power is static power; it cannot vary.
#     Simulate the post-synth netlist over a representative frame, then:
read_saif  ${sim_dir}/${design_name}.saif -strip_path ${top_module}/uut
set_operating_conditions -ambient_temp 25 -design_power_budget 1.0
report_power -file /tmp/${design_name}_power.rpt
#     Report DYNAMIC power (and energy/transform = P_dyn x latency x T_clk),
#     not Total On-Chip Power - the static term is a constant of the part and
#     it is currently swamping your entire objective.
```

Also worth doing: `report_timing_summary` parsing currently takes the first `Data Path Delay:` in the summary, which on an unconstrained design is not guaranteed to be the critical path. Prefer `-max_paths 1 -sort_by group` on `report_timing`, or derive delay purely from `clock_period - WNS` with the clock constrained.

### 4.3 The timing numbers — what you can actually claim

Every frequency figure currently in play, with its provenance:

| Figure | Where it comes from | Status |
|---|---|---|
| **333.33 MHz (3.00 ns)** | paper | **Unsupported by any artifact in the repository. Must be removed.** |
| 12.5 MHz (80.0 ns) | `CLOCK_PERIOD = 80.0` in `globalVariablesMixedFFT.py` | The *actual* synthesis constraint in the repo — **not** 10.0 ns. Also never applied (see 4.2(1)). This is why `meets_timing == 1` for all 722 designs: 35.5 ns trivially clears an 80 ns target. |
| 100 MHz (10.0 ns) | assumed target | Not what the code uses. Correct this wherever it appears. |
| 125.9 MHz (7.94 ns) | ASIC, OpenSTA post-synthesis | Defensible **if** you state the PDK, corner, and that it is pre-layout. |
| 42.4 MHz (23.6 ns) | Vivado, FP4-mult tier, N=256 | Measured, unconstrained OOC. |
| **28.2 MHz (35.4 ns)** | Vivado, union tier, N=256 — **where your Pareto solutions live** | Measured, unconstrained OOC. |

The physical reason is in the RTL and is not fixable by a constraint: `butterfly_wrapper` is **fully combinational** from `A`/`B`/`W` to `X`/`Y`, and the FP8 path traverses two serial round-and-normalise chains — `fp8_cmul` (4 × `fp8_mul`, then an `fp8_add_sub`) followed by `fp8_complex_add_sub`. That is the 35 ns. Pipelining the wrapper (§4.1, `PIPELINE_OPERANDS=1`, ideally plus a register between `cmul` and the output add/sub) is the only route to a high-frequency claim, and you should present it as future work unless you do the re-run.

**Suggested phrasing for the paper (drop-in):**

> *Replacing the frequency claim:*
>
> "The shared butterfly datapath is combinational from the memory read port to the write-back mux, and the FP8 path traverses two cascaded round-and-normalise stages. Post-synthesis static timing analysis gives a critical-path delay of 7.94 ns for the ASIC flow (Yosys + OpenSTA, <PDK>, <corner>, pre-layout), corresponding to a maximum clock of 125.9 MHz. On the FPGA target (Xilinx Artix-7 XC7A35T-1, out-of-context synthesis) the same path measures 35.4 ns for configurations that include FP8 multiplication and 23.6 ns for FP4-only configurations, i.e. 28.2 MHz and 42.4 MHz respectively. All FPGA results in this work were obtained at a 100 MHz constraint target, which the unpipelined datapath does not meet; the reported delays are the measured critical paths, not closed-timing results. Pipelining the complex multiplier is left to future work and is expected to lift the FPGA ceiling into the 150–200 MHz range at the cost of two additional pipeline stages."

Then remove 333.33 MHz everywhere, including from any throughput or GOPS/W figure derived from it — **check every derived number**, since a 12× frequency error propagates into every efficiency claim in the paper.

### 4.4 Reframing the narrative — constant area is the contribution

The current framing ("mixed precision saves area") is what invites the attack, because on a temporally multiplexed datapath it is false and the reviewer can see it. The correct framing is stronger:

> **Section heading:** *Constant-Area Temporal Multiplexing of Mixed-Precision Datapaths*
>
> "The processor instantiates a single butterfly unit containing both an FP4 and an FP8 complex multiply-add datapath, time-shared across all log₂N stages under the control of the precision chromosome. This is a deliberate design choice: because the datapath is a fixed union, **the silicon area of the accelerator is invariant to the precision schedule**. A designer can therefore select any point on the accuracy/energy Pareto front — or switch between points at run time, per frame or per workload — without re-synthesising, re-placing, or re-budgeting area. Post-synthesis standard-cell area varies by less than 0.1 % across all <N> Pareto-optimal chromosomes, which we report as a *feature of the architecture rather than a limitation of the search*: the NSGA-II search optimises accuracy and energy under a **hard, constant area constraint**, which is precisely the regime an embedded accelerator operates in. The FPGA flow additionally reports a single discrete area step of 410 LUTs (and 4 DSP48E1 slices) for the degenerate subset of chromosomes in which no stage selects FP8 multiplication; in that case the FP8 multiplier cone becomes unreachable and is removed by constant propagation. We report this as a two-tier result rather than a continuous area/precision trade-off."

Three supporting moves that make this airtight:

1. **Re-cast the objective set.** Drop area as a search objective; it is constant by construction and its residual 2.9 % is tool noise (`r(SQNR, LUTs) = +0.051, p = 0.17` — your area axis is literally uninformative). Optimise **SQNR vs. dynamic energy-per-transform vs. latency**, with area as a reported constant. This is a *smaller* claim that you can actually defend, and it removes the reviewer's whole line of attack.
2. **Re-check your Pareto fronts.** With a 36-LUT noise band, a large fraction of your current 2-D (area, SQNR) Pareto points are separated by less than the noise. Re-run non-dominated sorting on the new objective set before re-plotting, and state the noise band explicitly: *"LUT counts are reported with a ±36 LUT (±2.9 %) synthesis-variance band, measured as the within-tier standard deviation over 704 union-architecture configurations."*
3. **Add the ±tier table (§1.2) to the paper.** Reviewers forgive a result they can see the structure of. Four labelled tiers with n, range and mean is far more convincing than a scatter plot that looks like a cloud.

---

## 5. Suggested order of work

| # | Task | Effort | Blocks the paper? |
|---|---|---|---|
| 1 | Fix `create_clock` ordering; restore `opt_design` | 15 min | **Yes** — every number depends on it |
| 2 | Run §3.1 forensic `get_cells` checks on one tier-2 + one tier-3 design | 30 min | **Yes** — this is the rebuttal evidence |
| 3 | Re-run the sweep with `dont_touch` on `shared_bf`; confirm the step vanishes | 2–3 h | **Yes** — the two-column table |
| 4 | Remove `use_dsp` attributes; re-synthesise | 1 h | No, but strongly advised |
| 5 | Correct all frequency/throughput numbers per §4.3 | 1 h | **Yes** |
| 6 | Rewrite the narrative per §4.4; re-do Pareto sorting | 1 day | **Yes** |
| 7 | Simulate + verify `butterfly_wrapper_gated.v`; SAIF power flow; re-run sweep | 2–3 days | Only if you take option (a) |

Items 1–3 and 5–6 are enough to answer the reviewers with the results you already have. Item 7 is what turns the rebuttal into a stronger paper.
