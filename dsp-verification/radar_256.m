%% =========================================================================
%  FFT Analysis: DUT vs Golden Reference
%  11 Test Signals — 256-Point Complex FFT
%  =========================================================================
%
%  DATA FORMAT:
%  File format (per line):
%    4 hex chars = 16-bit word
%    bits [15:8] = FP8 E4M3 Real part
%    bits [7:0]  = FP8 E4M3 Imaginary part
%
%  FP8 E4M3 (OCP standard):
%    bit 7     : sign
%    bits [6:3]: exponent (bias = 7)
%    bits [2:0]: mantissa
%
%  Signals (256 complex samples each, 2816 lines total per file):
%    0  Impulse                   6  Gaussian Pulse
%    1  Single Tone               7  Radar Pulsed Sinusoid
%    2  Multi-Tone                8  Radar Clutter + Target
%    3  Chirp (LFM)               9  Radar Barker-13 Pulse
%    4  Sinusoid (complex)       10  Radar Doppler Burst
%    5  Step Function
% =========================================================================

clear; clc; close all;

%% -------------------------------------------------------------------------
%  USER SETTINGS
% -------------------------------------------------------------------------
INPUT_FILE  = 'fft_input.txt';
OUTPUT_FILE = 'fft_output.txt';

N        = 256;    % FFT / signal length updated for 256-point core
NUM_SIG  = 11;     % number of test signals
Fs       = 1.0;    % normalised sample rate; change to actual Fs (Hz) if known

SIGNAL_NAMES = { ...
    'Impulse', ...
    'Single Tone', ...
    'Multi-Tone', ...
    'Chirp (LFM)', ...
    'Sinusoid (complex)', ...
    'Step Function', ...
    'Gaussian Pulse', ...
    'Radar Pulsed Sinusoid', ...
    'Radar Clutter + Target', ...
    'Radar Barker-13 Pulse', ...
    'Radar Doppler Burst' };

%% -------------------------------------------------------------------------
%  BUILD FP8 E4M3 LOOKUP TABLE  
% -------------------------------------------------------------------------
FP8_LUT = build_fp8_e4m3_lut();   % 256-element double vector, index 0..255

%% -------------------------------------------------------------------------
%  LOAD AND DECODE DATA
% -------------------------------------------------------------------------
fprintf('Loading %s ...\n', INPUT_FILE);
[in_re, in_im]   = load_fp8_file(INPUT_FILE,  FP8_LUT, N, NUM_SIG);

fprintf('Loading %s ...\n', OUTPUT_FILE);
[out_re, out_im] = load_fp8_file(OUTPUT_FILE, FP8_LUT, N, NUM_SIG);

% Assemble complex matrices  (N x NUM_SIG)
x_in  = in_re  + 1i * in_im;
x_dut = out_re + 1i * out_im;

%% -------------------------------------------------------------------------
%  GOLDEN REFERENCE  (MATLAB double-precision FFT of decoded input)
% -------------------------------------------------------------------------
x_golden_mag  = zeros(N, NUM_SIG);
x_golden_cplx = zeros(N, NUM_SIG, 'like', 1i);

for s = 1:NUM_SIG
    gf = fft(x_in(:,s), N);
    x_golden_cplx(:,s) = gf;
    x_golden_mag(:,s)  = abs(gf);
end

%% -------------------------------------------------------------------------
%  PER-SIGNAL ERROR METRICS
% -------------------------------------------------------------------------
fprintf('\n%-5s %-28s %10s %10s %10s\n', ...
        'Sig#', 'Signal Name', 'NRMSE', 'SNR(dB)', 'Peak Err');
fprintf('%s\n', repmat('-',1,68));

nrmse_all = zeros(1, NUM_SIG);
snr_all   = zeros(1, NUM_SIG);

