#!/usr/bin/env python3
"""
Generate the FP16 twiddle-factor ROM contents for the FP16 baseline FFT cores.

Produces 512 lines of 32-bit binary (one per ROM entry), matching the layout
that twiddle_factor_fp16 expects:

    [31:16] FP16 real   [15:0] FP16 imag

Entry k holds W_1024^k = exp(-j*2*pi*k/1024) for k = 0 .. 511.  The RTL derives
smaller N by scaling k, and covers the second half-plane by conjugation, so
only the first 512 entries are stored -- exactly like the existing 24-bit
unified ROM in verilog_sources/twiddles_1024.txt.

Rounding is round-to-nearest-even, matching the RTL arithmetic.

Usage:
    python generate_fp16_twiddles.py --out twiddles_fp16_1024.txt
"""

import argparse
import math
import struct


def f32_to_fp16_bits(x: float) -> int:
    """IEEE 754 binary16 bit pattern for x, round-to-nearest-even."""
    packed = struct.pack(">e", x)          # ">e" is big-endian binary16
    return struct.unpack(">H", packed)[0]


def fp16_bits_to_float(bits: int) -> float:
    return struct.unpack(">e", struct.pack(">H", bits))[0]


def generate(max_n: int, out_path: str) -> None:
    entries = max_n // 2
    worst_err = 0.0

    with open(out_path, "w") as f:
        for k in range(entries):
            angle = -2.0 * math.pi * k / max_n
            re = math.cos(angle)
            im = math.sin(angle)

            re_bits = f32_to_fp16_bits(re)
            im_bits = f32_to_fp16_bits(im)

            # Normalise -0.0 to +0.0 so the ROM never stores a signed zero
            # (the RTL's conjugate logic keys off a non-zero imaginary field).
            if re_bits == 0x8000:
                re_bits = 0x0000
            if im_bits == 0x8000:
                im_bits = 0x0000

            err = max(abs(fp16_bits_to_float(re_bits) - re),
                      abs(fp16_bits_to_float(im_bits) - im))
            worst_err = max(worst_err, err)

            word = (re_bits << 16) | im_bits
            f.write(f"{word:032b}\n")

    print(f"Wrote {entries} FP16 twiddle entries to {out_path}")
    print(f"Worst-case quantisation error: {worst_err:.3e}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate the FP16 twiddle ROM.")
    ap.add_argument("--max-n", type=int, default=1024,
                    help="Largest FFT size the ROM must serve (default 1024)")
    ap.add_argument("--out", type=str, default="twiddles_fp16_1024.txt",
                    help="Output file")
    args = ap.parse_args()
    generate(args.max_n, args.out)
