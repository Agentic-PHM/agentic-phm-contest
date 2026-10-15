"""信号处理核心：特征计算、频谱、包络解调、轴承特征频率、故障波形合成。"""
import math

import numpy as np
from scipy import signal

# 演示量级统一标定：正常样本 RMS≈2.1 mm/s，外圈故障 severity=0.8 时≈6.7 mm/s
DEMO_SCALE = 4.558


def rms(x):
    x = np.asarray(x, dtype=float)
    return float(np.sqrt(np.mean(x ** 2)))


def peak_to_peak(x):
    x = np.asarray(x, dtype=float)
    return float(x.max() - x.min())


def kurtosis(x):
    """归一化四阶中心矩：高斯噪声≈3，纯正弦≈1.5，冲击成分使其显著升高。"""
    x = np.asarray(x, dtype=float)
    mu, sigma = x.mean(), x.std()
    return float(np.mean((x - mu) ** 4) / sigma ** 4)


def skewness(x):
    x = np.asarray(x, dtype=float)
    mu, sigma = x.mean(), x.std()
    return float(np.mean((x - mu) ** 3) / sigma ** 3)


def fft_spectrum(x, fs):
    x = np.asarray(x, dtype=float)
    spec = np.abs(np.fft.rfft(x - x.mean())) / len(x) * 2
    freqs = np.fft.rfftfreq(len(x), d=1.0 / fs)
    return freqs, spec


def envelope_spectrum(x, fs):
    """Hilbert 包络解调后取幅值谱：先去均值，再取包络并去直流量，突出调制成分。"""
    x = np.asarray(x, dtype=float)
    env = np.abs(signal.hilbert(x - x.mean()))
    env = signal.detrend(env)
    return fft_spectrum(env, fs)


def characteristic_frequencies(rpm, n_balls, d_over_D, contact_angle_deg):
    fr = rpm / 60.0
    phi = math.radians(contact_angle_deg)
    k = d_over_D * math.cos(phi)
    return {
        "fr": fr,
        "BPFO": fr * n_balls / 2 * (1 - k),
        "BPFI": fr * n_balls / 2 * (1 + k),
        "BSF": fr / (2 * d_over_D) * (1 - k ** 2),
        "FTF": fr / 2 * (1 - k),
    }


#: 合成器支持的故障机理（4.1：此前只有 none/bpfo/bpfi，且都是同一条冲击串公式）。
#: 分两类：**轴承类**在理论特征频率上留峰（bpfo/bpfi/混合）；**非轴承类**用不同机理
#: （不平衡=1X 占优、松动=2X/3X+半频谐波簇）；**unknown** 只有宽带能量抬升、不在任何
#: 已知特征频率留峰——用于检验智能体在该下结论时能否主动拒绝。
BEARING_FAULTS = ("bpfo", "bpfi", "bpfo_bpfi")
FAULT_KINDS = ("none",) + BEARING_FAULTS + ("unbalance", "looseness", "unknown")

#: 混合故障两列冲击的幅度配比（避免简单叠加把 RMS 顶穿、掩盖频域可辨性）
MIXED_WEIGHTS = {"bpfo": 0.62, "bpfi": 0.45}


def _add_impulse_train(x, t, fs, rate, severity, carrier_hz, decay, amplitude):
    """叠加一列周期性指数衰减冲击：间隔 = 1/特征频率。"""
    period = 1.0 / rate
    next_t = 0.1
    while next_t < t[-1]:
        idx = int(next_t * fs)
        if idx >= len(t):
            break
        seg = t[:len(t) - idx]
        x[idx:] += severity * amplitude * np.exp(-seg / decay) * np.sin(2 * np.pi * carrier_hz * seg)
        next_t += period


