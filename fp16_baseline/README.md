# FP16 Baseline FFT — reviewer-requested comparison point

IEEE 754 **binary16 (E5M10, bias 15)** reference implementation of the same FFT
architecture as the NSGA-optimised mixed-precision FP4/FP8 cores, so the paper
can report area / power / energy / accuracy against a conventional half-precision
design. It is the FP16 sibling of `fp32_baseline/`, built to the same conventions
and measured by the same flow, so FP4/FP8 → FP16 → FP32 forms one uniform table.

**Nothing in the existing tree was modified.** This directory is entirely
additive, and every module name is prefixed `fp16_` (or suffixed `_fp16`) so the
FP16, FP32 and mixed designs can all sit in one project without collisions.

> **Rebuilt 2026-09-29.** An earlier version of this directory targeted Vivado
> FPGA synthesis with a `verilog_sources/` layout, a fully combinational
> butterfly at `TOTAL_LATENCY = 11`, behavioural register-array memory and a
> `$readmemb` ROM. It has been replaced. That version also had a real bug — see
> *RESULT_BANK* below. Superseded files are listed at the end.

---

## What is here

```
fp16_baseline/
├── source/
│   ├── fp16_adder.v              fp16_add_sub, fp16_complex_add_sub
│   ├── fp16_multiplier.v         fp16_mul, fp16_cmul
│   ├── fp16_butterfly.v          fp16_butterfly_generation_unit (2-cycle internally pipelined), fp16_butterfly_wrapper
│   ├── fp16_memory.v             fp16_dual_bank_memory_concurrent (32-bit word, 4x sram_512x32_2rw macros)
│   ├── fp16_twiddle_rom.v        twiddle_factor_fp16 (synthesizable case-statement ROM; auto-generated)
│   ├── sram_512x32_2rw.v         width-matched SRAM macro model  (default)
│   ├── sram_variants/
│   │   └── sram_512x32_2rw_from64.v   same module name, wraps the existing 512x64 macro (--sram-width 64)
│   └── twiddles_fp16_1024.txt    512 x 32-bit ROM contents (auto-generated)
├── generated_cores/               (generated)
│   └── fp16_fft_<N>/              N = 2,4,8,16,32,64,128,256,512,1024
│       ├── fp16_fft_<N>_core.v
│       └── fp16_fft_<N>_top.v
├── sim/
│   ├── fp16_performance_evaluator.py   Icarus simulation: SQNR + exec-cycle count, per size
│   └── perf/                            (generated) fp16_sqnr_results.txt (kept) + fp16_perf_artifacts.zip
├── synth/
│   ├── run_fp16_synthesis.py           Yosys + OpenSTA PPA (Power, Area, CritDelay, Slack, NormLat, Energy/FFT)
│   └── (generated) fp16_ppa_report.txt (kept) + fp16_synth_artifacts.zip
├── fp16_template_generator.py     regenerates all cores/tops
├── generate_fp16_twiddles.py      regenerates the ROM contents (.txt table + synthesizable .v ROM)
├── run_fp16_design.py             runs all four steps in order for a shared --sizes list
└── README.md
```

**Path conventions.** Every script resolves its own default paths relative to
its own file location, so all of them behave the same regardless of the working
directory they're invoked from.

**Reused unchanged from `../verilog_sources/`:** `agu.v`
(`dit_fft_agu_streaming`) and `bit_reversal.v` (`bit_reverse`) — both are
format-agnostic, so they are instantiated directly rather than duplicated.
**Referenced from `../fp32_baseline/source/`:** `sram_512x64_2rw.v`, only under
`--sram-width 64`, so the repository has exactly one definition of that macro.

---

## One-command run

`--sram-width` is required — it changes what the area number means (see
*Memory* below).

```
python3 run_fp16_design.py --sram-width 64                  # all 10 sizes, all 4 steps
python3 run_fp16_design.py --sram-width 64 --sizes 16 1024   # a subset
python3 run_fp16_design.py --sram-width 64 --clock-period 8.0
```

