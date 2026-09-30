# =============================================================================
# vivado_implement.tcl
#
# Post-route PPA, measured on the SAME netlist that vivado_synthesis_v2.tcl
# reports post-synthesis.
#
# WHY THIS EXISTS
#   The whole project stops at synth_design + opt_design. Nothing is ever placed
#   or routed, so every area, timing and power number is a post-synthesis
#   ESTIMATE. That matters more on an FPGA than the word "estimate" suggests: a
#   net crosses fixed programmable interconnect (switch boxes, PIPs), each hop
#   adding delay, so routing is not a small correction on top of logic delay --
#   it is frequently comparable to it. Without placement Vivado has no sites to
#   measure between and estimates net delay statistically. Only post-route
#   timing is authoritative, and post-route report_power is the only one working
#   from real net capacitance rather than an estimate.
#
# HOW IT AVOIDS FORKING THE FLOW
#   It SOURCES vivado_synthesis_v2.tcl rather than copying it. v2 never calls
#   exit and never closes the in-memory project, so when it returns the
#   synthesised, opt_design'd, SAIF-annotated design is still open and its
#   post-synthesis CSV is already written. This script then places, phys-opts
#   and routes THAT design and re-reports.
#
#   Three consequences, all of them the point:
#     - v2 is untouched, so the completed 4227-evaluation sweep stays exactly
#       reproducible.
#     - the post-synthesis and post-route numbers come from one synthesis run,
#       so the delta between them is purely implementation and not a
#       re-synthesis artefact.
#     - both CSVs land side by side: argv 1 as v2 wrote it, and
#       <argv 1 rootname>_impl.csv from here.
#
# OUT-OF-CONTEXT
#   v2 synthesises with -mode out_of_context, and this implements in the same
#   mode: no I/O buffers are inserted and the boundary is timed by the 0.5 ns
#   input/output delays v2 puts in the XDC. That is the correct way to time a
#   core destined for the RISC-V integration rather than for device pins. It
#   also means the numbers here are for the core alone, not for a bitstream.
#
# argv: identical to vivado_synthesis_v2.tcl. Optional extras:
#       10 impl_csv   (default: <argv 1 rootname>_impl.csv)
#       11 directive  (place_design -directive; default Default)
#
# Invoke:
#   vivado -mode batch -source vivado_implement.tcl -nojournal \
#     -log /tmp/impl_<d>.log -tclargs \
#     <design> <csv> 10.0 <core.v> <top.v> ./verilog_sources \
#     xc7a35tcpg236-1 <saif> 1 tb_fft_power/uut
#
# STATUS: NOT RUN AGAINST VIVADO. Tcl syntax and control flow were exercised
#   under tclsh with every Vivado command stubbed, including the source of a
#   stubbed v2. Every implementation step is wrapped so a failure is reported
#   with the tool's own message and the CSV still gets written with what did
#   succeed -- a sweep must not lose 59 good designs to one routing failure.
# =============================================================================

set _impl_start [clock seconds]

# -----------------------------------------------------------------------------
# 1. Synthesis, utilisation, timing and power -- by sourcing v2 unmodified.
#    It reads the same $argv. Anything past index 9 it ignores.
# -----------------------------------------------------------------------------
set _here [file normalize [file dirname [info script]]]
set _v2 [file join $_here "vivado_synthesis_v2.tcl"]
if { ![file exists $_v2] } {
    puts "ERROR: vivado_synthesis_v2.tcl not found beside this script ($_v2)"
    exit 1
}
puts "INFO: ---- sourcing vivado_synthesis_v2.tcl for the synthesis stage ----"
source $_v2
puts "INFO: ---- synthesis stage done; entering implementation ----"

# v2 leaves these in scope: design_name csv_output clock_period saif_file
# saif_strip_path use_dsp lut_count lutram_count dsp_count bram_count ff_count
# dynamic_power_w static_power_w total_power wns critical_path_delay fmax_mhz
# saif_used dyn_vl opt_ran
set synth_lut   $lut_count
set synth_ff    $ff_count
set synth_dsp   $dsp_count
set synth_bram  $bram_count
set synth_wns   $wns
set synth_crit  $critical_path_delay
set synth_fmax  $fmax_mhz
set synth_dyn   $dynamic_power
set synth_stat  $static_power
set synth_tot   $total_power
set synth_saif  $saif_used

set impl_csv [file rootname $csv_output]_impl.csv
if { [llength $argv] >= 11 } { set impl_csv [lindex $argv 10] }
set place_directive "Default"
if { [llength $argv] >= 12 } { set place_directive [lindex $argv 11] }

# -----------------------------------------------------------------------------
# 2. place -> phys_opt -> route. Each step is individually reported, because
#    "routing failed" and "routing succeeded but timing is awful" are different
#    findings and the CSV must be able to tell them apart.
# -----------------------------------------------------------------------------
set place_ran 0
set physopt_ran 0
set route_ran 0
set impl_error ""

if { [catch { place_design -directive $place_directive } emsg] } {
    puts "ERROR: place_design failed: $emsg"
    set impl_error "place_design: $emsg"
} else {
    set place_ran 1
    puts "INFO: place_design complete (directive $place_directive)"
}

