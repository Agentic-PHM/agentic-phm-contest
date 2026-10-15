"""AGH Shell 白名单（P1 问题7）：用受限工具授权替代 session 级 yolo。

**问题**：`scripts/setup_agh_runtime.py` 往便携运行时注入 `session.setYolo(true)`，让 shell 调用
免逐次审批。那是"任意 Shell 执行"级别的授权，不是"只允许几个工业接口"。

**实测到的运行时语义**（`D:\\agh-contest\\runtime\\agnes.mjs`，2026-10-13 读源码确认）：
  - `49284`: `if (s.yolo || approvalMode === "off") needsAsk = false;`
  - `279464`: `command_policy` 只在 `needsAsk` 为真时才会被求值。
  → **只要 yolo 补丁在，`command_policy` 完全不被求值**，加白名单等于摆设。
  正确的做法是撤掉 yolo、改用白名单：命中 `allow` 的命令免审批，其余命令在无人值守下
  （`on_unavailable: deny`）直接被拒。

**匹配语义**（`279348 matchPolicy` / `279334 normalizeArgv`）：
  - `tool` 与工具名做全匹配，且不得自带锚点；
  - `allow` 规则的 `argv` 对**整条命令**做全匹配（`^(?:pattern)$`），且必须锚定绝对路径
    （`requiresAbsolutePath`，安全设计：只允许预先批准绝对路径可执行文件）；
  - `deny` 优先于一切；未命中任何规则则回落默认审批；
  - **shell 命令串会被折叠**：`command.trim().replace(/\\s+/g, " ")`——多行脚本在匹配时是单行。

因此本模块同时提供"提示词要用的命令形态"与"对应的 allow 规则"，两者同源，避免漂移；
`matches_any_allow()` 复刻运行时的 allow 语义，供离线测试与启动自检使用。
"""
import re

#: 绝对路径 curl（allow 规则强制要求绝对路径；相对调用无法被预先批准）
CURL_ABS = r"C:\Windows\System32\curl.exe"
#: 取证统一使用 `-sS -f`（审查 P1 问题5）：**`-f` 让 HTTP≥400 时 curl 退出非零**，
#: 于是"接口 503"会让工具层报错，而不是被当成"工具成功"。只用 `-s` 时 HTTP 错误仍返回 0，
#: `tool/result.isError=false` 就只能证明"工具没报错"，不能证明业务调用成功。
CURL_FLAGS = "-sS -f"
API_BASE = "http://127.0.0.1:8181"
SHELL_TOOL = "shell"

__all__ = ["CURL_ABS", "CURL_FLAGS", "API_BASE", "SHELL_TOOL", "get_command", "write_command",
           "allow_rules", "matches_any_allow", "profile_overlay_yaml", "verify_command"]


def get_command(url):
    """只读取证命令：绝对路径 curl + 完整 URL（单行，匹配 allow 规则 1）。"""
    return f'{CURL_ABS} {CURL_FLAGS} "{url}"'


def write_command(path, params):
    """写命令：绝对路径 curl + `-G --data-urlencode`，参数由 curl 负责 URL 编码。

    这样提示词不需要让模型自己拼 PowerShell 的 UrlEnc 辅助函数——那正是此前命令形态
    无法被白名单覆盖、只能靠 yolo 放行的原因。"""
    parts = [f'{CURL_ABS} {CURL_FLAGS} -G "{API_BASE}{path}"']
    for key, value in params.items():
        parts.append(f'--data-urlencode "{key}={value}"')
    return " ".join(parts)


def allow_rules():
    """注入到 AGH profile 的 `approval.command_policy` 规则（撤掉 yolo 后生效）。

    读接口**逐条枚举**而不是放行整个 `/api/*`——整改要求的正是"只允许有限几个工业接口"。
    写接口只放行 report/submit 与 task/create 两个端点（参数由 curl 编码，内容不限）。"""
    return [
        {"tool": SHELL_TOOL,
         "argv": r'^C:\\Windows\\System32\\curl\.exe -sS -f '
                 r'"http://127\.0\.0\.1:8181/api/'
                 r'(?:device|trend\?[^"]*|spectrum\?[^"]*|charfreq|equipment-map)"$',
         "action": "allow"},
        {"tool": SHELL_TOOL,
         "argv": r'^C:\\Windows\\System32\\curl\.exe -sS -f -G '
                 r'"http://127\.0\.0\.1:8181/api/(?:report/submit|task/create)" '
                 r'--data-urlencode .*$',
         "action": "allow"},
    ]


def _normalize(command):
    """复刻运行时对 shell 命令的规范化：trim + 空白折叠成单空格。"""
    return re.sub(r"\s+", " ", (command or "").strip())


def matches_any_allow(command):
    """本地复刻 `matchPolicy` 的 allow 语义（全匹配），用于离线自检提示词与策略是否一致。"""
    subject = _normalize(command)
    for rule in allow_rules():
        if rule["action"] != "allow":
            continue
        try:
            if re.fullmatch(rule["argv"], subject):
                return True
        except re.error:
            continue
    return False


def verify_command(command):
    """返回 None 表示该命令会被白名单放行；否则返回可读原因（启动自检用）。"""
    if matches_any_allow(command):
        return None
    return (f"命令未被 Shell 白名单覆盖，受限模式下会被拒：{_normalize(command)[:120]}…")


def profile_overlay_yaml():
    """渲染写入 profile 覆盖层的 YAML 片段（与 setup_agh_runtime.py 共用）。"""
    lines = ["approvals:", "  mode: smart", "  command_policy:"]

    def q(s):
        return "'" + s.replace("'", "''") + "'"

    for rule in allow_rules():
        lines.append(f"    - tool: {q(rule['tool'])}")
        lines.append(f"      argv: {q(rule['argv'])}")
        lines.append(f"      action: {rule['action']}")
    lines.append("  on_unavailable: deny   # 无人值守时未命中白名单一律拒绝，不再靠 yolo 全放行")
    return "\n".join(lines) + "\n"
