#!/usr/bin/env python3
"""Generate stimulus for / check the output of the FP16 baseline FFT core."""
import struct
import sys

import numpy as np

N = 256


def bits(x):
    return struct.unpack(">H", struct.pack(">e", np.float16(x)))[0]


def val(b):
    return float(struct.unpack(">e", struct.pack(">H", b))[0])


def gen(path):
    rng = np.random.default_rng(20260913)
    # Two tones plus noise, amplitude kept low so a 256-point unscaled
    # transform stays inside the FP16 normal range.
    n = np.arange(N)
    sig = (0.30 * np.cos(2 * np.pi * 11 * n / N)
           + 0.15 * np.sin(2 * np.pi * 47 * n / N)
           + 0.02 * rng.standard_normal(N))
    x = sig.astype(np.float16).astype(np.float64)

    with open(path, "w") as f:
        for v in x:
            f.write(f"{(bits(v) << 16) | bits(0.0):032b}\n")
    np.save("/tmp/fp16run/stim.npy", x)
    print(f"wrote {N} stimulus points to {path}")


def check(path):
    x = np.load("/tmp/fp16run/stim.npy")
    golden = np.fft.fft(x)

    got = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            w = int(line, 2)
            got.append(complex(val(w >> 16), val(w & 0xFFFF)))
    got = np.array(got)

    if len(got) != N:
        print(f"FAIL: expected {N} points, got {len(got)}")
        return 1

    err = np.abs(got - golden)
    ref = np.abs(golden)
    scale = max(ref.max(), 1e-12)
    nrmse = np.sqrt(np.mean(err ** 2)) / scale
    snr = 10 * np.log10(np.sum(ref ** 2) / max(np.sum(err ** 2), 1e-30))

    print(f"peak |X| (golden)   : {ref.max():.4f}")
    print(f"max abs error       : {err.max():.5f}")
    print(f"normalised RMSE     : {nrmse:.3e}")
    print(f"SNR vs float64 FFT  : {snr:.2f} dB")

    top_golden = np.argsort(ref)[-4:]
    top_got = np.argsort(np.abs(got))[-4:]
    print(f"dominant bins golden: {sorted(top_golden.tolist())}")
    print(f"dominant bins RTL   : {sorted(top_got.tolist())}")

    ok = snr > 40.0 and sorted(top_golden.tolist()) == sorted(top_got.tolist())
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 2


if __name__ == "__main__":
    if sys.argv[1] == "gen":
        gen(sys.argv[2])
    else:
        sys.exit(check(sys.argv[2]))
