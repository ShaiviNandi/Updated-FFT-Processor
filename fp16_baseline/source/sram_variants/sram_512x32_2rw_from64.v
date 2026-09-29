// =============================================================================
// sram_512x32_2rw -- WRAPPER over the existing 512x64 macro
//
// Same module name as source/sram_512x32_2rw.v, so exactly one of the two must
// be compiled. This one is selected by
//     synth/run_fp16_synthesis.py --sram-width 64
//     sim/fp16_performance_evaluator.py --sram-width 64
//
// Why it exists: the SRAM compiler output in fp32_baseline/fp32_SRAM_MACROS/
// only contains sram_512x64_2rw, which has a Liberty (and LEF/GDS). A
// width-matched 512x32 macro has not been generated yet, so OpenSTA has no
// timing or power data for it. This wrapper lets the FP16 baseline be
// synthesised and characterised TODAY against the Liberty that does exist.
//
// The cost, which must be stated wherever these numbers appear: each 64-bit
// macro stores only 32 useful bits, so FP16 memory area and memory power are
// over-counted by roughly 2x. Because the four macros are ~87-95% of total
// area in the FP32 baseline, this makes FP16 look far closer to FP32 in area
// than a real FP16 design would be. It is a pessimistic bound on FP16, not a
// measurement of it.
//
// The fix is one SRAM-compiler run: adapt
// fp32_baseline/fp32_SRAM_MACROS/sram_512x64_2rw.py with word_size = 32,
// regenerate, then drop the native model back in (default) and pass the new
// Liberty via --ram-lib.
//
// Requires fp32_baseline/source/sram_512x64_2rw.v on the compile line; the
// flow scripts add it automatically when --sram-width 64 is given. The macro
// model is reused rather than copied so there is exactly one definition of it
// in the repository.
// =============================================================================

module sram_512x32_2rw(
`ifdef USE_POWER_PINS
    vdd,
    gnd,
`endif
// Port 0: RW
    clk0,csb0,web0,addr0,din0,dout0,
// Port 1: RW
    clk1,csb1,web1,addr1,din1,dout1
  );

  parameter DATA_WIDTH = 32 ;
  parameter ADDR_WIDTH = 9 ;

`ifdef USE_POWER_PINS
    inout vdd;
    inout gnd;
`endif
  input  clk0;
  input  csb0;
  input  web0;
  input [ADDR_WIDTH-1:0]  addr0;
  input [DATA_WIDTH-1:0]  din0;
  output [DATA_WIDTH-1:0] dout0;
  input  clk1;
  input  csb1;
  input  web1;
  input [ADDR_WIDTH-1:0]  addr1;
  input [DATA_WIDTH-1:0]  din1;
  output [DATA_WIDTH-1:0] dout1;

  wire [63:0] dout0_64, dout1_64;

  sram_512x64_2rw inner (
  `ifdef USE_POWER_PINS
      .vdd(vdd), .gnd(gnd),
  `endif
      .clk0 (clk0),
      .csb0 (csb0),
      .web0 (web0),
      .addr0(addr0),
      .din0 ({32'b0, din0}),
      .dout0(dout0_64),

      .clk1 (clk1),
      .csb1 (csb1),
      .web1 (web1),
      .addr1(addr1),
      .din1 ({32'b0, din1}),
      .dout1(dout1_64)
  );

  assign dout0 = dout0_64[31:0];
  assign dout1 = dout1_64[31:0];

endmodule
