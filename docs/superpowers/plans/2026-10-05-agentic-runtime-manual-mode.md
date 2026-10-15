# AGH 统一调用与人工备用模式实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Agnes 模型调用统一到 AGH，增加明确的人工备用模式和本地 API Key 配置，同时确保 AGH 故障时不会伪造智能诊断结果。

**Architecture:** 建立共享 AGH CLI 调用层，聊天和自动诊断都通过该层运行；配置服务从当前 AGH profile 解析 Agnes `credentialRef` 并安全写入凭据库。后端持久化 `agent`/`manual` 模式并暴露真实状态，前端据此显示增强模式、人工备用或智能体不可用。

**Tech Stack:** Python 3.11、FastAPI、SQLite/JSON 本地配置、Agnes Harness CLI、React、Ant Design、Vite。

**Spec:** `docs/superpowers/specs/2026-10-05-agentic-runtime-manual-mode-design.md`

**Spec status:** 已通过用户审阅（2026-10-05）。

## Global Constraints

- 所有大模型调用都经由 AGH，且只使用 Agnes 模型。
- AGH/模型失败时不执行本地规则诊断，不写入诊断报告或自动工单。
- 基础监测和阈值报警在 AGH 离线时保持可用。
- 人工备用模式需要操作员明确进入，并在重启后保持；AGH 恢复不自动切回。
- API Key 只存本地 AGH 凭据库；不进入浏览器存储、API 响应、URL、命令行参数或应用日志。
- 仿真信号只能描述为程序合成数据，不能描述为现场实测数据。

## Review Focus

- `AGH_HOME` 未设置、AGH 目录缺失或 profile 配置损坏时，设置页给出可操作错误且不回退到直连 API。
- 凭据引用缺失或指向非 Agnes provider 时，拒绝写 Key，保留现有凭据。
- Key 有空白或命令行特殊字符时，不经 shell 拼接、不出现在参数或日志里。
- AGH 在诊断途中离线时，将运行标记失败，不落报告/自动工单，也不继续显示虚假进度。
- 数据库里有旧报告而当前模式是人工备用时，前端仍正确标明旧记录，不能将旧结论算成当前运行成功。
- 所有模型交互路径都只经过 AGH；静态巡检提示需明确是监测规则信息，不得包装为 AGH 推理。
- 无效 API Key、缺少模型配置和 AGH daemon 离线均产生明确不可用状态；不能静默切到本地规则或直连模型。
- 状态轮询不可每次启动慢速模型探测；状态应使用快速运行时健康状态及最近一次成功/失败的 provider probe，并在保存 Key 后将验证标记为待验证。

---

### Task 1: 共享 AGH 调用层

**Files:**
- Create: `phmlite/agh_runtime.py`
- Modify: `phmlite/autodiag.py`
- Modify: `scripts/setup_agh.md`

**Interfaces:**
- Produces: `run_prompt(prompt: str, *, timeout: float, mode: str = "text") -> str`。以参数数组调用包内 AGH runtime，profile 使用统一的当前配置（默认 `local-dev`），环境变量传递 `AGH_HOME`；非零退出、超时和运行时缺失转换为不含密钥的明确异常。
- Produces: `probe_provider(timeout: float = 45) -> None`。使用 AGH `doctor provider --probe --json` 验证当前 Agnes 凭据，不打印模型回复或凭据。
- Autodiag 后续只通过该模块启动诊断会话。

- [x] **Step 1: 新建 `agh_runtime.py` 并实现路径解析、无 shell 子进程调用和超时/退出错误归类。**
- [x] **Step 2: 将 `autodiag._run_agh` 改为调用共享入口，保持现有 AGH 会话审计目录和日志脱敏。**
- [x] **Step 3: 更新 AGH 部署说明，列明由设置页写入当前 profile 凭据，保留手工部署步骤作为恢复方式。**
- [x] **Step 4: 检查错误文本、命令参数和新日志不包含 Key。**
- [x] **Step 5: 新增 `tests/test_agh_runtime.py` 覆盖运行时路径缺失、超时、非零退出、stdout/stderr 脱敏及参数数组调用。**
- [x] **Step 6: 在 PowerShell 运行 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:\Python311\python.exe' -m pytest tests/test_agh_runtime.py -q`，绕过环境中的 Xonsh pytest 插件初始化问题。**
- [x] **Step 7: 中文提交：`重构：统一通过 AGH 调用 Agnes`。**