def synthesize(fs, n, rpm, fault_type, severity, noise, carrier_hz=3200, decay=0.001, seed=42):
    """工频本底正弦 + 按机理叠加故障成分 + 白噪声。

    机理（4.1 扩展）：
      bpfo / bpfi        单列冲击串，间隔 = 对应轴承特征频率；
      bpfo_bpfi          混合故障：BPFO 与 BPFI 两列冲击同时存在（幅度按 MIXED_WEIGHTS 配比）；
      unbalance          不平衡：1X 转频幅值随 severity 放大，无冲击（峭度保持接近 3）；
      looseness          基础松动：2X/3X 谐波簇 + 0.5X 半频成分，非轴承特征频率；
      unknown            未建模故障：仅宽带能量抬升，**不在任何已知特征频率留峰**；
      none               正常。
    """
    if fault_type not in FAULT_KINDS:
        raise ValueError(f"不支持的故障类型: {fault_type}")
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    fr = rpm / 60.0
    x = 0.6 * np.sin(2 * np.pi * fr * t) + 0.25 * np.sin(2 * np.pi * 2 * fr * t)
    cf = characteristic_frequencies(rpm, 8, 0.32, 0)

    if fault_type in BEARING_FAULTS:
        rates = {"bpfo": [("bpfo", cf["BPFO"])],
                 "bpfi": [("bpfi", cf["BPFI"])],
                 "bpfo_bpfi": [("bpfo", cf["BPFO"]), ("bpfi", cf["BPFI"])]}[fault_type]
        for name, rate in rates:
            weight = 1.0 if len(rates) == 1 else MIXED_WEIGHTS[name]
            _add_impulse_train(x, t, fs, rate, severity, carrier_hz, decay, 10.0 * weight)
    elif fault_type == "unbalance":
        # 1X 占优且相位稳定：纯正弦抬升，不产生冲击（峭度不升）
        x += severity * 2.4 * np.sin(2 * np.pi * fr * t)
    elif fault_type == "looseness":
        # 松动：2X/3X 谐波簇（经典松动特征），非轴承特征频率，可据此与轴承缺陷区分。
        # 不加 0.5X 半频项：半频属油膜涡动/whip 机理，混进来会在包络谱产生互调，
        # 实测会在 BSF 附近造出伪峰（2026-10-13 量化：BSF 峰/底噪 23.4× vs 基线 4.8×），
        # 足以被误判成滚动体缺陷——那正是 4.1 说的"物理假设不自洽"。
        x += severity * (1.1 * np.sin(2 * np.pi * 2 * fr * t)
                         + 0.6 * np.sin(2 * np.pi * 3 * fr * t))
    elif fault_type == "unknown":
        # 未建模故障：宽带能量抬升（RMS 会涨、会触发阈值），但谱上无特征频率峰
        x += severity * 2.2 * rng.normal(0, 1.0, n)

    x += rng.normal(0, noise, n)
    return x * DEMO_SCALE


# ---------------------------------------------------------------------------
# 调相机多通道仿真（轴振/瓦振/转速/温度）：故障注入统一驱动，常数为物理模型标定值
# ---------------------------------------------------------------------------
SHAFT_GAIN = 2.0       # 轴振故障放大增益（径向位移对转子故障最敏感）
PEDESTAL_GAIN = 1.6    # 瓦振故障放大增益弱于轴振（经轴承座衰减）
RPM_FLUCT_GAIN = 10.0  # 故障时转速波动噪声放大系数（故障下转速轻微波动）
TEMP_TAU = 240.0       # 轴承温度一阶惯性时间常数（秒）：注入后缓升、恢复后缓降

# 部件级温度通道（热力图视图，绑 equipment_map 的 Bearing_DE/NDE、Cooling_System、
# Lubrication_System）：与"轴承温度"同用 temperature_next 一阶惯性模型，但每路
# 独立状态/增益/时间常数；基线与增益为演示标定，阈值的定义处在 db.py。
# 故障注入（fault.severity）后目标温度抬升→实测缓升，恢复后缓降回基线。
PART_TEMP_DE_BASE = 48.0     # 驱动端轴温基线 °C（与轴振1X/1Y severity 强耦合）
PART_TEMP_NDE_BASE = 46.0    # 非驱动端轴温基线 °C（与轴振2X/2Y 耦合，增幅略低于 DE）
COOL_AIR_BASE = 38.0         # 冷风温度基线 °C（冷却系统，与总体 severity 弱耦合+缓慢漂移）
OIL_TEMP_BASE = 42.0         # 油温基线 °C（润滑系统，弱耦合；演示配置）
PART_TEMP_DE_GAIN = 42.0     # severity=1 目标抬升量 °C：轴温对转子故障最敏感（强耦合）
PART_TEMP_NDE_GAIN = 36.0    # NDE 抬升略低于 DE
COOL_AIR_GAIN = 8.0          # 冷风弱耦合：故障振动对冷却效率影响有限
OIL_TEMP_GAIN = 12.0         # 油温弱耦合（演示配置）
PART_TEMP_DE_TAU = 200.0     # 轴瓦金属热容小→响应较快（秒）
PART_TEMP_NDE_TAU = 220.0
COOL_AIR_TAU = 320.0         # 冷风经冷却器大热容→响应最慢（秒）
OIL_TEMP_TAU = 260.0
COOL_AIR_DRIFT_AMP = 1.0     # 冷风温度慢漂移幅度 °C（环境/负荷缓变的演示项）
COOL_AIR_DRIFT_PERIOD = 1800.0  # 漂移周期（秒）

