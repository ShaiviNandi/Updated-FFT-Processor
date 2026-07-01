# fft_batch_test.asm
#
# Memory map (32KB / 8192 words total):
#   0x0000 (Word 0)    : program instructions
#   0x2000 (Word 2048) : input samples,  11 x 256 words
#   0x5000 (Word 5120) : output results, 11 x 256 words

_start:
    li   s11, 11             # Number of test signals
    li   s10, 0              # Current test index
    li   s1, 8192            # SAMPLES_BASE address (2048 * 4)
    li   s2, 20480           # RESULTS_BASE address (5120 * 4)

outer_loop:
    bge  s10, s11, halt      # If test index >= 11, we are done

    # 1. Load 256 samples into accelerator
    li   s0, 0               # i = 0
    li   t1, 256
loop_load:
    lw   t0, 0(s1)
    fftload x0, t0, s0
    addi s1, s1, 4
    addi s0, s0, 1
    blt  s0, t1, loop_load

    # 2. Fire FFT and wait
    fftstart
    li   a1, 8000            # generous timeout
    fftwait a0, a1

    # 3. Store 256 results to memory
    li   s0, 0
loop_store:
    fftstore t0, s0
    sw   t0, 0(s2)
    addi s2, s2, 4
    addi s0, s0, 1
    blt  s0, t1, loop_store

    # Increment test counter and repeat
    addi s10, s10, 1
    j    outer_loop

halt:
    j halt