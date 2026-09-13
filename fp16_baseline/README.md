# FP16 Baseline FFT — reviewer-requested comparison point

IEEE 754 **binary16 (E5M10, bias 15)** reference implementation of the same FFT
architecture as the NSGA-optimised mixed-precision FP4/FP8 cores, so the paper
can report area / power / timing / accuracy against a conventional FP16 design.

**Nothing in the existing tree was modified.** This directory is entirely
additive, and every module name is prefixed `fp16_` so both designs can sit in
one project without collisions.

---

## What is here

```
fp16_baseline/
├── verilog_sources/
│   ├── fp16_adder.v              fp16_add_sub, fp16_complex_add_sub
│   ├── fp16_multiplier.v         fp16_mul, fp16_cmul
│   ├── fp16_butterfly.v          fp16_butterfly_generation_unit, fp16_butterfly_wrapper
│   ├── fp16_memory.v             fp16_dual_bank_memory_concurrent (32-bit word)
│   ├── fp16_twiddle_rom.v        twiddle_factor_fp16
│   └── twiddles_fp16_1024.txt    512 × 32-bit ROM contents
├── generated_cores/
│   └── fp16_fft_<N>/             N = 2,4,8,16,32,64,128,256,512,1024
│       ├── fp16_fft_<N>_core.v
│       └── fp16_fft_<N>_top.v
├── tb/                           self-check harnesses (see Verification)
├── fp16_template_generator.py    regenerates all cores/tops
├── generate_fp16_twiddles.py     regenerates the ROM contents
├── vivado_synthesis_fp16.tcl     batch-mode synthesis (clone of ../vivado_synthesis.tcl)
├── run_fp16_synthesis.py         batch driver + FP16-vs-mixed comparison table
└── README.md
```

**Reused unchanged from `verilog_sources/`:** `agu.v` (`dit_fft_agu_streaming`)
and `bit_reversal.v` (`bit_reverse`). Both are format-agnostic, so they are
instantiated directly rather than duplicated — duplicating them would create
conflicting module definitions when both designs are compiled together.

---

## Architectural parity (what makes the comparison fair)

Everything structural is matched to `mixed_fft_<N>_core`:

| | Mixed FP4/FP8 | FP16 baseline |
|---|---|---|
| Address generation | `dit_fft_agu_streaming` | same instance |
| Input reordering | `bit_reverse` | same instance |
| Memory | dual-bank ping-pong, 2 sub-banks, TDP, 1-cycle read | identical organisation |
| Datapath alignment | `TOTAL_LATENCY = 11` | `TOTAL_LATENCY = 11` |
| Twiddle pipeline | 11 registers | 11 registers |
| Inter-stage flush | 12-cycle stall | 12-cycle stall |
| Control FSM | IDLE / RUN / FLUSH / DONE, async active-low reset | identical |
| Butterfly | one shared unit, II = 1 | one shared unit, II = 1 |

**Measured: a 256-point transform takes 1123 cycles in *both* designs.** Latency
is therefore identical by construction and any reported difference is purely
area, power and *F*max.

### Deliberate differences

These are the things the baseline is supposed to differ in — flag them in the
paper rather than hiding them:

1. **32-bit memory word** (`[31:16]` real, `[15:0]` imag) instead of the 24-bit
   unified FP8+FP4 word. A true FP16 design must store 32 bits per complex
   sample; truncating to 24 would not be FP16 and reviewers would say so.
2. **No precision plumbing.** The mixed core carries per-stage
   `STAGE<i>_MULT_PREC` / `ADD_PREC` / `OUT_PREC` localparams, a
   `current_stage_stable` tracker, a `stable_stage_pipe`, and eight
   `fp4↔fp8` converters around the butterfly. None of that exists in a
   single-precision design, so it is all removed. Leaving dead muxing in would
   inflate the baseline's area and flatter your result.
3. **Twiddle ROM is 32-bit, no `PRECISION` port**, and its file path is a
   parameter (`TWIDDLE_FILE`) rather than the hardcoded absolute path in
   `twiddle_rom.v`.

### Numeric conventions — matched on purpose

Matched to `adder.v` / `multiplier.v` so the accuracy comparison isolates
*precision*, not *exception handling*:

- Round-to-Nearest-Even on both add and multiply.
- **Overflow saturates** to the largest finite normal (±65504.0). No Inf/NaN
  encodings are produced, exactly as the FP4/FP8 units saturate.
