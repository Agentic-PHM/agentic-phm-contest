"""4.1 扩展回归：故障机理的**物理可辨性**必须由测量证实，而不是自称支持。

判据用**峰/底噪比**而不是绝对幅值——宽带能量（未建模故障、强噪声）会把包络谱底噪整体抬起来，
只看绝对幅值会把"噪声floor 变高"误判成"出现了特征峰"（2026-10-13 实测踩到）。

实测基线（fs=16000, N=16384, rpm=2980, severity=0.8, noise=0.05, seed=42），
四列为包络谱在 BPFO/BPFI/BSF/FTF ±3Hz 的峰 / 10~1000Hz 中位底噪：

    机理          BPFO   BPFI    BSF    FTF |   RMS   峭度
    none           2.0    1.8    4.8    2.5 |  2.11  1.90
    bpfo         195.7    5.5    1.3    4.4 |  6.70 10.50
    bpfi           1.8  291.9    2.5   10.4 |  9.17  5.98
    bpfo_bpfi     56.8   51.6    1.1    3.8 |  6.05  5.77
    unbalance      2.8    2.2    5.3    1.6 |  8.17  1.53
    looseness      3.1    2.3    5.8    6.1 |  4.40  2.80
    unknown        1.5    2.8    2.4    1.7 |  8.32  3.02

即：轴承类在自身频率上 50~290 倍，非轴承类在所有轴承频率上 ≤6.1 倍 —— 可区分。
"""
import numpy as np
import pytest

from phmlite.dsp import (FAULT_KINDS, characteristic_frequencies, envelope_spectrum,
                         fft_spectrum, kurtosis, rms, synthesize)

FS, N, RPM = 16000, 16384, 2980
CF = characteristic_frequencies(RPM, 8, 0.32, 0)
BEARING_FREQS = (CF["BPFO"], CF["BPFI"], CF["BSF"], CF["FTF"])
TOL = 3.0
#: 判定"这个频率上有显著峰"的峰/底噪比下限（实测轴承故障 ≥50，非轴承机理 ≤6.1）
SIGNIFICANT = 20.0
#: 判定"这个频率上没有峰"的上限（实测基线 1.1~4.8，留一倍余量）
FLOOR_LIMIT = 12.0


def _sig(fault, severity=0.8, noise=0.05, seed=42, rpm=RPM):
    return synthesize(FS, N, rpm=rpm, fault_type=fault, severity=severity,
                      noise=noise, seed=seed)


def _ratio(sig, target, tol=TOL):
    """包络谱在 target±tol 的峰值 ÷ 10~1000Hz 中位底噪（显著峰的判据）。"""
    freqs, amps = envelope_spectrum(sig, FS)
    band = (freqs >= target - tol) & (freqs <= target + tol)
    floor_band = (freqs > 10) & (freqs < 1000)
    floor = float(np.median(amps[floor_band])) or 1e-12
    return float(np.max(amps[band])) / floor if band.any() else 0.0


def _amp_at(sig, target, tol=1.0):
    freqs, amps = fft_spectrum(sig, FS)
    band = (freqs >= target - tol) & (freqs <= target + tol)
    return float(np.max(amps[band])) if band.any() else 0.0


def test_all_declared_kinds_are_synthesizable():
    for kind in FAULT_KINDS:
        sig = _sig(kind, severity=0.8 if kind != "none" else 0.0)
        assert sig.shape == (N,) and np.isfinite(sig).all(), kind


def test_unknown_kind_is_rejected_loudly():
    with pytest.raises(ValueError):
        _sig("not-a-kind")


# ---- 轴承类：只在自身理论特征频率上留显著峰 ----

def test_bpfo_and_bpfi_peak_only_at_their_own_frequency():
    bpfo, bpfi = _sig("bpfo"), _sig("bpfi")
    assert _ratio(bpfo, CF["BPFO"]) > SIGNIFICANT
    assert _ratio(bpfo, CF["BPFI"]) < FLOOR_LIMIT
    assert _ratio(bpfi, CF["BPFI"]) > SIGNIFICANT
    assert _ratio(bpfi, CF["BPFO"]) < FLOOR_LIMIT


