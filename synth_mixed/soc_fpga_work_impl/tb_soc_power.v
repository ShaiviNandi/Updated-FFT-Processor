// =============================================================================
// tb_soc_power.v  --  GENERATED, do not edit
//
// Activity stimulus for the SoC, for generate_saif_funcsim.tcl.
//
// Written by run_soc_fpga.py rather than kept as a static file, because the
// SAIF script's xsim invocation passes a fixed define list
// (-d DUT_TOP, -d FFT_N, -d FRAMES) and runs in /tmp/fsaif_<design>/. The
// firmware path therefore cannot arrive as a define and cannot be relative, so
// the driver substitutes an absolute path here at generation time. Forking the
// TCL to add a define would mean the SoC no longer goes through the same
// unmodified script as everything else.
//
// CONTRACT WITH generate_saif_funcsim.tcl
//   - the DUT instance must be named `uut`: the script logs
//     `[get_objects -r /tb_soc_power/uut/*]`
//   - the module name must match the file stem, which the script uses as the
//     xelab top
//   - the simulation must end on its own; the script issues `run all`
//
// Without a real SAIF the SoC's power is vectorless, and on this device that is
// roughly two thirds static leakage and barely moves between designs -- useless
// for an energy figure. That is the whole reason this file exists rather than
// letting the power pass run unannotated.
// =============================================================================
`timescale 1ns/1ps

`ifndef DUT_TOP
  `define DUT_TOP soc_fft_top
`endif
`ifndef FFT_N
  `define FFT_N 256
`endif
`ifndef FRAMES
  `define FRAMES 4
`endif

module tb_soc_power;

    localparam integer N      = `FFT_N;
    // FRAMES here means transforms to exercise, capped by what the firmware
    // actually runs (11 in fft_batch_test.asm). The SAIF only needs enough
    // activity to be representative, not the whole batch -- a netlist
    // simulation of all 11 is slow and adds nothing once the pattern repeats.
    localparam integer FRAMES = (`FRAMES < 1) ? 1 :
                                (`FRAMES > 11) ? 11 : `FRAMES;
    localparam integer EXPECTED = N * FRAMES;

    reg clk = 0;
    reg resetn = 0;
    always #(10.0 / 2.0) clk = ~clk;

    `DUT_TOP uut (
        .clk    (clk),
        .resetn (resetn)
    );

    // The firmware image, absolute so it resolves from the SAIF script's own
    // scratch directory.
    initial $readmemh("/home/digital-1/SRIP2026/Updated-FFT-Processor/synth_mixed/soc_fpga_work_impl/firmware.hex", uut.u_soc.mem);

    // Count completed result stores off the PCPI handshake, exactly as
    // tb_fft_soc_perf.v does, so the run ends on real progress rather than a
    // guessed cycle budget.
    wire        p_valid = uut.u_soc.pcpi_valid;
    wire [31:0] p_insn  = uut.u_soc.pcpi_insn;
    wire        p_ready = uut.u_soc.pcpi_ready;
    wire is_store = p_valid && (p_insn[6:0] == 7'b0001011)
                            && (p_insn[14:12] == 3'b010);

    integer n_store = 0;
    reg     done_flag = 0;

    always @(posedge clk) begin
        if (resetn && !done_flag && is_store && p_ready) begin
            n_store = n_store + 1;
            if (n_store >= EXPECTED) done_flag = 1;
        end
    end

    initial begin
        resetn = 0;
        repeat (10) @(posedge clk);
        resetn = 1;
        wait (done_flag === 1'b1);
        repeat (4) @(posedge clk);
        $display("=== tb_soc_power: %0d of %0d results stored over %0d frame(s) ===",
                 n_store, EXPECTED, FRAMES);
        $finish;
    end

    // Safety net. A netlist simulation of a CPU is slow, so this is generous;
    // it reports how far it got rather than failing silently.
    initial begin
        repeat (2000000) @(posedge clk);
        if (!done_flag) begin
            $display("=== tb_soc_power TIMEOUT: %0d of %0d results stored ===",
                     n_store, EXPECTED);
            $finish;
        end
    end

endmodule
