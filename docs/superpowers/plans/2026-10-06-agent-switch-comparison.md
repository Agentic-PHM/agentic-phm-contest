# 智能体开关对照实验实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 人工备用模式只展示基础监测与阈值报警；智能体增强模式保留 AGH 吉祥物与诊断闭环，并用相同故障完成可复现 A/B 验证。

**Architecture:** 复用现有 `agent` / `manual` 模式 API 和顶部切换按钮，不增设独立开关。将全局吉祥物作为 `MainPage` 的条件子组件，仅在确认运行模式为 `agent` 后挂载；人工模式因此卸载聊天、轮询、主动巡检和播报，而主页面监测轮询继续运行。端到端 A/B 结果记录在独立证据文档中。

**Tech Stack:** Python 3.11、FastAPI 现有模式 API、React、Ant Design、Node.js 内置 `node:test`、Vite。

**Spec:** `docs/superpowers/specs/2026-10-06-agent-switch-comparison-design.md`

**Avatar settings spec:** `docs/superpowers/specs/2026-10-06-agent-avatar-settings-design.md`

## Global Constraints

- 关闭智能体只停止吉祥物、聊天、主动播报和自主诊断；监测、趋势及阈值报警继续工作。
- AGH 或模型失败时不生成本地规则诊断结论、诊断报告或智能体来源工单。
- 复用人工备用模式持久化和智能体恢复验证规则；不得创建第二套开关状态。
- A/B 两组使用同一 `BPFO / severity=0.8`；实验结束复位场景并恢复实验前模式，不删除原有数据库记录。

## Review Focus

- **模式尚未加载或 API 暂不可用**：全局吉祥物不能提前显示；`shouldRenderGlobalAgent` 对空值必须返回 `false`。由 Task 1 状态测试覆盖。
- **人工备用模式且旧诊断状态为 running/done**：吉祥物、旧事件播报和聊天不能残留；组件应卸载。由 Task 2 浏览器切换检查覆盖。
- **人工备用模式故障报警**：报警仍产生，但自动诊断 `run_id` 不变、报告和智能体工单数量不增加。由 Task 3 A/B 实验覆盖。
- **切回智能体模式但 AGH/模型不可用**：现有顶部状态继续标记不可用，聊天和 AGH 诊断不应假装可用。由 Task 2 浏览器检查及 Task 3 的失败记录规则覆盖。
- **实验中途失败或初始模式不是 agent**：不报告成功；最终将故障清零并恢复原模式。由 Task 3 收尾步骤覆盖。

---

### Task 1: 智能体展示状态判定

**Files:**
- Modify: `dashboard/src/agentState.js`
- Test: `dashboard/tests/agent-state.test.js`

**Interfaces:**
- Produces: `shouldRenderGlobalAgent(operation) -> boolean`。仅 `operation.mode === 'agent'` 时返回 `true`；空值、未知模式和 `manual` 返回 `false`。

- [x] **Step 1: Write the failing test**

在 `dashboard/tests/agent-state.test.js` 增加 `shouldRenderGlobalAgent` 的断言：agent 为 `true`；manual、null 和未知 mode 为 `false`。用动态导入后先断言导出为函数，保证缺少实现时测试以断言失败结束。

- [x] **Step 2: Run the focused test and confirm RED**

Run: `node --test dashboard/tests/agent-state.test.js`（工作目录 `dashboard/`）
Expected: 新增断言失败，提示 `shouldRenderGlobalAgent` 尚未导出。

- [x] **Step 3: Implement the minimal helper**

在 `dashboard/src/agentState.js` 导出 `shouldRenderGlobalAgent(operation)`，按接口只判断 `operation?.mode === 'agent'`。

- [x] **Step 4: Run the focused test and confirm GREEN**

Run: `node --test dashboard/tests/agent-state.test.js`（工作目录 `dashboard/`）
Expected: 所有 agent-state 测试通过。

- [x] **Step 5: Include the state gate in the implementation commit**

状态判定与其 UI 使用方一并提交，避免出现单独落地但尚未接线的中间版本。

### Task 2: 人工备用模式卸载吉祥物

**Files:**
- Modify: `dashboard/src/App.jsx`
- Modify: `dashboard/src/global-agent/GlobalAgent.jsx`
- Modify: `dashboard/src/agentState.js`
- Test: `dashboard/tests/agent-state.test.js`

**Interfaces:**
- `MainPage` 将已轮询的 `operation.data` 传给 `GlobalAgent`；`GlobalAgent({ operation })` 不再单独轮询 `/api/operation/mode`。
- `MainPage` 只在 `shouldRenderGlobalAgent(operation.data)` 为真时挂载 `GlobalAgent`。模式切换 POST 成功后的现有 `operation.refetch()` 负责立即刷新挂载状态。
- 人工备用提示文案明确“吉祥物、聊天与主动播报已关闭；监测与报警继续运行”。

- [x] **Step 1: Add a failing status-copy assertion**

在 `dashboard/tests/agent-state.test.js` 的人工模式用例中，断言 `getAgentAvailability({mode:'manual', ...})` 的 detail 明确包含吉祥物/主动播报关闭及监测/报警继续运行的状态；运行焦点测试并确认断言失败。

- [x] **Step 2: Update the manual-mode status detail**

在 `dashboard/src/agentState.js` 更新人工模式 detail；保持 `label: '人工备用模式'` 和现有 `code: 'manual'`，不改变后端模式协议。

- [x] **Step 3: Run the focused test and confirm GREEN**

