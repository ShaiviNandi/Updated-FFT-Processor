// =============================================================================
// tb_fft_power.v -- activity-generating testbench for SAIF-based power analysis
//
// PURPOSE
//   This testbench exists to make `report_power` a MEASUREMENT instead of a
//   vectorless guess. It drives a representative workload through the FFT core
//   so that xsim can record real switching activity into a SAIF, which
//   vivado_synthesis_v2.tcl then reads.
//
//   It is NOT a correctness testbench. It checks the handshake and flags
//   stalls, but it does not verify transform results - use the existing
//   functional TB for that.
//
// PARAMETERISATION
//   The DUT module name and the transform size differ per chromosome, so both
//   arrive as compile-time defines (xvlog/iverilog -d):
//
//       -d DUT_TOP=probe_fp8x4_top  -d FFT_N=256  [-d FRAMES=4]
//
//   ADDR_W is derived from FFT_N with $clog2, matching the generated top's
//   load_addr / unload_addr width.
//
// STIMULUS
//   Pseudo-random FP8 E4M3 pairs with the exponent constrained to 4..10, i.e.
//   well-scaled normal values. Exponent 15 (NaN/Inf) and 0 (zero/subnormal)
//   are avoided so the datapath sees representative, non-degenerate activity
//   rather than the short-circuit path in fp8_mul. The generator is a fixed
//   LCG, so the SAIF is reproducible across designs - which matters, because
//   two chromosomes can only be compared on power if they saw identical data.
//
// SAIF WINDOW
//   Frame 0 is a warm-up. `saif_window` goes high for frames 1..FRAMES-1, so
//   the script can gate logging on it and exclude reset and load transients:
//
//       log_saif [get_objects -r /tb_fft_power/uut/*]
//
//   (Gating on the signal is optional; the warm-up is short relative to the
//   measured frames either way.)
//
// Run standalone (Icarus) to sanity-check the handshake:
//   iverilog -g2012 -DDUT_TOP=<top> -DFFT_N=256 -o tb.vvp <sources> tb_fft_power.v
// =============================================================================

`timescale 1ns/1ps

`ifndef DUT_TOP
  `define DUT_TOP fft_top
`endif
`ifndef FFT_N
  `define FFT_N 256
`endif
`ifndef FRAMES
  `define FRAMES 4
`endif