for s = 1:NUM_SIG
    gm = x_golden_mag(:,s);
    dm = abs(x_dut(:,s));

    gm_max = max(gm); dm_max = max(dm);
    if gm_max > 0 && dm_max > 0
        gm_s = gm * (dm_max / gm_max);
    else
        gm_s = gm;
    end

    err = gm_s - dm;
    nrmse_all(s) = sqrt(mean(err.^2)) / (dm_max + eps);
    snr_all(s)   = 10*log10(sum(gm_s.^2) / (sum(err.^2) + eps));

    fprintf('  %2d  %-28s %10.4f %10.2f dB %10.4f\n', ...
            s-1, SIGNAL_NAMES{s}, nrmse_all(s), snr_all(s), max(abs(err)));
end

%% =========================================================================
%  SECTION 1 — TIME DOMAIN
%% =========================================================================
fprintf('\n[1/6] Time-domain plots ...\n');
t = (0:N-1);

for s = 1:NUM_SIG
    hf = figure('Name', sprintf('[Time] %d: %s', s-1, SIGNAL_NAMES{s}), ...
                'NumberTitle','off','Color','w','Position', [80 80 1000 420]);

    % Real Part
    subplot(1,2,1);
    plot(t, real(x_in(:,s)), 'b-', 'LineWidth', 1.2);
    grid on; xlim([0 N-1]);
    xlabel('Sample Index', 'FontSize', 11, 'Color', 'k'); 
    ylabel('Amplitude', 'FontSize', 11, 'Color', 'k');
    title('Real Part', 'FontSize', 12, 'FontWeight', 'bold');
    set(gca, 'FontSize', 10, 'Color', 'w', 'XColor', 'k', 'YColor', 'k', 'GridAlpha', 0.15);

    % Imaginary Part
    subplot(1,2,2);
    plot(t, imag(x_in(:,s)), 'r-', 'LineWidth', 1.2);
    grid on; xlim([0 N-1]);
    xlabel('Sample Index', 'FontSize', 11, 'Color', 'k'); 
    ylabel('Amplitude', 'FontSize', 11, 'Color', 'k');
    title('Imaginary Part', 'FontSize', 12, 'FontWeight', 'bold');
    set(gca, 'FontSize', 10, 'Color', 'w', 'XColor', 'k', 'YColor', 'k', 'GridAlpha', 0.15);

    sgtitle(sprintf('Time Domain: %s (N=256)', SIGNAL_NAMES{s}), 'FontSize', 14, 'FontWeight', 'bold');
    saveas(hf, sprintf('time_sig%02d_%s.png', s-1, safe_name(SIGNAL_NAMES{s})));
end

%% =========================================================================
%  SECTION 2 — FREQUENCY DOMAIN
%% =========================================================================
fprintf('[2/6] Frequency-domain spectra ...\n');
freq_axis = (0:N-1) * (Fs / N);

