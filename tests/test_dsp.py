import math
import numpy as np
from phmlite.dsp import (rms, peak_to_peak, kurtosis, skewness,
                         fft_spectrum, envelope_spectrum,
                         characteristic_frequencies, synthesize)

FS, N = 16000, 16384

def sine(f, a=1.0, seconds=1.02):  # 整周期窗口（50Hz×1.02s=51 周期），避免截断边缘效应
    t = np.arange(int(FS * seconds)) / FS
    return a * np.sin(2 * np.pi * f * t)

def test_rms_of_sine_is_amplitude_over_root2():
    assert abs(rms(sine(50, a=2.0)) - 2.0 / math.sqrt(2)) < 1e-6

def test_peak_to_peak():
    assert abs(peak_to_peak(sine(50, a=2.0)) - 4.0) < 1e-6

def test_kurtosis_of_sine_is_1_5():
    assert abs(kurtosis(sine(50)) - 1.5) < 0.05

def test_skewness_of_sine_is_0():
    assert abs(skewness(sine(50))) < 1e-6

def test_fft_spectrum_finds_peak_at_50hz():
    freqs, amps = fft_spectrum(sine(50, a=1.0), FS)
    peak_hz = freqs[int(np.argmax(amps))]
    assert abs(peak_hz - 50.0) < FS / N  # 分辨率内

def test_envelope_spectrum_of_bpfo_impulses_peaks_at_bpfo():
    cf = characteristic_frequencies(rpm=2980, n_balls=8, d_over_D=0.32, contact_angle_deg=0)
    x = synthesize(FS, N, rpm=2980, fault_type="bpfo", severity=1.0, noise=0.02)
    freqs, amps = envelope_spectrum(x, FS)
    mask = (freqs > cf["BPFO"] - 3) & (freqs < cf["BPFO"] + 3)
    assert mask.any() and amps[mask].max() > np.percentile(amps, 99)

def test_synthesize_bpfo_raises_kurtosis_and_rms():
    normal = synthesize(FS, N, rpm=2980, fault_type="none", severity=0, noise=0.05)
    faulty = synthesize(FS, N, rpm=2980, fault_type="bpfo", severity=0.8, noise=0.05)
    assert kurtosis(faulty) > kurtosis(normal) and kurtosis(faulty) > 5.0
    assert rms(faulty) > 2 * rms(normal)

def test_characteristic_frequencies_values():
    cf = characteristic_frequencies(2980, 8, 0.32, 0)
    fr = 2980 / 60
    assert abs(cf["fr"] - fr) < 1e-9
    assert abs(cf["BPFO"] - fr * 4 * (1 - 0.32)) < 0.01   # ≈135.09
    assert abs(cf["BPFI"] - fr * 4 * (1 + 0.32)) < 0.01   # ≈262.24
    assert abs(cf["FTF"] - fr / 2 * (1 - 0.32)) < 0.01    # ≈16.89
    assert abs(cf["BSF"] - fr / (2 * 0.32) * (1 - 0.32**2)) < 0.01  # ≈69.66
