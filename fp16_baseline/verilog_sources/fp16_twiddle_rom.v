// =============================================================================
// FP16 Twiddle Factor ROM
//
// Mirrors twiddle_factor_unified in verilog_sources/twiddle_rom.v: same
// dynamic k-scaling for runtime N, same half-plane symmetry + conjugation,
// same hardcoded -1.0 midpoint.  The stored word is 32-bit complex FP16
// instead of the 24-bit unified FP8+FP4 word, and there is no PRECISION port.
//
// ROM word format: [31:16] FP16 Real, [15:0] FP16 Imag
//
// NOTE: the file path is a parameter here (the mixed-precision ROM hardcodes
// an absolute path).  Override TWIDDLE_FILE at instantiation, or run the
// simulator from the directory holding twiddles_fp16_1024.txt.
// =============================================================================

module twiddle_factor_fp16 #(
    parameter MAX_N         = 1024,
    parameter ADDR_WIDTH    = $clog2(MAX_N) + 1,
    parameter TWIDDLE_FILE  = "twiddles_fp16_1024.txt"
)(
    input  [ADDR_WIDTH-1:0] k,            // Index k
    input  [ADDR_WIDTH-1:0] n,            // Current FFT size N
    output reg [31:0]       twiddle_out
);

    // --------------------------------------------------------
    // 1. ROM Declaration (512 entries, 32-bit each)
    // --------------------------------------------------------
    reg [31:0] rom [0:511];

    initial begin
        $readmemb(TWIDDLE_FILE, rom);
    end

    // --------------------------------------------------------
    // 2. Dynamic Scaling Logic
    // --------------------------------------------------------
    reg [ADDR_WIDTH-1:0] scaled_k;

    always @(*) begin
        case (n)
            1024: scaled_k = k;
            512:  scaled_k = {k, 1'b0};         // k * 2
            256:  scaled_k = {k, 2'b00};        // k * 4
            128:  scaled_k = {k, 3'b000};       // k * 8
            64:   scaled_k = {k, 4'b0000};      // k * 16
            32:   scaled_k = {k, 5'b00000};     // k * 32
            16:   scaled_k = {k, 6'b000000};    // k * 64
            8:    scaled_k = {k, 7'b0000000};   // k * 128
            4:    scaled_k = {k, 8'b00000000};  // k * 256
            2:    scaled_k = {k, 9'b000000000}; // k * 512
            default: scaled_k = 11'd0;
        endcase
    end

    // --------------------------------------------------------
    // 3. Symmetry Logic & Fetch
    // --------------------------------------------------------
    reg       use_conjugate;
    reg [9:0] rom_addr;       // Address within the 0-511 block
    reg       is_midpoint;

    always @(*) begin
        is_midpoint = 1'b0;

        if (scaled_k == 512) begin
            // 180 degrees (Index 512) is a boundary case
            is_midpoint   = 1'b1;
            rom_addr      = 0;
            use_conjugate = 1'b0;
        end
        else if (scaled_k > 511) begin
            // Second half (180 < angle < 360) -> Symmetry
            rom_addr      = 1024 - scaled_k;
            use_conjugate = 1'b1;
        end
        else begin
            // First half (0 <= angle < 180)
            rom_addr      = scaled_k;
            use_conjugate = 1'b0;
        end
    end

    // --------------------------------------------------------
    // 4. Output Generation
    // --------------------------------------------------------
    // Continuous assignment, NOT a reg assigned inside the conditional below.
    // Assigning it only in the else-branch (as the 24-bit unified ROM does)
    // makes synthesis infer a latch on this signal.
    wire [31:0] raw_data = rom[rom_addr];

    always @(*) begin
        if (is_midpoint) begin
            // Hardcoded -1.0 (real = -1.0 -> 0xBC00, imag = +0.0 -> 0x0000)
            twiddle_out = 32'hBC00_0000;
        end else begin
            twiddle_out = raw_data;

            // Apply conjugate (flip the sign of the imaginary part)
            if (use_conjugate) begin
                if (twiddle_out[15:0] != 16'h0000)
                    twiddle_out[15:0] = {~twiddle_out[15], twiddle_out[14:0]};
            end
        end
    end

endmodule
