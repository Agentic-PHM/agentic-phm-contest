"""Shared, secret-safe invocation boundary for the local AGH runtime."""
from __future__ import annotations

import json
import os
import shutil
import re
import subprocess
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class AGHError(RuntimeError):
    """A safe-to-display AGH failure with a stable machine-readable category."""

    def __init__(self, category: str, message: str, detail: str = ""):
        super().__init__(message)
        self.category = category
        #: 原始 stderr 等诊断细节：**只在进程内**用于程序判断（如识别会话级故障），
        #: 不写日志、不进 API、不落盘——日志里只出现脱敏后的摘要。
        self.detail = detail


def home() -> Path:
    """AGH_HOME 解析（证据索引等模块复用同一默认，避免两处硬编码）。"""
    agh_dir = Path(os.environ.get("AGH_DIR", r"D:\agh-contest"))
    return Path(os.environ.get("AGH_HOME", str(agh_dir / "agh-home")))


def _configuration():
    agh_dir = Path(os.environ.get("AGH_DIR", r"D:\agh-contest"))
    agh_home = home()
    profile = os.environ.get("PHM_AGH_PROFILE", "local-dev").strip()
    node = agh_dir / "node24" / "node.exe"
    runtime = agh_dir / "runtime" / "agnes.mjs"
    if not profile:
        raise AGHError("profile_invalid", "未配置 AGH profile。")
    if not node.is_file() or not runtime.is_file():
        raise AGHError("runtime_missing", "未找到 AGH 运行时，请检查 AGH_DIR。")
    return agh_dir, agh_home, profile, node, runtime


def _invoke(arguments: list[str], *, timeout: float, command_first: bool = False,
            workspace: str | None = None) -> str:
    agh_dir, agh_home, profile, node, runtime = _configuration()
    cli_options = ["--profile", profile, "--cwd", str(workspace or REPO_ROOT)]
    command_arguments = [*arguments, *cli_options] if command_first else [*cli_options, *arguments]
    command = [str(node), str(runtime), *command_arguments]
    environment = dict(os.environ)
    environment["AGH_HOME"] = str(agh_home)
    try:
        result = subprocess.run(
            command,
            cwd=str(agh_dir),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise AGHError("timeout", "AGH 调用超时。") from None
    except FileNotFoundError:
        raise AGHError("runtime_missing", "无法启动 AGH 运行时，请检查 AGH_DIR。") from None
    except OSError:
        raise AGHError("runtime_unavailable", "AGH 运行时无法启动。") from None

    if result.returncode != 0:
        # 退出码是排查的唯一线索（2026-10-13：两次工具失效用例只留下 "cli_failed"，
        # 看不出是崩了、被拒了还是配置错）。stderr 可能含凭据，默认**不落盘**；
        # 需要诊断时用 PHM_AGH_DEBUG_STDERR=1 临时打开，并做凭据脱敏。
        detail = _stderr_hint(result.stderr)
        raise AGHError("cli_failed", f"AGH 调用失败（退出码 {result.returncode}{detail}）。",
                       detail=result.stderr or "")
    return result.stdout or ""


#: 凭据脱敏：stderr 里不得出现疑似密钥的连续 token
_SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{6,}|[A-Fa-f0-9]{32,}|Bearer\s+\S+)", re.IGNORECASE)


def _stderr_hint(stderr: str) -> str:
    """把 stderr 压成一行安全提示。默认只回报长度；调试开关打开时附脱敏后的尾部。"""
    text = (stderr or "").strip()
    if not text:
        return "，stderr 空"
    if os.environ.get("PHM_AGH_DEBUG_STDERR") != "1":
        return f"，stderr {len(text)} 字节（设 PHM_AGH_DEBUG_STDERR=1 可查看脱敏内容）"
    tail = _SECRET.sub("<redacted>", text[-400:].replace("\n", " "))
    # 错误消息在**开头**（2026-10-13 实测：只留尾部 400 字符拿到的是堆栈尾巴、
    # 真正的报错被截掉，白打开一次调试开关）。头尾都给。
    head = _SECRET.sub("<redacted>", text[:400].replace("\n", " "))
    if len(text) <= 400:
        return f"，stderr: {head}"
    return f"，stderr 头部: {head} … 尾部: {tail}"


