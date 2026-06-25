`timescale 1ns/1ps
module tb_pipelined_fft_8_sweep_2;
    reg clk; reg rst; reg start; wire done;
    reg load_en; reg [2:0] load_addr; reg [15:0] load_data;
    reg unload_en; reg [2:0] unload_addr; wire [15:0] unload_data;
    integer i, ti, out_file;
    integer cycle_count, total_cycles, load_cycles, unload_cycles_cnt;
    reg [15:0] tv [87:0];

    pipelined_fft_8_sweep_2_top dut (
        .clk(clk), .rst(rst), .start(start), .done(done),
        .load_en(load_en), .load_addr(load_addr), .load_data(load_data),
        .unload_en(unload_en), .unload_addr(unload_addr), .unload_data(unload_data)
    );

    initial clk = 0;
    always #5 clk = ~clk;

    initial begin
        #32340;
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
        tv[8] = 16'h3600;
        tv[9] = 16'h3200;
        tv[10] = 16'h8000;
        tv[11] = 16'hb200;
        tv[12] = 16'hb600;
        tv[13] = 16'hb200;
        tv[14] = 16'h0000;
        tv[15] = 16'h3200;
        tv[16] = 16'h3600;
        tv[17] = 16'h0032;
        tv[18] = 16'h8000;
        tv[19] = 16'h0032;
        tv[20] = 16'hb680;
        tv[21] = 16'h00b2;
        tv[22] = 16'h8000;
        tv[23] = 16'h00b2;
        tv[24] = 16'h3600;
        tv[25] = 16'h352b;
        tv[26] = 16'h8036;
        tv[27] = 16'hb5ab;
        tv[28] = 16'h3600;
        tv[29] = 16'hb5ab;
        tv[30] = 16'h8036;
        tv[31] = 16'h352b;
        tv[32] = 16'h3600;
        tv[33] = 16'h8036;
        tv[34] = 16'hb680;
        tv[35] = 16'h00b6;
        tv[36] = 16'h3600;
        tv[37] = 16'h8036;
        tv[38] = 16'hb680;
        tv[39] = 16'h00b6;
        tv[40] = 16'h3600;
        tv[41] = 16'h3600;
        tv[42] = 16'h3600;
        tv[43] = 16'h3600;
        tv[44] = 16'hb600;
        tv[45] = 16'hb600;
        tv[46] = 16'hb600;
        tv[47] = 16'hb600;
        tv[48] = 16'h0000;
        tv[49] = 16'h0000;
        tv[50] = 16'h1f00;
        tv[51] = 16'h3100;
        tv[52] = 16'h3600;
        tv[53] = 16'h3100;
        tv[54] = 16'h1f00;
        tv[55] = 16'h0000;
        tv[56] = 16'h0000;
        tv[57] = 16'h0000;
        tv[58] = 16'h8036;
        tv[59] = 16'hb232;
        tv[60] = 16'hb680;
        tv[61] = 16'hb2b2;
        tv[62] = 16'h0000;
        tv[63] = 16'h0000;
        tv[64] = 16'h3600;
        tv[65] = 16'h3132;
        tv[66] = 16'h8035;
        tv[67] = 16'hb132;
        tv[68] = 16'hb680;
        tv[69] = 16'hb1b2;
        tv[70] = 16'h00b5;
        tv[71] = 16'h31b2;
        tv[72] = 16'h3600;
        tv[73] = 16'h3600;
        tv[74] = 16'h3600;
        tv[75] = 16'h3600;
        tv[76] = 16'h3600;
        tv[77] = 16'hb600;
        tv[78] = 16'hb600;
        tv[79] = 16'h3600;
        tv[80] = 16'h1a00;
        tv[81] = 16'h2323;
        tv[82] = 16'h8032;
        tv[83] = 16'hb232;
        tv[84] = 16'hb680;
        tv[85] = 16'haeae;
        tv[86] = 16'h00a7;
        tv[87] = 16'h1696;
        out_file = $fopen("/home/digital-1/SRIP2026/Updated-FFT-Processor/sim/pipelined_fft_8_sweep_2_output.txt", "w");
        rst = 0; start = 0; load_en = 0; load_addr = 0; load_data = 0; unload_en = 0; unload_addr = 0; total_cycles = 0;
        repeat(8) @(posedge clk);
        rst = 1;
        repeat(4) @(posedge clk);

        $display("\n================================================================");
        $display("  Pipelined Run Report  --  pipelined_fft_8_sweep_2 (FFT-8)");
        $display("================================================================");

        for (ti = 0; ti < 11; ti = ti + 1) begin
            @(posedge clk);
            load_cycles = 0; load_en = 1;
            for (i = 0; i < 8; i = i + 1) begin
                load_addr = i[2:0]; load_data = tv[ti*8 + i];
                @(posedge clk); load_cycles = load_cycles + 1;
            end
            load_en = 0;
            @(posedge clk); load_cycles = load_cycles + 1;

            cycle_count = 0; start = 1;
            @(posedge clk); start = 0;
            cycle_count = cycle_count + 1;

            wait_cnt = 0;
            while (!done && wait_cnt < 582) begin
                @(posedge clk); cycle_count = cycle_count + 1; wait_cnt = wait_cnt + 1;
            end
            @(posedge clk);

            unload_cycles_cnt = 0; unload_en = 1;
            for (i = 0; i < 8; i = i + 1) begin
                unload_addr = i[2:0];
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
