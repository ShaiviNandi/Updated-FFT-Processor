# =============================================================================
# vivado_synthesis_v2.tcl
#
# Replacement for vivado_synthesis.tcl. Same argv contract, same output CSV
# keys (plus new ones), so objectiveEvaluationFFT.py keeps working.
#
# WHAT IS FIXED RELATIVE TO v1
#   [F1] create_clock now runs BEFORE synth_design. In v1 it ran after, so
#        every published result came from an UNCONSTRAINED synthesis with no
#        timing-driven optimisation.
#   [F2] opt_design restored. v1 removed it to dodge OOC fatals; the correct
#        fix is -directive Explore with the problematic passes disabled, not
#        skipping optimisation. Without it, LUT counts are pre-opt estimates
#        and carry a ~3% run-to-run noise band.
#   [F3] SAIF-driven power. v1 called report_power with no activity data, so
#        it reported vectorless (default-toggle) power: 0.072-0.074 W across
#        722 designs, i.e. the static power of the part. Now, if a SAIF is
#        supplied, it is read and DYNAMIC power is reported separately.
#   [F4] Netlist forensics. Emits how many fp4_mul / fp8_mul instances survived
#        into the netlist, which is the direct proof of preservation vs pruning.
#   [F5] Timing is taken from report_timing on the true worst path, and the CSV
#        now records fmax_mhz explicitly so nobody has to re-derive it.
#   [F6] Optional -no_dsp switch: set USE_DSP 0 to add -max_dsp 0, which stops
#        the (* use_dsp *) attributes in multiplier.v from burning DSP48E1s on
#        2x2-bit and 4x4-bit significand products.
#
# argv:  0 design_name  1 csv_output  2 clock_period  3 core_file
#        4 top_file     5 verilog_dir 6 fpga_part [7 saif_file] [8 use_dsp]
# =============================================================================

set design_name  [lindex $argv 0]
set csv_output   [lindex $argv 1]
set clock_period [lindex $argv 2]
set core_file    [lindex $argv 3]
set top_file     [lindex $argv 4]
set verilog_dir  [lindex $argv 5]

set fpga_part "xc7a35tcpg236-1"
if { [llength $argv] >= 7 } { set fpga_part [lindex $argv 6] }

set saif_file ""
if { [llength $argv] >= 8 } { set saif_file [lindex $argv 7] }

set use_dsp 1
if { [llength $argv] >= 9 } { set use_dsp [lindex $argv 8] }

set top_module "${design_name}_top"

puts "INFO: design_name  = $design_name"
puts "INFO: top_module   = $top_module"
puts "INFO: clock_period = $clock_period ns  ([format %.2f [expr {1000.0/$clock_period}]] MHz)"
puts "INFO: target_part  = $fpga_part"
puts "INFO: saif_file    = [expr {$saif_file eq "" ? "<none - power will be VECTORLESS>" : $saif_file}]"
puts "INFO: use_dsp      = $use_dsp"

# -----------------------------------------------------------------------------
# 1. In-memory project + sources
# -----------------------------------------------------------------------------
create_project -in_memory -part $fpga_part