- Subnormal **inputs** are handled correctly in both units; subnormal
  **results** are produced by the adder and flushed to zero by the multiplier
  (mirroring `fp8_mul`'s underflow behaviour).

If a reviewer specifically wants IEEE Inf/NaN propagation, say so and it is a
small change to the result-assembly block in each unit.

---

## Verification performed

All run with Icarus Verilog 12.0 against numpy `float16` / `float64` references.

**1. Arithmetic, bit-exactness — 40,000 random operand pairs**

Vector mix: full-range random bit patterns, small normals, near-cancellation
pairs, subnormals, saturating magnitudes, and wide exponent spreads.

```
add: 40000/40000 exact  (0 mismatches)
sub: 40000/40000 exact  (0 mismatches)
mul: 40000/40000 exact  (0 mismatches)
```

Bit-exact against numpy `float16` with the saturate/flush conventions applied.

**2. End-to-end transform vs `numpy.fft.fft` (float64)**

| N | cycles | SNR | max abs error | dominant bins |
|---|---|---|---|---|
| 16 | 83 | 65.60 dB | 0.0011 | match |
| 64 | 267 | 63.68 dB | 0.0040 | match |
| 256 | 1123 | 59.32 dB | 0.0186 | match |
| 1024 | 5243 | 60.21 dB | 0.0714 | match |

**3. Elaboration** — all ten `fp16_fft_<N>_top` modules elaborate clean.

**4. Synthesis smoke test (Yosys 0.33)** — `fp16_fft_256_top` maps fully to
gates with **no inferred latches** anywhere in the FP16 sources. This caught a
real latch in the first draft of the twiddle ROM, now fixed. The only remaining
warning is the tri-state check in `fp16_memory.v` (`=== 1'bz`), inherited
verbatim from your `memory.v`, which Vivado handles.

**5. Synthesis flow plumbing** — `vivado_synthesis_fp16.tcl` and
`run_fp16_synthesis.py` were exercised end-to-end against a stubbed Vivado
(mock `report_utilization` / `report_power` / `report_timing_summary` output):
argument handling, source selection, twiddle staging, CSV schema, the
`--with-mixed` second invocation, `--report-only`, and the missing-binary error
path all behave correctly.

### Not yet done — flagging honestly

- **No Vivado run has been performed on this RTL** — Vivado is not available
  in the environment this was built in, so the flow below is plumbing-tested
  against a stub, not against real Vivado. Yosys confirms the RTL is
  synthesisable and latch-free, but the actual timing, area and power numbers
  are still yours to produce. Run `--sizes 256` first as a smoke test before
  committing to the full sweep. The
  `(* use_dsp = "yes" *)` attribute on the 11×11 multiplier is inherited from
  the existing style; on a real FP16 core you may want to sweep
  `use_dsp = yes/no`, since one FP16 multiply maps very differently to a DSP48
  than an FP4 or FP8 one does.
- **Not integrated with the RISC-V / PicoRV32 flow.** The `risc-v-integration/`
  path assumes 16-bit load/unload words; the FP16 top uses 32-bit words, so the
  CUSTOM-0 encoding and the firmware's load/unload loop need widening. Tell me
  when you want that and I'll do it as a separate additive change.
- **Sizes 2, 4, 8, 32, 128, 512 were elaborated but not simulated** end-to-end.
  They come from the same generator as the four sizes that were simulated, so
  the risk is low, but they are untested.
- `objectiveEvaluationFFT.py` / `performance_evaluator.py` have not been touched
  and do not yet know about these cores.

---

## How to run

**Simulation** (from a directory containing `twiddles_fp16_1024.txt`):

```
iverilog -g2005 -o fft.vvp -s fp16_fft_256_top \
    fp16_baseline/verilog_sources/*.v \
    verilog_sources/agu.v verilog_sources/bit_reversal.v \
    fp16_baseline/generated_cores/fp16_fft_256/*.v \
    <your_testbench>.v
vvp fft.vvp
```

**Reproduce the arithmetic check:**

```
cd fp16_baseline
python3 tb/check_fp16_arith.py gen fp16_vectors.txt
iverilog -g2005 -o tb.vvp -s tb_fp16_arith \
    verilog_sources/fp16_adder.v verilog_sources/fp16_multiplier.v tb/tb_fp16_arith.v
vvp tb.vvp
python3 tb/check_fp16_arith.py check fp16_vectors.txt fp16_results.txt
```

**Reproduce the transform check (256-point):**

```
cd fp16_baseline
cp verilog_sources/twiddles_fp16_1024.txt .
python3 tb/check_fp16_fft.py gen fft_in.txt
iverilog -g2005 -o fft.vvp -s tb_fp16_fft_256 \
    verilog_sources/*.v ../verilog_sources/agu.v ../verilog_sources/bit_reversal.v \
    generated_cores/fp16_fft_256/*.v tb/tb_fp16_fft_256.v
vvp fft.vvp
python3 tb/check_fp16_fft.py check fft_out.txt
```

**Regenerate the RTL:**

```
python3 fp16_template_generator.py                  # all 10 sizes
python3 fp16_template_generator.py --sizes 256 1024
python3 generate_fp16_twiddles.py --out verilog_sources/twiddles_fp16_1024.txt
```

---

## Vivado batch synthesis

`vivado_synthesis_fp16.tcl` is a behavioural clone of `../vivado_synthesis.tcl`
— same in-memory project, same `synth_design -mode out_of_context`, same
report regexes, same CSV schema — so the FP16 metrics slot straight into your
existing tables. The original TCL is **not modified**.

It takes two extra `-tclargs` beyond the original seven:

| # | arg | why |
|---|---|---|
| 8 | `shared_dir` | the FP16 sources live in their own tree, so `agu.v` and `bit_reversal.v` are pulled from `../verilog_sources` by name. Only those two — nothing else from the mixed tree leaks in. |
| 9 | `twiddle_file` | copied into the CWD so the ROM's relative `$readmemb` resolves the same way no matter where Vivado was launched. |

### Run it

From the repository root — the same directory you run
`runMixedFFTOptimization.py` from:

```
python3 fp16_baseline/run_fp16_synthesis.py --with-mixed
```

`--with-mixed` also re-synthesises each matching NSGA-optimised core **in the
same session, through the unmodified `vivado_synthesis.tcl`**. One Vivado
version, one part, one clock, one run — that is the controlled comparison to
put in the paper, rather than pairing fresh FP16 numbers against mixed numbers
from an older sweep.

Other useful invocations:

```
# one size, quick smoke test before committing to the full sweep
python3 fp16_baseline/run_fp16_synthesis.py --sizes 256

# FP16 only, all ten sizes
python3 fp16_baseline/run_fp16_synthesis.py

# override the defaults scraped from globalVariablesMixedFFT.py
python3 fp16_baseline/run_fp16_synthesis.py --clock 80.0 --part xc7a35tcpg236-1 \
    --vivado /home/digital-1/2025.2/Vivado/bin/vivado

# re-tabulate from existing CSVs without re-running Vivado
python3 fp16_baseline/run_fp16_synthesis.py --report-only --with-mixed
```

Defaults are read out of `globalVariablesMixedFFT.py` by text-scrape (not
import, which would create directories and append to `optimization.log` as a
side effect): `CLOCK_PERIOD = 80.0`, `FPGA_DEVICE = xc7a35tcpg236-1`,
`VIVADO_PATH = /home/digital-1/2025.2/Vivado/bin/vivado`.

### Output

Per-design CSVs and logs land in `reports/fp16_baseline/`:

```
reports/fp16_baseline/
├── fp16_fft_<N>_metrics.csv        same schema as your existing *_metrics.csv
├── fp16_fft_<N>_vivado.log
├── fp16_fft_<N>_run.log            stdout/stderr capture
├── mixed_fft_<N>_metrics.csv       only with --with-mixed
└── fp16_vs_mixed_comparison.csv    joined table with FP16/mixed ratios
```

and the table is printed to the terminal as FP16 rows, mixed rows, and a ratio
block where `>1` means FP16 costs more.

### To run it by hand for one design

```
vivado -mode batch -source fp16_baseline/vivado_synthesis_fp16.tcl -tclargs \
    fp16_fft_256 \
    $(pwd)/reports/fp16_baseline/fp16_fft_256_metrics.csv \
    80.0 \
    $(pwd)/fp16_baseline/generated_cores/fp16_fft_256/fp16_fft_256_core.v \
    $(pwd)/fp16_baseline/generated_cores/fp16_fft_256/fp16_fft_256_top.v \
    $(pwd)/fp16_baseline/verilog_sources \
    xc7a35tcpg236-1 \
    $(pwd)/verilog_sources \
    $(pwd)/fp16_baseline/verilog_sources/twiddles_fp16_1024.txt
```

### One thing to watch

At `CLOCK_PERIOD = 80.0 ns` the mixed-precision cores have a lot of slack. An
FP16 butterfly is a much deeper combinational path (11×11 multiply plus a
5-bit-exponent aligner and normaliser, all unpipelined inside one butterfly),
so if FP16 misses timing at 80 ns that is a **real** result worth reporting,
not a setup error. Report WNS for both rather than only "met/not met". If you
want an Fmax comparison rather than a fixed-clock one, sweep `--clock` down
until each design's WNS crosses zero.

---

## Related note on the existing tree (not changed)

While synthesis-checking my ROM I found the same construct in your
`verilog_sources/twiddle_rom.v`: `raw_data` is a `reg` assigned only inside the
`else` branch of `if (is_midpoint)`, which makes synthesis **infer a latch** on
that 24-bit signal. Yosys flags it; Vivado will too, as a `WARNING` you may
have been scrolling past. It is functionally harmless (`twiddle_out` is fully
assigned on every path) but it costs area and sits in the twiddle path.

I fixed it in `fp16_twiddle_rom.v` by making `raw_data` a continuous
assignment. **I did not touch your file** — the one-line equivalent change
there would be `reg [23:0] raw_data;` → `wire [23:0] raw_data = rom[rom_addr];`
with the assignment removed from the always block. Your call whether to apply
it; note that doing so would change the mixed-precision area numbers slightly,
so if you do it, re-run the mixed cores too.
