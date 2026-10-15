import json
import urllib.error
import urllib.request
from urllib.parse import quote

from conftest import VALID_EVIDENCE, api, operator_headers, wait_until, fake_index
from phmlite.server import app

# P0 问题2：报告接口不再接受空证据，提交必须带结构合规的证据数组
EV = "&diagnosis_status=confirmed&diagnosis_code=bpfo&evidence_json=" + quote(VALID_EVIDENCE, safe="")


def _allow_active_run(monkeypatch, run_id):
    monkeypatch.setattr(app.state.autodiag, "_index_provider", fake_index)
    monkeypatch.setattr(app.state.autodiag, "status", lambda: {
        "state": "running", "mode": "agh", "run_id": run_id,
    })


def _get_status(base_url, path):
    try:
        with urllib.request.urlopen(base_url + path, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())

def test_trend_reports_data_provenance(base_url):
    """4.1：趋势接口必须能区分模拟历史种子与真实采样，评委据此判断数据口径。"""
    d = api(base_url, "/api/trend?point=DE&kpi=rms&hours=24")
    assert {"seeded_points", "live_points", "unclassified_points"} <= set(d)
    assert d["seeded_points"] > 0, "启动时应已灌入 24h 模拟历史"
    assert all(s.get("origin") in ("seed", "live", "unknown") for s in d["series"])
    assert d["seeded_points"] + d["live_points"] + d["unclassified_points"] == len(d["series"])


def test_device_shape(base_url):
    d = api(base_url, "/api/device")
    assert d["name"] == "P-101"
    pt = d["points"][0]
    assert pt["name"] == "DE" and set(pt["current"]) == {"rms", "kurtosis", "pp"} and "level" in pt

def test_trend_and_waveform(base_url):
    tr = api(base_url, "/api/trend?point=DE&kpi=rms&hours=24")
    assert len(tr["series"]) > 100 and {"ts", "v"} <= set(tr["series"][0])
    w = api(base_url, "/api/waveform?point=DE&n=4096")
    assert w["fs"] == 16000 and len(w["values"]) == 4096

def test_spectrum_and_charfreq(base_url):
    sp = api(base_url, "/api/spectrum?point=DE&type=envelope&top=12")
    assert sp["type"] == "envelope" and sp["peaks"] and {"hz", "amp"} <= set(sp["peaks"][0])
    cf = api(base_url, "/api/charfreq?rpm=2980")
    assert abs(cf["BPFO"] - 135.09) < 0.1

def test_equipment_map_has_9_components(base_url):
    d = api(base_url, "/api/equipment-map")
    ids = [c["id"] for c in d["components"]]
    assert len(ids) == 9
    assert set(ids) == {"bearing_de", "bearing_nde", "rotor", "shaft", "stator",
                        "bedplate", "cooling_system", "lubrication_system", "terminal_box"}
    assert all({"id", "name_cn", "anchor", "sensors"} <= set(c) for c in d["components"])

def test_report_component_roundtrip(base_url, monkeypatch):
    _allow_active_run(monkeypatch, "rc")
    r1 = api(base_url, "/api/report/submit?run_id=rc&equip=P-101&conclusion=BPFO-defect&confidence=0.8"
                        "&component_id=bearing_de" + EV)
    _allow_active_run(monkeypatch, "rc2")
    r2 = api(base_url, "/api/report/submit?run_id=rc2&equip=P-101&conclusion=BPFO-defect&confidence=0.8"
                        + EV)                     # 旧调用不带 component_id
    by_id = {r["id"]: r for r in api(base_url, "/api/report/list?equip=P-101")["rows"]}
    assert by_id[r1["id"]]["component"] == "bearing_de"
    assert by_id[r2["id"]]["component"] is None

def test_scenario_set_triggers_alarm_but_artifact_writes_need_active_agh_run(base_url):
    before_alarms = len(api(base_url, "/api/alarm/history?equip=P-101")["rows"])
    op = operator_headers(base_url)  # P1 问题4：控制面凭据（登录签发的会话 token）
    api(base_url, "/api/scenario/set?key=fault.type&value=bpfo",
        headers=op)                                                        # 手动注入（字符串值）
    api(base_url, "/api/scenario/set?key=fault.severity&value=0.8", headers=op)
    # P0 问题3：报警由采样循环按"连续 3 个样本"推进，不再靠请求次数凑
    wait_until(lambda: len(api(base_url, "/api/alarm/history?equip=P-101")["rows"]) > before_alarms,
               message="注入故障后采样循环未在超时内产生报警")
    d = api(base_url, "/api/device")
    assert d["points"][0]["level"] in ("notice", "severe")
    assert api(base_url, "/api/alarm/history?equip=P-101")["rows"]
    before_tasks = len(app.state.db.task_list())
    before_reports = len(app.state.db.report_list())
    task_status, _ = _get_status(base_url, "/api/task/create?title=t&desc=d&level=severe&source=agent")
    report_status, _ = _get_status(base_url, "/api/report/submit?run_id=r1&equip=P-101&conclusion=c&confidence=0.9&evidence_json=%7B%7D")
    assert task_status == report_status == 409
    assert len(app.state.db.task_list()) == before_tasks
    assert len(app.state.db.report_list()) == before_reports
    op = operator_headers(base_url)  # scenario 响应默认脱敏 fault 真值（P0），控制台凭据可见
    assert api(base_url, "/api/scenario/set?key=fault.type&value=none", op)["fault"]["type"] == "none"


def test_agent_artifacts_require_the_matching_active_run(base_url, monkeypatch):
    _allow_active_run(monkeypatch, "active-run")
    mismatch_status, _ = _get_status(
        base_url, "/api/report/submit?run_id=other-run&equip=P-101&conclusion=c&confidence=0.9")
    mismatch_task_status, _ = _get_status(
        base_url, "/api/task/create?title=t&desc=d&level=severe&source=agent&run_id=other-run")
    assert mismatch_status == 409
    assert mismatch_task_status == 409

    report = api(base_url, "/api/report/submit?run_id=active-run&equip=P-101&conclusion=BPFO-defect&confidence=0.9" + EV)
    task = api(base_url, "/api/task/create?title=t&desc=d&level=severe&source=agent&run_id=active-run")
    assert app.state.db.get_task(task["id"])["run_id"] == "active-run"
    assert app.state.db.get_report(report["id"])["run_id"] == "active-run"