def test_mixed_fault_shows_both_bearing_frequencies():
    """混合故障（外圈+内圈）必须**同时**出现两个显著峰——4.2 里"未见完整验证"的一类。

    这是真正更难的用例：只看第一个峰就下结论会漏掉另一半。"""
    mixed = _sig("bpfo_bpfi", severity=1.0)
    assert _ratio(mixed, CF["BPFO"]) > SIGNIFICANT
    assert _ratio(mixed, CF["BPFI"]) > SIGNIFICANT
    assert rms(mixed) > 4.5 or kurtosis(mixed) > 4.0     # 足以触发报警


def test_severity_scales_the_bearing_signature_monotonically():
    peaks = [_ratio(_sig("bpfo", severity=s), CF["BPFO"]) for s in (0.3, 0.6, 0.9)]
    assert peaks[0] < peaks[1] < peaks[2]


def test_different_seeds_keep_the_signature():
    """不同随机种子下机理不变——4.2 要求"不同随机种子"覆盖。"""
    for seed in (1, 7, 20261013):
        assert _ratio(_sig("bpfo", seed=seed), CF["BPFO"]) > SIGNIFICANT, seed


# ---- 非轴承类：留下与轴承缺陷不同的证据 ----

def test_unbalance_is_1x_dominant_without_impulses():
    """不平衡：1X 随 severity 放大，且**不产生冲击**（峭度保持接近 3）。"""
    low, high = _sig("unbalance", severity=0.3), _sig("unbalance", severity=1.0)
    assert _amp_at(high, CF["fr"]) > _amp_at(low, CF["fr"])
    assert kurtosis(high) < 4.0, "不平衡不应产生冲击成分"
    for freq in BEARING_FREQS:
        assert _ratio(high, freq) < FLOOR_LIMIT, freq


def test_looseness_shows_harmonics_but_not_bearing_frequencies():
    """松动：2X 谐波抬升，且不得在任何轴承特征频率留下显著峰（否则会被误判成滚动体缺陷）。

    实测教训：早期模型混入 0.5X 半频项（那是油膜涡动机理）后，BSF 处峰/底噪从 5.8 涨到 23.4，
    足以被误判成滚动体缺陷——已移除并保留此回归。"""
    loose, base = _sig("looseness", severity=1.0), _sig("none", severity=0.0)
    assert _amp_at(loose, 2 * CF["fr"]) > _amp_at(base, 2 * CF["fr"])
    for freq in BEARING_FREQS:
        assert _ratio(loose, freq) < FLOOR_LIMIT, freq


def test_unknown_fault_raises_energy_without_any_characteristic_peak():
    """未建模故障：能量抬升（会触发阈值）但**任何已知特征频率上都没有显著峰**。

    这正是"无法识别时主动拒绝下结论"用例的物理基础：RMS 8.3（severe）、峭度 3.0（正常）、
    四个轴承频率的峰/底噪比 1.5~2.8，与基线同量级。"""
    base, unknown = _sig("none", severity=0.0), _sig("unknown", severity=1.0)
    assert rms(unknown) > 3 * rms(base), "未建模故障应显著抬高能量"
    assert kurtosis(unknown) < 4.0, "宽带能量不产生冲击成分"
    for freq in BEARING_FREQS:
        assert _ratio(unknown, freq) < FLOOR_LIMIT, freq


def test_variable_speed_moves_characteristic_frequencies():
    """变转速：特征频率随转速线性移动——4.2 要求覆盖更多转速与转速变化。"""
    for rpm in (1200, 2400, 2980, 3600):
        cf = characteristic_frequencies(rpm, 8, 0.32, 0)
        assert _ratio(_sig("bpfo", rpm=rpm), cf["BPFO"]) > SIGNIFICANT, rpm


def test_noise_does_not_create_false_bearing_peaks():
    """噪声强度提升不得凭空造出特征峰——抗误报的物理前提。"""
    for noise in (0.05, 0.2, 0.35, 0.5):
        sig = _sig("none", severity=0.0, noise=noise)
        for freq in BEARING_FREQS:
            assert _ratio(sig, freq) < FLOOR_LIMIT, (noise, freq)
