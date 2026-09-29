// =============================================================================
// MOVED -- this file is a placeholder and defines no modules.
//
// The FP16 memory now has two implementations, one per verification track:
//
//     source/mem_regarray/fp16_memory.v   register arrays  (FPGA / Vivado, BRAM)
//     source/mem_sram/fp16_memory.v       SRAM macros      (ASIC / Yosys + OpenSTA)
//
// Both define fp16_dual_bank_memory_concurrent with the same port list; the
// flow compiles exactly one, selected by --memory regarray | sram.
//
// This stub exists only because the file could not be deleted remotely (the
// local shell cannot mount this folder). It is safe to delete, and the source
// collector ignores it either way.
// =============================================================================
