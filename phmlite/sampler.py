"""独立采样循环（P0 问题3）：仿真时钟驱动，与 HTTP 请求解耦。

背景：此前 `GET /api/device` 既读数据又推进仿真（信号合成 → 特征入库 → 报警判定 → 唤起诊断），
`/api/channels` 与 `/api/health` 也会推进 tick 与温度一阶状态。于是采样频率由浏览器请求决定——
页面越多、刷新越快，采样与报警节奏越不同；而没人打开页面时，这条监测链路根本不跑。

现在：独立线程按固定周期采样一次并缓存快照，GET 只读快照；报警绑定样本序号（`seq`），
不再由 HTTP 请求次数决定。
"""
import logging
import os
import threading
import time

_LOGGER = logging.getLogger(__name__)

#: 默认采样周期（秒）。演示环境下 3s 与前端 3~5s 轮询同量级，但节奏已由后端决定。
DEFAULT_INTERVAL = 3.0


def interval_from_env(default=DEFAULT_INTERVAL):
    """采样周期（秒）：`PHM_SAMPLE_INTERVAL` 可调；<=0 表示关闭独立循环（退回按需采样）。"""
    raw = os.environ.get("PHM_SAMPLE_INTERVAL")
    if raw is None:
        return float(default)
    try:
        return float(raw)
    except ValueError:
        _LOGGER.warning("PHM_SAMPLE_INTERVAL=%r 不是数字，改用默认 %ss", raw, default)
        return float(default)


class PeriodicSampler:
    """按固定周期调用 `sample_fn(seq)`，缓存最近一次快照供只读接口使用。

    - 采样由本线程驱动，与请求无关；无人访问页面时监测链路照常运行。
    - `snapshot()` 只读；仅在冷启动（循环未跑或已关闭）时才同步补一次，避免首个请求拿到空值。
    - 采样异常不终止循环：记录 `last_error` 后继续按周期重试。
    """

    def __init__(self, sample_fn, interval=None, name="sampler"):
        self.sample_fn = sample_fn
        self.interval = interval_from_env() if interval is None else float(interval)
        self.name = name
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._first = threading.Event()
        self._thread = None
        self._seq = 0
        self._snapshot = None
        self.last_error = None
        self.sample_count = 0

    # ---- 生命周期 ----
    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        """启动采样线程，并等首个样本就绪后再返回（保证服务起来就有快照）。"""
        if self.interval <= 0 or self.running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"phm-{self.name}", daemon=True)
        self._thread.start()
        self._first.wait(timeout=max(1.0, self.interval * 3))
        return self

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None
        return self

    # ---- 采样 ----
    def sample_once(self):
        """同步采一次并缓存；返回快照。正常运行由循环调用，测试与冷启动也走这里。"""
        with self._lock:
            self._seq += 1
            seq = self._seq
            try:
                value = self.sample_fn(seq)
            except Exception as exc:  # noqa: BLE001 —— 采样失败不能拖垮循环
                self.last_error = type(exc).__name__
                _LOGGER.warning("采样失败（seq=%s）：%s", seq, exc)
                self._seq -= 1
                raise
            self.last_error = None
            self.sample_count += 1
            self._snapshot = {"seq": seq, "at": time.time(), "value": value}
            self._first.set()
            return self._snapshot

    def snapshot(self):
        """只读最近快照；冷启动（无快照）时同步补一次。"""
        with self._lock:
            snap = self._snapshot
        if snap is None:
            snap = self.sample_once()
        return snap

    @property
    def seq(self):
        """当前样本序号（报警与快照都以此为准，而非请求次数）。"""
        with self._lock:
            return self._seq

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:  # noqa: BLE001 —— 单次失败不影响后续周期
                pass
            self._stop.wait(self.interval)  # stop() 会立刻唤醒
