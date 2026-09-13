#!/usr/bin/env python3
"""Golden-model checker for the FP16 adder / multiplier RTL.

Generates random operand pairs, then compares the RTL results against a
numpy float16 reference with the design's saturating / flush-to-zero
conventions applied.
"""
import random
import struct
import sys

import numpy as np

NVEC = 40000
MAX_NORMAL = np.float16(65504.0)


def bits(x):
    return struct.unpack(">H", struct.pack(">e", np.float16(x)))[0]


def val(b):
    return np.float16(struct.unpack(">e", struct.pack(">H", b))[0])


def is_special(b):
    """Inf/NaN -- the design does not encode these, so skip such operands."""
    return (b >> 10) & 0x1F == 0x1F


def ref(op, a, b):
    """Golden result bits, with saturate-on-overflow / flush-on-underflow."""
    with np.errstate(over="ignore", invalid="ignore", under="ignore"):
        if op == "add":
            r = np.float16(np.float32(a) + np.float32(b))
        elif op == "sub":
            r = np.float16(np.float32(a) - np.float32(b))
        else:
            r = np.float16(np.float32(a) * np.float32(b))
    if np.isinf(r):
        r = np.float16(np.copysign(MAX_NORMAL, r))
    if r == 0:
        return 0x0000  # design emits +0 for any zero result
    rb = bits(r)
    # The multiplier flushes subnormal results to zero.
    if op == "mul" and (rb >> 10) & 0x1F == 0:
        return 0x0000
    return rb


def gen_vectors(path):
    rng = random.Random(20260913)
    out = []
    while len(out) < NVEC:
        mode = rng.randrange(6)
        if mode == 0:      # full-range random bit patterns
            x, y = rng.randrange(1 << 16), rng.randrange(1 << 16)
        elif mode == 1:    # small normals, the FFT's common case
            x = bits(rng.uniform(-4, 4))
            y = bits(rng.uniform(-4, 4))
        elif mode == 2:    # near-cancellation
            x = bits(rng.uniform(-2, 2))
            y = x ^ 0x8000
        elif mode == 3:    # subnormals
            x = rng.randrange(1 << 10) | (rng.randrange(2) << 15)
            y = rng.randrange(1 << 10) | (rng.randrange(2) << 15)
        elif mode == 4:    # large magnitudes -> overflow / saturation
            x = bits(rng.uniform(-65000, 65000))
            y = bits(rng.uniform(-65000, 65000))
        else:              # wide exponent spread
            x = bits(rng.uniform(-1e-3, 1e-3))
            y = bits(rng.uniform(-1e3, 1e3))
        if is_special(x) or is_special(y):
            continue
        out.append((x, y))

    with open(path, "w") as f:
        for x, y in out:
            f.write(f"{(x << 16) | y:032b}\n")
    return out


def check(vectors, path):
    fails = {"add": 0, "sub": 0, "mul": 0}
    examples = []
    with open(path) as f:
        lines = f.read().split("\n")

    for i, (x, y) in enumerate(vectors):
        parts = lines[i].split()
        if len(parts) != 3:
            print(f"malformed line {i}: {lines[i]!r}")
            return 1
        got = {"add": int(parts[0], 2), "sub": int(parts[1], 2), "mul": int(parts[2], 2)}
        a, b = val(x), val(y)
        for op in ("add", "sub", "mul"):
            want = ref(op, a, b)
            if got[op] != want:
                fails[op] += 1
                if len(examples) < 12:
                    examples.append(
                        f"  [{op}] a={a!r}(0x{x:04x}) b={b!r}(0x{y:04x}) "
                        f"got=0x{got[op]:04x}({val(got[op])!r}) "
                        f"want=0x{want:04x}({val(want)!r})"
                    )

    total = len(vectors)
    print(f"vectors: {total}")
    for op in ("add", "sub", "mul"):
        pct = 100.0 * fails[op] / total
        print(f"  {op}: {total - fails[op]}/{total} exact  ({fails[op]} mismatches, {pct:.4f}%)")
    if examples:
        print("\nfirst mismatches:")
        print("\n".join(examples))
    return 0 if sum(fails.values()) == 0 else 2


if __name__ == "__main__":
    if sys.argv[1] == "gen":
        gen_vectors(sys.argv[2])
    else:
        vs = gen_vectors("/dev/null") if False else None
        vs = []
        with open(sys.argv[2]) as f:
            for line in f:
                line = line.strip()
                if line:
                    w = int(line, 2)
                    vs.append((w >> 16, w & 0xFFFF))
        sys.exit(check(vs, sys.argv[3]))
