"""P0 问题3 回归：采样必须由独立循环（仿真时钟）驱动，HTTP 请求只读快照。

此前 GET /api/device 自己合成信号、写特征、判报警并唤起诊断，/api/channels 与 /api/health
也会推进 tick 与温度状态——采样频率于是由浏览器请求次数决定，没人开页面时监测链路不跑。
"""
import time

from conftest import api, operator_headers
from phmlite.db import DB
from phmlite.sampler import PeriodicSampler

#: 测试环境的采样周期（见 conftest 的 PHM_SAMPLE_INTERVAL）
INTERVAL = 0.2


def test_sampler_advances_without_any_request(base_url):
    """没人访问接口时采样照样推进——"自主工业运维"的最低要求。"""
    seq0 = api(base_url, "/api/device")["sample_seq"]
    time.sleep(INTERVAL * 3)          # 期间不打任何接口
    seq1 = api(base_url, "/api/device")["sample_seq"]
    assert seq1 > seq0, "无请求期间采样循环没有推进"


def test_read_endpoints_do_not_drive_sampling(base_url):
    """只读接口连打多次，采样序号只应随采样周期增长，而不是随请求次数增长。"""
    first = api(base_url, "/api/device")["sample_seq"]
    t0 = time.time()
    for _ in range(20):
        api(base_url, "/api/device")
        api(base_url, "/api/channels")
        api(base_url, "/api/health")
    elapsed = time.time() - t0
    grown = api(base_url, "/api/device")["sample_seq"] - first
    # 旧实现：60 次请求会推进 60 个样本。新实现只跟采样周期走（留 2 个样本余量防抖动）。
    assert grown <= elapsed / INTERVAL + 2, \
        f"{elapsed:.2f}s 内 60 次只读请求推进了 {grown} 个样本，采样仍被请求驱动"


def test_device_snapshot_exposes_sample_clock(base_url):
    """快照要带独立样本序号与采样时刻：报警与展示都以此为准，而非请求次数。"""
    d = api(base_url, "/api/device")
    assert isinstance(d["sample_seq"], int) and d["sample_seq"] > 0
    assert d["sampled_at"]


def test_alarm_is_bound_to_a_sample_sequence(base_url):
    """报警绑定样本序号，采样时间与序号都落在报警行上。"""
    before = len(api(base_url, "/api/alarm/history?equip=P-101")["rows"])
    op = operator_headers(base_url)          # P1 问题4：控制面写接口需会话凭据
    api(base_url, "/api/scenario/set?key=fault.type&value=bpfo", op)
    api(base_url, "/api/scenario/set?key=fault.severity&value=0.8", op)
    deadline = time.time() + 20
    row = None
    while time.time() < deadline:
        rows = api(base_url, "/api/alarm/history?equip=P-101")["rows"]
        if len(rows) > before:
            row = rows[0]
            break
        time.sleep(0.05)
    api(base_url, "/api/scenario/set?key=fault.type&value=none", op)
    api(base_url, "/api/scenario/set?key=fault.severity&value=0", op)

    assert row is not None, "注入故障后采样循环未产生报警"
    assert isinstance(row["seq"], int), f"报警缺样本序号：{row}"
    assert row["ts"]


def test_same_sample_sequence_cannot_reach_the_alarm_threshold(tmp_path):
    """同一采样序号重复判定不计数：连续判据必须由样本推进，而不是由调用次数推进。"""
    db = DB(tmp_path / "gate.db").migrate()
    # 同一 seq（7）重复 5 次：不推进样本就不该触发
    for _ in range(5):
        assert db.maybe_alarm("P-101", "DE", 9.0, 10.0, 60.0, seq=7) is None
    # 样本推进到连续第 3 个才落报警
    assert db.maybe_alarm("P-101", "DE", 9.0, 10.0, 60.0, seq=8) is None
    row = db.maybe_alarm("P-101", "DE", 9.0, 10.0, 60.0, seq=9)
    assert row and row["level"] == "severe" and row["seq"] == 9


def test_continuous_three_samples_trigger_alarm(tmp_path):
    db = DB(tmp_path / "three.db").migrate()
    assert db.maybe_alarm("P-101", "DE", 9.0, 10.0, 60.0, seq=1) is None
    assert db.maybe_alarm("P-101", "DE", 9.0, 10.0, 60.0, seq=2) is None
    assert db.maybe_alarm("P-101", "DE", 9.0, 10.0, 60.0, seq=3) is not None


def test_periodic_sampler_keeps_running_after_errors():
    """单次采样失败不能终止循环（真实采样会碰 IO/计算异常）。"""
    state = {"n": 0}

    def flaky(seq):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("第一次采样失败")
        return {"seq": seq}

    s = PeriodicSampler(flaky, interval=0.05, name="test-flaky").start()
    try:
        deadline = time.time() + 5
        while time.time() < deadline and s.sample_count < 2:
            time.sleep(0.02)
        assert s.sample_count >= 2, "采样循环在首次异常后停止了"
    finally:
        s.stop()
    assert s.running is False


def test_periodic_sampler_snapshot_is_read_only_after_first_sample():
    calls = []

    def fn(seq):
        calls.append(seq)
        return seq

    s = PeriodicSampler(fn, interval=60, name="test-frozen").start()   # 周期很长：只有首个样本
    try:
        assert s.sample_count == 1
        seq = s.snapshot()["seq"]
        for _ in range(5):
            assert s.snapshot()["seq"] == seq        # 只读：不再采样
        assert len(calls) == 1
    finally:
        s.stop()


def test_periodic_sampler_disabled_falls_back_to_on_demand():
    """PHM_SAMPLE_INTERVAL<=0 关闭循环时退回按需采样，接口仍可用（排查用）。"""
    s = PeriodicSampler(lambda seq: {"seq": seq}, interval=0, name="test-off")
    s.start()
    assert s.running is False
    assert s.snapshot()["seq"] == 1
