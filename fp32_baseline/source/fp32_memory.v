// =============================================================================
// MOVED -- this file is a placeholder and defines no modules.
//
// The FP32 memory now has two implementations, one per verification track:
//
//     source/mem_regarray/fp32_memory.v   register arrays  (FPGA / Vivado, BRAM)
//     source/mem_sram/fp32_memory.v       SRAM macros      (ASIC / Yosys + OpenSTA)
//
// Both define fp32_dual_bank_memory_concurrent with the same port list; the
// flow compiles exactly one, selected by --memory regarray | sram.
// --memory sram is the default and reproduces the published fp32_ppa_report.txt.
//
// This stub exists only because the file could not be deleted remotely (the
// local shell cannot mount this folder). Safe to delete; the source collector
// ignores it either way.
// =============================================================================
