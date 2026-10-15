"""手动仿真开关：scenario.json 的读取、白名单 set 与原子落盘。"""
import copy
import json
import os
import tempfile

DEFAULTS = {
    "rpm": 2980,
    "fault": {"type": "none", "severity": 0.0},
    "noise": 0.05,
    "temp_base": 45.0,   # 轴承温度基线（°C）：温度模型 target = temp_base + temp_gain·severity
    "temp_gain": 45.0,   # severity=1 时的温度目标抬升量（°C）
    "override": {"rms": None, "kurtosis": None},
}

#: 可注入的故障机理（4.1 扩展：从 none/bpfo/bpfi 扩到混合/非轴承/未建模，见 dsp.FAULT_KINDS）
_FAULT_TYPES = ("none", "bpfo", "bpfi", "bpfo_bpfi", "unbalance", "looseness", "unknown")

# 白名单键 → 允许的值类型；未列出的键一律拒绝
_WHITELIST_TYPES = {
    "rpm": (int, float),
    "noise": (int, float),
    "temp_base": (int, float),
    "temp_gain": (int, float),
    "fault.type": (str,),
    "fault.severity": (int, float),
    "override.rms": (int, float, type(None)),
    "override.kurtosis": (int, float, type(None)),
}


def allows_none(key):
    """该键是否可空。HTTP 只能传字符串，可空键需要把 ""/null 解释为"清除"，
    否则 override 一旦设上就再也撤不掉（2026-10-13 实测：复位被 400 拒绝）。"""
    return type(None) in _WHITELIST_TYPES.get(key, ())


class Scenario:
    def __init__(self, path):
        self.path = str(path)

    def ensure(self):
        """文件不存在时写入默认场景，返回当前场景。"""
        if not os.path.exists(self.path):
            self._write(copy.deepcopy(DEFAULTS))
        return self.get()

    def get(self):
        with open(self.path, encoding="utf-8") as f:
            return json.load(f)

    def set(self, key, value):
        """按白名单更新点分键并原子落盘，返回更新后的完整场景。非法键抛 KeyError。"""
        if key not in _WHITELIST_TYPES:
            raise KeyError(key)
        self._validate(key, value)
        data = self.get()
        node = data
        parts = key.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
        self._write(data)
        return data

    @staticmethod
    def _validate(key, value):
        if not isinstance(value, _WHITELIST_TYPES[key]):
            raise ValueError(f"{key} 值类型非法: {value!r}")
        if key == "fault.type" and value not in _FAULT_TYPES:
            raise ValueError(f"fault.type 仅支持 {_FAULT_TYPES}: {value!r}")
        if key == "fault.severity" and not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"fault.severity 须在 0~1: {value!r}")

    def _write(self, data):
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