# 每路温度通道的模型参数（name → 基线/增益/时间常数/漂移），server 与测试共用同一份
PART_TEMP_MODEL = {
    "轴温DE": {"base": PART_TEMP_DE_BASE, "gain": PART_TEMP_DE_GAIN, "tau": PART_TEMP_DE_TAU},
    "轴温NDE": {"base": PART_TEMP_NDE_BASE, "gain": PART_TEMP_NDE_GAIN, "tau": PART_TEMP_NDE_TAU},
    "冷风温度": {"base": COOL_AIR_BASE, "gain": COOL_AIR_GAIN, "tau": COOL_AIR_TAU,
                 "drift_amp": COOL_AIR_DRIFT_AMP, "drift_period": COOL_AIR_DRIFT_PERIOD},
    "油温": {"base": OIL_TEMP_BASE, "gain": OIL_TEMP_GAIN, "tau": OIL_TEMP_TAU},
}


def part_temp_target(name, severity, t_now=0.0):
    """单路部件温度的目标值：基线 + 增益·severity（冷风温度叠加缓慢正弦漂移项）。"""
    m = PART_TEMP_MODEL[name]
    target = m["base"] + m["gain"] * float(severity)
    amp, period = m.get("drift_amp", 0.0), m.get("drift_period", 0.0)
    if amp > 0 and period > 0:
        target += amp * math.sin(2.0 * math.pi * t_now / period)
    return target


def part_temp_next(prev, name, severity, dt, t_now=0.0):
    """单路部件温度一阶趋近：向目标值指数过渡（dt 秒），注入后缓升、恢复后缓降。"""
    return temperature_next(prev, part_temp_target(name, severity, t_now), dt,
                            tau=PART_TEMP_MODEL[name]["tau"])


def fault_severity(scenario):
    """有效故障强度：fault.type 为 none 时一律按 0 处理（振动通道与温度同源联动）。"""
    fault = scenario.get("fault") or {}
    if fault.get("type", "none") == "none":
        return 0.0
    return float(fault.get("severity") or 0.0)


def channel_sample(kind, base, severity, sigma, seed):
    """单通道一次采样：确定性分量 基线×(1+增益·severity) + 按种子生成的正态噪声。"""
    gain = {"shaft": SHAFT_GAIN, "pedestal": PEDESTAL_GAIN, "rpm": 0.0}[kind]
    sigma_eff = sigma * (1.0 + RPM_FLUCT_GAIN * severity) if kind == "rpm" else sigma
    rng = np.random.default_rng(seed)
    return base * (1.0 + gain * severity) + float(rng.normal(0.0, sigma_eff))


def noise_value(sigma, seed):
    """按种子生成单个正态噪声（与 channel_sample 同一递推口径）。"""
    return float(np.random.default_rng(seed).normal(0.0, sigma))


def temperature_next(current, target, dt, tau=TEMP_TAU):
    """一阶惯性环节闭环解：温度向目标值指数趋近（dt 秒），注入后缓升、恢复后缓降。"""
    return target + (current - target) * math.exp(-max(dt, 0.0) / tau)


def linear_forecast(values, x_hours, notice, severe, min_points=4, sig_level=2.0):
    """近期序列最小二乘线性外推：估算到达阈值的剩余时间（小时区间，由斜率±标准误张成）。
    “明显上升”以斜率显著性判定（slope > sig_level·标准误），r2 仅作拟合质量展示；
    数据不足或无明显上升趋势时不编造数字：eta 为 None 并给出 reason。"""
    out = {"method": "linear", "r2": None, "points": len(values),
           "eta_notice_hours": None, "eta_severe_hours": None, "reason": None}
    if len(values) < min_points:
        out["reason"] = f"数据点不足（{len(values)}<{min_points}）"
        return out
    x = np.asarray(x_hours, dtype=float)
    y = np.asarray(values, dtype=float)
    (slope, intercept), pcov = np.polyfit(x, y, 1, cov=True)
    se = float(np.sqrt(pcov[0][0])) if np.isfinite(pcov[0][0]) and pcov[0][0] > 0 else 0.0
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 - float(np.sum((y - (slope * x + intercept)) ** 2)) / ss_tot if ss_tot > 0 else 0.0
    out["r2"] = round(float(r2), 3)
    if slope <= 0 or slope <= sig_level * se:
        out["reason"] = "无明显上升趋势"
        return out
    x_last, y_last = float(x[-1]), float(y[-1])

    def _eta(threshold):
        if y_last >= threshold:
            return [0.0, 0.0]
        dt_lo = (threshold - y_last) / (slope + se)
        dt_hi = (threshold - y_last) / (slope - se) if slope - se > 0 else (threshold - y_last) / slope
        return [round(max(dt_lo, 0.0), 2), round(max(dt_hi, 0.0), 2)]

    out["eta_notice_hours"] = _eta(notice)
    out["eta_severe_hours"] = _eta(severe)
    return out
