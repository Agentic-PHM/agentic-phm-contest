"""提示词 ↔ 请求解析器 契约测试。

后端现在**不再接受模型自报的 evidence_json**，实测证据由服务端"AGH 事件流里的命令
+ 本 run 的真实 HTTP 命中记录 + 响应体"三者关联生成。因此 `request_details` 能否识别
模型实际发出的命令，直接决定该端点算不算取到证据：**识别不了 → 该端点证据不存在 →
一次本来成功的诊断会被降级**（2026-10-14 真机上模型就写过 `curl.exe -sS -f $u`
这种用变量拼 URL 的形式，而解析器会拒绝它）。

这类"提示词与校验器漂移"无法靠手写用例覆盖（手写用例永远和解析器一致），所以这里直接
从 `_prompt()` 取真实提示词，断言其中的命令形态与被解析器接受。
"""
import json
import re

import pytest

from phmlite.agh_policy import CURL_ABS, CURL_FLAGS
from phmlite.agh_evidence import request_details

ALARM = {"point": "DE", "level": "severe", "kpi": "kurtosis", "value": 10.49,
         "ts": "2026-10-14T00:00:00"}


def _prompt(monkeypatch, tmp_path, restricted=False):
    from phmlite.autodiag import AutoDiag
    from phmlite.db import DB
    if restricted:
        monkeypatch.setenv("PHM_AGH_RESTRICTED_SHELL", "1")
    else:
        monkeypatch.delenv("PHM_AGH_RESTRICTED_SHELL", raising=False)
    ad = AutoDiag(DB(tmp_path / f"c{int(restricted)}.db").migrate(), tmp_path)
    ad.alarm = dict(ALARM)
    ad.run_id = "auto-contract-1"
    return ad._prompt()


@pytest.mark.parametrize("restricted", [False, True])
def test_prompt_read_commands_are_recognized_as_requests(monkeypatch, tmp_path, restricted):
    """提示词里的只读取证命令必须能被 `request_details` 识别为**单个实测请求**。

    用提示词原文里的字面 URL 直接构造命令（只读步骤没有占位符，可以原样取用），
    从而覆盖"提示词与解析器漂移"这一整类问题。"""
    prompt = _prompt(monkeypatch, tmp_path, restricted)
    urls = set(re.findall(r'"(http://127\.0\.0\.1:8181/api/[^"<]*)"', prompt))
    assert urls, "提示词里应出现字面 API URL"
    for url in urls:
        command = f'curl -sS --fail-with-body "{url}"'
        endpoint, _ = request_details(command)
        assert endpoint is not None, f"提示词给出的 URL 无法被识别：{url}"
    # 至少覆盖这几类取证（趋势/频谱/特征频率里的至少一类在 URL 集合中）
    assert any("/api/spectrum" in u for u in urls)


@pytest.mark.parametrize("restricted", [False, True])
def test_prompt_write_commands_are_literal_and_recognized(monkeypatch, tmp_path, restricted):
    """写命令必须是**字面量 URL + --data-urlencode**，且被解析器接受。

    这正是真机上失败过的形态：模型用 `$u=\"$base/api/report/submit?...\"` 再 `curl $u`，
    解析器（正确地）拒绝它，于是报告虽落库、实测证据却关联不上。"""
    prompt = _prompt(monkeypatch, tmp_path, restricted)
    for endpoint in ("/api/report/submit", "/api/task/create"):
        assert f'"http://127.0.0.1:8181{endpoint}"' in prompt, endpoint
    # 形如 curl ... -G "http://.../api/report/submit" --data-urlencode "k=v" 的命令必须可解析
    command = ('curl -sS --fail-with-body -G "http://127.0.0.1:8181/api/report/submit" '
               '--data-urlencode "run_id=auto-contract-1" --data-urlencode "conclusion=外圈缺陷"')
    endpoint, args = request_details(command)
    assert endpoint == "/api/report/submit" and args["run_id"] == "auto-contract-1"


@pytest.mark.parametrize("restricted", [False, True])
def test_prompt_forbids_variable_built_urls(monkeypatch, tmp_path, restricted):
    """反面对照：解析器拒绝变量拼 URL，所以提示词必须**明说不要那么写**。

    没有这句约束时，模型换个写法就会让证据关联失败（今天真机实测踩到），而提示词本身
    看不出问题——这条测试就是防止有人把这句话删掉。"""
    prompt = _prompt(monkeypatch, tmp_path, restricted)
    assert "字面量" in prompt and ("变量" in prompt or "拼接" in prompt)
    variable_form = ("$u=\"http://127.0.0.1:8181/api/report/submit?run_id=x\"\n"
                     "curl -sS --fail-with-body $u")
    assert request_details(variable_form) == (None, None)   # 解析器确实拒绝
