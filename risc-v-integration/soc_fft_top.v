// =============================================================================
// soc_fft_top.v
//
// Synthesis wrapper so the whole PicoRV32 + FFT SoC can go through
// vivado_synthesis_v2.tcl unmodified.
//
// WHY IT EXISTS
//   Two reasons, both about the flow rather than the design.
//
//   1. v2 derives its top module as "${design_name}_top". The SoC's top is
//      called picorv32_fft_soc, which can never match that pattern, so passing
//      design_name=soc_fft and this wrapper as the top file is what lets the SoC
//      use the same script the three precision tracks use. No TCL is forked.
//
//   2. picorv32_fft_soc has NO output ports -- only clk and resetn. Every signal
//      inside is therefore unobservable from the boundary, and synthesis is
//      entitled to delete the entire design: the CPU drives a memory nothing
//      reads, so nothing is required to exist. DONT_TOUCH on the instance is
//      what stops that.
//
//      This is the failure mode to watch for. A LUT count in the low hundreds,
//      or BRAM at zero, means the subsystem was optimised away and the numbers
//      describe an empty shell rather than a SoC. run_soc_fpga.py refuses to
//      report when the count falls below a plausibility floor, but check the
//      utilisation report yourself the first time.
//
//   The alternative -- adding observability ports to picorv32_fft_soc -- would
//   change the design being measured and modify a file the RISC-V integration
//   depends on. The attribute does not.
//
// MEM_WORDS
//   8192 words (32 KB) matches what tb_fft_soc.v and tb_fft_soc_perf.v
//   instantiate, which is what the 11 x 256 batch firmware needs. The module's
//   own default is 4096, so leaving it unset would synthesise a different, and
//   smaller, memory than the one the throughput was measured on.
//
// STATUS: NOT SYNTHESISED. Elaborates and simulates under Icarus Verilog
//   against the same firmware the throughput run uses. The DONT_TOUCH behaviour
//   and the BRAM inference on the 32 KB memory are both Vivado-specific and
//   unverified; the first real run settles them.
// =============================================================================
`timescale 1ns/1ps

`ifndef SOC_MEM_WORDS
  `define SOC_MEM_WORDS 8192
`endif

module soc_fft_top (
    input wire clk,
    input wire resetn
);

    // DONT_TOUCH: with no outputs on the boundary there is nothing to preserve
    // this subsystem, so without it synthesis may remove all of it.
    (* DONT_TOUCH = "yes", KEEP_HIERARCHY = "yes" *)
    picorv32_fft_soc #(
        .MEM_WORDS (`SOC_MEM_WORDS)
    ) u_soc (
        .clk    (clk),
        .resetn (resetn)
    );

endmodule