#: 会话级故障的签名：AGH 的工作区会话按 cwd 复用，长期累积后**打不开**
#: （2026-10-13 实测：本项目工作区会话建于 10-02、累积 5365 条事件后，
#: `-p` 一律 2 秒内以 INTERNAL_ERROR(-32603) / RESULT_INVALID 失败，
#: 而同一命令换任意其它 cwd 立即成功）。这不是提示词或工具的问题，
#: 所以不能靠重试同一工作区解决——必须换工作区（= 新建会话）。
_SESSION_BROKEN = ("INTERNAL_ERROR", "RESULT_INVALID", "DAEMON_CONNECTION_CLOSED")


def _session_broken(exc: AGHError) -> bool:
    """判定是否为**会话级**故障（该 cwd 的 AGH 会话已不可打开）。

    必须看进程内的 `detail`（原始 stderr），不能只看消息：消息里的 stderr 默认被省略，
    只按消息判断会导致检测随调试开关的有无而时灵时不灵（2026-10-13 实测踩到：
    验收步骤 ② 因此没触发自愈、直接判 FAIL）。"""
    if getattr(exc, "category", "") != "cli_failed":
        return False
    hay = f"{exc}\n{getattr(exc, 'detail', '') or ''}"
    return any(k in hay for k in _SESSION_BROKEN)


def _scratch_workspace() -> str:
    """每次运行独立的临时工作区：AGH 据此新建一个干净会话。

    不做删除/改写 AGH 的会话库（那会毁掉既有审计历史）；只是换一个 cwd 让 AGH 开机新会话。
    只复制 `.agh/skills/`（技能自动加载需要；F2），**不复制 data/**——临时工作区里没有
    scenario.json 真值，诊断智能体即使用相对路径也读不到（P1 真值隔离的纵深防御）。"""
    base = home() / "scratch-workspaces"
    path = base / f"ws-{os.getpid()}-{int(time.time() * 1000)}"
    (path / ".agh").mkdir(parents=True, exist_ok=True)
    skills = REPO_ROOT / ".agh" / "skills"
    if skills.is_dir():
        shutil.copytree(skills, path / ".agh" / "skills")
    return str(path)


#: 允许重试的瞬时失败类别（AGH 侧偶发内部错误/连接被关，重试即恢复）
_RETRYABLE = ("cli_failed", "timeout")


def _retry_backoff():
    """重试退避秒数（逗号分隔）。可配置是为了测试：默认 3s/8s 的退避会让跑测试白白等 11 秒，
    还会打乱时序敏感用例的节奏（2026-10-13 实测：加退避后 test_channels 的 ETA 用例在全量跑时挂）。
    与项目其它旋钮一致，用环境变量控制。"""
    raw = os.environ.get("PHM_AGH_RETRY_BACKOFF", "3,8")
    try:
        return tuple(float(x) for x in raw.split(",") if x.strip())
    except ValueError:
        return (3.0, 8.0)


def _invoke_retrying(arguments: list[str], *, timeout: float, command_first: bool = False,
                     workspace: str | None = None):
    """带退避的重试封装。

    2026-10-13 实测：AGH 调用存在约 1/10 量级的**偶发**失败（`INTERNAL_ERROR`、
    `DAEMON_CONNECTION_CLOSED`，stderr 只有百来字节；同一命令立刻重跑就成功）。
    此前只有"会话"层重试一次，**provider 预检没有重试**——预检偶发一次失败就把整轮诊断
    判死（真机验收 ③ 就是这样间歇性 FAIL 的）。这里对两类瞬时失败统一退避重试。"""
    last = None
    deadline = time.monotonic() + timeout
    for attempt, delay in enumerate((0.0, *_retry_backoff()), start=1):
        remaining = timeout if attempt == 1 else deadline - time.monotonic()
        if remaining <= delay:
            raise AGHError("timeout", "AGH总执行预算已耗尽。")
        if delay:
            time.sleep(delay)
        try:
            return _invoke(arguments, timeout=remaining - delay, command_first=command_first,
                           workspace=workspace)
        except AGHError as exc:
            if exc.category not in _RETRYABLE:
                raise
            last = exc
    raise last


