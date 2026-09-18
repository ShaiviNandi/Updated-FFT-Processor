# Figure Revision — Response to the Reviewer Comment on Figs. 5–8

> *"the fonts, legends, and markers in Figs. 5–8 are too small at normal viewing scale — larger fonts and fewer plots per figure are needed to make the reported trends and Pareto fronts readable."*

## 1. What actually caused it

Not the font sizes on their own. The old code built a **2×3 grid on an 18 × 11 inch canvas** and set labels to 10 pt. Reproduced in a 3.5 inch journal column, that label renders at

```
10 pt × (3.5 / 18) ≈ 1.9 pt
```

which no amount of zooming fixes, because the PNG was also only 200 dpi. So the fix has to be three-part, and all three parts are necessary:

1. **author each figure at its final printed size**, so 10 pt means 10 pt;
2. **at most two panels per figure**, so each panel keeps its width;
3. **vector PDF output**, so there is no resolution ceiling.

## 2. Before / after

| | before | after |
|---|---|---|
| Panels per figure | 6 (Fig. 5), 3 (Fig. 7), 4 (Fig. 8) | **2 max, enforced in code** |
| Canvas | 18 × 11 in | 7.16 × 3.15 in (IEEE double column) |
| Axis label | 10 pt nominal → **~1.9 pt printed** | 10 pt nominal → **10 pt printed** |
| Tick labels | 8–10 pt nominal | 9 pt printed |
| Legend | 8 pt, duplicated per panel | 9 pt, one shared legend per figure |
| Scatter marker | `s=60`, colour only | `s=110`, colour **+ marker shape**, white separation ring |
| Line width / marker | 2.0 / 8 | 2.2 / 9 with white edge |
| Palette | green `#4CAF50` / pink `#E91E63` | blue `#0072B2` / vermillion `#D55E00` |
| Output | 200 dpi PNG | **vector PDF** + 600 dpi PNG |
| Effective type size at print | ~1.9 pt | ~10 pt (**≈5×**) |

`figureStyle.panel_grid()` raises a `ValueError` if anyone asks for more than two panels, so a later edit cannot quietly reintroduce the 2×3 grid.

## 3. Palette change

The old green/pink pair was measured at **ΔE 6.1 for deuteranopia** (marginal — legal only with a secondary encoding) and the green sat at **2.71:1** contrast against white, below the 3:1 floor. The replacement passes every check:

```
TIMING ("#0072B2", "#D55E00") — light surface, categorical
  [PASS] lightness band        both inside L 0.43–0.77
  [PASS] chroma floor          both ≥ 0.1
  [PASS] CVD separation        ΔE 21.9 (protan) · 30.9 (tritan)
  [PASS] normal-vision floor   ΔE 31.2
  [PASS] contrast vs surface   both ≥ 3:1
```

Marker shape also differs between the two classes (`o` vs `X`) and the histogram adds hatching, so identity never depends on colour alone — which is what keeps the figures readable in greyscale print.

The four-metric trend palette (`#0072B2`, `#D55E00`, `#009E73`, `#7B3294`) warns at ΔE 6.7 for the blue/purple pair under deuteranopia. That is acceptable here because the four metrics **never share a panel** — each is a single series on its own axes — so colour is decorative rather than identity-bearing. Don't reuse that palette for four overlaid series.

## 4. New figure map

| Suggested | File stem | Panels |
|---|---|---|
| Fig. 5a | `pareto_fft<N>_accuracy` | cost vs SQNR \| SQNR vs critical-path delay |
| Fig. 5b | `pareto_fft<N>_area` | cost vs area \| area vs SQNR *(4-objective runs only)* |
| Fig. 5c | `pareto_fft<N>_timing` | cost vs delay \| area vs delay |
| Fig. 6 | `pareto_fft<N>_3var` | SQNR vs cost, colour = delay (single-hue sequential ramp) |
| Fig. 7a | `pareto_fft<N>_delay_dist` | delay distribution \| normalised latency vs SQNR |
| Fig. 7b | `pareto_fft<N>_delay_bubble` | delay vs cost, marker area ∝ LUTs |
| Fig. 8a | `sweep_cost_accuracy` | min cost vs N \| max SQNR vs N |
| Fig. 8b | `sweep_area_timing` | min area vs N \| min delay vs N |

