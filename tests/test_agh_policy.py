"""P1 问题7 回归：受限 Shell 白名单与诊断提示词必须同源一致。

背景：此前靠 `session.setYolo(true)` 让 shell 免审批——那是"任意 Shell 执行"级授权。
运行时代码（agnes.mjs:49284）显示 yolo 会把 `needsAsk` 直接置 false，而 `command_policy`
只在 `needsAsk` 为真时求值（:279464），所以**只要 yolo 在，白名单就是摆设**。
本测试锁定撤掉 yolo 后的白名单语义，并保证提示词里的命令形态确实落在白名单内。
"""
import re

import pytest

from phmlite import agh_policy as pol
from phmlite.autodiag import AutoDiag

ALARM = {"point": "DE", "level": "severe", "kpi": "kurtosis", "value": 10.49,
         "ts": "2026-10-13T00:00:00"}


def _restricted_prompt(monkeypatch, tmp_path):
    monkeypatch.setenv("PHM_AGH_RESTRICTED_SHELL", "1")
    from phmlite.db import DB
    ad = AutoDiag(DB(tmp_path / "p.db").migrate(), tmp_path)
    assert ad.restricted_shell is True
    ad.alarm = dict(ALARM)
    ad.run_id = "auto-test-0001"
    return ad._prompt()


def test_allow_rules_are_host_acceptable_shape():
    """规则的形态必须能通过 AGH host 的 `checkCommandRule`：argv 以 ^ 开头、allow 必须绝对路径。"""
    for rule in pol.allow_rules():
        assert rule["action"] == "allow"
        assert rule["argv"].startswith("^"), "host 要求 argv 锚定在开头"
        body = rule["argv"][1:]
        assert re.match(r"^(?:/|\\[/\\]|[A-Za-z]:)", body), "allow 规则必须锚定绝对路径"
        assert "^" not in rule["tool"] and "$" not in rule["tool"], "tool 模式不得自带锚点"
        re.compile(rule["argv"])          # 必须是合法正则
        re.compile(rule["tool"])


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8181/api/device",
    "http://127.0.0.1:8181/api/trend?point=DE&kpi=rms&hours=24",
    "http://127.0.0.1:8181/api/spectrum?point=DE&type=fft&top=12",
    "http://127.0.0.1:8181/api/spectrum?point=NDE&type=envelope&top=12",
    "http://127.0.0.1:8181/api/charfreq",
    "http://127.0.0.1:8181/api/equipment-map",
])
def test_restricted_prompt_commands_are_allowed(url):
    assert pol.verify_command(pol.get_command(url)) is None, url


def test_write_commands_are_allowed():
    submit = pol.write_command("/api/report/submit", {
        "run_id": "auto-1", "equip": "P-101",
        "conclusion": "驱动端轴承外圈BPFO缺陷：包络主峰134.77Hz命中理论BPFO",
        "confidence": "0.92", "component_id": "bearing_de", "evidence_json": "[{}]"})
    task = pol.write_command("/api/task/create", {
        "title": "[P-101] 停机检查：BPFO", "desc": "依据", "level": "severe",
        "source": "agent", "run_id": "auto-1"})
    assert pol.verify_command(submit) is None
    assert pol.verify_command(task) is None


@pytest.mark.parametrize("command", [
    "rm -rf /",                                             # 任意破坏性命令
    "powershell -c Get-Content data\\scenario.json",        # 换个解释器读真值
    "type data\\scenario.json",                             # 直读真值文件
    'curl.exe -s "http://127.0.0.1:8181/api/device"',       # 相对路径：无法被预批准（host 设计使然）
    f'{pol.CURL_ABS} -s "http://127.0.0.1:8181/api/scenario/get"',   # 未枚举的接口
    f'{pol.CURL_ABS} -s "http://evil.example.com/api/device"',       # 非本机主机
    f'{pol.CURL_ABS} -o stolen.json "http://127.0.0.1:8181/api/device"',  # 借 curl 落盘
    f'{pol.CURL_ABS} -s -G "http://127.0.0.1:8181/api/task/close" --data-urlencode "id=1"',
])
def test_dangerous_commands_are_not_pre_approved(command):
    assert pol.matches_any_allow(command) is False, command


def test_restricted_prompt_self_check_catches_policy_drift(monkeypatch, tmp_path):
    """提示词与白名单一旦漂移，受限模式必须立刻失败，而不是等到演示时诊断卡死。"""
    monkeypatch.setattr(pol, "allow_rules", lambda: [])
    with pytest.raises(RuntimeError, match="受限 Shell 模式自检失败"):
        _restricted_prompt(monkeypatch, tmp_path)


def test_restricted_prompt_uses_absolute_curl_and_covers_all_steps(monkeypatch, tmp_path):
    prompt = _restricted_prompt(monkeypatch, tmp_path)
    assert pol.CURL_ABS in prompt
    for step in ("①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨"):
        assert step in prompt
    assert "--data-urlencode" in prompt          # 写接口用 curl 编码，不再让模型拼 PowerShell
    assert "只授权了上述绝对路径 curl 命令" in prompt
    # 非受限模式（默认）保持原样：相对 curl + PowerShell 编码辅助函数
    monkeypatch.delenv("PHM_AGH_RESTRICTED_SHELL")
    from phmlite.db import DB
    ad = AutoDiag(DB(tmp_path / "q.db").migrate(), tmp_path)
    ad.alarm = dict(ALARM)
    ad.run_id = "auto-test-0002"
    plain = ad._prompt()
    assert pol.CURL_ABS not in plain and "curl -sS -f" in plain
    # 默认模式也改用 -G --data-urlencode：中文交给 curl 编码。
    # 让模型自己写百分号转义会被它写错字节（2026-10-13 实测把「轴承」编成「轴才」，
    # 报告结论因此变成乱码），所以两种模式都不允许手写编码。
    assert "--data-urlencode" in plain


def test_both_prompts_require_fail_fast_curl(monkeypatch, tmp_path):
    """审查 P1 问题5：只用 `-s` 时 HTTP 503 仍返回 0，工具层 isError=false 会被当成业务成功。
    两种模式的取证命令都必须带 `-f`，白名单也必须与命令形态一致（否则受限模式会被拒）。"""
    from phmlite.db import DB
    monkeypatch.delenv("PHM_AGH_RESTRICTED_SHELL", raising=False)
    plain = AutoDiag(DB(tmp_path / "f.db").migrate(), tmp_path)
    plain.alarm, plain.run_id = dict(ALARM), "auto-f-1"
    for prompt in (plain._prompt(), _restricted_prompt(monkeypatch, tmp_path)):
        assert "-sS -f" in prompt, "取证命令必须 fail-fast"
        assert "curl -s " not in prompt and "curl.exe -s " not in prompt
        assert "--data-urlencode" in prompt, "中文参数必须交给 curl 编码"
        assert "不要自己写百分号转义" in prompt or "交给 curl" in prompt
    assert pol.CURL_FLAGS == "-sS -f"
    for rule in pol.allow_rules():
        assert "-sS -f" in rule["argv"]
