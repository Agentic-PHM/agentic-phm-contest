"""Task C 新接口用例：/api/channels、/api/health、/api/forecast、/api/login。"""
import json
import urllib.error
import urllib.request

from conftest import api, operator_headers, reset_simulation, wait_samples
from phmlite.dsp import (PART_TEMP_MODEL, linear_forecast, part_temp_next,
                         temperature_next)
from phmlite.server import app

EXPECTED_CHANNELS = {"轴振1X", "轴振1Y", "轴振2X", "轴振2Y",
                     "瓦振1X", "瓦振1Y", "瓦振2X", "瓦振2Y", "电机转速", "轴承温度",
                     "轴温DE", "轴温NDE", "冷风温度", "油温"}

# 部件级温度通道（热力图视图）阈值：定义处在 phmlite/db.py（对齐 equipment_map）
PART_TEMP_THRESHOLDS = {"轴温DE": (65, 80), "轴温NDE": (65, 80),
                        "冷风温度": (40, 50), "油温": (50, 60)}


def _post(base_url, path, body):
    req = urllib.request.Request(base_url + path, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _set_fault(base_url, ftype, severity):
    op = operator_headers(base_url)   # P1 问题4：控制面写接口需登录签发的会话凭据
    api(base_url, f"/api/scenario/set?key=fault.type&value={ftype}", op)
    api(base_url, f"/api/scenario/set?key=fault.severity&value={severity}", op)


def _channel_map(base_url):
    return {c["name"]: c for c in api(base_url, "/api/channels")["channels"]}


def test_channels_shape_groups_and_thresholds(base_url):
    _set_fault(base_url, "none", 0.0)
    reset_simulation(app)        # 清掉上一个用例的热积累（采样循环按真实时间积分温度）
    wait_samples(base_url, 1)    # 等一个"无故障 + 常温"的新样本
    chs = api(base_url, "/api/channels")["channels"]
    names = {c["name"] for c in chs}
    assert EXPECTED_CHANNELS <= names and {"DE振动", "NDE振动"} <= names
    assert {"轴振", "瓦振", "转速", "温度", "DE振动", "NDE振动"} <= {c["group"] for c in chs}
    for c in chs:
        assert {"name", "group", "value", "unit", "level", "notice", "severe"} <= set(c)
        assert c["level"] in ("normal", "notice", "severe") and 0 < c["notice"] < c["severe"]
    by = _channel_map(base_url)
    assert by["轴振1X"]["unit"] == "μm" and \
        (by["轴振1X"]["notice"], by["轴振1X"]["severe"]) == (80, 120)
    assert by["瓦振1X"]["unit"] == "mm/s" and \
        (by["瓦振1X"]["notice"], by["瓦振1X"]["severe"]) == (4.5, 7.1)
    assert by["电机转速"]["unit"] == "rpm" and \
        (by["电机转速"]["notice"], by["电机转速"]["severe"]) == (3010, 3040)
    assert by["轴承温度"]["unit"] == "°C" and \
        (by["轴承温度"]["notice"], by["轴承温度"]["severe"]) == (65, 75)
    assert (by["DE振动"]["notice"], by["DE振动"]["severe"]) == (4.5, 7.1)  # DE/NDE 沿用 RMS 阈值
    assert 2950 < by["电机转速"]["value"] < 3010      # 正常：额定 2980 ± 微噪声
    assert 40 < by["轴承温度"]["value"] < 65          # 正常：基线附近
    assert all(by[n]["level"] == "normal" for n in EXPECTED_CHANNELS)


def test_fault_drives_all_channels(base_url):
    try:
        _set_fault(base_url, "bpfo", 1.0)
        wait_samples(base_url, 1)                      # 等采样循环取到"故障已注入"的样本
        snaps = []
        for _ in range(4):                             # P0 问题3：数字按**样本**演化，不再按请求次数
            wait_samples(base_url, 1)
            snaps.append({c["name"]: c["value"] for c in api(base_url, "/api/channels")["channels"]})
        for n in EXPECTED_CHANNELS:                    # 样本递推：每个通道数字都在变
            assert max(snaps[i][n] for i in range(4)) > min(snaps[i][n] for i in range(4))
        by = _channel_map(base_url)
        for n in ("轴振1X", "轴振1Y", "轴振2X", "轴振2Y"):
            assert by[n]["level"] == "severe" and by[n]["value"] > 120  # 轴振按 severity 放大
        for n in ("瓦振1X", "瓦振1Y", "瓦振2X", "瓦振2Y"):
            assert by[n]["level"] in ("notice", "severe")
        assert by["轴振1X"]["value"] / 55.0 > by["瓦振1X"]["value"] / 2.0  # 瓦振增幅弱于轴振
        assert by["DE振动"]["level"] in ("notice", "severe")
    finally:
        _set_fault(base_url, "none", 0.0)


def test_health_range_and_fault_drop(base_url):
    _set_fault(base_url, "none", 0.0)
    reset_simulation(app)
    wait_samples(base_url, 1)
    h0 = api(base_url, "/api/health")
    assert 90 <= h0["score"] <= 100                    # 无故障：接近满分
    assert len(h0["components"]) >= 10
    assert all(0 <= c["score"] <= 100 for c in h0["components"])
    try:
        _set_fault(base_url, "bpfi", 1.0)
        wait_samples(base_url, 1)
        h1 = api(base_url, "/api/health")
        assert h1["score"] <= h0["score"] - 15         # 故障注入后显著下降
        assert min(c["score"] for c in h1["components"]) == 0.0  # 轴振越严重阈值归零
    finally:
        _set_fault(base_url, "none", 0.0)


def test_forecast_structure_and_eta_after_fault(base_url):
    try:
        _set_fault(base_url, "bpfo", 0.8)
        for _ in range(6):
            wait_samples(base_url, 1)                  # P0 问题3：等采样循环产生 6 个故障样本
        f = api(base_url, "/api/forecast?point=DE")
        assert set(f) >= {"method", "r2", "points", "eta_notice_hours", "eta_severe_hours"}
        assert f["method"] == "linear" and f["points"] >= 6 and isinstance(f["r2"], float)
        assert f["eta_notice_hours"] is not None
        lo, hi = f["eta_notice_hours"]
        assert 0 <= lo <= hi
        assert f["eta_severe_hours"] is not None
        assert f["eta_severe_hours"][0] <= f["eta_severe_hours"][1]
    finally:
        _set_fault(base_url, "none", 0.0)


def test_forecast_rejects_flat_or_short_series():
    flat = linear_forecast([2.0, 2.1, 2.0, 2.2, 2.1, 2.05], [0, 1, 2, 3, 4, 5], 4.5, 7.1)
    assert flat["eta_notice_hours"] is None and flat["eta_severe_hours"] is None
    assert flat["reason"] == "无明显上升趋势"
    short = linear_forecast([2.0, 2.1, 2.2], [0, 1, 2], 4.5, 7.1)
    assert short["eta_notice_hours"] is None and "数据点不足" in short["reason"]


def test_forecast_rising_series_eta_interval():
    y = [2.0 + 0.5 * t for t in range(8)]              # 每小时稳定上升 0.5
    f = linear_forecast(y, [float(t) for t in range(8)], 4.5, 7.1)
    assert f["eta_notice_hours"] == [0.0, 0.0]         # 末点 5.5 已过注意阈值
    lo, hi = f["eta_severe_hours"]
    assert 2.0 <= lo <= hi <= 5.0                      # 剩余 (7.1-5.5)/0.5 = 3.2h


def test_temperature_first_order_rise_and_fall():
    t = 45.0
    for _ in range(600):                               # 注入后 10 分钟（1s 步长）
        t = temperature_next(t, 90.0, 1.0)
    assert t > 75.0                                    # 缓升越过严重阈值
    for _ in range(1800):                              # 恢复后 30 分钟
        t = temperature_next(t, 45.0, 1.0)
    assert t < 50.0                                    # 缓降回基线附近


def test_login_success_and_failure(base_url):
    code, body = _post(base_url, "/api/login", {"username": "admin", "password": "123456"})
    assert code == 200 and body["name"] == "管理员" and len(body["token"]) >= 16
    code, body = _post(base_url, "/api/login", {"username": "admin", "password": "wrong"})
    assert code == 401
    code, _ = _post(base_url, "/api/login", {"username": "guest", "password": "123456"})
    assert code == 401


def test_part_temp_channels_shape_and_baseline(base_url):
    """热力图温度通道：在册、单位/阈值正确、健康时贴近各自基线且不越注意阈值。"""
    _set_fault(base_url, "none", 0.0)
    by = _channel_map(base_url)
    assert set(PART_TEMP_THRESHOLDS) <= set(by)
    for n, (notice, severe) in PART_TEMP_THRESHOLDS.items():
        assert by[n]["group"] == "温度" and by[n]["unit"] == "°C"
        assert (by[n]["notice"], by[n]["severe"]) == (notice, severe)
        assert by[n]["level"] == "normal"                      # 健康态全部正常档
        base = PART_TEMP_MODEL[n]["base"]
        assert base - 2.5 < by[n]["value"] < notice            # 基线附近、未越限


def test_part_temp_inertia_on_fault(base_url):
    """注入 severity=1.0：温度走一阶惯性缓升，不会瞬间跳到目标值（严禁编造）。"""
    _set_fault(base_url, "none", 0.0)
    try:
        _set_fault(base_url, "bpfo", 1.0)
        by = _channel_map(base_url)                            # 注入后立即采样
        assert by["轴温DE"]["value"] < 60                       # 目标 48+42=90 不会瞬时到达
        assert by["轴温DE"]["level"] == "normal"
    finally:
        _set_fault(base_url, "none", 0.0)


def test_part_temp_rise_toward_target_and_recovery():
    """severity=1 后各路温度向目标缓升（DE 强耦合增幅最大），恢复后缓降回基线。"""
    t = {name: m["base"] for name, m in PART_TEMP_MODEL.items()}
    for step in range(1200):                                   # 注入后 20 分钟（1s 步长）
        now = float(step)
        t = {name: part_temp_next(v, name, 1.0, 1.0, t_now=now) for name, v in t.items()}
    assert 80.0 <= t["轴温DE"] < 90.0                          # 越过严重阈值 80，趋近目标 90
    assert 80.0 <= t["轴温NDE"] < t["轴温DE"]                  # NDE 增幅略低于 DE
    assert t["冷风温度"] > 40.0                                 # 弱耦合也越过注意阈值
    assert 50.0 <= t["油温"] < 60.0                            # 油温趋向 54（演示配置目标）
    for step in range(3600):                                   # 恢复后 60 分钟
        now = 1200.0 + step
        t = {name: part_temp_next(v, name, 0.0, 1.0, t_now=now) for name, v in t.items()}
    assert t["轴温DE"] < 50.0                                  # 缓降回基线附近
    assert t["轴温NDE"] < 48.0 and t["油温"] < 44.0


def test_part_temp_cooling_drift_stays_below_notice():
    """健康态冷风温度慢漂移：长仿真 24h 始终低于注意阈值 40（漂移为演示缓变项）。"""
    v = PART_TEMP_MODEL["冷风温度"]["base"]
    peak = v
    for step in range(24 * 3600):
        v = part_temp_next(v, "冷风温度", 0.0, 1.0, t_now=float(step))
        peak = max(peak, v)
    assert peak < 40.0


def test_part_temp_same_model_as_bearing_temp():
    """部件温度与既有轴承温度同用 temperature_next 一阶惯性（同 tau 时结果一致）。"""
    a = temperature_next(50.0, 90.0, 10.0, tau=200.0)
    b = part_temp_next(50.0, "轴温DE", 1.0, 10.0)
    # part_temp_next 目标=90 且 tau=200，但含 t_now=0 漂移项为 0，仅轴温无漂移 → 应完全一致
    assert abs(a - b) < 1e-12