Runs, in order: `fp16_template_generator.py` → `generate_fp16_twiddles.py` →
`sim/fp16_performance_evaluator.py` → `synth/run_fp16_synthesis.py`, stopping at
the first failing step. Each step also has its own CLI.

Requires on `$PATH`: `iverilog`/`vvp`, `yosys`, `sta` (OpenSTA). Needs `numpy`.

---

## Architecture

Structurally matched to `mixed_fft_<N>_core` at the repository root and to the
FP32 baseline:

| | Mixed FP4/FP8 | FP16 baseline | FP32 baseline |
|---|---|---|---|
| Address generation | `dit_fft_agu_streaming` | same instance | same instance |
| Input reordering | `bit_reverse` | same instance | same instance |
| Memory | dual-bank ping-pong, 2 sub-banks, TDP, 1-cycle read | identical, from SRAM macros | identical, from SRAM macros |
| Memory word | 24-bit unified | **32-bit** | 64-bit |
| `TOTAL_LATENCY` | 11 | **13** | **13** |
| Inter-stage flush | 12-cycle stall | **14-cycle** | **14-cycle** |
| Control FSM | IDLE / RUN / FLUSH / DONE, async active-low reset | identical | identical |
| Butterfly | one shared unit, II = 1, combinational | one shared unit, II = 1, **internally 2-cycle pipelined** | one shared unit, II = 1, **internally 2-cycle pipelined** |

### `TOTAL_LATENCY = 13` and the 2-cycle butterfly

The FP32 baseline splits its butterfly into three single-primitive pipeline
stages because a 24×24 multiply chained into two dependent FP32 adds measures
~13.4 ns at 45 nm — over a 10 ns budget:

```
cycle T   : 4 real multiplies (B x W)                     -> register
cycle T+1 : complex-multiply combine (ac-bd, ad+bc = W*B)  -> register
cycle T+2 : final complex add/sub, X = A + WB, Y = A - WB  (combinational)
```

**This baseline keeps the same 2-cycle depth even though FP16's narrower
arithmetic would very likely meet 10 ns with fewer — or zero — stages.** That is
a deliberate choice for architectural parity across the precision sweep, not a
re-derived minimum: holding the control path fixed makes area and energy the only
variables. Re-deriving a per-precision optimum is a legitimate alternative
experiment, but it would confound the comparison.

The operand (A/B) and twiddle pipelines feed the butterfly's *inputs* and stay at
depth 11; only the write-back pipeline, which waits on the *output*, grows by 2.

**Consequence, stated plainly:** this baseline is **not** cycle-matched to the
mixed-precision cores. Cycle counts are `2 * num_stages` higher — 1139 vs 1123 at
N=256 — exactly as for FP32. That is why **Energy/FFT**, which multiplies each
design's own power by its own ExecCycles, is the comparable metric rather than
raw cycles.

### `RESULT_BANK` — a bug the rebuild fixes

Load writes bank 0 and every stage writes the opposite bank from the one it
reads, so after `log2(N)` stages the result sits in bank 1 for an even stage
count and **bank 0 for an odd one**. The superseded version hardcoded the unload
bank to 1, so every odd-`log2(N)` size read the wrong bank and returned garbage.

Measured on the old cores: **N = 8 gave 1.13 dB and N = 32 gave 1.30 dB**, where
the even sizes passed at ~65 dB. N = 2, 8, 32, 128, 512 were all affected. Any
FP16 number previously reported for those sizes is invalid. The generator now
derives `RESULT_BANK` from `log2(N) % 2`, matching the FP32 baseline, and all ten
sizes pass — see *Measured results*.

### Memory — the one choice that decides what the area number means

FP16's word is 32 bits, but the only SRAM the compiler has produced is
`sram_512x64_2rw` (in `fp32_baseline/fp32_SRAM_MACROS/`), sized for FP32. The RTL
always instantiates the width-matched `sram_512x32_2rw`; exactly one model of that
module is compiled, and the flow picks which:

