"""24h 历史种子：seeded random，可复现。

4.1：这是**模拟历史**，不是设备真实连续运行记录——两者必须可区分，不能混同为现场故障演化。
因此落库时标记 `origin="seed"`（真实采样为 `origin="live"`），`/api/trend` 会把它带出来。
"""
from datetime import datetime, timedelta

import numpy as np


def seed_history(db, points, hours=24, step_minutes=5, seed=7):
    """围绕正常运行量级生成确定性历史：rms≈2.1(±0.4)、峭度≈3.1、峰峰值≈5.0，全部 normal 区。"""
    rng = np.random.default_rng(seed)
    now = datetime.now().replace(microsecond=0)
    n = int(hours * 60 / step_minutes)
    for point in points:
        rms_v = np.clip(rng.normal(2.1, 0.4, n), 1.6, 3.4)
        kurt_v = np.clip(rng.normal(3.1, 0.15, n), 2.6, 3.6)
        pp_v = np.clip(rng.normal(5.0, 0.3, n), 4.2, 5.8)
        for i in range(n):
            ts = (now - timedelta(minutes=step_minutes * (n - i))).isoformat(timespec="seconds")
            db.insert_eigen(point, ts, rms=float(rms_v[i]), kurtosis=float(kurt_v[i]),
                            pp=float(pp_v[i]), level="normal", origin="seed")