def run_prompt(prompt: str, *, timeout: float, mode: str = "text") -> str:
    """Run one model-backed prompt through AGH; never invokes a provider directly.

    --standalone：不复用持久工作区会话——daemon 重启后旧会话内核句柄失效会导致全部 turn
    1000ms 超时中止（2026-10-11 实测）。
    注意：实测 session_key 仍按工作区复用（16/16 次诊断落在同一 session_key），
    所以 run_id ↔ session **不是** 1:1；关联粒度是 turn，见 phmlite/agh_evidence.py。

    2026-10-13：正因为 session_key 按工作区复用，该会话会一直增长直到**无法打开**。
    命中会话级故障时改用独立工作区（新建会话）——这是自愈而不是掩盖：换工作区后若仍失败，
    说明不是会话问题。瞬时失败另由 `_invoke_retrying` 退避重试。"""
    if mode not in {"text", "json"}:
        raise AGHError("invalid_request", "AGH 输出模式无效。")
    args = ["-p", prompt, "--mode", mode, "--standalone"]
    deadline = time.monotonic() + timeout
    workspace = _scratch_workspace() if os.environ.get("PHM_AGH_FRESH_WORKSPACE") == "1" else None
    try:
        return _finish(_invoke_retrying(args, timeout=timeout, workspace=workspace))
    except AGHError as exc:
        if not _session_broken(exc):
            raise
        scratch = _scratch_workspace()
        _log_session_rotation(scratch, exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AGHError("timeout", "AGH总执行预算已耗尽。") from None
        return _finish(_invoke_retrying(args, timeout=remaining, workspace=scratch))


def _finish(output: str) -> str:
    # daemon 刚启动时的首跑存在竞态：会话建好、yolo 已开，但 prompt 未入队即静默退出 0。
    # 空回复一律按失败处理（AGH 的 hook-max-tokens 修复保证正常路径输出非空）。
    if not (output or "").strip():
        raise AGHError("invalid_response", "AGH 会话空回复（疑似启动竞态或网关过载），请重试。")
    return output


def _log_session_rotation(scratch: str, exc: AGHError) -> None:
    """把"换了工作区"记进 stderr：诊断日志里要能看出这一次不是默认工作区。"""
    print(f"agnes: 默认工作区会话不可用，改用独立工作区重试（{scratch}）"
          f"：{str(exc)[:200]}", file=sys.stderr)


def probe_provider(timeout: float = 45) -> None:
    """Verify the selected provider and model through AGH without exposing output.

    走 `_invoke_retrying`：预检同样是 AGH 调用，会被同一个偶发内部错误打中；
    此前没有重试，一次偶发就把整轮诊断判死（见 `_invoke_retrying` 的说明）。"""
    output = _invoke_retrying(
        ["doctor", "provider", "--probe", "--json"], timeout=timeout, command_first=True
    )
    try:
        report = json.loads(output)
    except (TypeError, json.JSONDecodeError):
        raise AGHError("provider_unavailable", "AGH 返回了无法识别的连接验证结果。") from None

    provider = next((row for row in report if isinstance(row, dict) and row.get("name") == "provider"), None) \
        if isinstance(report, list) else None
    if not provider or provider.get("status") != "ok":
        raise AGHError("provider_unavailable", "Agnes 模型连接验证失败，请检查 Key 和 AGH profile。")