| | `--sram-width 64` | `--sram-width 32` |
|---|---|---|
| Model | wrapper over the existing 512×64 macro, upper 32 bits tied off | native width-matched macro |
| Liberty | the one you already have | **must be generated** |
| Runs today | yes | no |
| FP16 memory area | **over-counted ~2×** | correct |

This matters more than it sounds: in the FP32 baseline the four macros are
**87–95 % of total area**, so memory word width is most of what a precision
comparison measures. A half-used 64-bit macro hides the entire effect and makes
FP16 look far closer to FP32 than it is — a pessimistic bound on FP16, not a
measurement of it.

The fix is one SRAM-compiler run: adapt
`fp32_baseline/fp32_SRAM_MACROS/sram_512x64_2rw.py` with `word_size = 32`,
re-run OpenRAM, then use `--sram-width 32 --ram-lib <new Liberty>`. Until then,
**state which setting produced any table you publish.** There is no default;
the argument is required and the chosen value is printed in the report header.

### Synthesizable twiddle ROM

`fp16_twiddle_rom.v` is a combinational case-statement ROM auto-generated by
`generate_fp16_twiddles.py` from the same computed entries as
`twiddles_fp16_1024.txt`, not a `$readmemb` memory — a `$readmemb` ROM depends on
a relative path resolving against whatever working directory the tool runs from,
which is fine for Icarus but fragile across Yosys/OpenSTA invocations. It also
avoids an inferred latch that an always-block-assigned `raw_data` produces (the
same construct is still present in `verilog_sources/twiddle_rom.v` — see *Related
note* at the end).

### Numeric conventions

Matched to `adder.v` / `multiplier.v` and to the FP32 baseline so the accuracy
comparison isolates *precision*, not *exception handling*:

- Round-to-Nearest-Even on both add and multiply.
- **Overflow saturates** to the largest finite normal (±65504.0, `0x7BFF`). No
  Inf/NaN encodings are produced.
- Subnormal **inputs** handled correctly in both units; subnormal **results** are
  produced by the adder and flushed to zero by the multiplier.

---

## Measured results

### Accuracy and cycles — `sim/perf/fp16_sqnr_results.txt`

Same 11 test signals, same golden (FFT of the input quantised to the design's
format), same SQNR definition, same exact-match-counts-as-100 dB averaging as the
mixed and FP32 evaluators.

| N | ExecCycles | Avg SQNR (dB) | Avg non-exact (dB) | Exact |
|---|---|---|---|---|
| 2 | 18 | 112.71 | 146.59 | 8/11 |
| 4 | 35 | 120.17 | 127.74 | 3/11 |
| 8 | 57 | 86.87 | 81.94 | 3/11 |
| 16 | 91 | 79.87 | 77.86 | 1/11 |
| 32 | 153 | 76.69 | 74.36 | 1/11 |
| 64 | 279 | 73.02 | 70.32 | 1/11 |
| 128 | 549 | 72.28 | 69.51 | 1/11 |
| 256 | 1139 | 71.39 | 68.53 | 1/11 |
| 512 | 2433 | 70.59 | 67.65 | 1/11 |
| 1024 | 5263 | 69.78 | 66.76 | 1/11 |

**ExecCycles are identical to the FP32 baseline's at every size** (18 / 35 / 57 /
91 / 153 / 279 / 549 / 1139 / 2433 / 5263), confirming the control paths match
exactly. SQNR sits between the mixed-precision cores' 22–39 dB and FP32's
143–155 dB, as a 10-bit mantissa should.

### PPA — `synth/fp16_ppa_report.txt`

**Not yet produced.** See *Not done* below.

---

## Verification performed

**Arithmetic, bit-exactness — 40,000 random operand pairs.** FP16 adder and
multiplier are bit-exact against numpy `float16` (with the saturate/flush
conventions applied): 0 mismatches on add, sub and mul. Vector mix covered
full-range patterns, small normals, near-cancellation, subnormals, saturating
magnitudes and wide exponent spreads. These two files carry over unchanged from
the superseded version, where they were verified.