module tb_fft_power;

    localparam integer N       = `FFT_N;
    localparam integer ADDR_W  = (N <= 2) ? 1 : $clog2(N);
    localparam integer FRAMES  = `FRAMES;
    localparam integer CLK_NS  = 10;          // 100 MHz stimulus clock
    localparam integer TIMEOUT = 200000;      // cycles before we give up

    reg                 clk = 1'b0;
    reg                 rst = 1'b0;           // ACTIVE LOW (matches the DUT)
    reg                 start = 1'b0;

    reg                 load_en = 1'b0;
    reg  [ADDR_W-1:0]   load_addr = {ADDR_W{1'b0}};
    reg  [15:0]         load_data = 16'h0000;

    reg                 unload_en = 1'b0;
    reg  [ADDR_W-1:0]   unload_addr = {ADDR_W{1'b0}};
    wire [15:0]         unload_data;

    wire                done;

    reg                 saif_window = 1'b0;   // high during the measured frames
    integer             frame, i, guard;
    integer             nonzero_out;

    // -------------------------------------------------------------------------
    // Clock
    // -------------------------------------------------------------------------
    always #(CLK_NS/2) clk = ~clk;

    // -------------------------------------------------------------------------
    // DUT. Instance MUST be named `uut` - generate_saif.tcl scopes log_saif to
    // /tb_fft_power/uut/*.
    // -------------------------------------------------------------------------
    `DUT_TOP uut (
        .clk        (clk),
        .rst        (rst),
        .start      (start),
        .done       (done),
        .load_en    (load_en),
        .load_addr  (load_addr),
        .load_data  (load_data),
        .unload_en  (unload_en),
        .unload_addr(unload_addr),
        .unload_data(unload_data)
    );

    // -------------------------------------------------------------------------
    // Reproducible FP8 E4M3 pair generator.
    //   bit  [15:8] = real part {sign, exp[3:0], mant[2:0]}
    //   bit  [ 7:0] = imag part
    // Exponent held in 4..10 so every operand is a well-scaled normal number.
    // -------------------------------------------------------------------------
    reg [63:0] lfsr = 64'h1234_5678_9ABC_DEF0;

    function [63:0] lcg_next;
        input [63:0] s;
        begin
            lcg_next = s * 64'd6364136223846793005 + 64'd1442695040888963407;
        end
    endfunction

    function [7:0] rand_fp8;
        input [63:0] s;
        reg          sgn;
        reg [3:0]    exp;
        reg [2:0]    mant;
        begin
            sgn      = s[40];
            exp      = 4'd4 + (s[43:41] % 4'd7);   // 4 .. 10
            mant     = s[46:44];
            rand_fp8 = {sgn, exp, mant};
        end
    endfunction

    task next_sample;
        begin
            lfsr      = lcg_next(lfsr);
            load_data[15:8] = rand_fp8(lfsr);
            lfsr      = lcg_next(lfsr);
            load_data[7:0]  = rand_fp8(lfsr);
        end
    endtask

    // -------------------------------------------------------------------------
    // One frame: load N samples, run, read N results back.
    // -------------------------------------------------------------------------
    task run_frame;
        input integer fno;
        begin
            // ---- load ----
            @(negedge clk);
            load_en = 1'b1;
            for (i = 0; i < N; i = i + 1) begin
                load_addr = i[ADDR_W-1:0];
                next_sample;
                @(negedge clk);
            end
            load_en   = 1'b0;
            load_data = 16'h0000;
            @(negedge clk);

            // ---- run ----
            start = 1'b1;
            @(negedge clk);
            start = 1'b0;

            guard = 0;
            while (done !== 1'b1 && guard < TIMEOUT) begin
                @(posedge clk);
                guard = guard + 1;
            end
            if (guard >= TIMEOUT) begin
                $display("ERROR: frame %0d timed out after %0d cycles - the SAIF",
                         fno, TIMEOUT);
                $display("ERROR: would be unusable. Check the start/done handshake.");
                $finish;
            end
            $display("frame %0d: done after %0d cycles%s",
                     fno, guard, (fno == 0) ? " (warm-up, excluded)" : "");

            // ---- unload (exercises the read path and the output converters) ----
            @(negedge clk);
            unload_en = 1'b1;
            nonzero_out = 0;
            for (i = 0; i < N; i = i + 1) begin
                unload_addr = i[ADDR_W-1:0];
                @(negedge clk);
                if (unload_data !== 16'h0000) nonzero_out = nonzero_out + 1;
            end
            unload_en = 1'b0;
            @(negedge clk);

            if (nonzero_out == 0) begin
                $display("WARNING: frame %0d read back all zeros - SAIF activity", fno);
                $display("WARNING: will be unrepresentative. Check the load protocol.");
            end
        end
    endtask

    // -------------------------------------------------------------------------
    initial begin
        $display("=== tb_fft_power : N=%0d  ADDR_W=%0d  frames=%0d ===",
                 N, ADDR_W, FRAMES);

        // reset (active low)
        rst = 1'b0;
        repeat (8) @(negedge clk);
        rst = 1'b1;
        repeat (4) @(negedge clk);

        for (frame = 0; frame < FRAMES; frame = frame + 1) begin
            saif_window = (frame != 0);       // frame 0 is warm-up
            run_frame(frame);
        end
        saif_window = 1'b0;

        repeat (4) @(negedge clk);
        $display("=== tb_fft_power complete: %0d measured frame(s) ===", FRAMES - 1);
        $finish;
    end

endmodule