for s = 1:NUM_SIG
    gm = x_golden_mag(:,s);
    dm = abs(x_dut(:,s));
    gm_s = gm * (max(dm) / (max(gm) + eps));

    hf = figure('Name', sprintf('[Spectrum] %d: %s', s-1, SIGNAL_NAMES{s}), ...
                'NumberTitle','off','Color','w','Position', [80 80 1200 500]);

    % Linear Magnitude
    subplot(1,2,1);
    hold on;
    plot(freq_axis, gm_s, 'b-',  'LineWidth', 1.5, 'DisplayName', 'Golden');
    plot(freq_axis, dm,   'r--', 'LineWidth', 1.2, 'DisplayName', 'DUT');
    hold off;
    grid on; xlim([0 Fs/2]);
    xlabel('Normalised Frequency', 'FontSize', 11); ylabel('|FFT|', 'FontSize', 11);
    title('Linear Magnitude', 'FontSize', 12, 'FontWeight', 'bold');
    lg = legend('Location', 'best', 'FontSize', 9);
    set(lg, 'Color', 'w', 'EdgeColor', 'k', 'TextColor', 'k');
    set(gca, 'FontSize', 10, 'Color', 'w', 'XColor', 'k', 'YColor', 'k', 'GridAlpha', 0.15);

    % dB Magnitude
    subplot(1,2,2);
    hold on;
    plot(freq_axis, mag2db_floor(gm_s), 'b-',  'LineWidth', 1.5, 'DisplayName', 'Golden');
    plot(freq_axis, mag2db_floor(dm),   'r--', 'LineWidth', 1.2, 'DisplayName', 'DUT');
    hold off;
    grid on; xlim([0 Fs/2]);
    xlabel('Normalised Frequency', 'FontSize', 11); ylabel('Magnitude (dB)', 'FontSize', 11);
    title('dB Magnitude', 'FontSize', 12, 'FontWeight', 'bold');
    lg = legend('Location', 'best', 'FontSize', 9);
    set(lg, 'Color', 'w', 'EdgeColor', 'k', 'TextColor', 'k');
    set(gca, 'FontSize', 10, 'Color', 'w', 'XColor', 'k', 'YColor', 'k', 'GridAlpha', 0.15);

    sgtitle(sprintf('Spectrum (N=256): %s (SNR = %.1f dB)', SIGNAL_NAMES{s}, snr_all(s)), ...
            'FontSize', 14, 'FontWeight', 'bold');
    saveas(hf, sprintf('spectrum_sig%02d_%s.png', s-1, safe_name(SIGNAL_NAMES{s})));
end

%% =========================================================================
%  SECTION 3 — DETAILED COMPARISON
% =========================================================================
fprintf('[3/6] Comparison panels ...\n');

for s = 1:NUM_SIG
    gm   = x_golden_mag(:,s);
    gcx  = x_golden_cplx(:,s);
    dm   = abs(x_dut(:,s));
    gm_s = gm * (max(dm) / (max(gm) + eps));
    err  = gm_s - dm;

    hf = figure('Name', sprintf('[Compare] %d: %s', s-1, SIGNAL_NAMES{s}), ...
                'NumberTitle','off','Color','w','Position',[80 80 1200 750]);

    subplot(3,2,[1 2]);
    hold on;
    plot(freq_axis, gm_s, 'b-',  'LineWidth',1.5, 'DisplayName','Golden (scaled)');
    plot(freq_axis, dm,   'r--', 'LineWidth',1.1, 'DisplayName','DUT Output');
    hold off;
    grid on; xlim([0 Fs/2]);
    xlabel('Normalised Frequency', 'FontSize',10, 'Color','k');
    ylabel('|FFT|', 'FontSize',10, 'Color','k');
    title('Magnitude Overlay', 'FontWeight','bold', 'Color','k');
    lg = legend('Location','best','FontSize',8);
    set(lg, 'Color', 'w', 'EdgeColor', 'k', 'TextColor', 'k');
    set(gca,'FontSize',9,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);

    subplot(3,2,[3 4]);
    stem(freq_axis, err, 'k.', 'MarkerSize',3);
    grid on; xlim([0 Fs/2]);
    xlabel('Normalised Frequency', 'FontSize',10, 'Color','k');
    ylabel('Error', 'FontSize',10, 'Color','k');
    title(sprintf('Error = Golden - DUT  (NRMSE=%.4f, SNR=%.1f dB)', ...
          nrmse_all(s), snr_all(s)), 'FontWeight','bold', 'Color','k');
    set(gca,'FontSize',9,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);

    subplot(3,2,5);
    histogram(err, 40, 'FaceColor',[0.2 0.5 0.85], 'EdgeColor','none');
    xlabel('Error value', 'FontSize',10, 'Color','k');
    ylabel('Count', 'FontSize',10, 'Color','k');
    title('Error Distribution', 'FontWeight','bold', 'Color','k');
    grid on;
    set(gca,'FontSize',9,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);

    subplot(3,2,6);
    plot(freq_axis, angle(gcx) * (180/pi), 'm-', 'LineWidth',0.8);
    grid on; xlim([0 Fs/2]);
    xlabel('Normalised Frequency', 'FontSize',10, 'Color','k');
    ylabel('Phase (deg)', 'FontSize',10, 'Color','k');
    title('Golden FFT Phase', 'FontWeight','bold', 'Color','k');
    set(gca,'FontSize',9,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);

    sgtitle(sprintf('DUT vs Golden (N=256) — Signal %d: %s', s-1, SIGNAL_NAMES{s}), ...
            'FontSize',11,'FontWeight','bold');
    saveas(hf, sprintf('compare_sig%02d_%s.png', s-1, safe_name(SIGNAL_NAMES{s})));
