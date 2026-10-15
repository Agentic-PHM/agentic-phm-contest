import os
import threading, time, urllib.request, json as _json
import pytest, uvicorn
from phmlite.server import app

# 测试环境关闭任务②自动唤起（不会真的拉起 AGH 子进程）；autodiag 专属测试自建实例并注入假 runner
os.environ.setdefault("PHM_AUTODIAG", "0")
# 测试服务不应在 lifespan 中调用本机 AGH 或模型。
os.environ.setdefault("PHM_STARTUP_PROVIDER_PROBE", "0")
# P0 问题3：采样由独立循环驱动，测试把周期压到 0.2s，让"连续 3 个样本"能在秒级内发生
os.environ.setdefault("PHM_SAMPLE_INTERVAL", "0.2")
# P1 问题5：复测时间窗默认 30s 会拖慢测试；这里置 0（只按样本数判），时间窗由
# tests/test_task_lifecycle.py 的专门用例显式设置大窗口来验证。
os.environ.setdefault("AUTODIAG_RETEST_WINDOW", "0")
# AGH 瞬时失败的重试退避默认 3s/8s：测试里置 0，既不让跑测试白等 11 秒，
# 也避免退避打乱时序敏感用例的节奏（2026-10-13 实测：加退避后 ETA 用例在全量跑时挂）。
os.environ.setdefault("PHM_AGH_RETRY_BACKOFF", "0,0")

@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    app.state.data_dir = data_dir            # server 启动时读它，测试隔离
    config = uvicorn.Config(app, host="127.0.0.1", port=8201, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:8201/api/device", timeout=1); break
        except Exception:
            time.sleep(0.2)
    yield "http://127.0.0.1:8201"
    server.should_exit = True

def api(base_url, path, headers=None):
    req = urllib.request.Request(base_url + path, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as r:
        return _json.loads(r.read())


# ---- P1 问题4 共用测试件：控制面凭据（登录签发的会话 token，不再用固定字符串）----

_OPERATOR_HEADERS = {}


def operator_headers(base_url):
    """控制面请求头：登录一次并缓存（scenario 写接口/完整场景视图需要）。"""
    if base_url not in _OPERATOR_HEADERS:
        from phmlite import operator_auth
        _OPERATOR_HEADERS[base_url] = operator_auth.login(base_url)
    return dict(_OPERATOR_HEADERS[base_url])


# ---- P0 问题3 共用测试件：采样由后端时钟驱动，请求只读快照 ----

def wait_samples(base_url, count=3, timeout=20.0):
    """等采样循环推进 count 个新样本，返回最新 sample_seq。

    采样不再由请求次数驱动，所以测试不能靠"多打几次接口"凑够连续判据。"""
    start = api(base_url, "/api/device")["sample_seq"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        seq = api(base_url, "/api/device")["sample_seq"]
        if seq - start >= count:
            return seq
        time.sleep(0.05)
    raise AssertionError(f"采样循环未在 {timeout}s 内推进 {count} 个样本（start={start}）")


def wait_until(probe, timeout=20.0, message="条件未在超时内满足"):
    """轮询等待条件成立（报警/快照随采样推进而出现）。"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = probe()
        if last:
            return last
        time.sleep(0.05)
    raise AssertionError(f"{message}（最后一次结果 {last!r}）")


def reset_simulation(server_app):
    """复位进程内仿真状态（温度一阶状态），避免用例继承上一个用例的热积累。

    连续采样循环会按真实时间积分温度模型，而 τ 最长 320s，因此共享会话里必须显式复位。"""
    for attr in ("temp_state", "part_temp_state"):
        if hasattr(server_app.state, attr):
            delattr(server_app.state, attr)


# ---- P0 问题2 共用测试件：合规证据 + 可注入的执行索引 ----

#: 结构合规的证据数组（报告接口与 _settle 核验的最低要求）
VALID_EVIDENCE = _json.dumps([
    {"step": "时域", "tool": "/api/device", "key_numbers": ["DE.kurtosis=10.49 severe"],
     "supports": "峭度远超阈值"},
    {"step": "频域", "tool": "/api/spectrum envelope + /api/charfreq",
     "key_numbers": ["包络主峰134.766Hz", "理论BPFO=135.093Hz"], "supports": "命中 BPFO"},
], ensure_ascii=False)

#: 取证类命令（不含 run_id），用于凑够"完整诊断"的工具调用数
_FORENSIC_COMMANDS = [
    'curl.exe -s "http://127.0.0.1:8181/api/device"',
    'curl.exe -s "http://127.0.0.1:8181/api/trend?point=DE&kpi=rms&hours=24"',
    'curl.exe -s "http://127.0.0.1:8181/api/spectrum?point=DE&type=fft&top=12"',
    'curl.exe -s "http://127.0.0.1:8181/api/spectrum?point=DE&type=envelope&top=12"',
    'curl.exe -s "http://127.0.0.1:8181/api/charfreq"',
]


def fake_index(run_id, commands=None, *, with_submit=True, with_task=True,
               run_id_in_prompt=True, write_error=False):
    """构造与 agh_evidence.index_for_run 同形的执行索引，供离线单测注入。

    commands 显式给出时按原样使用（用于构造"工具调用不足"等场景）。
    `write_error=True` 让写接口的工具调用带 is_error（审查 P1 问题5：工具失败不得记 done）。"""
    if commands is None:
        commands = list(_FORENSIC_COMMANDS)
        if with_submit:
            commands.append('curl.exe -s "http://127.0.0.1:8181/api/report/submit'
                            f'?run_id={run_id}&conclusion=c"')
        if with_task:
            commands.append('curl.exe -s "http://127.0.0.1:8181/api/task/create'
                            f'?title=t&run_id={run_id}"')
    calls = [{"tool_use_id": f"t{i}", "ts": "2026-10-11T00:00:00Z", "name": "shell",
              "command": cmd,
              "is_error": write_error and any(e in cmd for e in
                                              ("/api/report/submit", "/api/task/create"))}
             for i, cmd in enumerate(commands)]
    write_endpoints = ("/api/report/submit", "/api/task/create")
    write_calls = [c for c in calls
                   if any(e in (c["command"] or "") for e in write_endpoints)]
    per_endpoint = {e: any(c["is_error"] is False and e in (c["command"] or "") for c in calls)
                    for e in write_endpoints}
    return {
        "run_id": run_id,
        "session_key": "agnes:local:local-dev:cli:workspace:test",
        "turns": [7],
        "window": {"first_ts": "2026-10-11T00:00:00Z", "last_ts": "2026-10-11T00:01:00Z"},
        "model": ["agnes-3.0-flash"],
        "tool_calls": calls,
        "checks": {"run_id_in_prompt": run_id_in_prompt, "run_id_in_tool_args": True,
                   "tool_call_count": len(commands),
                   "tool_error_count": sum(1 for c in calls if c["is_error"]),
                   "write_calls": len(write_calls),
                   "write_calls_ok": all(per_endpoint.values()),
                   "write_endpoints_ok": per_endpoint},
    }
