`timescale 1ns / 1ps

module cordic_twiddle_generator #(
    parameter LATENCY = 10,       // Matches CORDIC_LATENCY in top generator
    parameter ADDR_WIDTH = 11     // Matches ADDR_WIDTH
)(
    input  wire                  clk,
    input  wire                  rst,
    
    input  wire [ADDR_WIDTH-1:0] k_in,
    input  wire                  valid_in,
    input  wire                  precision_sel, // 0: FP4, 1: FP8
    
    output reg  [15:0]           twiddle_out
);

    // -------------------------------------------------------------------------
    // Internal Fixed-Point Configuration (Q2.14 Format)
    // -------------------------------------------------------------------------
    localparam W = 16;
    
    // CORDIC inverse gain (1 / 1.64676) ≈ 0.60725 in Q2.14
    localparam signed [W-1:0] CORDIC_GAIN = 16'h26DD; 
    
    // Pre-computed arctangent table (in Q2.14 radians)
    // atan(2^-i) for i = 0 to 9
    wire signed [W-1:0] atan_table [0:9];
    assign atan_table[0] = 16'h3243; // atan(1)    = 0.7853 rad
    assign atan_table[1] = 16'h1DAC; // atan(0.5)  = 0.4636 rad
    assign atan_table[2] = 16'h0FAD; // atan(0.25) = 0.2449 rad
    assign atan_table[3] = 16'h07F5; // atan(0.125)= 0.1243 rad
    assign atan_table[4] = 16'h03FE; // atan(2^-4) = 0.0624 rad
    assign atan_table[5] = 16'h01FF; // atan(2^-5) = 0.0312 rad
    assign atan_table[6] = 16'h00FF; // atan(2^-6) = 0.0156 rad
    assign atan_table[7] = 16'h007F; // atan(2^-7) = 0.0078 rad
    assign atan_table[8] = 16'h003F; // atan(2^-8) = 0.0039 rad
    assign atan_table[9] = 16'h001F; // atan(2^-9) = 0.0019 rad

    // -------------------------------------------------------------------------
    // Phase Mapping
    // -------------------------------------------------------------------------
    // Twiddle factor is W_N^k = cos(-2*pi*k/N) + j*sin(-2*pi*k/N).
    // The specific mapping depends on how the AGU scales k_in. 
    // Assuming k_in is scaled such that full scale maps to -pi (or similar).
    // This is a placeholder phase mapping; adjust the shift based on your AGU's exact k_in normalization.
    wire signed [W-1:0] initial_z = -({1'b0, k_in} <<< (W - ADDR_WIDTH - 2)); 

    // -------------------------------------------------------------------------
    // Pipeline Registers
    // -------------------------------------------------------------------------
    reg signed [W-1:0] x_pipe [0:LATENCY];
    reg signed [W-1:0] y_pipe [0:LATENCY];
    reg signed [W-1:0] z_pipe [0:LATENCY];
    reg                v_pipe [0:LATENCY];
    reg                p_pipe [0:LATENCY]; // Propagate precision_sel

    // Initial state: Start at X = CORDIC_GAIN, Y = 0 to naturally scale the output
    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            x_pipe[0] <= 0;
            y_pipe[0] <= 0;
            z_pipe[0] <= 0;
            v_pipe[0] <= 0;
            p_pipe[0] <= 0;
        end else begin
            x_pipe[0] <= CORDIC_GAIN;
            y_pipe[0] <= 16'd0;
            z_pipe[0] <= initial_z;
            v_pipe[0] <= valid_in;
            p_pipe[0] <= precision_sel;
        end
    end

    // -------------------------------------------------------------------------
    // Unrolled CORDIC Micro-Rotations
    // -------------------------------------------------------------------------
    genvar i;
    generate
        for (i = 0; i < LATENCY; i = i + 1) begin : cordic_stages
            wire d = z_pipe[i][W-1]; // Direction: 1 if z is negative, 0 if positive
            
            wire signed [W-1:0] x_shifted = x_pipe[i] >>> i;
            wire signed [W-1:0] y_shifted = y_pipe[i] >>> i;
            
            wire signed [W-1:0] next_x = d ? (x_pipe[i] + y_shifted) : (x_pipe[i] - y_shifted);
            wire signed [W-1:0] next_y = d ? (y_pipe[i] - x_shifted) : (y_pipe[i] + x_shifted);
            wire signed [W-1:0] next_z = d ? (z_pipe[i] + atan_table[i]) : (z_pipe[i] - atan_table[i]);

            always @(posedge clk or negedge rst) begin
                if (!rst) begin
                    x_pipe[i+1] <= 0;
                    y_pipe[i+1] <= 0;
                    z_pipe[i+1] <= 0;
                    v_pipe[i+1] <= 0;
                    p_pipe[i+1] <= 0;
                end else begin
                    x_pipe[i+1] <= next_x;
                    y_pipe[i+1] <= next_y;
                    z_pipe[i+1] <= next_z;
                    v_pipe[i+1] <= v_pipe[i];
                    p_pipe[i+1] <= p_pipe[i];
                end
            end
        end
    endgenerate

    // -------------------------------------------------------------------------
    // Output Quantization & Packing (Fixed to FP4/FP8)
    // -------------------------------------------------------------------------
    // The final stage outputs cos(theta) and sin(theta) in Q2.14 format.
    wire signed [W-1:0] final_cos = x_pipe[LATENCY];
    wire signed [W-1:0] final_sin = y_pipe[LATENCY];

    // Placeholder wires for your specific Floating-Point converters
    wire [7:0] cos_fp8, sin_fp8;
    wire [3:0] cos_fp4, sin_fp4;

    // TODO: Instantiate or write your specific Q2.14 to FP8/FP4 conversion logic here
    // based on your exact exponent/mantissa bit-widths (e.g., E4M3, E5M2).
    assign cos_fp8 = 8'h00; // Replace with actual conversion
    assign sin_fp8 = 8'h00; 
    assign cos_fp4 = 4'h0;  
    assign sin_fp4 = 4'h0;  

    always @(posedge clk or negedge rst) begin
        if (!rst) begin
            twiddle_out <= 16'h0000;
        end else if (v_pipe[LATENCY]) begin
            if (p_pipe[LATENCY] == 1'b1) begin
                // FP8: Pack Real and Imaginary into 16 bits
                twiddle_out <= {cos_fp8, sin_fp8};
            end else begin
                // FP4: Pack Real and Imaginary (padded) into 16 bits
                twiddle_out <= {4'h0, cos_fp4, 4'h0, sin_fp4};
            end
        end
    end

endmodule