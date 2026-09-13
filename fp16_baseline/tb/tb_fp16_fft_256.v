// End-to-end functional check for the 256-point FP16 baseline FFT.
// Loads fft_in.txt (256 lines of 32-bit binary complex FP16), runs one
// transform, and dumps the spectrum to fft_out.txt.
`timescale 1ns/1ps

module tb_fp16_fft_256;
    localparam N = 256;

    reg clk = 0;
    reg rst = 0;
    reg start = 0;
    wire done;

    reg         load_en = 0;
    reg  [7:0]  load_addr = 0;
    reg  [31:0] load_data = 0;

    reg         unload_en = 0;
    reg  [7:0]  unload_addr = 0;
    wire [31:0] unload_data;

    always #5 clk = ~clk;

    fp16_fft_256_top dut (
        .clk         (clk),
        .rst         (rst),
        .start       (start),
        .done        (done),
        .load_en     (load_en),
        .load_addr   (load_addr),
        .load_data   (load_data),
        .unload_en   (unload_en),
        .unload_addr (unload_addr),
        .unload_data (unload_data)
    );

    reg [31:0] stimulus [0:N-1];
    integer f, i, cycles;

    initial begin
        $readmemb("fft_in.txt", stimulus);

        rst = 0;
        repeat (5) @(posedge clk);
        rst = 1;
        repeat (5) @(posedge clk);

        // ---- load ----
        for (i = 0; i < N; i = i + 1) begin
            @(negedge clk);
            load_en   = 1'b1;
            load_addr = i[7:0];
            load_data = stimulus[i];
        end
        @(negedge clk);
        load_en = 1'b0;
        repeat (5) @(posedge clk);

        // ---- run ----
        @(negedge clk); start = 1'b1;
        @(negedge clk); start = 1'b0;

        cycles = 0;
        while (!done && cycles < 200000) begin
            @(posedge clk);
            cycles = cycles + 1;
        end

        if (!done) begin
            $display("TB: TIMEOUT after %0d cycles", cycles);
            $finish;
        end
        $display("TB: transform complete in %0d cycles", cycles);

        repeat (5) @(posedge clk);

        // ---- unload ----
        f = $fopen("fft_out.txt", "w");
        unload_en = 1'b1;
        for (i = 0; i < N; i = i + 1) begin
            @(negedge clk);
            unload_addr = i[7:0];
            @(negedge clk);          // 1-cycle memory read latency
            @(negedge clk);
            $fwrite(f, "%b\n", unload_data);
        end
        unload_en = 1'b0;
        $fclose(f);

        $display("TB: wrote %0d spectrum points", N);
        $finish;
    end
endmodule