**Full transform, all ten sizes** — the SQNR table above. All pass, including
the five odd-`log2(N)` sizes that the superseded version got wrong.

**Both SRAM variants give identical simulation results** — N=8 and N=256 return
86.87 dB / 71.39 dB and 57 / 1139 cycles under `--sram-width 32` and
`--sram-width 64` alike, as they must.

**Yosys elaboration + mapping**, sizes 2 / 8 / 256 / 1024: completes, maps to
gates, **zero inferred latches**, and instantiates exactly **4 SRAM macros** per
design, matching the FP32 organisation. Mapped cells 5,287 (N=2) → 13,017
(N=1024) with a constant 707 flip-flops, consistent with the
constant-area-by-construction framing.

### Not done — flagging honestly

- **No Yosys-with-45nm-Liberty or OpenSTA run.** `45_nm_PDK/` is not on the
  machine this was built on, and `sta` is not installed there, so
  `synth/run_fp16_synthesis.py` is **unverified end to end**. It is a faithful
  port of `run_fp32_synthesis.py` (same passes, same regexes, same report
  schema), but the first real run may need fixing. Run `--sizes 256` first.
- **The Yosys checks above used two local substitutions** because this
  environment has Yosys 0.33: `flatten` instead of `flatten -noscopeinfo`, and a
  blackbox stub instead of the SRAM behavioural model. Both `flatten
  -noscopeinfo` and reading the OpenRAM model abort on 0.33 — and **your
  original `fp32_baseline/source/sram_512x64_2rw.v` aborts identically**, so this
  is a Yosys-version difference, not a defect in either model. Your Yosys
  demonstrably handles both, since `fp32_ppa_report.txt` exists.
- **`--sram-width 32` cannot be run at all yet** — no 512×32 Liberty exists. The
  script refuses rather than silently substituting the 64-bit one.
- **Not integrated with the RISC-V / PicoRV32 flow.** `risc-v-integration/`
  assumes 16-bit load/unload words; the FP16 top uses 32-bit, so the CUSTOM-0
  encoding and firmware loop need widening.
- **`objectiveEvaluationFFT.py` has not been touched** and does not know about
  these cores.

---

## Superseded by the rebuild

These files are from the Vivado-track version and are no longer part of the
design. Nothing in the current flow reads them:

```
fp16_baseline/verilog_sources/         -> replaced by source/
fp16_baseline/vivado_synthesis_fp16.tcl
fp16_baseline/run_fp16_synthesis.py    -> replaced by synth/run_fp16_synthesis.py
fp16_baseline/tb/                      -> replaced by sim/
```

`source/fp16_adder.v` and `source/fp16_multiplier.v` are the same verified files
carried over from `verilog_sources/`. Delete the list above when convenient;
they were left in place rather than removed automatically.

If you still want the FPGA-track numbers, the Vivado TCL is a working clone of
`vivado_synthesis.tcl` — but note that it clones the **v1** flow
(`create_clock` after `synth_design`, no `opt_design`, vectorless power), which
`area-power-variance-rootcause.md` retired, and that its FP16 rows for N = 2, 8,
32, 128, 512 came from the `RESULT_BANK`-broken cores.

---

## Related note on the existing tree (not changed)

`verilog_sources/twiddle_rom.v` assigns `raw_data` (a `reg`) only inside the
`else` branch of `if (is_midpoint)`, which makes synthesis **infer a latch** on
that 24-bit signal. Functionally harmless — `twiddle_out` is fully assigned on
every path — but it costs area and sits in the twiddle path. The FP16 and FP32
baselines both avoid it by construction (case-statement ROM). The one-line
equivalent fix there is `reg [23:0] raw_data;` → `wire [23:0] raw_data =
rom[rom_addr];` with the assignment removed from the always block. **Your file
was not modified**; applying it would shift the mixed-precision area numbers
slightly, so re-run those cores if you do.
