// =============================================================================
// tb_fft_soc.v
//
// Loads sim/firmware.hex into instruction/data memory, preloads a 256-point
// impulse test vector at byte address 0x1000, runs the SoC, and dumps the
// 256-word results region (byte address 0x1800) to sim/results.hex for
// out-of-band comparison (sw/check_results.py).
// =============================================================================
`timescale 1ns/1ps

module tb_fft_soc;

    reg clk = 0;
    reg resetn = 0;

    always #5 clk = ~clk; // 100 MHz

    picorv32_fft_soc #(.MEM_WORDS(4096)) dut (
        .clk    (clk),
        .resetn (resetn)
    );

    integer i;
    localparam SAMPLES_BASE_WORD = 4096 / 4;  // 1024
    localparam RESULTS_BASE_WORD = 6144 / 4;  // 1536
    localparam N = 256;

    // FP8 (E4M3-style) packed complex sample: {real[7:0], imag[7:0]}.
    // 0x38 is a recognizable nonzero mantissa/exponent pattern (treated purely
    // as a bit pattern here -- exact FP8 value doesn't matter for this test,
    // only that it's nonzero and distinguishable from the all-zero rest of
    // the vector).
    localparam [15:0] IMPULSE_SAMPLE = 16'h3800;

    initial begin
        $readmemh("sim/firmware.hex", dut.mem);

        // Impulse test vector: sample 0 nonzero, samples 1..255 = 0
        for (i = 0; i < N; i = i + 1)
            dut.mem[SAMPLES_BASE_WORD + i] = 32'h0;
        dut.mem[SAMPLES_BASE_WORD + 0] = {16'h0, IMPULSE_SAMPLE};

        // Hold reset, then release
        resetn = 0;
        repeat (10) @(posedge clk);
        resetn = 1;

        // Run long enough for the load loop (~256*6 cycles), the FFT itself
        // (~(N/2)*log2(N)+11 ~= 1035 cycles per the roadmap's latency model),
        // and the store loop (~256*6 cycles). 20000 cycles is generous slack.
        repeat (20000) @(posedge clk);

        $display("---- dumping results region ----");
        for (i = 0; i < N; i = i + 1)
            $display("result[%0d] = 0x%08h", i, dut.mem[RESULTS_BASE_WORD + i]);

        $finish;
    end

    // Safety timeout
    initial begin
        #1000000;
        $display("TIMEOUT -- simulation did not finish");
        $finish;
    end

endmodule
