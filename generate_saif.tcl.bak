# =============================================================================
# generate_saif.tcl
#
# Produces a per-design SAIF (switching activity) file so that report_power in
# vivado_synthesis_v2.tcl reports a MEASUREMENT instead of a vectorless guess.
#
# This is the missing half of the power objective. Without it, dynamic power is
# computed from a default toggle-rate assumption that is identical for every
# chromosome - which is exactly why the published sweep shows power taking only
# three values (0.072 / 0.073 / 0.074 W) across 722 designs.
#
# FLOW
#   1. behavioural (RTL) simulation of <design>_top under xsim
#   2. the testbench drives a representative frame of input data and lets the
#      FFT run to completion - the precision schedule in the DUT is what makes
#      the activity chromosome-dependent
#   3. write_saif over the DUT scope only (not the testbench)
#
# RTL-level SAIF is the right choice here: it is fast enough for a 700-design
# sweep, and the quantity that varies with the chromosome is datapath operand
# activity, which RTL captures. Post-synthesis (timing) SAIF adds glitch
# activity and is worth doing for the handful of Pareto-optimal designs that
# end up in the paper's table - note in the paper which one you used.
#
# argv: 0 design_name  1 core_file  2 top_file  3 verilog_dir  4 tb_file
#       5 saif_out     6 fft_n       [7 frames]
#
# The testbench is parameterised by compile-time defines because the DUT module
# name and the transform size change per chromosome:
#     -d DUT_TOP=<design>_top  -d FFT_N=<n>  -d FRAMES=<k>
#
# Invoke:
#   vivado -mode batch -source generate_saif.tcl -tclargs \
#          probe_fp8x4 <core.v> <top.v> ./verilog_sources \
#          ./tb/tb_fft_power.v /tmp/probe_fp8x4.saif 256 4
# =============================================================================

set design_name [lindex $argv 0]
set core_file   [lindex $argv 1]
set top_file    [lindex $argv 2]
set verilog_dir [lindex $argv 3]
set tb_file     [lindex $argv 4]
set saif_out    [lindex $argv 5]

set fft_n 256
if { [llength $argv] >= 7 } { set fft_n [lindex $argv 6] }
set frames 4
if { [llength $argv] >= 8 } { set frames [lindex $argv 7] }

set tb_module        [file rootname [file tail $tb_file]]
set top_module_name  "${design_name}_top"
set work_dir   "/tmp/saif_${design_name}"
file mkdir $work_dir

puts "INFO: SAIF generation for $design_name"
puts "INFO:   tb        = $tb_module"
puts "INFO:   FFT_N     = $fft_n   frames = $frames"
puts "INFO:   saif_out  = $saif_out"

# -----------------------------------------------------------------------------
# 1. Collect sources
# -----------------------------------------------------------------------------
set srcs {}
foreach f [glob -nocomplain ${verilog_dir}/*.v] {
    if { [string match "*tb_*" [file tail $f]] == 0 } { lappend srcs $f }
}
lappend srcs $core_file $top_file $tb_file

# -----------------------------------------------------------------------------
# 2. Compile + elaborate with xvlog/xelab, run with xsim
#    (done via exec so this works without a project on disk)
# -----------------------------------------------------------------------------
set here [pwd]
cd $work_dir

# DUT_TOP / FFT_N / FRAMES reach the testbench as defines
set defs [list -d DUT_TOP=${top_module_name} -d FFT_N=${fft_n} -d FRAMES=${frames}]
if { [catch { eval exec xvlog -sv $defs [lrange $srcs 0 end] } emsg] } {
    puts "ERROR: xvlog failed:\n$emsg"
    cd $here
    exit 1
}

if { [catch { exec xelab -debug typical -top $tb_module -snapshot ${tb_module}_snap } emsg] } {
    puts "ERROR: xelab failed:\n$emsg"
    cd $here
    exit 1
}

# xsim command script: open a SAIF over the DUT only, run, write it out
set do_file "${work_dir}/run_saif.tcl"
set dfp [open $do_file w]
# Skip the warm-up frame: the testbench raises saif_window once frame 0 is
# done, so reset and first-load transients stay out of the recorded activity.
puts $dfp "set _guard 0"
puts $dfp "run 2 us"
puts $dfp "while { \[get_value /${tb_module}/saif_window\] == 0 && \$_guard < 200 } {"
puts $dfp "    run 2 us"
puts $dfp "    incr _guard"
puts $dfp "}"
puts $dfp "if { \$_guard >= 200 } { puts \"ERROR: saif_window never asserted\"; quit }"
puts $dfp "puts \"INFO: warm-up complete, opening SAIF\""
puts $dfp "open_saif {$saif_out}"
# log activity for the whole DUT subtree; adjust the scope name if your
# testbench instantiates the DUT under a different label than 'uut'
puts $dfp "log_saif \[get_objects -r /${tb_module}/uut/*\]"
puts $dfp "run all"
puts $dfp "close_saif"
puts $dfp "quit"
close $dfp

if { [catch { exec xsim ${tb_module}_snap -tclbatch $do_file } emsg] } {
    puts "ERROR: xsim failed:\n$emsg"
    cd $here
    exit 1
}

cd $here

if { [file exists $saif_out] } {
    puts "INFO: SAIF written: $saif_out ([file size $saif_out] bytes)"
} else {
    puts "ERROR: SAIF was not produced. Check that the DUT instance in"
    puts "ERROR: $tb_module is named 'uut', or edit the log_saif scope above."
    exit 1
}
