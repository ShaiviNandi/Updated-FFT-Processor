# fft_impulse_test.asm
#
# Loads a 256-point impulse test vector (sample 0 = nonzero, rest = 0) into the
# accelerator via FFTLOAD, runs the transform, and reads all 256 results back
# via FFTSTORE into a results buffer in memory.
#
# Expected result: the DFT of a unit impulse at n=0 is constant across all bins
# (X[k] = x[0] for all k), so every one of the 256 stored results should equal
# the same packed value that was loaded at index 0 -- this is the simplest
# possible hardware-in-the-loop correctness check that doesn't require porting
# the golden FFT model into this testbench.
#
# Memory map (matches tb_fft_soc.v):
#   0x0000          program (this file)
#   0x1000 (4096)   input samples,  256 x 32-bit words (lower 16 bits used)
#   0x1800 (6144)   output results, 256 x 32-bit words (lower 16 bits used)

_start:
    li   s0, 0              # index i = 0
    li   s1, 4096            # samples base address
loop_load:
    lw   t0, 0(s1)
    fftload x0, t0, s0
    addi s1, s1, 4
    addi s0, s0, 1
    li   t1, 256
    blt  s0, t1, loop_load

    fftstart

    li   a1, 8000            # generous timeout in cycles
    fftwait a0, a1

    li   s0, 0
    li   s2, 6144            # results base address
loop_store:
    fftstore t0, s0
    sw   t0, 0(s2)
    addi s2, s2, 4
    addi s0, s0, 1
    li   t1, 256
    blt  s0, t1, loop_store

halt:
    j halt