Run: `node --test dashboard/tests/agent-state.test.js`（工作目录 `dashboard/`）
Expected: 新状态文案断言及既有状态测试通过。

- [x] **Step 4: Wire conditional component mounting**

在 `dashboard/src/App.jsx` 将 `GlobalAgent` 移入 `MainPage` 渲染树，并依据 `shouldRenderGlobalAgent(operation.data)` 条件挂载、传递 `operation.data`。在 `dashboard/src/global-agent/GlobalAgent.jsx` 接收 `operation` prop，删除该组件自己的 operation-mode 轮询；保留其余 hooks 和 agent 模式能力。人工模式下组件卸载，所有组件轮询、计时器、对话状态与吉祥物画面随之销毁。

- [x] **Step 5: Run the dashboard test suite**

Run: `npm test`（工作目录 `dashboard/`）
Expected: 所有 Node 测试通过。

- [x] **Step 6: Include the UI mode gate in the implementation commit**

状态门控、动态报警提示和系统设置形象迁移会作为一个可部署前端版本提交。

### Task 3: A/B 运行验证与交付记录

**Files:**
- Create: `evidence/agent-switch-ab-2026-10-06.md`
- Modify: `evidence/checklist.md`
- Modify: `evidence/manual_check.md`
- Modify: `docs/superpowers/plans/2026-10-02-handoff-agent-driven-mode.md`
- Modify: `README.md`

**Interfaces:**
- Experiment reads existing endpoints: `/api/operation/mode`, `/api/scenario/get`, `/api/scenario/set`, `/api/device`, `/api/alarm/history`, `/api/autodiag/status`, `/api/report/list`, and task list API.
- Experiment writes only through existing mode/scenario UI or APIs; it does not delete DB rows or modify credentials.

- [x] **Step 1: Run Python regression suite**

Run in repository root: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; & 'D:\Python311\python.exe' -m pytest tests/ -q`
Expected: all tests pass; preserve and report any pre-existing failure.

- [x] **Step 2: Build the production frontend**

Run in `dashboard/`: `npm run build`
Expected: Vite build succeeds and writes `dashboard/dist/`.

- [x] **Step 3: Capture pre-experiment state**

Record current operation mode, fault configuration, report/task/alarm counts, and active AGH/provider state. Reset only the scenario fault to `none`; retain existing records. Do not start the experiment unless AGH and provider are ready.

- [x] **Step 4: Run the agent-enabled group**

Select/confirm `agent`, inject `bpfo` at severity `0.8`, and record resulting severe alarm, terminal autodiag state, `run_id`, report ID, task ID and elapsed time. Count functional closure only if the current run is completed by AGH and report/task IDs match that run; report the 90-second timing result separately. The 2026-10-06 run closed at about 123 seconds, so the time target failed.

- [x] **Step 5: Run the manual-baseline group**

Reset the scenario to `none`, switch to `manual`, confirm the mascot and chat are absent, then inject the same `bpfo / 0.8` fault. Confirm threshold alarms still occur, the prior autodiag run ID is unchanged, and reports/tasks do not increase during the manual group. Record observations, including any deviation, without deleting generated rows.

- [x] **Step 6: Restore the pre-experiment state**

In a `finally`-style cleanup, set fault type to `none`; restore the original mode. If an API or AGH failure prevents a group from completing, record it as failed and still perform cleanup.

- [x] **Step 7: Write evidence and update completion docs**

Write the actual observed values and run identifiers to `evidence/agent-switch-ab-2026-10-06.md`; update the checklist/readme/handoff to mark the baseline behavior complete only when both groups satisfy the acceptance criteria. Keep the earlier 2026-09-30 run labeled as historical evidence.

- [x] **Step 8: Verify final repository and deploy**

Confirm no `.env`, AGH home, or DB files are staged. Restart PHMLite and confirm `8181` responds; check AGH `4178` only if it was started for the experiment. Confirm the repository test/build results and that experiment cleanup restored scenario and mode.

- [ ] **Step 9: Commit evidence and documentation**

### Task 4: 系统设置中的形象切换与共用昵称

**Files:**
- Create: `dashboard/src/avatarSettings.js`
- Modify: `dashboard/src/pages/Settings.jsx`
- Modify: `dashboard/src/global-agent/GlobalAgent.jsx`
- Modify: `dashboard/src/global-agent/global-agent.css`
- Modify: `dashboard/src/pages/settings.css`
- Test: `dashboard/tests/avatar-settings.test.js`

- [x] 提取形象清单和浏览器偏好读写；保留现有形象索引键，昵称按所有形象共用。
- [x] 系统设置页增加单模型预览、形象按钮和昵称保存；聊天面板移除形象切换入口。
- [x] 通过同页事件和 storage 事件同步当前浏览器内的设置；手动浏览器验收后恢复原偏好。
- [x] `node --test` 通过；系统设置页切换第二/第三形象，修改昵称后确认名牌同步，再恢复默认形象和昵称。

```bash
git add evidence/agent-switch-ab-2026-10-06.md evidence/checklist.md evidence/manual_check.md docs/superpowers/plans/2026-10-02-handoff-agent-driven-mode.md README.md
git commit -m "记录智能体开关 A/B 对照实验"
```

## Final Delivery

- Push the completed Chinese commits to `origin main` through proxy `http://127.0.0.1:7897`.
- Keep the local PHMLite app on `127.0.0.1:8181` and verify after restart; the dashboard is served from `dashboard/dist/` by that backend.