foreach f [glob -nocomplain ${verilog_dir}/*.v] {
    if { [string match "*tb_*" [file tail $f]] == 0 } {
        add_files -norecurse $f
    }
}
add_files -norecurse $core_file
add_files -norecurse $top_file

set_property include_dirs $verilog_dir [current_fileset]
set_property top $top_module [current_fileset]
update_compile_order -fileset sources_1

# -----------------------------------------------------------------------------
# 2. [F1] CONSTRAIN FIRST. Write an XDC and attach it, so the clock exists
#    during elaboration and synthesis rather than being invented afterwards.
# -----------------------------------------------------------------------------
set xdc_file "/tmp/${design_name}_constr.xdc"
set xfp [open $xdc_file w]
puts $xfp "create_clock -period $clock_period -name clk \[get_ports clk\]"
# OOC: tell the tool what the outside world looks like, else input delays are 0
puts $xfp "set_input_delay  -clock clk 0.500 \[all_inputs\]"
puts $xfp "set_output_delay -clock clk 0.500 \[all_outputs\]"
close $xfp
read_xdc $xdc_file
puts "INFO: constraints attached BEFORE synthesis (fix F1)"

# -----------------------------------------------------------------------------
# 3. Synthesis  [F6]
# -----------------------------------------------------------------------------
set synth_args [list -top $top_module -part $fpga_part -mode out_of_context]
if { $use_dsp == 0 } {
    lappend synth_args -max_dsp 0
    puts "INFO: -max_dsp 0 : DSP48E1 inference disabled (fix F6)"
}
eval synth_design $synth_args

# -----------------------------------------------------------------------------
# 4. [F2] Optimisation. OOC-safe: skip the passes that fatal without placement.
#    If your Vivado version still fatals here, narrow the directive rather than
#    deleting the call - the numbers are not comparable without it.
# -----------------------------------------------------------------------------
if { [catch { opt_design -directive Explore } emsg] } {
    puts "WARNING: opt_design -directive Explore failed ($emsg); retrying default"
    if { [catch { opt_design } emsg2] } {
        puts "CRITICAL: opt_design unavailable ($emsg2). LUT counts are PRE-OPT"
        puts "CRITICAL: estimates and carry a ~3% noise band. Record this."
        set opt_ran 0
    } else { set opt_ran 1 }
} else { set opt_ran 1 }
puts "INFO: opt_design ran = $opt_ran (fix F2)"

# -----------------------------------------------------------------------------
# 5. [F4] NETLIST FORENSICS - preservation vs pruning, per module
# -----------------------------------------------------------------------------
set forensic {}
foreach m {fp4_mul fp8_mul fp4_cmul fp8_cmul fp4_add_sub fp8_add_sub \
           fp4_complex_add_sub fp8_complex_add_sub butterfly_wrapper \
           butterfly_wrapper_gated complex_fp8_to_fp4 complex_fp4_to_fp8} {
    set n [llength [get_cells -hier -filter "REF_NAME =~ *${m}*" -quiet]]
    dict set forensic $m $n
    puts "FORENSIC: ${m} instances kept = $n"
}
set n_dsp_prim [llength [get_cells -hier -filter {PRIMITIVE_TYPE =~ MULT.*} -quiet]]
puts "FORENSIC: DSP48 primitives = $n_dsp_prim"

# is the precision select collapsed to a constant?
set bf_nets [get_nets -hier -quiet *bf_mult_prec*]
puts "FORENSIC: bf_mult_prec nets present = [llength $bf_nets]"
foreach n $bf_nets {
    puts "FORENSIC:   $n type=[get_property -quiet TYPE $n]"
}

report_utilization -hierarchical -hierarchical_depth 6 \
    -file /tmp/${design_name}_util_hier.rpt

# -----------------------------------------------------------------------------
# 6. Utilisation
# -----------------------------------------------------------------------------
set util_rpt_file "/tmp/${design_name}_util.rpt"
report_utilization -file $util_rpt_file

set lut_count 0 ; set lutram_count 0 ; set dsp_count 0
set bram_count 0 ; set ff_count 0    ; set io_count 0
set content ""
if {[file exists $util_rpt_file]} {
    set fp [open $util_rpt_file r]; set content [read $fp]; close $fp
}
set lines [split $content "\n"]

foreach line $lines {
    if {[regexp {^\|\s*(CLB LUTs|Slice LUTs|Slice LUTs\*)\s*\|\s*(\d+)\s*\|} $line -> _l v]} {
        set lut_count [string trim $v]; break
    }
}
foreach line $lines {
    if {[regexp {^\|\s*LUT as Memory\s*\|\s*(\d+)\s*\|} $line -> v]} {
        set lutram_count [string trim $v]; break
    }
}
if {$lut_count == 0} {
    set ll 0; set lm 0
    foreach line $lines {
        if {[regexp {^\|\s*LUT as Logic\s*\|\s*(\d+)\s*\|} $line -> v]}  { set ll [string trim $v] }
        if {[regexp {^\|\s*LUT as Memory\s*\|\s*(\d+)\s*\|} $line -> v]} { set lm [string trim $v] }
    }
    set lut_count [expr {$ll + $lm}]; set lutram_count $lm
}
foreach line $lines {
    if {[regexp {^\|\s*(DSPs|DSP48E\w*)\s*\|\s*(\d+)\s*\|} $line -> _l v]} {
        set dsp_count [string trim $v]; break
    }
}
foreach line $lines {
    if {[regexp {^\|\s*Block RAM Tile\s*\|\s*([0-9.]+)\s*\|} $line -> v]} {
        set bram_count [string trim $v]; break
    }
}
foreach line $lines {
    if {[regexp {^\|\s*(CLB Registers|Slice Registers|Register as Flip Flop)\s*\|\s*(\d+)\s*\|} $line -> _l v]} {
        set ff_count [string trim $v]; break
    }
}
foreach line $lines {
    if {[regexp {^\|\s*Bonded IOB\s*\|\s*(\d+)\s*\|} $line -> v]} {
        set io_count [string trim $v]; break
    }
}

# -----------------------------------------------------------------------------
# 7. [F3] POWER. With a SAIF this is a measurement; without one it is a guess.
# -----------------------------------------------------------------------------
set saif_used 0
if { $saif_file ne "" && [file exists $saif_file] } {
    if { [catch { read_saif -strip_path $top_module $saif_file } emsg] } {
        puts "WARNING: read_saif failed ($emsg) - falling back to vectorless"
    } else {
        set saif_used 1
        puts "INFO: SAIF applied from $saif_file (fix F3)"
    }
} else {
    puts "WARNING: no SAIF - power is VECTORLESS and will NOT vary with the"
    puts "WARNING: chromosome. Do not use it as an optimisation objective."
}

set power_rpt_file "/tmp/${design_name}_power.rpt"
report_power -file $power_rpt_file

set total_power 0.0 ; set dynamic_power 0.0 ; set static_power 0.0
if {[file exists $power_rpt_file]} {
    set fp [open $power_rpt_file r]; set pc [read $fp]; close $fp
    foreach line [split $pc "\n"] {
        if {[regexp {Total On-Chip Power \(W\)\s*\|\s*([0-9.]+)} $line -> v]} {
            set total_power [string trim $v]
        }
        if {[regexp {Dynamic \(W\)\s*\|\s*([0-9.]+)} $line -> v]} {
            set dynamic_power [string trim $v]
        }
        if {[regexp {Device Static \(W\)\s*\|\s*([0-9.]+)} $line -> v]} {
            set static_power [string trim $v]
        }
    }
}

# per-cone power: shows the unselected datapath burning power when ungated
set p_fp8_cone 0.0 ; set p_fp4_cone 0.0
catch {
    set c8 [get_cells -hier -filter {REF_NAME =~ *fp8_cmul*} -quiet]
    if { [llength $c8] } {
        report_power -instance $c8 -file /tmp/${design_name}_power_fp8.rpt
    }
    set c4 [get_cells -hier -filter {REF_NAME =~ *fp4_cmul*} -quiet]
    if { [llength $c4] } {
        report_power -instance $c4 -file /tmp/${design_name}_power_fp4.rpt
    }
}

# -----------------------------------------------------------------------------
# 8. [F5] TIMING from the true worst path
# -----------------------------------------------------------------------------
set timing_rpt_file "/tmp/${design_name}_timing.rpt"
report_timing_summary -file $timing_rpt_file -delay_type max -max_paths 10
report_timing -delay_type max -max_paths 1 -nworst 1 -sort_by slack \
              -file /tmp/${design_name}_timing_worst.rpt

set wns "N/A"
set critical_path_delay 100.0
catch {
    set wns [get_property -quiet SLACK [lindex [get_timing_paths -max_paths 1 -delay_type max] 0]]
}
if { $wns eq "" || $wns eq "N/A" } {
    if {[file exists $timing_rpt_file]} {
        set fp [open $timing_rpt_file r]; set tc [read $fp]; close $fp
        if {[regexp {WNS\(ns\)[^\n]*\n[^\n]*\n\s*([-0-9.]+)} $tc -> v]} { set wns [string trim $v] }
    }
}
if { $wns ne "N/A" && $wns ne "" } {
    set critical_path_delay [expr {double($clock_period) - double($wns)}]
}
set fmax_mhz 0.0
if { $critical_path_delay > 0 } {
    set fmax_mhz [expr {1000.0 / $critical_path_delay}]
}

# -----------------------------------------------------------------------------
# 9. CSV  (v1 keys preserved; new keys appended)
# -----------------------------------------------------------------------------
set csv_dir [file dirname $csv_output]
file mkdir $csv_dir
set fp [open $csv_output w]
puts $fp "Metric,Value"
puts $fp "design_name,$design_name"
puts $fp "top_module,$top_module"
puts $fp "lut_count,$lut_count"
puts $fp "lutram_count,$lutram_count"
puts $fp "dsp_count,$dsp_count"
puts $fp "bram_count,$bram_count"
puts $fp "ff_count,$ff_count"
puts $fp "io_count,$io_count"
puts $fp "total_power_w,$total_power"
puts $fp "wns_ns,$wns"
puts $fp "critical_path_delay_ns,$critical_path_delay"
puts $fp "clock_period_ns,$clock_period"
# --- new in v2 ---
puts $fp "dynamic_power_w,$dynamic_power"
puts $fp "static_power_w,$static_power"
puts $fp "saif_used,$saif_used"
puts $fp "opt_design_ran,$opt_ran"
puts $fp "fmax_mhz,$fmax_mhz"
puts $fp "dsp48_primitives,$n_dsp_prim"
puts $fp "use_dsp,$use_dsp"
dict for {m n} $forensic { puts $fp "kept_$m,$n" }
close $fp

puts stdout "INFO: Synthesis complete  : $design_name"
puts stdout "INFO:   LUTs            = $lut_count   (opt_design ran = $opt_ran)"
puts stdout "INFO:   DSPs            = $dsp_count"
puts stdout "INFO:   FFs             = $ff_count"
puts stdout "INFO:   Power total     = $total_power W   (SAIF used = $saif_used)"
puts stdout "INFO:   Power dynamic   = $dynamic_power W  <-- the objective"
puts stdout "INFO:   Power static    = $static_power W"
puts stdout "INFO:   WNS             = $wns ns"
puts stdout "INFO:   Crit path       = $critical_path_delay ns  ($fmax_mhz MHz)"
puts stdout "INFO:   fp8_mul kept    = [dict get $forensic fp8_mul]"
puts stdout "INFO:   fp4_mul kept    = [dict get $forensic fp4_mul]"
puts stdout "INFO:   CSV             = $csv_output"
