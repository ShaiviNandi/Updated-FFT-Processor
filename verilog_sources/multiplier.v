module fp4_mul(
    input [3:0] a,
    input [3:0] b,
    output [3:0] out
);
    wire sign_a = a[3], sign_b = b[3];
    wire [1:0] exp_a = a[2:1], exp_b = b[2:1];
    wire mant_a = a[0], mant_b = b[0];
    wire sign_out = sign_a ^ sign_b;
    wire a_zero = (exp_a == 2'b00 && mant_a == 1'b0);
    wire b_zero = (exp_b == 2'b00 && mant_b == 1'b0);

    wire [2:0] exp_sum = {1'b0, exp_a} + {1'b0, exp_b};
    wire signed [3:0] exp_temp = $signed({1'b0, exp_sum}) - 4'sd1;

    wire hidden_a = (exp_a != 2'b00);
    wire hidden_b = (exp_b != 2'b00);
    wire [2:0] sig_a = {1'b0, hidden_a, mant_a};
    wire [2:0] sig_b = {1'b0, hidden_b, mant_b};

    (* use_dsp = "yes" *) wire [4:0] prod = sig_a * sig_b;
    
    wire need_norm = (prod >= 5'd8);
    wire [4:0] prod_norm = (need_norm) ? (prod >> 1) : prod;
    wire signed [3:0] exp_norm = (need_norm) ? (exp_temp + 1) : exp_temp;
    wire mant_out = prod_norm[1];

    reg [3:0] output_reg;
    always @(*) begin
        if(a_zero || b_zero || exp_norm<0) begin
            output_reg = 4'b0000;
        end
        else if(exp_norm > 2'b11 || exp_norm == 2'b11 && mant_out) begin
            output_reg = {sign_out, 2'b11, 1'b1};
        end
        else begin
            output_reg = {sign_out, exp_norm[1:0], mant_out};
        end
    end
    assign out = output_reg;
endmodule

module fp4_cmul (
    input [3:0] a,
    input [3:0] b,
    input [3:0] c,
    input [3:0] d,
    output [3:0] out_real,
    output [3:0] out_imag
);
    wire [3:0] ac, bd, ad, bc;
    fp4_mul m1(.a(a), .b(c), .out(ac));
    fp4_mul m2(.a(b), .b(d), .out(bd));
    fp4_mul m3(.a(a), .b(d), .out(ad));
    fp4_mul m4(.a(b), .b(c), .out(bc));

    wire [3:0] res_real;
    wire [3:0] res_imag;
    fp4_add_sub s1(.a(ac), .b(bd), .sub(1'b1), .out(res_real));
    fp4_add_sub a1(.a(ad), .b(bc), .sub(1'b0), .out(res_imag));
    
    assign out_real = res_real;
    assign out_imag = res_imag;
endmodule

module fp8_mul(
    input [7:0] a,
    input [7:0] b,
    output [7:0] out
);
    wire sign_a = a[7], sign_b = b[7];
    wire [3:0] exp_a = a[6:3], exp_b = b[6:3];
    wire [2:0] mant_a = a[2:0], mant_b = b[2:0];
    wire sign_out = sign_a ^ sign_b;
    wire a_zero = (exp_a == 4'b0000 && mant_a == 3'b000);
    wire b_zero = (exp_b == 4'b0000 && mant_b == 3'b000);

    wire [4:0] exp_sum = {1'b0, exp_a} + {1'b0, exp_b};
    wire signed [5:0] exp_temp = $signed({1'b0, exp_sum}) - 4'sd7;

    wire hidden_a = (exp_a != 4'b0000);
    wire hidden_b = (exp_b != 4'b0000);
    wire [4:0] sig_a = {1'b0, hidden_a, mant_a};
    wire [4:0] sig_b = {1'b0, hidden_b, mant_b};

    (* use_dsp = "yes" *) wire [10:0] prod = sig_a * sig_b;
    
    wire need_norm = (prod >= 8'd128);
    wire [10:0] prod_norm = (need_norm) ? (prod >> 1) : prod;
    wire signed [5:0] exp_norm = (need_norm) ? (exp_temp + 1) : exp_temp;
    wire [2:0] mant_out = prod_norm[5:3];

    reg [7:0] output_reg;
    always @(*) begin
        if(a_zero || b_zero || exp_norm<0) begin
            output_reg = 8'h00;
        end
        else if(exp_norm > 4'b1111 || exp_norm == 4'b1111 && mant_out) begin
            output_reg = {sign_out, 4'b1111, 3'b111};
        end
        else begin
            output_reg = {sign_out, exp_norm[3:0], mant_out};
        end
    end
    assign out = output_reg;
endmodule

module fp8_cmul (
    input [7:0] a,
    input [7:0] b,
    input [7:0] c,
    input [7:0] d,
    output [7:0] out_real,
    output [7:0] out_imag
);
    wire [7:0] ac, bd, ad, bc;
    fp8_mul m1(.a(a), .b(c), .out(ac));
    fp8_mul m2(.a(b), .b(d), .out(bd));
    fp8_mul m3(.a(a), .b(d), .out(ad));
    fp8_mul m4(.a(b), .b(c), .out(bc));

    wire [7:0] res_real, res_imag;
    fp8_add_sub s1(.a(ac), .b(bd), .sub(1'b1), .out(res_real));
    fp8_add_sub a1(.a(ad), .b(bc), .sub(1'b0), .out(res_imag));
    
    assign out_real = res_real;
    assign out_imag = res_imag;
endmodule