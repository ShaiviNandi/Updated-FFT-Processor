`timescale 1ns/1ps
module fft_pcpi_wrapper (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        pcpi_valid,
    input  wire [31:0] pcpi_insn,
    input  wire [31:0] pcpi_rs1,
    input  wire [31:0] pcpi_rs2,
    output reg         pcpi_wr,
    output reg  [31:0] pcpi_rd,
    output reg         pcpi_wait,
    output reg         pcpi_ready
);
    wire is_custom0 = pcpi_valid && (pcpi_insn[6:0] == 7'b0001011);
    wire [2:0] f3 = pcpi_insn[14:12];

    reg         fft_start;
    wire        fft_done;
    reg         fft_load_en;
    reg  [7:0]  fft_load_addr;
    reg  [15:0] fft_load_data;
    reg         fft_unload_en;
    reg  [7:0]  fft_unload_addr;
    wire [15:0] fft_unload_data;

    mixed_fft_256_top u_fft (
        .clk(clk), .rst(rst_n), .start(fft_start), .done(fft_done),
        .load_en(fft_load_en), .load_addr(fft_load_addr), .load_data(fft_load_data),
        .unload_en(fft_unload_en), .unload_addr(fft_unload_addr), .unload_data(fft_unload_data)
    );

    reg status_done;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) status_done <= 0;
        else if (fft_start) status_done <= 0;
        else if (fft_done) status_done <= 1;
    end

    reg [2:0] state;
    reg [31:0] timeout;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state <= 0; pcpi_ready <= 0; pcpi_wr <= 0; pcpi_rd <= 0; pcpi_wait <= 0;
            fft_start <= 0; fft_load_en <= 0; fft_unload_en <= 0;
        end else begin
            // Default drops to prevent stalls
            pcpi_ready <= 0; pcpi_wr <= 0; pcpi_wait <= 0;
            fft_start <= 0; fft_load_en <= 0; fft_unload_en <= 0;

            if (state == 0) begin
                if (is_custom0 && !pcpi_ready) begin
                    pcpi_wait <= 1;
                    if (f3 == 3'b001) begin // LOAD: 1-cycle pulse
                        fft_load_en <= 1; fft_load_addr <= pcpi_rs2[7:0]; fft_load_data <= pcpi_rs1[15:0];
                        state <= 1; 
                    end else if (f3 == 3'b010) begin // STORE: Initiate multi-cycle hold
                        fft_unload_en <= 1; fft_unload_addr <= pcpi_rs1[7:0];
                        state <= 2; 
                    end else if (f3 == 3'b011) begin // START: 1-cycle pulse
                        fft_start <= 1; state <= 1;
                    end else if (f3 == 3'b100) begin // WAIT
                        timeout <= pcpi_rs1; state <= 5;
                    end else if (f3 == 3'b101) begin // STATUS
                        pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= {31'b0, status_done};
                    end
                end
            end
            else if (state == 1) begin // Handshake complete for 1-cycle operations
                pcpi_ready <= 1; state <= 0;
            end
            else if (state == 2) begin // STORE Pipeline Cycle 2
                pcpi_wait <= 1; fft_unload_en <= 1; fft_unload_addr <= pcpi_rs1[7:0];
                state <= 3;
            end
            else if (state == 3) begin // STORE Pipeline Cycle 3
                pcpi_wait <= 1; fft_unload_en <= 1; fft_unload_addr <= pcpi_rs1[7:0];
                state <= 4;
            end
            else if (state == 4) begin // STORE Capture Data
                fft_unload_en <= 1;               // keep unload enabled during capture
                pcpi_ready <= 1;
                pcpi_wr    <= 1;
                pcpi_rd    <= {16'b0, fft_unload_data};
                state      <= 0;
            end
            else if (state == 5) begin // Polling wait loop
                pcpi_wait <= 1;
                if (status_done) begin
                    pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= 1; state <= 0;
                end else if (timeout == 1) begin
                    pcpi_ready <= 1; pcpi_wr <= 1; pcpi_rd <= 0; state <= 0;
                end else begin
                    timeout <= timeout - 1;
                end
            end
        end
    end
endmodule
