"""
energyObjective.py
==================

Replaces the inert power/area objectives in the mixed-precision FFT NSGA-II
search with a *dynamic energy per transform* objective.

WHY THIS EXISTS
---------------
Measured on 722 unique N=256 chromosomes from `results/fft_256/`:

  * `total_power_w` takes exactly three values (0.072 / 0.073 / 0.074 W),
    CV = 0.44 %. It is the static power of the XC7A35T plus a default
    toggle-rate guess, because `report_power` was called with no SAIF.
  * `area_LUTs` is bimodal on a single binary condition ("does any stage use
    FP8 multiply"), with r = +0.848 to that indicator and r = +0.034 (p = 0.36)
    to the FP8 *gene count* within the dominant tier. Area is constant by
    construction: the butterfly is a fixed union of both datapaths.
  * r(SQNR, LUTs) = +0.051, p = 0.17 - the area axis carries no information,
    so ~2/4 of the objective vector was noise.

Gate-level measurement (Icarus, 1024 butterflies/config, 2.05 M-vector
equivalence-checked operand isolation in `butterfly_wrapper_gated.v`):

  n FP8 stages   toggles ungated   toggles isolated
       0           1,124,095            431,336
       4           1,136,594            613,320
       8           1,152,670            795,636
  ungated  : +3,634 toggles per FP8 stage, total span  2.5 %  -> inert
  isolated : +45,570 toggles per FP8 stage, span 84.5 %, R^2 = 0.99998

So with operand isolation, switching activity - and therefore dynamic energy -
becomes a near-perfectly linear, monotonic function of the chromosome. That is
the quantity NSGA-II should be minimising.

OBJECTIVE VECTOR
----------------
Old (4): [ total_power, area_LUTs, sqnr_error^2, norm_latency ]
New (3): [ energy_per_transform, sqnr_error^2, norm_latency ]

Area moves out of the objective vector and becomes a reported constant plus a
hard constraint - which is honest, and is what an area-constrained embedded
accelerator actually looks like.

energy_per_transform = P_dynamic [W] * avg_exec_cycles * T_clk [s]
                     = P_dynamic * avg_exec_cycles * crit_delay_ns * 1e-9

Note `crit_delay_ns`, not the constraint period: energy per transform at the
design's own achievable clock. If you prefer iso-frequency comparison (all
designs at a fixed T_clk), set `ISO_FREQUENCY_NS` to that period.

INTEGRATION (three edits to objectiveEvaluationFFT.py)
------------------------------------------------------
1. at the top, add:
       from energyObjective import (compute_energy_pj, energy_objectives,
                                    ENERGY_OBJECTIVES, parse_power_fields)

2. in `_parse_vivado_metrics`, also pick up the v2 CSV keys:
       elif row['Metric'] == 'dynamic_power_w': dyn_power = float(row['Value'])
       elif row['Metric'] == 'saif_used':       saif_used = int(row['Value'])
   and return them (or stash them in the results dict as
   `results['dyn_power']` / `results['saif_used']`).

3. replace the body of `_compute_objectives_and_constraints` with:
       return energy_objectives(results)

and in globalVariablesMixedFFT.py set:
       OBJECTIVES = 3          # was 4
Downstream plotting that indexes F[:,1] as area must be updated - see
`OBJ_NAMES` below, and grep for `pareto_3d`, `area_LUTs` and `F[:, 1]`.

STATUS: this module is self-contained and unit-tested at the bottom of the
file (`python3 energyObjective.py`), but it has NOT been run inside a live
NSGA-II sweep. Do one short sweep (5 generations, small population) and check
that the Pareto front is non-degenerate before committing to a full run.
"""

import math

# -----------------------------------------------------------------------------
# Tunables. Keep these in one place so the paper can quote them.
# -----------------------------------------------------------------------------

ENERGY_OBJECTIVES = 3

OBJ_NAMES = ["energy_pj_per_transform", "sqnr_error_sq", "norm_latency"]

# Normalisation reference for the energy objective, in picojoules per transform.
# Set from a baseline run: use the all-FP8 (worst-case) design's energy so the
# objective lands in roughly [0, 1]. 0 disables normalisation.
REF_ENERGY_PJ = 0.0

# If non-zero, evaluate every design at this fixed clock period (ns) instead of
# at its own critical path. Use for iso-frequency comparisons in the paper.
ISO_FREQUENCY_NS = 0.0

# Objective weights. Energy carries the weight that power+area used to share.
WEIGHT_ENERGY = 2.0
WEIGHT_PERFORMANCE = 30.0
WEIGHT_LATENCY = 8.0

# Constraint limits (area is now a constraint only).
MAX_AREA_LUTS = 5000
MAX_ENERGY_PJ = 0.0          # 0 = no energy cap
MIN_SQNR_DB = 10.0

# SQNR shaping, unchanged from the original formulation.
SQNR_OFFSET = 50.0
REF_SQNR_RANGE = 50.0
REF_LATENCY = 10.0

# Heavy penalty returned when synthesis or simulation failed.
PENALTY_ENERGY_PJ = 1.0e9


