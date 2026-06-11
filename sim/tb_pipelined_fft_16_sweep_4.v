`timescale 1ns/1ps
module tb_pipelined_fft_16_sweep_4;
    reg clk; reg rst; reg start; wire done;
    reg load_en; reg [3:0] load_addr; reg [15:0] load_data;
    reg unload_en; reg [3:0] unload_addr; wire [15:0] unload_data;
    integer i, ti, out_file;
    integer cycle_count, total_cycles, load_cycles, unload_cycles_cnt;
    reg [15:0] tv [175:0];

    pipelined_fft_16_sweep_4_top dut (
        .clk(clk), .rst(rst), .start(start), .done(done),
        .load_en(load_en), .load_addr(load_addr), .load_data(load_data),
        .unload_en(unload_en), .unload_addr(unload_addr), .unload_data(unload_data)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    initial begin
        #39820;
        $display("WATCHDOG TIMEOUT");
        $finish;
    end

    initial begin : STIM
        integer wait_cnt;
        tv[0] = 16'h3600;
        tv[1] = 16'h0000;
        tv[2] = 16'h0000;
        tv[3] = 16'h0000;
        tv[4] = 16'h0000;
        tv[5] = 16'h0000;
        tv[6] = 16'h0000;
        tv[7] = 16'h0000;
        tv[8] = 16'h0000;
        tv[9] = 16'h0000;
        tv[10] = 16'h0000;
        tv[11] = 16'h0000;
        tv[12] = 16'h0000;
        tv[13] = 16'h0000;
        tv[14] = 16'h0000;
        tv[15] = 16'h0000;
        tv[16] = 16'h3600;
        tv[17] = 16'h3200;
        tv[18] = 16'h8000;
        tv[19] = 16'hb200;
        tv[20] = 16'hb600;
        tv[21] = 16'hb200;
        tv[22] = 16'h0000;
        tv[23] = 16'h3200;
        tv[24] = 16'h3600;
        tv[25] = 16'h3200;
        tv[26] = 16'h8000;
        tv[27] = 16'hb200;
        tv[28] = 16'hb600;
        tv[29] = 16'hb200;
        tv[30] = 16'h0000;
        tv[31] = 16'h3200;
        tv[32] = 16'h3600;
        tv[33] = 16'h0031;
        tv[34] = 16'h8080;
        tv[35] = 16'h0027;
        tv[36] = 16'h8000;
        tv[37] = 16'h0027;
        tv[38] = 16'h8000;
        tv[39] = 16'h8031;
        tv[40] = 16'hb600;
        tv[41] = 16'h80b1;
        tv[42] = 16'h0000;
        tv[43] = 16'h80a7;
        tv[44] = 16'h0000;
        tv[45] = 16'h80a7;
        tv[46] = 16'h0080;
        tv[47] = 16'h80b1;
        tv[48] = 16'h3600;
        tv[49] = 16'h3623;
        tv[50] = 16'h3232;
        tv[51] = 16'ha336;
        tv[52] = 16'hb680;
        tv[53] = 16'h23b6;
        tv[54] = 16'h3232;
        tv[55] = 16'hb6a3;
        tv[56] = 16'h3600;
        tv[57] = 16'hb6a3;
        tv[58] = 16'h3232;
        tv[59] = 16'h23b6;
        tv[60] = 16'hb680;
        tv[61] = 16'ha336;
        tv[62] = 16'h3232;
        tv[63] = 16'h3623;
        tv[64] = 16'h3600;
        tv[65] = 16'h8036;
        tv[66] = 16'hb680;
        tv[67] = 16'h00b6;
        tv[68] = 16'h3600;
        tv[69] = 16'h8036;
        tv[70] = 16'hb680;
        tv[71] = 16'h00b6;
        tv[72] = 16'h3600;
        tv[73] = 16'h8036;
        tv[74] = 16'hb680;
        tv[75] = 16'h00b6;
        tv[76] = 16'h3600;
        tv[77] = 16'h8036;
        tv[78] = 16'hb680;
        tv[79] = 16'h00b6;
        tv[80] = 16'h3600;
        tv[81] = 16'h3600;
        tv[82] = 16'h3600;
        tv[83] = 16'h3600;
        tv[84] = 16'h3600;
        tv[85] = 16'h3600;
        tv[86] = 16'h3600;
        tv[87] = 16'h3600;
        tv[88] = 16'hb600;
        tv[89] = 16'hb600;
        tv[90] = 16'hb600;
        tv[91] = 16'hb600;
        tv[92] = 16'hb600;
        tv[93] = 16'hb600;
        tv[94] = 16'hb600;
        tv[95] = 16'hb600;
        tv[96] = 16'h0000;
        tv[97] = 16'h0000;
        tv[98] = 16'h0000;
        tv[99] = 16'h1200;
        tv[100] = 16'h1f00;
        tv[101] = 16'h2900;
        tv[102] = 16'h3100;
        tv[103] = 16'h3500;
        tv[104] = 16'h3600;
        tv[105] = 16'h3500;
        tv[106] = 16'h3100;
        tv[107] = 16'h2900;
        tv[108] = 16'h1f00;
        tv[109] = 16'h1200;
        tv[110] = 16'h0000;
        tv[111] = 16'h0000;
        tv[112] = 16'h0000;
        tv[113] = 16'h0000;
        tv[114] = 16'h0000;
        tv[115] = 16'h0000;
        tv[116] = 16'hb680;
        tv[117] = 16'hb2b2;
        tv[118] = 16'h00b6;
        tv[119] = 16'h32b2;
        tv[120] = 16'h3600;
        tv[121] = 16'h3232;
        tv[122] = 16'h8036;
        tv[123] = 16'hb232;
        tv[124] = 16'h0000;
        tv[125] = 16'h0000;
        tv[126] = 16'h0000;
        tv[127] = 16'h0000;
        tv[128] = 16'h3600;
        tv[129] = 16'h352c;
        tv[130] = 16'h3132;
        tv[131] = 16'h2934;
        tv[132] = 16'h8035;
        tv[133] = 16'ha934;
        tv[134] = 16'hb132;
        tv[135] = 16'hb52c;
        tv[136] = 16'hb680;
        tv[137] = 16'hb5ac;
        tv[138] = 16'hb1b2;
        tv[139] = 16'ha9b4;
        tv[140] = 16'h00b5;
        tv[141] = 16'h29b4;
        tv[142] = 16'h31b2;
        tv[143] = 16'h35ac;
        tv[144] = 16'h3600;
        tv[145] = 16'h3600;
        tv[146] = 16'h3600;
        tv[147] = 16'h3600;
        tv[148] = 16'h3600;
        tv[149] = 16'hb600;
        tv[150] = 16'hb600;
        tv[151] = 16'h3600;
        tv[152] = 16'h3600;
        tv[153] = 16'hb600;
        tv[154] = 16'h3600;
        tv[155] = 16'hb600;
        tv[156] = 16'h3600;
        tv[157] = 16'h0000;
        tv[158] = 16'h0000;
        tv[159] = 16'h0000;
        tv[160] = 16'h1900;
        tv[161] = 16'h1a1a;
        tv[162] = 16'h8026;
        tv[163] = 16'ha828;
        tv[164] = 16'hb180;
        tv[165] = 16'hafaf;
        tv[166] = 16'h00b5;
        tv[167] = 16'h32b2;
        tv[168] = 16'h3600;
        tv[169] = 16'h3131;
        tv[170] = 16'h8033;
        tv[171] = 16'hac2c;
        tv[172] = 16'hac80;
        tv[173] = 16'ha2a2;
        tv[174] = 16'h009e;
        tv[175] = 16'h1595;
        out_file = $fopen("C:/Updated-FFT-Processor/sim/pipelined_fft_16_sweep_4_output.txt", "w");
        rst = 0; start = 0; load_en = 0; load_addr = 0; load_data = 0; unload_en = 0; unload_addr = 0; total_cycles = 0;
        repeat(8) @(posedge clk);
        rst = 1;
        repeat(4) @(posedge clk);

        $display("\n================================================================");
        $display("  Pipelined Run Report  --  pipelined_fft_16_sweep_4 (FFT-16)");
        $display("================================================================");

        for (ti = 0; ti < 11; ti = ti + 1) begin
            @(posedge clk);
            load_cycles = 0; load_en = 1;
            for (i = 0; i < 16; i = i + 1) begin
                load_addr = i[3:0]; load_data = tv[ti*16 + i];
                @(posedge clk); load_cycles = load_cycles + 1;
            end
            load_en = 0;
            @(posedge clk); load_cycles = load_cycles + 1;

            cycle_count = 0; start = 1;
            @(posedge clk); start = 0;
            cycle_count = cycle_count + 1;

            wait_cnt = 0;
            while (!done && wait_cnt < 610) begin
                @(posedge clk); cycle_count = cycle_count + 1; wait_cnt = wait_cnt + 1;
            end
            @(posedge clk);

            unload_cycles_cnt = 0; unload_en = 1;
            for (i = 0; i < 16; i = i + 1) begin
                unload_addr = i[3:0];
                @(posedge clk); unload_cycles_cnt = unload_cycles_cnt + 1;
                @(posedge clk); unload_cycles_cnt = unload_cycles_cnt + 1;
                @(posedge clk); unload_cycles_cnt = unload_cycles_cnt + 1;
                $fwrite(out_file, "%04h\n", unload_data);
            end
            unload_en = 0;
            repeat(2) @(posedge clk);
            
            // Log explicitly to console for python wrapper
            total_cycles = total_cycles + load_cycles + cycle_count + unload_cycles_cnt;
            $display("  -> Test %0d | Exec Cycles: %0d | Load: %0d | Unload: %0d", ti, cycle_count, load_cycles, unload_cycles_cnt);
        end
        $display("================================================================");
        $display("FINAL_METRICS | Total Cycles: %0d", total_cycles);
        $display("================================================================");
        
        $fclose(out_file);
        $finish;
    end
endmodule