if { $place_ran } {
    # phys_opt_design is optional: it improves timing but a failure here must not
    # cost us the routing result.
    if { [catch { phys_opt_design } emsg] } {
        puts "WARNING: phys_opt_design failed ($emsg) - continuing to route"
    } else {
        set physopt_ran 1
        puts "INFO: phys_opt_design complete"
    }
    if { [catch { route_design } emsg] } {
        puts "ERROR: route_design failed: $emsg"
        if { $impl_error eq "" } { set impl_error "route_design: $emsg" }
    } else {
        set route_ran 1
        puts "INFO: route_design complete"
    }
}

# -----------------------------------------------------------------------------
# 3. Post-route utilisation. LUT counts move during opt/place/phys_opt, so the
#    post-synthesis area is not the implemented area.
# -----------------------------------------------------------------------------
proc impl_util_counts { rpt } {
    # Returns {lut lutram ff bram dsp}, -1 for anything not found.
    #
    # Deliberately the SAME parsing v2 uses, label variants and the
    # LUT-as-Logic + LUT-as-Memory fallback included. A post-route report can
    # name rows differently from a post-synthesis one (Slice vs CLB), and if the
    # two stages were parsed by different rules the area delta would measure the
    # parser rather than the design.
    # Empty, not -1 and not 0: a number that was never measured must not be
    # able to turn into a percentage further down.
    set out [list {} {} {} {} {}]
    if { ![file exists $rpt] } { return $out }
    set fp [open $rpt r]; set content [read $fp]; close $fp
    set lines [split $content "\n"]

    set lut 0 ; set lutram 0 ; set ff 0 ; set bram 0 ; set dsp 0
    foreach line $lines {
        if {[regexp {^\|\s*(CLB LUTs|Slice LUTs|Slice LUTs\*)\s*\|\s*(\d+)\s*\|} $line -> _l v]} {
            set lut [string trim $v]; break
        }
    }
    foreach line $lines {
        if {[regexp {^\|\s*LUT as Memory\s*\|\s*(\d+)\s*\|} $line -> v]} {
            set lutram [string trim $v]; break
        }
    }
    if {$lut == 0} {
        set ll 0; set lm 0
        foreach line $lines {
            if {[regexp {^\|\s*LUT as Logic\s*\|\s*(\d+)\s*\|} $line -> v]}  { set ll [string trim $v] }
            if {[regexp {^\|\s*LUT as Memory\s*\|\s*(\d+)\s*\|} $line -> v]} { set lm [string trim $v] }
        }
        set lut [expr {$ll + $lm}]; set lutram $lm
    }
    foreach line $lines {
        if {[regexp {^\|\s*(DSPs|DSP48E\w*)\s*\|\s*(\d+)\s*\|} $line -> _l v]} {
            set dsp [string trim $v]; break
        }
    }
    foreach line $lines {
        if {[regexp {^\|\s*Block RAM Tile\s*\|\s*([0-9.]+)\s*\|} $line -> v]} {
            set bram [string trim $v]; break
        }
    }
    foreach line $lines {
        if {[regexp {^\|\s*(CLB Registers|Slice Registers|Register as Flip Flop)\s*\|\s*(\d+)\s*\|} $line -> _l v]} {
            set ff [string trim $v]; break
        }
    }
    return [list $lut $lutram $ff $bram $dsp]
}

set impl_lut "" ; set impl_lutram "" ; set impl_ff ""
set impl_bram "" ; set impl_dsp ""
if { $place_ran } {
    set util_rpt "/tmp/${design_name}_impl_util.rpt"
    catch { report_utilization -file $util_rpt }
    lassign [impl_util_counts $util_rpt] impl_lut impl_lutram impl_ff impl_bram impl_dsp
}

# -----------------------------------------------------------------------------
# 4. Post-route timing, extracted exactly as v2 extracts it, so the two numbers
#    are comparable by construction.
# -----------------------------------------------------------------------------
# All three stay EMPTY unless routing actually happened. v2 initialises its
# equivalents to 100.0 ns and 0 MHz, which then read as measurements in a CSV;
# that is a trap this script does not repeat.
set impl_wns ""
set impl_crit ""
set impl_fmax ""
if { $route_ran } {
    catch { report_timing_summary -file /tmp/${design_name}_impl_timing.rpt \
                                  -delay_type max -max_paths 10 }
    catch { report_timing -delay_type max -max_paths 1 -nworst 1 -sort_by slack \
                          -file /tmp/${design_name}_impl_timing_worst.rpt }
    catch {
        set impl_wns [get_property -quiet SLACK \
            [lindex [get_timing_paths -max_paths 1 -delay_type max] 0]]
    }
    if { $impl_wns ne "" && $impl_wns ne "N/A" } {
        set impl_crit [expr {double($clock_period) - double($impl_wns)}]
        if { $impl_crit > 0 } { set impl_fmax [expr {1000.0 / $impl_crit}] }
    }
}