Two structural changes worth mentioning in the response letter:

**The 3-D scatter is gone.** A rotated 3-D projection loses its axis labels entirely at column width — a reader cannot read a value off it. Fig. 6 now carries the same three variables in two dimensions with a single-hue sequential ramp for delay, which is legible at 3.5 inches and in greyscale.

**Panels whose data is constant are dropped automatically**, with a logged warning. On the existing N=256 data this fires immediately: power is 0.073 W for every design, so every power-versus-x panel drops. That is the correct behaviour — and it is the same finding as the root-cause analysis, arriving from the plotting side.

## 5. Other readability fixes made along the way

- **Selective direct labels.** The old comparison figure annotated *every* point at 8 pt. Now only the first, last and extremum are labelled, with compact formatting (`1,307`, not `1.31e+03`), plus 16 % y-margin so the top label is not clipped.
- **Non-colliding power-of-two ticks.** `512` and `1024` overlapped; labels rotate 45° above eight ticks.
- **Thresholds no longer squash the data.** With an 80 ns target and a 24–36 ns data range, the old code forced 80 into view and compressed all the points into the bottom quarter of the panel — the same readability failure the reviewer flagged, from a different cause. The reference line is now drawn only when it is within 1.6 × the data range; otherwise it becomes a legend note ("all clear 80 ns clock target").
- **Empty legend classes suppressed.** "Violates timing" no longer appears when no design violates timing.
- **Recessive structure.** Top/right spines off, grid at 30 % alpha behind the data, axis text in ink tokens rather than a series hue.
- **`pdf.fonttype = 42`** so the PDFs embed TrueType rather than Type-3 — required by most IEEE submission checkers.

## 6. Integration

Three edits to `runMixedFFTOptimization.py`:

```python
# 1. after the existing matplotlib imports
import figureStyle                      # installs the rcParams profile
from paperFigures import (plot_pareto_figures,
                          plot_size_comparison_figures)

# 2. replace the body of plot_pareto_front(...)
plot_pareto_figures(pareto_objectives, fft_size, results_subdir,
                    feasible=feasible,
                    crit_delay_fn=_crit_delay_ns_from_norm_latency)

# 3. replace the `fig, axes = plt.subplots(2, 2, figsize=(16, 11))` block
plot_size_comparison_figures(sizes, best_power, best_area,
                             best_sqnr, best_delay, RESULTS_DIR)
```

Rename the old functions to `_legacy_*` rather than deleting them until the new figures are in the manuscript.

Pass `crit_delay_fn` — without it the module reconstructs delay from normalised latency using its own inversion, which is correct only while `REFERENCE_CLOCK_PERIOD_NS` matches the value the sweep ran at.

`paperFigures.decode_objectives` handles **both** the legacy 4-objective vector and the 3-objective energy vector, so the figures survive the objective-set change without another edit.

## 7. Status

Rendered and eyeballed against the real `results/fft_256/all_solutions_fft256.csv` and `best_chromosomes.csv`; previews are in `results/_figures_preview/` (including a `target30ns/` variant that forces both timing classes to appear so the two-class rendering, hatching and shared legend could be checked).

**Not yet run inside a live NSGA-II sweep** — the three edits above are untested in place. Regenerate with:

```bash
python3 render_demo_figures.py results results/_figures_preview
```

If the venue reproduces figures smaller than the IEEE template, raise `figureStyle.FONT_SCALE`; every size tracks it.

## 8. Suggested response-letter wording

> "We thank the reviewer for pointing this out. The figures have been redrawn. Each figure now contains at most two panels (previously up to six) and is authored at the final printed column width, so axis labels render at 10 pt and tick labels at 9 pt rather than the ~2 pt effective size of the previous versions, in which a 10 pt label on an 18-inch canvas was reduced to column width. Scatter markers are enlarged and now differ in shape as well as colour, legends are enlarged and consolidated to one per figure, and all figures are supplied as vector PDF. The colour pair was additionally changed to one that is separable under deuteranopia and protanopia and meets a 3:1 contrast floor. The three-dimensional scatter of former Fig. 6 has been replaced by a two-dimensional projection with critical-path delay on a sequential colour ramp, since the projected axes of the former version were not readable at column width."
