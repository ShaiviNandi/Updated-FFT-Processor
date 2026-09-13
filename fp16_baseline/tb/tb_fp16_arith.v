// Arithmetic self-check harness for the FP16 baseline units.
// Reads operand pairs from fp16_vectors.txt (two 16-bit binary words per line)
// and writes "add sub mul" results to fp16_results.txt for checking against a
// golden model.
`timescale 1ns/1ps

module tb_fp16_arith;
    localparam NVEC = 40000;

    reg [31:0] vec [0:NVEC-1];
    reg [15:0] a, b;
    wire [15:0] r_add, r_sub, r_mul;

    fp16_add_sub u_add (.a(a), .b(b), .sub(1'b0), .out(r_add));
    fp16_add_sub u_sub (.a(a), .b(b), .sub(1'b1), .out(r_sub));
    fp16_mul     u_mul (.a(a), .b(b),             .out(r_mul));

    integer f, i;
    initial begin
        $readmemb("fp16_vectors.txt", vec);
        f = $fopen("fp16_results.txt", "w");
        for (i = 0; i < NVEC; i = i + 1) begin
            a = vec[i][31:16];
            b = vec[i][15:0];
            #1;
            $fwrite(f, "%b %b %b\n", r_add, r_sub, r_mul);
            #1;
        end
        $fclose(f);
        $display("TB: wrote %0d results", NVEC);
        $finish;
    end
endmodule