# -----------------------------------------------------------------------------
# 5. Post-route power. This is the one that gains the most from routing:
#    report_power now works from real routed net capacitance instead of an
#    estimate. The SAIF is re-read first -- annotation should survive
#    implementation, but re-reading costs nothing and removes the doubt.
# -----------------------------------------------------------------------------
set impl_saif_reread 0
set impl_dyn "" ; set impl_stat "" ; set impl_tot ""
if { $route_ran } {
    if { $saif_file ne "" && [file exists $saif_file] } {
        if { [catch { read_saif -strip_path $saif_strip_path $saif_file } emsg] } {
            puts "WARNING: post-route read_saif failed ($emsg) - relying on the"
            puts "WARNING: annotation carried over from the synthesis stage."
        } else {
            set impl_saif_reread 1
        }
    }
    set prpt "/tmp/${design_name}_impl_power.rpt"
    catch { report_power -file $prpt }
    # parse_power is defined by v2 and still in scope.
    if { [file exists $prpt] } {
        lassign [parse_power $prpt] impl_tot impl_dyn impl_stat
    }
}

# -----------------------------------------------------------------------------
# 6. CSV. Post-synthesis values are repeated here so one file carries the whole
#    comparison and the delta cannot be assembled from mismatched runs.
# -----------------------------------------------------------------------------
proc pct { new old } {
    # A delta is reported only when BOTH sides are real positive measurements.
    # Anything else yields an empty cell, so a stage that did not run leaves a
    # blank rather than an invented percentage.
    if { $old eq "" || $new eq "" } { return "" }
    if { ![string is double -strict $old] || ![string is double -strict $new] } { return "" }
    if { double($old) <= 0.0 || double($new) < 0.0 } { return "" }
    return [format %.2f [expr {100.0 * (double($new) - double($old)) / double($old)}]]
}

set elapsed [expr {[clock seconds] - $_impl_start}]

file mkdir [file dirname $impl_csv]
set fp [open $impl_csv w]
puts $fp "Metric,Value"
puts $fp "design_name,$design_name"
puts $fp "clock_period_ns,$clock_period"
puts $fp "place_ran,$place_ran"
puts $fp "phys_opt_ran,$physopt_ran"
puts $fp "route_ran,$route_ran"
puts $fp "impl_error,\"$impl_error\""
puts $fp "impl_saif_reread,$impl_saif_reread"
puts $fp "impl_elapsed_s,$elapsed"

puts $fp "synth_lut_count,$synth_lut"
puts $fp "synth_ff_count,$synth_ff"
puts $fp "synth_dsp_count,$synth_dsp"
puts $fp "synth_bram_count,$synth_bram"
puts $fp "synth_wns_ns,$synth_wns"
puts $fp "synth_critical_path_delay_ns,$synth_crit"
puts $fp "synth_fmax_mhz,$synth_fmax"
puts $fp "synth_dynamic_power_w,$synth_dyn"
puts $fp "synth_static_power_w,$synth_stat"
puts $fp "synth_total_power_w,$synth_tot"
puts $fp "saif_used,$synth_saif"

puts $fp "impl_lut_count,$impl_lut"
puts $fp "impl_lutram_count,$impl_lutram"
puts $fp "impl_ff_count,$impl_ff"
puts $fp "impl_bram_count,$impl_bram"
puts $fp "impl_dsp_count,$impl_dsp"
puts $fp "impl_wns_ns,$impl_wns"
puts $fp "impl_critical_path_delay_ns,$impl_crit"
puts $fp "impl_fmax_mhz,$impl_fmax"
puts $fp "impl_dynamic_power_w,$impl_dyn"
puts $fp "impl_static_power_w,$impl_stat"
puts $fp "impl_total_power_w,$impl_tot"

puts $fp "delta_lut_pct,[pct $impl_lut $synth_lut]"
puts $fp "delta_crit_pct,[pct $impl_crit $synth_crit]"
puts $fp "delta_fmax_pct,[pct $impl_fmax $synth_fmax]"
puts $fp "delta_dynamic_power_pct,[pct $impl_dyn $synth_dyn]"
close $fp

puts stdout "INFO: ---- implementation complete : $design_name ----"
puts stdout "INFO:   place / phys_opt / route = $place_ran / $physopt_ran / $route_ran   (${elapsed}s)"
proc show { label new old unit } {
    set d [pct $new $old]
    set shown [expr {$new eq "" ? "not measured" : "$new $unit"}]
    set delta [expr {$d eq "" ? "" : "   ($d %)"}]
    puts stdout [format "INFO:   %-11s post-synth %-10s -> post-route %s%s" \
                 $label "$old $unit" $shown $delta]
}
show "LUTs"      $impl_lut  $synth_lut  ""
show "Crit path" $impl_crit $synth_crit "ns"
show "f_max"     $impl_fmax $synth_fmax "MHz"
show "Dyn power" $impl_dyn  $synth_dyn  "W"
puts stdout "INFO:   CSV         = $impl_csv"
if { $impl_error ne "" } {
    puts stdout "INFO:   NOTE: implementation reported an error; see impl_error in the CSV."
}