### Task 2: AGH Key 设置服务

**Files:**
- Create: `phmlite/agent_settings.py`
- Modify: `phmlite/server.py`
- Modify: `scripts/setup_agh.md`

**Interfaces:**
- Produces: `get_agnes_key_status() -> dict`，只返回 `{configured: bool, profile: str}`。
- Produces: `save_agnes_api_key(api_key: str) -> None`，读取当前 AGH `configuration.json`，解析启用的 `agnes-ai` account 的 `credentialRef`，将 `{version: 1, kind: "api-key", provider: "agnes-ai", value: api_key}` 原子写入该引用对应的 secrets 文件。
- Produces: `POST /api/settings/agnes-key` 接受 `{api_key}`；`GET /api/settings/agnes-key` 仅返回设置状态；`POST /api/settings/agnes-key/probe` 通过 Task 1 的 AGH provider probe 验证。
- 设置写接口仅接受 loopback 且同源请求；错误响应不含密钥。

- [x] **Step 1: 创建配置模块，解析 profile 配置、provider 和 credentialRef；拒绝不在 `<AGH_HOME>/secrets/agnes-ai/` 下的目标。**
- [x] **Step 2: 实现临时空文件 ACL 加固后再写入凭据 JSON，并原子替换目标；使用当前 Windows 用户与 SYSTEM 显式私有 ACL，任何 ACL 子步骤失败都不覆盖旧 Key。**
- [x] **Step 3: 增加只返回 configured 状态的 GET 接口、Key 写入 POST 接口和 loopback/same-origin 校验。**
- [x] **Step 4: 增加仅经 AGH 执行的 provider probe 接口；把 profile/运行时/凭据/模型错误映射成不含敏感值的响应。**
- [x] **Step 5: 新增 `tests/test_agent_settings.py` 覆盖 Key 状态不返回秘密、有效/无效 credentialRef、路径穿越、ACL 失败不覆盖旧值、原子替换，以及请求来源限制。使用临时 profile 与虚构 Key。**
- [x] **Step 6: 在 PowerShell 运行 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:\Python311\python.exe' -m pytest tests/test_agent_settings.py -q`。**
- [x] **Step 7: 中文提交：`feat：增加 Agnes Key 本地安全配置接口`。**

### Task 3: 聊天模型调用改为 AGH

**Files:**
- Modify: `phmlite/agnes_chat.py`
- Modify: `phmlite/server.py`
- Modify: `dashboard/src/global-agent/GlobalAgent.jsx`

**Interfaces:**
- `agnes_chat.ask(message, history)` 经 Task 1 的 `run_prompt` 返回结构化 `{reply, actions}`。
- `agnes_chat.summarize(message, draft_reply, results)` 也经 AGH；任何阶段失败都返回明确 AGH 不可用错误，不返回虚构的完成话术。
- `server._execute_agent_action` 的白名单保持不变，失败动作继续以 `ok: false` 返回。

- [x] **Step 1: 在聊天系统提示中要求 AGH 会话只返回严格 JSON `{reply, actions}`，限制操作仍为现有六种白名单动作。**
- [x] **Step 2: 删除 `urllib.request`、`_call_api` 和 `.env` 模型直连；ask 与 summarize 统一走 `run_prompt`。**
- [x] **Step 3: 后端把 AGH 未配置、不可用、超时、非零退出映射为明确 HTTP 错误，且不执行未成功解析的动作。**
- [x] **Step 4: 前端聊天窗口显示 AGH/Key 明确错误，不播放成功动作或完成播报。**
- [x] **Step 5: 新增 `tests/test_agnes_chat_api.py`，注入 AGH runner 并断言 ask/summarize 使用共享 AGH 接口；异常、无效 JSON 或缺少 reply 时不执行动作、不返回成功话术。**
- [x] **Step 6: 在 PowerShell 运行 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:\Python311\python.exe' -m pytest tests/test_agent_actions.py tests/test_agnes_chat_api.py -q`。**
- [x] **Step 7: 中文提交：`重构：聊天与操作总结统一经 AGH 执行`。**

