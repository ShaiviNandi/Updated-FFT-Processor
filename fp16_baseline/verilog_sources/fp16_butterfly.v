// =============================================================================
// FP16 Radix-2 DIT Butterfly
//
// Memory / datapath format: 32-bit complex FP16
//   [31:16] FP16 Real, [15:0] FP16 Imag
//
// Mirrors fp8_butterfly_generation_unit in verilog_sources/butterfly.v.
// This is a single-precision baseline, so there are no precision-select
// inputs and no format converters in the datapath -- that muxing overhead
// belongs to the mixed-precision design and is deliberately absent here.
//
//   X = A + W*B
//   Y = A - W*B
// =============================================================================

module fp16_butterfly_generation_unit(
    input  [31:0] A,
    input  [31:0] B,
    input  [31:0] W,
    output [31:0] X,
    output [31:0] Y
);

    // step 1: complex multiplication  W * B
    wire [31:0] wb_product;

    fp16_cmul cmul_inst(
        .a        (B[31:16]),  // real part of B
        .b        (B[15:0]),   // imag part of B
        .c        (W[31:16]),  // real part of W
        .d        (W[15:0]),   // imag part of W
        .out_real (wb_product[31:16]),
        .out_imag (wb_product[15:0])
    );

    // step 2: complex addition
    fp16_complex_add_sub adder_inst(
        .a   (A),
        .b   (wb_product),
        .sub (1'b0),
        .out (X)
    );

    // step 3: complex subtraction
    fp16_complex_add_sub sub_inst(
        .a   (A),
        .b   (wb_product),
        .sub (1'b1),
        .out (Y)
    );

endmodule


// -----------------------------------------------------------------------------
// Thin wrapper kept for structural parity with butterfly_wrapper in
// verilog_sources/mixed_precision_wrappers.v, so the FP16 core instantiates
// a like-named block.  No precision plumbing: the baseline is FP16 throughout.
// -----------------------------------------------------------------------------
module fp16_butterfly_wrapper (
    input  [31:0] A, B,
    input  [31:0] W,
    output [31:0] X, Y
);
    fp16_butterfly_generation_unit bf (
        .A (A),
        .B (B),
        .W (W),
        .X (X),
        .Y (Y)
    );
endmodule
