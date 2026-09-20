# =============================================================================
# generate_saif_funcsim.tcl
#
# Produces a SAIF from a POST-SYNTHESIS FUNCTIONAL NETLIST simulation, so that
# net names match the netlist report_power annotates - by construction.
#
# WHY THIS EXISTS (measured 2026-09-18)
#   generate_saif.tcl simulates the RTL. Annotating that SAIF onto the
#   synthesized netlist reaches only 19-26% of nets, because synthesis renames
#   and absorbs the combinational interior (bf_mult_prec vanishes entirely;
#   fp4_add_sub reports 0 instances despite existing). The un-annotated
#   remainder is filled probabilistically from the annotated boundary - and
#   that interior is exactly where operand isolation lives, so the gating's
#   per-chromosome effect was invisible:
#
#       4/8 vs 8/8 FP8 stages, gated wrapper:
#           vectorless (structure-aware, workload-blind) : 27.6% apart
#           RTL-SAIF   (workload-aware on ~20% of nets)  :  2.4% apart
#
#   A >10x disagreement between two methods on the same three designs is proof
#   that neither resolves the quantity. Simulating the netlist itself removes
#   the name-matching problem and should push coverage toward 100%.
#
# FLOW
#   1. synth_design, identically to vivado_synthesis_v2.tcl (same part, same
#      XDC, same OOC mode, same opt_design) so the netlist simulated here is
#      the netlist that gets annotated there
#   2. write_verilog -mode funcsim  ->  UNISIM-primitive Verilog netlist
#   3. xvlog / xelab / xsim that netlist against tb/tb_fft_power.v, with
#      unisims_ver + glbl so GSR initialises the flops out of X
#   4. open_saif / log_saif / run all  ->  SAIF whose names match the netlist
#
#   The netlist is self-contained: write_verilog bakes in BRAM INIT strings and
#   the twiddle ROM's LUT INIT values, so no $readmemb path is needed.
#
# CHECKSUM DISCIPLINE
#   This script and vivado_synthesis_v2.tcl each run their own synth_design.
#   Vivado is deterministic for identical inputs, so the netlists match - but
#   verify rather than assume. Both runs print
#       Synth Design complete | Checksum: xxxxxxxx
#   and those values MUST be equal, or this SAIF describes a different netlist
#   than the one being annotated:
#
#       grep -h "Synth Design complete" /tmp/fsaif_<d>.log /tmp/pwr_<d>.log
#
# argv: 0 design_name  1 core_file  2 top_file  3 verilog_dir  4 tb_file
#       5 saif_out     6 fft_n      7 frames    8 fpga_part    9 clock_period
#
# Invoke:
#   vivado -mode batch -source generate_saif_funcsim.tcl -nojournal \
#     -log /tmp/fsaif_probe_fp8x4.log -tclargs \
#     probe_fp8x4 <core.v> <top.v> ./verilog_sources ./tb/tb_fft_power.v \
#     /tmp/probe_fp8x4_fs.saif 256 4 xc7a35tcpg236-1 10.0
#
# STATUS: NOT RUN AGAINST VIVADO. Tcl syntax and control flow were exercised
#   under tclsh with every Vivado command stubbed, in both a success and a
#   missing-glbl path. The xelab library/glbl invocation is the part most
#   likely to need adjusting on a given install; the script fails loudly with
#   the tool's own message and a hint if it does.
# =============================================================================

set design_name  [lindex $argv 0]
set core_file    [lindex $argv 1]
set top_file     [lindex $argv 2]
set verilog_dir  [lindex $argv 3]
set tb_file      [lindex $argv 4]
set saif_out     [lindex $argv 5]

set fft_n 256
if { [llength $argv] >= 7 } { set fft_n [lindex $argv 6] }
set frames 4
if { [llength $argv] >= 8 } { set frames [lindex $argv 7] }
set fpga_part "xc7a35tcpg236-1"
if { [llength $argv] >= 9 } { set fpga_part [lindex $argv 8] }
set clock_period 10.0
if { [llength $argv] >= 10 } { set clock_period [lindex $argv 9] }

set top_module [format "%s_top" $design_name]
set tb_module  [file rootname [file tail $tb_file]]
set work_dir   "/tmp/fsaif_${design_name}"
set netlist_v  "${work_dir}/${design_name}_funcsim.v"
file mkdir $work_dir

puts "INFO: funcsim SAIF generation for $design_name"
puts "INFO:   top       = $top_module"
puts "INFO:   tb        = $tb_module"
puts "INFO:   FFT_N     = $fft_n   frames = $frames"
puts "INFO:   part      = $fpga_part   clock = $clock_period ns"
puts "INFO:   netlist   = $netlist_v"
puts "INFO:   saif_out  = $saif_out"

# -----------------------------------------------------------------------------
# 1. Synthesise - MUST match vivado_synthesis_v2.tcl exactly
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