end

%% =========================================================================
%  SECTION 4 — SPECTROGRAMS (Adjusted for N=256)
% =========================================================================
fprintf('[4/6] Spectrograms ...\n');

% Scaled down window sizes for 256 samples
win_len = 32; 
hop     = 8;
nsg     = 64;

for s = 1:NUM_SIG
    hf = figure('Name', sprintf('[Spectrogram] %d: %s', s-1, SIGNAL_NAMES{s}), ...
                'NumberTitle','off','Color','w','Position',[80 80 1000 420]);

    subplot(1,2,1);
    spectrogram(real(x_dut(:,s)), hann(win_len), win_len-hop, nsg, Fs, 'yaxis');
    colormap('jet'); colorbar;
    title('Real part', 'FontWeight','bold', 'Color','k');
    xlabel('Time (s)', 'FontSize',9, 'Color','k');
    ylabel('Frequency (Hz)', 'FontSize',9, 'Color','k');
    set(gca,'FontSize',8,'Color','w','XColor','k','YColor','k');

    subplot(1,2,2);
    spectrogram(imag(x_dut(:,s)), hann(win_len), win_len-hop, nsg, Fs, 'yaxis');
    colormap('jet'); colorbar;
    title('Imaginary part', 'FontWeight','bold', 'Color','k');
    xlabel('Time (s)', 'FontSize',9, 'Color','k');
    ylabel('Frequency (Hz)', 'FontSize',9, 'Color','k');
    set(gca,'FontSize',8,'Color','w','XColor','k','YColor','k');

    sgtitle(sprintf('Spectrogram — Signal %d: %s', s-1, SIGNAL_NAMES{s}), ...
            'FontSize',12,'FontWeight','bold');
    saveas(hf, sprintf('spectrogram_sig%02d_%s.png', s-1, safe_name(SIGNAL_NAMES{s})));
end

%% =========================================================================
%  SECTION 5 — SUMMARY DASHBOARD
% =========================================================================
fprintf('[5/6] Summary dashboard ...\n');

hf = figure('Name','FFT Summary Dashboard', ...
            'NumberTitle','off','Color','w','Position',[40 40 1900 1200]);

for s = 1:NUM_SIG
    subplot(3,4,s);
    gm   = x_golden_mag(:,s);
    dm   = abs(x_dut(:,s));
    gm_s = gm * (max(dm)/(max(gm)+eps));

    hold on;
    plot(freq_axis(1:N/2), mag2db_floor(gm_s(1:N/2)), 'b-',  'LineWidth',1.2);
    plot(freq_axis(1:N/2), mag2db_floor(dm(1:N/2)),   'r--', 'LineWidth',0.9);
    hold off;
    grid on; xlim([0 Fs/2]);
    xlabel('Freq','FontSize',7,'Color','k'); ylabel('dB','FontSize',7,'Color','k');
    title(sprintf('%d: %s\nSNR=%.1f dB', s-1, SIGNAL_NAMES{s}, snr_all(s)), ...
          'FontSize',7,'FontWeight','bold','Color','k');
    set(gca,'FontSize',7,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);
end