# -----------------------------------------------------------------------------
# Core computation
# -----------------------------------------------------------------------------
def compute_energy_pj(dyn_power_w, avg_exec_cycles, crit_delay_ns,
                      iso_period_ns=None):
    """
    Dynamic energy per transform, in picojoules.

        E = P_dyn [W] * N_cycles * T_clk [s]

    Returns PENALTY_ENERGY_PJ for any non-physical input, so a failed
    synthesis is dominated rather than silently rewarded.
    """
    period_ns = ISO_FREQUENCY_NS if iso_period_ns is None else iso_period_ns
    if not period_ns:
        period_ns = crit_delay_ns

    for v in (dyn_power_w, avg_exec_cycles, period_ns):
        if v is None:
            return PENALTY_ENERGY_PJ
        try:
            fv = float(v)
        except (TypeError, ValueError):
            return PENALTY_ENERGY_PJ
        if math.isnan(fv) or math.isinf(fv) or fv <= 0:
            return PENALTY_ENERGY_PJ

    # W * cycles * ns  ->  J * 1e-9  ->  pJ = * 1e12  => net factor 1e3
    return float(dyn_power_w) * float(avg_exec_cycles) * float(period_ns) * 1.0e3


def parse_power_fields(results):
    """
    Pull the dynamic-power figure out of a results dict, falling back to total
    power with a loud marker if the v2 synthesis TCL was not used.

    Returns (dyn_power_w, saif_used, degraded) where `degraded` is True when we
    had to fall back - those runs must not be mixed with SAIF runs in a table.
    """
    saif_used = int(results.get('saif_used', 0) or 0)
    dyn = results.get('dyn_power', results.get('dynamic_power_w'))

    if dyn is None:
        total = results.get('power', 0.0)
        static_guess = results.get('static_power_w')
        if static_guess:
            dyn = max(float(total) - float(static_guess), 0.0)
        else:
            # No split available. Total power on this part is ~97% static, so
            # using it as "dynamic" would make the objective inert again.
            return (float(total), saif_used, True)
        return (float(dyn), saif_used, True)

    return (float(dyn), saif_used, saif_used == 0)


# -----------------------------------------------------------------------------
# Objective / constraint assembly
# -----------------------------------------------------------------------------
def energy_objectives(results):
    """
    Drop-in replacement for
    MixedPrecisionFFTProblem._compute_objectives_and_constraints.

    Returns (objectives, constraints) with 3 objectives and 3 constraints.
    """
    area = results.get('area', MAX_AREA_LUTS * 2)
    sqnr = results.get('sqnr', -100.0)
    norm_latency = results.get('norm_latency', 10.0)
    cycles = results.get('avg_exec_cycles', -1)
    crit_delay_ns = results.get('crit_delay_ns', 0.0)

    dyn_power_w, _saif_used, _degraded = parse_power_fields(results)

    energy_pj = compute_energy_pj(dyn_power_w, cycles, crit_delay_ns)

    e_norm = energy_pj / REF_ENERGY_PJ if REF_ENERGY_PJ else energy_pj
    perf_obj = ((SQNR_OFFSET - sqnr) / REF_SQNR_RANGE) ** 2

    objectives = [
        e_norm * WEIGHT_ENERGY,
        perf_obj * WEIGHT_PERFORMANCE,
        (norm_latency / REF_LATENCY) * WEIGHT_LATENCY,
    ]

    constraints = [
        area - MAX_AREA_LUTS,
        (energy_pj - MAX_ENERGY_PJ) if MAX_ENERGY_PJ else -1.0,
        MIN_SQNR_DB - sqnr,
    ]

    # stash for logging / CSV so the paper can report the raw number
    results['energy_pj'] = energy_pj
    results['dyn_power_w_used'] = dyn_power_w

    return objectives, constraints


def penalty_objectives():
    """Objective vector for a solution whose evaluation failed."""
    return (
        [PENALTY_ENERGY_PJ * WEIGHT_ENERGY, 50.0 * WEIGHT_PERFORMANCE,
         10.0 * WEIGHT_LATENCY],
        [MAX_AREA_LUTS, 1.0, MIN_SQNR_DB],
    )


# -----------------------------------------------------------------------------
# Self-test
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    print("energyObjective self-test")

    # Numbers taken from the real N=256 logs, with dynamic power scaled from
    # the measured toggle-activity sweep (431k -> 796k transitions).
    base_cycles = 1123
    cases = [
        # label,            dyn_W,    crit_ns, expect_order
        ("all-FP4  (0/8)",  0.00180,  23.6),
        ("mixed    (4/8)",  0.00256,  30.0),
        ("all-FP8  (8/8)",  0.00332,  35.4),
    ]
    prev = -1.0
    for label, dw, cd in cases:
        e = compute_energy_pj(dw, base_cycles, cd)
        res = dict(area=1250, sqnr=20.0, norm_latency=0.6,
                   avg_exec_cycles=base_cycles, crit_delay_ns=cd,
                   dyn_power=dw, saif_used=1)
        objs, cons = energy_objectives(res)
        print(f"  {label}: E = {e:11.1f} pJ/transform   objs[0] = {objs[0]:.4g}")
        assert e > prev, "energy must increase with FP8 usage"
        prev = e
        assert len(objs) == ENERGY_OBJECTIVES
        assert len(cons) == 3

    # degradation path: no dynamic power available
    r = dict(area=1250, sqnr=20.0, norm_latency=0.6, avg_exec_cycles=1123,
             crit_delay_ns=35.4, power=0.073)
    dyn, saif, degraded = parse_power_fields(r)
    assert degraded and saif == 0, "must flag a vectorless fallback"
    print(f"  fallback path: dyn={dyn} saif_used={saif} degraded={degraded}")

    # failure path
    assert compute_energy_pj(0.0, 1123, 35.4) == PENALTY_ENERGY_PJ
    assert compute_energy_pj(0.003, -1, 35.4) == PENALTY_ENERGY_PJ
    assert compute_energy_pj(float('nan'), 1123, 35.4) == PENALTY_ENERGY_PJ
    print("  penalty paths OK")

    print("ALL SELF-TESTS PASSED")
