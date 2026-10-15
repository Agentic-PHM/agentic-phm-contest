"""P0 防真值泄漏回归：诊断提示词不得含注入真值；scenario 接口默认脱敏、操作台可见。"""
import time

from conftest import VALID_EVIDENCE, api, operator_headers
from test_autodiag import _make, ALARM, ad_db  # noqa: F401  复用会话级服务器夹具
from phmlite.autodiag import TERMINAL
from phmlite.server import app

# 提示词与对外状态里不得出现的真值痕迹（注入 bpfo 0.8 的前提下）
TRUTH_TOKENS = ("bpfo", "bpfi", "fault.type", "severity", "外圈", "内圈")


def _fault(kind="bpfo", severity=0.8):
    return {"fault": {"type": kind, "severity": severity}}


def _settle(ad, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ad.state in TERMINAL:
            return
        time.sleep(0.05)
    raise AssertionError(f"会话未收敛: {ad.state}")


def test_prompt_contains_no_fault_truth(ad_db, monkeypatch):
    """注入 bpfo/0.8 后，诊断提示词不得泄漏任何故障标签与强度（P0 审计红线）。"""
    prompts = []

    def capture(prompt, run_id):
        prompts.append(prompt)
        ad_db.submit_report(run_id, "P-101", "结论来自取证", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("停机检查", "取证依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, capture, monkeypatch)
    ad.observe(_fault("bpfo", 0.8), [dict(ALARM)])
    _settle(ad)
    assert prompts, "诊断未被唤起"
    low = prompts[0].lower()
    for token in ("fault.type", "severity"):
        assert token not in low, f"提示词泄漏真值痕迹: {token}"
    # Listing the output vocabulary is not leaking the selected scenario.
    before = ad._prompt()
    ad.fault = {"type": "bpfi", "severity": 0.17}
    assert ad._prompt() == before
    assert "8181/api/device" in prompts[0]  # 取证指令本身仍在


def test_status_payload_has_no_fault(ad_db, monkeypatch):
    def complete(prompt, run_id):
        ad_db.submit_report(run_id, "P-101", "外圈BPFO缺陷", 0.9, VALID_EVIDENCE, diagnosis_status="confirmed", diagnosis_code="bpfo")
        ad_db.create_task("检查", "依据", "severe", "agent", run_id=run_id)

    ad = _make(ad_db, complete, monkeypatch)
    ad.observe(_fault(), [dict(ALARM)])
    _settle(ad)
    assert "fault" not in ad.status()


def test_scenario_api_redacts_truth_without_operator_credentials(base_url):
    """P0/P1：无凭据（智能体视角）读不到真值；持控制台会话 token 才完整可见。"""
    OP = operator_headers(base_url)
    api(base_url, "/api/scenario/set?key=fault.type&value=bpfo", headers=OP)
    api(base_url, "/api/scenario/set?key=fault.severity&value=0.7", headers=OP)
    red = api(base_url, "/api/scenario/get")
    assert red["fault"] == {"type": "hidden", "severity": None} and red["truth_redacted"] is True
    full = api(base_url, "/api/scenario/get", headers=OP)
    assert full["fault"] == {"type": "bpfo", "severity": 0.7} and "truth_redacted" not in full
    # P1 问题4：写接口已要求凭据，所以写响应对持凭据者不再脱敏（能写的人本来就能读）；
    # 无凭据的写入直接 403，见 test_control_plane_writes_require_credentials。
    echoed = api(base_url, "/api/scenario/set?key=fault.severity&value=0.6", headers=OP)
    assert echoed["fault"]["type"] == "bpfo"
    api(base_url, "/api/scenario/set?key=fault.type&value=none", headers=OP)
    api(base_url, "/api/scenario/set?key=fault.severity&value=0", headers=OP)


def test_control_plane_writes_require_credentials(base_url):
    """P1 问题4：控制面写接口不能再"无凭据也能改真值、只脱敏响应"。

    此前固定字符串 `X-Operator: console` 也打在前端包里，任何能发 HTTP 的人都能伪造。"""
    import urllib.error
    import urllib.request

    def status(path, headers=None):
        req = urllib.request.Request(base_url + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    forged = {"X-Operator": "console"}          # 旧门禁：静态字符串，前端包里就有
    assert status("/api/scenario/set?key=fault.type&value=bpfo") == 403
    assert status("/api/scenario/set?key=fault.type&value=bpfo", forged) == 403
    assert status("/api/testtools/chaos?target=spectrum&value=on", forged) == 403
    assert status("/api/testtools/chaos?target=spectrum&value=on") == 403

    OP = operator_headers(base_url)
    assert status("/api/scenario/set?key=fault.type&value=bpfo", OP) == 200
    assert status("/api/testtools/chaos?target=spectrum&value=off", OP) == 200
    # 收尾：复位场景
    assert status("/api/scenario/set?key=fault.type&value=none", OP) == 200


def test_operator_token_is_per_session_not_a_static_literal(base_url):
    """控制台凭据是登录签发的随机值：不再是可预测的静态字符串。"""
    from phmlite import operator_auth
    a = operator_auth.login(base_url)["X-Operator-Token"]
    b = operator_auth.login(base_url)["X-Operator-Token"]
    assert a != b and len(a) >= 32 and a != "console"
    # 未登录的随机值不通过
    import urllib.error
    import urllib.request
    req = urllib.request.Request(base_url + "/api/scenario/set?key=fault.type&value=none",
                                 headers={"X-Operator-Token": "0" * 32})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("未签发的 token 不应被接受")
    except urllib.error.HTTPError as e:
        assert e.code == 403