### Task 4: 后端模式状态与失败收尾

**Files:**
- Create: `phmlite/operation_mode.py`
- Modify: `phmlite/server.py`
- Modify: `phmlite/autodiag.py`
- Modify: `phmlite/db.py` (only if manual work-order source requires a constrained enum)

**Interfaces:**
- Mode values: `agent` and `manual`; persisted under the server data directory.
- Produces: `GET /api/operation/mode -> {mode, agh_available, key_configured}` and `POST /api/operation/mode` accepting `{mode}`.
- In agent mode, `AutoDiag.observe()` may dispatch only if AGH provider is available; dispatch failures end in `failed` with a safe reason.
- Agent report/work-order writes require the current active AGH run ID; task rows persist that ID. A failed AGH session rolls back its partial agent artifacts.
- In manual mode, `observe()` does not dispatch. Manual work orders are stored with source `manual` and are not associated with an AGH run.

- [x] **Step 1: Add atomic persisted mode state with default `agent`; do not equate `agent` selected with AGH ready.**
- [x] **Step 2: Add mode/status endpoints and validate mode changes; require AGH runtime health and Agnes provider probe before switching back to `agent`.**
- [x] **Step 3: Remove `_run_local`, `_rule_diagnose`, local signal/diagnostic imports and fallback report/task writes.**
- [x] **Step 4: Make every AGH failure close the active run as `failed`, emit a categorized event, and leave reports/tasks untouched.**
- [x] **Step 5: Preserve the existing environment override as an automatic-diagnosis kill switch; expose its disabled state without claiming a run began.**
- [x] **Step 6: Update autodiag and operation-mode tests for fail-closed behavior, manual no-dispatch, persistence, probe requirement, and recovery reset.**
- [x] **Step 7: Focused verification: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:\Python311\python.exe' -m pytest tests/test_autodiag.py tests/test_operation_mode.py tests/test_agnes_chat_api.py -q` → 23 passed.**
- [x] **Step 8: 中文提交：`feat：增加人工备用模式并移除规则诊断回退`。**
- [x] **Final review follow-up:** Bind agent report/work-order writes to the active AGH run ID, migrate task rows with nullable `run_id`, and remove partial artifacts if an AGH session fails after tool writes.

### Task 5: 模式与诊断状态界面

**Files:**
- Modify: `dashboard/src/App.jsx`
- Modify: `dashboard/src/pages/AgentDiagnosis.jsx`
- Modify: `dashboard/src/pages/Workflow.jsx`
- Modify: `dashboard/src/pages/Report.jsx`
- Modify: `dashboard/src/global-agent/GlobalAgent.jsx`
- Modify: related styles under `dashboard/src/`

**Interfaces:**
- Header receives `GET /api/operation/mode` status and displays agent-ready, agent-unavailable or manual-mode state.
- A deliberate operator action calls `POST /api/operation/mode` to enter manual mode or return to agent mode.
- AgentDiagnosis and Workflow consume `/api/autodiag/status`; they never infer active diagnostics solely from elapsed time or alarm/report timestamps.

- [x] **Step 1: Add a visible mode badge and explicit “进入人工备用模式” / “切回智能体模式” controls.**
- [x] **Step 2: Display AGH offline, missing Key, model error and failed run as distinct non-success states.**
- [x] **Step 3: In manual mode, disable chat and autonomous diagnosis affordances while leaving monitoring/alarm navigation active.**
- [x] **Step 4: Replace timer-derived investigating/verify states in AgentDiagnosis and Workflow with backend autodiag state.**
- [x] **Step 5: Ensure stale reports are labeled as history and do not override current mode/failure status.**
- [x] **Step 6: Review state transitions with Node tests for loading, no-Key, offline, failed, manual and recovered states; controls use the same readiness gate.**
- [x] **Step 7: In `dashboard/`, `npm test` → 9 passed; `npm run build` → success, output in `dashboard/dist/`.**
- [x] **Step 8: 中文提交：`feat：展示 AGH 状态与人工备用工作流`。**