set xdc_file "${work_dir}/${design_name}_constr.xdc"
set xfp [open $xdc_file w]
puts $xfp "create_clock -period $clock_period -name clk \[get_ports clk\]"
puts $xfp "set_input_delay  -clock clk 0.500 \[get_ports -filter {DIRECTION == IN && NAME != clk}\]"
puts $xfp "set_output_delay -clock clk 0.500 \[all_outputs\]"
close $xfp
read_xdc $xdc_file

synth_design -top $top_module -part $fpga_part -mode out_of_context

if { [catch { opt_design -directive Explore } emsg] } {
    puts "WARNING: opt_design failed ($emsg) - this netlist may differ from the"
    puts "WARNING: power run's netlist. CHECK THE CHECKSUMS before believing"
    puts "WARNING: any power number derived from this SAIF."
}

# -----------------------------------------------------------------------------
# 2. Write the functional netlist
# -----------------------------------------------------------------------------
if { [catch { write_verilog -mode funcsim -force $netlist_v } emsg] } {
    puts "ERROR: write_verilog -mode funcsim failed: $emsg"
    exit 1
}
if { ![file exists $netlist_v] } {
    puts "ERROR: netlist was not written to $netlist_v"
    exit 1
}
puts "INFO: funcsim netlist written ([file size $netlist_v] bytes)"

# -----------------------------------------------------------------------------
# 3. Compile netlist + testbench + glbl, elaborate against the UNISIM libs
# -----------------------------------------------------------------------------
set here [pwd]
cd $work_dir

# glbl drives GSR, which initialises the netlist's flops. Without it a
# gate-level sim can sit at X indefinitely and the SAIF is worthless.
set glbl_v ""
if { [info exists ::env(XILINX_VIVADO)] } {
    set cand "$::env(XILINX_VIVADO)/data/verilog/src/glbl.v"
    if { [file exists $cand] } { set glbl_v $cand }
}
if { $glbl_v eq "" } {
    puts "WARNING: glbl.v not found via XILINX_VIVADO - elaborating without it."
    puts "WARNING: If the testbench times out or reports all-zero readback, the"
    puts "WARNING: flops never left X and this is the reason."
}

set vlog_files [list $netlist_v $tb_file]
if { $glbl_v ne "" } { lappend vlog_files $glbl_v }

set defs [list -d DUT_TOP=${top_module} -d FFT_N=${fft_n} -d FRAMES=${frames}]
if { [catch { eval exec xvlog -sv $defs [lrange $vlog_files 0 end] } emsg] } {
    puts "ERROR: xvlog failed:\n$emsg"
    cd $here
    exit 1
}

set tops [list $tb_module]
if { $glbl_v ne "" } { lappend tops glbl }
set xelab_args [list --relax -debug typical -timescale 1ns/1ps \
                     -L unisims_ver -L unimacro_ver -L secureip \
                     -snapshot ${tb_module}_fsnap]
if { [catch { eval exec xelab $xelab_args [lrange $tops 0 end] } emsg] } {
    puts "ERROR: xelab failed:\n$emsg"
    puts "ERROR: the -L library names are the usual suspect on a given install."
    puts "ERROR: Try dropping '-L secureip', or adding '-L xil_defaultlib'."
    cd $here
    exit 1
}

# -----------------------------------------------------------------------------
# 4. Run, gating the SAIF window on the testbench's own warm-up flag.
#    A netlist sim is slower than RTL, so the guard is 400 x 2 us here.
# -----------------------------------------------------------------------------
set do_file "${work_dir}/run_saif.tcl"
set dfp [open $do_file w]
puts $dfp "set _guard 0"
puts $dfp "run 2 us"
puts $dfp "while { \[get_value /${tb_module}/saif_window\] == 0 && \$_guard < 400 } {"
puts $dfp "    run 2 us"
puts $dfp "    incr _guard"
puts $dfp "}"
puts $dfp "if { \$_guard >= 400 } { puts \"ERROR: saif_window never asserted - netlist sim stuck (X?)\"; quit }"
puts $dfp "puts \"INFO: warm-up complete, opening SAIF\""
puts $dfp "open_saif {$saif_out}"
puts $dfp "log_saif \[get_objects -r /${tb_module}/uut/*\]"
puts $dfp "run all"
puts $dfp "close_saif"
puts $dfp "quit"
close $dfp

if { [catch { exec xsim ${tb_module}_fsnap -tclbatch $do_file } emsg] } {
    puts "ERROR: xsim failed:\n$emsg"
    cd $here
    exit 1
}
cd $here

if { [file exists $saif_out] } {
    puts "INFO: funcsim SAIF written: $saif_out ([file size $saif_out] bytes)"
    puts "INFO: strip_path for read_saif is still ${tb_module}/uut"
    puts "INFO: NOW COMPARE the 'Synth Design complete | Checksum:' line in"
    puts "INFO: this log against the power run's log. They must be equal."
} else {
    puts "ERROR: SAIF was not produced. Check the DUT instance is named 'uut'"
    puts "ERROR: in $tb_module, and that the sim did not sit at X."
    exit 1
}
