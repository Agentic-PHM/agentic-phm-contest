"""P1 阈值配置统一：equipment_map 绑定传感器的 warning/critical 必须与 db.py
通道 notice/severe 完全一致（db.py 是唯一权威源；3D 部件卡与报警阈值同源展示）。
本测试是防漂移回归阀：改 db 常量或映射表任一侧不同步即失败。"""
import json
from pathlib import Path

import pytest

from phmlite.db import (COOL_AIR_NOTICE, COOL_AIR_SEVERE, OIL_TEMP_NOTICE,
                        OIL_TEMP_SEVERE, PEDESTAL_NOTICE, PEDESTAL_SEVERE,
                        RMS_NOTICE, RMS_SEVERE, RPM_NOTICE, RPM_SEVERE,
                        SHAFT_NOTICE, SHAFT_SEVERE, SHAFT_TEMP_NOTICE,
                        SHAFT_TEMP_SEVERE)

REPO = Path(__file__).resolve().parents[1]

# 通道名 → db.py 权威口径 (notice, severe)；normal 档不入报警判定，不参与校验
DB_BANDS = {
    "轴振1X": (SHAFT_NOTICE, SHAFT_SEVERE), "轴振1Y": (SHAFT_NOTICE, SHAFT_SEVERE),
    "轴振2X": (SHAFT_NOTICE, SHAFT_SEVERE), "轴振2Y": (SHAFT_NOTICE, SHAFT_SEVERE),
    "瓦振1X": (PEDESTAL_NOTICE, PEDESTAL_SEVERE), "瓦振1Y": (PEDESTAL_NOTICE, PEDESTAL_SEVERE),
    "轴温DE": (SHAFT_TEMP_NOTICE, SHAFT_TEMP_SEVERE), "轴温NDE": (SHAFT_TEMP_NOTICE, SHAFT_TEMP_SEVERE),
    "冷风温度": (COOL_AIR_NOTICE, COOL_AIR_SEVERE), "油温": (OIL_TEMP_NOTICE, OIL_TEMP_SEVERE),
    "转速": (RPM_NOTICE, RPM_SEVERE),
}


def _map_sensors():
    data = json.loads((REPO / "data" / "equipment_map.json").read_text(encoding="utf-8"))
    out = {}
    for comp in data["components"]:
        for sen in comp.get("sensors", []):
            out[sen["channel"]] = sen
    return out


def test_equipment_map_thresholds_match_db_constants():
    sensors = _map_sensors()
    missing = [ch for ch in DB_BANDS if ch not in sensors]
    assert not missing, f"equipment_map 缺少绑定通道: {missing}"
    for ch, (notice, severe) in DB_BANDS.items():
        sen = sensors[ch]
        assert sen["warning"] == notice, \
            f"{ch}: map warning={sen['warning']} != db notice={notice}（改 db 后需同步映射表）"
        assert sen["critical"] == severe, \
            f"{ch}: map critical={sen['critical']} != db severe={severe}"
        assert sen["normal"] < sen["warning"], f"{ch}: normal 档必须低于 warning"


def test_public_models_copy_in_sync():
    """构建源副本（public/models）必须与 data/ 一致，否则 3D 卡片显示旧阈值。"""
    a = (REPO / "data" / "equipment_map.json").read_text(encoding="utf-8")
    b = (REPO / "dashboard" / "public" / "models" / "equipment_map.json").read_text(encoding="utf-8")
    assert json.loads(a) == json.loads(b)


def test_db_thresholds_sanity():
    """db 常量自身单调性：normal 基线 < notice < severe 由各通道语义保证，此处守底。"""
    for notice, severe in ((SHAFT_NOTICE, SHAFT_SEVERE), (PEDESTAL_NOTICE, PEDESTAL_SEVERE),
                           (SHAFT_TEMP_NOTICE, SHAFT_TEMP_SEVERE), (COOL_AIR_NOTICE, COOL_AIR_SEVERE),
                           (OIL_TEMP_NOTICE, OIL_TEMP_SEVERE), (RPM_NOTICE, RPM_SEVERE),
                           (RMS_NOTICE, RMS_SEVERE)):
        assert notice < severe


@pytest.mark.parametrize("copy", ["dashboard/public/models/equipment_map.json",
                                  "dashboard/dist/models/equipment_map.json"])
def test_dist_copy_matches_data(copy):
    src = json.loads((REPO / "data" / "equipment_map.json").read_text(encoding="utf-8"))
    dst = json.loads((REPO / copy).read_text(encoding="utf-8"))
    assert src == dst