subplot(3,4,12); axis off;
lh = legend([plot(NaN,NaN,'b-','LineWidth',2); plot(NaN,NaN,'r--','LineWidth',2)], ...
             {'Golden Reference','DUT Output'});
lh.FontSize = 11; lh.Location = 'center';

sgtitle('256-pt FFT — All 11 Signals: Golden vs DUT (dB, FP8 E4M3 decoded)', ...
        'FontSize',14,'FontWeight','bold');
saveas(hf, 'fft_dashboard_256.png');

%% =========================================================================
%  SECTION 6 — ERROR METRICS BAR CHARTS
% =========================================================================
fprintf('[6/6] Error metrics summary ...\n');

hf = figure('Name','FFT Error Metrics','NumberTitle','off', ...
            'Color','w','Position',[80 80 1200 500]);

subplot(1,2,1);
b = bar(0:NUM_SIG-1, snr_all, 'FaceColor','flat');
b.CData = parula(NUM_SIG);
grid on; grid minor;
xticks(0:NUM_SIG-1); xticklabels(SIGNAL_NAMES); xtickangle(35);
ylabel('SNR (dB)', 'Color','k'); title('DUT vs Golden — SNR per Signal','FontWeight','bold','Color','k');
set(gca,'FontSize',8,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);

subplot(1,2,2);
b2 = bar(0:NUM_SIG-1, nrmse_all*100, 'FaceColor','flat');
b2.CData = autumn(NUM_SIG);
grid on; grid minor;
xticks(0:NUM_SIG-1); xticklabels(SIGNAL_NAMES); xtickangle(35);
ylabel('NRMSE (%)', 'Color','k'); title('DUT vs Golden — NRMSE per Signal','FontWeight','bold','Color','k');
set(gca,'FontSize',8,'Color','w','XColor','k','YColor','k','GridAlpha',0.15);

sgtitle('FP8 E4M3 DUT FFT (256-pt) — Error Metrics vs Double-Precision Golden', ...
        'FontSize',12,'FontWeight','bold');
saveas(hf, 'fft_error_metrics_256.png');

fprintf('\n=== Done. All figures saved to current directory. ===\n');

%% =========================================================================
%  LOCAL HELPER FUNCTIONS
%% =========================================================================

function lut = build_fp8_e4m3_lut()
    lut = zeros(256,1);
    for b = 0:255
        s_bit = bitshift(b, -7);
        e     = bitand(bitshift(b, -3), 15);
        m     = bitand(b, 7);
        if b == 255
            lut(b+1) = NaN;
        elseif e == 0
            v = (m/8.0) * 2.0^(-6);        % subnormal
            lut(b+1) = (-1)^s_bit * v;
        else
            v = (1.0 + m/8.0) * 2.0^(e-7); % normal
            lut(b+1) = (-1)^s_bit * v;
        end
    end
end

function [re_mat, im_mat] = load_fp8_file(filename, FP8_LUT, N, NUM_SIG)
    fid = fopen(filename,'r');
    if fid < 0, error('Cannot open: %s', filename); end
    raw = textscan(fid,'%s');
    fclose(fid);

    hex_strs = raw{1};
    vals     = uint16(hex2dec(hex_strs));

    re_bytes = uint8(bitshift(vals,-8));
    im_bytes = uint8(bitand(vals, 255));

    re_vals = FP8_LUT(double(re_bytes)+1);
    im_vals = FP8_LUT(double(im_bytes)+1);

    re_mat = reshape(re_vals, N, NUM_SIG);
    im_mat = reshape(im_vals, N, NUM_SIG);
end

function db_val = mag2db_floor(x)
    pk = max(abs(x(:))) + eps;
    db_val = 20 * log10(max(abs(x), 1e-6 * pk));
end

function s = safe_name(name)
    s = regexprep(name,'[^a-zA-Z0-9]','_');
    s = regexprep(s,'_+','_');
end