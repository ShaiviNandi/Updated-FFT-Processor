import math
import argparse

def generate_twiddles(N, data_width, fraction_bits, filename):
    """
    Generates a hex file containing Twiddle factors for an N-point FFT.
    Twiddle factor W_N^k = e^(-j * 2 * pi * k / N)
    """
    num_twiddles = N // 2
    max_val = (1 << fraction_bits)

    print(f"Generating {num_twiddles} twiddle factors for {N}-point FFT...")
    
    with open(filename, 'w') as f:
        for k in range(num_twiddles):
            angle = -2.0 * math.pi * k / N
            
            # Real part (Cosine) and Imaginary part (Sine)
            real_val = int(round(math.cos(angle) * max_val))
            imag_val = int(round(math.sin(angle) * max_val))

            # Handle two's complement for negative values
            if real_val < 0:
                real_val = (1 << data_width) + real_val
            if imag_val < 0:
                imag_val = (1 << data_width) + imag_val

            # Combine into a single hex line (Real MSB, Imag LSB)
            combined = (real_val << data_width) | imag_val

            # Format as hex padding with leading zeros
            hex_width = (2 * data_width) // 4
            format_str = f"{{:0{hex_width}x}}\n"
            
            f.write(format_str.format(combined))
            
    print(f"Successfully wrote {filename}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Twiddle Factor ROM hex file.")
    parser.add_argument("--N", type=int, default=256, help="FFT Size (e.g., 256)")
    parser.add_argument("--width", type=int, default=16, help="Data bit width (e.g., 16)")
    parser.add_argument("--frac", type=int, default=14, help="Fractional bits for Q-format (e.g., 14 for Q2.14)")
    parser.add_argument("--out", type=str, default="twiddles.hex", help="Output filename")
    
    args = parser.parse_args()
    generate_twiddles(args.N, args.width, args.frac, args.out)