### Task 6: 设置页与 Agnes Key 录入

**Files:**
- Create: `dashboard/src/pages/Settings.jsx`
- Create: `dashboard/src/pages/settings.css`
- Modify: `dashboard/src/App.jsx`
- Modify: `dashboard/src/api.js` if a POST helper is needed

**Interfaces:**
- Settings page calls Task 2 status/save/probe endpoints.
- The input is a password field; after save it is cleared. The UI displays configured/not-configured and probe result only, never the key or suffix.

- [x] **Step 1: Add the Settings navigation entry and page using the existing Ant Design layout.**
- [x] **Step 2: Implement password input, save action, configured state and explicit probe action.**
- [x] **Step 3: Show actionable error and restart guidance when AGH caches credentials; never echo the submitted value.**
- [x] **Step 4: Inspect that successful save clears component state and UI renders only configured/probe status; no live Key was entered.**
- [x] **Step 5: In `dashboard/`, `npm test` → 12 passed; `npm run build` → success, output in `dashboard/dist/`.**
- [x] **Step 6: 中文提交：`feat：增加 Agnes Key 设置页面`。**

### Task 7: 文档和交付说明

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-10-02-handoff-agent-driven-mode.md`
- Modify: `evidence/checklist.md`
- Modify: `scripts/setup_agh.md`
- Modify: `docs/superpowers/specs/2026-10-05-agentic-runtime-manual-mode-design.md`

- [x] **Step 1: Update README’s data wording to “程序合成的仿真振动信号” and remove the “不含任何真实设备或客户数据” sentence fragment.**
- [x] **Step 2: Document first-run Key setup, AGH startup, mode switching and the exact functionality unavailable when AGH is offline; update setup_agh to remove the obsolete `.env` injection path.**
- [x] **Step 3: Update contest evidence language so only AGH-generated diagnostic artifacts count as the agentic closure; manual mode is the baseline comparison.**
- [x] **Step 4: Confirm the README describes signals as program-generated simulation data without making unsupported real-device/customer-data claims; remove the provenance inference from the design spec.**
- [ ] **Step 5: 中文提交：`文档：更新智能体模式与本地部署说明`。**

### Task 8: 全量回归与安全检查

**Files:**
- Verify: `tests/`
- Verify: `dashboard/`
- Verify: final diff and tracked/untracked files

- [x] **Step 1: 在 PowerShell 运行 `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:\Python311\python.exe' -m pytest tests/ -q`；确认原有 46 个测试全部通过，并记录新增测试后的总数（84 passed）。**
- [x] **Step 2: Run `npm run build` from `dashboard/` after all frontend changes (success; Vite reports the existing large-chunk warning).**
- [x] **Step 3: Search the repository for direct Agnes API endpoints, model-key reads in the browser, local-rule fallback writes, and stale timer-derived diagnosis state; no matches in the runtime/frontend source.**
- [x] **Step 4: Inspect the final diff and confirm no `.env`, key material, SQLite journal, generated runtime secret, or unrelated local data is staged.**
- [x] **Step 5: Record that live end-to-end AGH provider verification needs the operator's own Key; no Key is requested or displayed.**

## Execution Notes

- Work in an isolated worktree when executing; leave the running checkout and SQLite journal untouched.
- Use temporary test profiles and fabricated keys in tests; never use or print the currently configured private key.
- Do not stop or mutate the already-running application and AGH services while implementing. Any live smoke check must use an isolated data directory and test credentials.
- The user requires the `D:\Python311` full-suite check; this environment needs `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` to avoid an unrelated Xonsh console initialization error.
- Build the frontend from `dashboard/` only.
- After implementation, inspect the final diff and report any AGH runtime behavior that could not be verified without the operator's own Key.
