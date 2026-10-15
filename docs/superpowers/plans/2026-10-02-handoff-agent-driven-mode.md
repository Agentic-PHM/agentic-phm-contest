# 交接文档：智能体驱动的自主运维模式（当前说明更新于 2026-10-05）

## 当前状态与操作方式（2026-10-05，以本节和 README 为准）

本文件下方保留 2026-10-03 的历史交接内容，记录当时的实现与彩排；其中 `.env` 配置、聊天直连模型、AGH 离线时本地规则诊断等说明已经过时，不代表当前代码行为。

- **主线**：基础监测和阈值报警提供工业系统底座；只有经过 AGH 的智能体会话实际完成，才算自主取证、诊断报告和智能体工单闭环。当前核心实现见 `phmlite/agh_runtime.py`、`phmlite/agnes_chat.py`、`phmlite/autodiag.py` 和 `phmlite/operation_mode.py`。
- **每日启动**：先启动 PHMLite，再在另一个终端启动 AGH；两个终端分别设置相同的 `AGH_DIR`、`AGH_HOME`、profile 和端口。Windows + Git Bash 示例：

  ```bash
  # 在两个终端都执行
  export AGH_DIR='/d/agh-contest'
  export AGH_HOME='D:\agh-contest\agh-home'
  export PHM_AGH_PROFILE='local-dev'
  export AGH_PORT='4178'
  ```

  ```bash
  # 终端一：PHMLite
  cd /d/TSI/agentic-phm-contest && python scripts/run_server.py
  ```

  ```bash
  # 终端二：AGH
  cd "$AGH_DIR" && ./node24/node.exe runtime/agnes.mjs serve --profile "$PHM_AGH_PROFILE" --cwd "D:\TSI\agentic-phm-contest" --port "$AGH_PORT"
  ```

  系统地址为 `http://127.0.0.1:8181/`，演示账号 `admin / 123456`；AGH 使用 4178 端口。部署前按 [`scripts/setup_agh.md`](../../../scripts/setup_agh.md) 初始化 portable runtime 和 profile。
- **首次配置 Key**：登录后进入“系统设置”，输入部署者自己的 Agnes API Key 并保存。Key 写入当前 AGH profile 的本地凭据库；不要放入 `.env`、仓库、命令行或聊天。设置页配置的是已初始化 profile 中的 Agnes account 与 `credentialRef`。后端启动后会等待 AGH 在线和 Key 配置完成，再自动探测一次；需要立即重试时可手动点击连接验证。
- **模式与离线行为**：智能体增强模式要求 AGH 与 Agnes provider 均可用；AGH、Key 或模型失败时，聊天和自动诊断报告/工单失败关闭，不生成规则版替代结论。操作员可明确切换到持久化的人工备用模式；此时监测、趋势、阈值报警和设备视图继续工作，吉祥物、聊天、主动播报、自主诊断及智能体报告/工单暂停。严重报警按当前模式和 AGH 状态显示实际诊断进度，不会统一假报“智能体已介入”。AGH 恢复后不会自动切回，需由操作员显式恢复并通过验证。
- **演示前**：`data/scenario.json` 会保留故障设置。开始演示前调用 `http://127.0.0.1:8181/api/scenario/set?key=fault.type&value=none` 复位；确认界面显示“智能体已就绪”后，在实时监视页注入 BPFO/强度 0.8，不再手动触发诊断。耗时取决于模型响应；2026-10-06 一次闭环耗时约 123 秒，超过 90 秒目标，不能承诺固定时限。人工备用模式下同样可注入故障作对照，只应看到监测变化和阈值报警，不应出现新的智能体报告或工单。
- **数据口径**：演示信号由程序合成，属于仿真振动信号；不要将仿真结果表述为现场测量值。
- **验证命令**：后端使用 `D:\Python311\python.exe -m pytest tests/ -q`；前端测试和构建必须在 `dashboard/` 目录执行。`.env`、AGH `agh-home` 和本机凭据不可提交。若 Key 曾进入共享或公开 Git 历史，向外发布前先轮换。

---

## 历史交接快照（截至 2026-10-03；以下仅供回溯）

> 本文档是跨会话交接件：新会话读完即可接手全部上下文并开工。
> 前置状态：设备总览 3D 数字孪生 + 桌面宠物 + 咨询对话 + 登录视频背景均已上线并验证，
> 全部已提交推送（至 commit 2fa1944/16d6337 之后的 69c8b98/3502590 线）。

---

## 一、一分钟了解项目

- **比赛**：2026 江苏省高校 AI 黑客松， deadline **2026-10-15 12:00**。题面核心：用官方 Agnes API（模型 agnes-3.0-flash，OpenAI 兼容）+ Agnes 智能体框架（AGH 运行时），接入一个真实系统，体现模型的**不可缺性**与工程化落地。
- **本系统**：调相机（同步补偿机组）智能运维平台。故事线：**监测 → 报警 → 智能体自主取证诊断 → 工单生成 → 恢复后自动复测关单**（2026-10-11 起闭环为真实工单生命周期，非话术）。
- **已验证的核心命题**（evidence/checklist.md）：无智能体时系统只有报警行；诊断结论、证据链、置信度、工单全部由智能体产生。
- **仓库**：本目录（D:\TSI\agentic-phm-contest，远端 GitHub 私库 Agentic-PHM/agentic-phm-contest）。

## 二、运行方式（每天开工先跑）

```bash
# 1) 比赛系统（后端+前端构建产物同端口）
cd /d/TSI/agentic-phm-contest && (python scripts/run_server.py > /d/TSI/tsi-log/phmlite.log 2>&1 &)
#    → http://127.0.0.1:8181  登录 admin/123456

# 2) 老系统演示对照（可选）：8080 静态+反代
python D:\TSI\serve_tsi_dist.py 8080
```

- Python = D:\Python311（需 fastapi/uvicorn/numpy/scipy，已装）。
- 一键脚本：D:\TSI\start_all.bat / stop_all.bat（Redis+jar+8181+8080）。
- **构建前端必须在 dashboard 目录**：`cd dashboard && npm run build`（其他目录跑会静默输出到错误位置——已踩坑两次）。
- Agnes API：`.env`（AGNES_API_KEY/AGNES_BASE_URL=https://api.agnes-ai.cn/v1，模型 agnes-3.0-flash，国内直连无需代理）。

## 三、当前已完成（不要重做）

| 模块 | 状态 |
|---|---|
| phmlite 后端 | 16 路仿真通道（轴振/瓦振/转速/温度，与故障注入联动）、四级阈值+连续3点报警、/api/equipment-map、/api/agent/chat（Agnes 知识问答）、/api/report、/api/task、审计中间件；pytest 34 绿 |
| 3D 数字孪生 | Stage3D（engine.js）：调相机 GLB（7.2MB Draco）、9 部件语义、四视图（3D构图/结构拆图[带合成动画]/热力图/振动云图）、部件卡跟随投影+近大远小+稳健包围盒（最大mesh锚定剔除杂件）、视口自适应取景（半角公式已修正：tan(fov/2)） |
| 全局 IP 形象 | GlobalAgent：无框可拖动桌面宠物、7 款 GLB（500MB→3.5MB 压缩）点击切换、8 个程序化动作、登场按时间段问候、每 7 分钟定时播报、点击弹 Agnes 咨询对话（短句通俗，知识=9部件语义+指标口径） |
| 登录页 | 视频背景已接（public/login-bg.mp4/.webm，当前为旧版动画占位，**用户新视频待替换**） |
| 诊断闭环彩排 | evidence/run_20260930_183606：报警→12 次工具调用→BPFO 结论(0.92)→工单，四维评审对照全 ✓ |

## 四、关键文件地图

```
phmlite/server.py          # 全部 API 路由 + 审计中间件 + index no-cache
phmlite/autodiag.py        # 任务②：severe 报警去重 → 自动唤起 AGH -p 诊断（离线降级本地规则）
phmlite/agnes_chat.py      # Agnes 对话客户端 + 知识库系统提示词
phmlite/db.py              # SQLite：eigen_history/alarms/reports/tasks/run_log + 阈值常量
phmlite/dsp.py             # 信号合成与温度模型
data/scenario.json         # 故障注入开关（fault.type: none|bpfo|bpfi, severity 0~1）
data/equipment_map.json    # 9 部件语义表（id/中文名/绑定测点/阈值/机理特征）——智能体与 3D 共用
dashboard/src/global-agent/GlobalAgent.jsx   # 全局桌面宠物（拖动/切换/气泡/对话面板/自动诊断播报）
dashboard/src/pages/overview/stage3d/        # 3D 引擎 + 四视图模式
dashboard/src/pages/overview/stage3d/AgentAvatar3D.jsx  # 形象迷你视窗（动作系统 AGENT_ACTIONS）
dashboard/public/agents/*.glb                # 7 款压缩形象
dashboard/public/login-bg.mp4/.webm          # 登录视频（待用户新素材替换）
agent/skills/diagnosis.md + .agh/skills/diagnosis/SKILL.md  # 诊断技能（两份需同步）
scripts/setup_agh.md       # AGH 便携运行时接入手册（AGH 在 D:\agh-contest，2026-10-03 已重建）
scripts/e2e_autodiag.py    # 任务② E2E 驱动（注入→零人工→记录状态机耗时）
evidence/checklist.md      # 四维评审对照表（评委问答的弹药库）
```

## 五、已知坑（都踩过，别再踩）

1. **构建必须在 dashboard 目录**（错误目录构建会静默输出到别处，dist 不更新）。
2. **浏览器缓存**：server 已对 index.html 发 no-cache；但带旧缓存的浏览器需 Ctrl+Shift+R 一次。验证时用 `?v=随机数` 破缓存。
3. **AI 模型 GLB 不自动播放自带动画**（动画轨含缩放/位移会把形象撑爆移位）；包围盒用"最大 mesh 锚定+邻近合并"（setFromObject 会被杂件撑大）。
4. **取景半角公式**：竖直半视场角 = fov/2（35° 镜头即 17.5°），别把整个 fov 当半角（形象 2 倍超大只露脑袋的元凶）。
5. AGH 的 web_fetch 对 127.0.0.1 有 SSRF 防护 → 智能体取证走 shell+curl（技能里已写）。
6. 每形象可配 `yaw`（朝向）与 `zoom`（大小）：agent-5/6/7 需 yaw:270（5/6 是 2026-10-03 补的，出厂侧身朝右，
   90 会变成背面、270 转正），agent-7 另配 zoom:3.5，其余默认。
   注意：GlobalAgent 对 AgentAvatar3D 的调用**没传 zoom**（组件默认 1），配置里的 zoom 字段目前是死配置。
7. **AGH 运行时已被重建过一次（2026-10-03）**：`D:\agh-contest` 曾被清理，从 vendor zip 按 scripts/setup_agh.md 全套恢复
   （解压→ACL 私有 DACL→注密钥→consent LOCAL→yolo 补丁→serve 4178）。本机主机名是 **AFU**（不是文档旧例 WIN-1IB86RUVKU7），
   icacls 授权必须用 `whoami` 实测账号名；ACL 顺序坑详见 setup_agh.md 第 9 节。
8. **scenario.json 是持久化的**：注入的 fault 跨服务重启保留；演示前先 `scenario/set fault.type=none` 复位，
   否则一开页面轮询就可能触发报警/诊断。注意 2026-10-12 起控制面写接口需登录凭据
   （`X-Operator-Token`），命令行复位要先 POST /api/login 取 token，见 README「演示流程」。
9. 自动唤起诊断的 AGH 会话时长有波动（模型步进延迟），提示词已限定 8 次工具调用 + 明确提交 URL 模板
   （不限定时模型会把 conclusion 填成纯数字）；实测 48~82s，预算上限 AUTODIAG_BUDGET=150s。
10. **3D 部件卡从静态路径 `/models/equipment_map.json` 取映射表**（engine.js MAP_URL），无 no-cache 头，
    改 equipment_map 后浏览器要 Ctrl+Shift+R 强刷一次，否则按启发式缓存拿旧表（部件卡显示旧绑定/--）。
    绑定通道名必须与 phmlite CHANNEL_SPECS 精确一致（2026-10-03 修过一轮："DE轴承温度"→"轴温DE"、
    "NDE轴承温度"→"轴温NDE"、"瓦振6X/6Y"→"瓦振1X/1Y"，旧名永远匹配不到、卡片恒显"--"；
    定子/接线柜的"--"是预留通道，属预期）。
11. **诊断真值隔离（P0，2026-10-11 修复；P1 加固 2026-10-12）**：诊断提示词与全部智能体可见接口
    严禁出现注入真值（fault.type/severity）。scenario 读接口默认脱敏 fault 字段，仅持控制台
    会话凭据者可见完整场景；/api/autodiag/status 也不再回显 fault。
    **2026-10-12 加固**：原门禁是固定字符串 `X-Operator: console`，它同样打在前端包里、可被
    诊断智能体用 `-H` 伪造，且 `/api/scenario/set` 不要凭据也能写；现改为登录签发的随机会话
    token，写接口无凭据一律 403（详见第 22 条）。
    诊断提示词原文落盘 data/autodiag/<run_id>.log 供独立评分器审计：
    `D:/Python311/python.exe scripts/score_autodiag.py --agh-audit D:gh-contestgh-home\dataudit
    --out evidence/autodiag-blind-xxx.json`（核查提示词无真值 + 盲诊结论比对真值）。
    首次盲诊实证：evidence/autodiag-blind-1011-031333.json（PASS，68s，置信度 0.92，
    证据链明确排除 BPFI/BSF/FTF——这是"独立识别"而非"复述答案"的铁证，评委问答直接引用）。
12. **AGH serve 刚启动后的首跑有会话竞态**：CLI 建好会话、yolo 已开但 prompt 未入队即静默退出 0
    （表现为 autodiag missing_report）。已双保险：run_prompt 空回复按 invalid_response 失败 +
    autodiag 无产物时自动重试一次。AGH serve 起后建议先手动跑一条 `-p "只回复ok"` 暖机再演示。
14. **工单生命周期与"闭环"话术（P0 问题3，2026-10-11 落地）**：诊断完成 ≠ 工单创建 ≠ 工单关闭，
    三态在 /api/autodiag/status 的 phases 里显式区分；UI 文案严禁把"工单已生成"说成"已完成/已闭环"。
    现在闭环是真的：恢复正常注入后，autodiag 连续 3 次采样无严重报警 → 自动关单（备注"自动复测通过"）
    → 吉祥物播报"复测通过，工单#N已关闭"（关单先归位再播报，_reset 清事件列表，顺序反了播报会丢）。
    手动接口 GET /api/task/close?id=&note=；自动关单只碰 agent 来源的关联工单，人工单不受影响。
    复测进行中重新注入故障 → 播报"复测中止"、工单保持待处理、同故障允许再次唤起。
17. **P1 三项（2026-10-11）**：①诊断提示词升级为技能同款全流程（趋势+FFT+包络谱+特征频率+
    **三判据交叉验证**：时域/频域/趋势至少两项一致才下结论，证据链必须覆盖三类证据），
    工具调用预算 8→10，实测延迟 +12~16s 仍在 90s 内，两轮盲测 16/16 全对；
    ②阈值统一配置源：**db.py 是唯一权威源**，equipment_map 绑定传感器 warning=notice、
    critical=severe（轴温 65/80、冷风 40/50、油温 50/60、转速 3010/3040，此前 map 里 95/55/3300
    的行业三档口径与运行时报警口径脱节）——tests/test_threshold_consistency.py 是防漂移回归阀，
    改 db 常量必须同步映射表并重建前端（dist 副本也在校验范围内）；
    ③置信度全部标注"模型自评（未做统计校准）"，盲测汇总自动累加实测校准表
    （按自评分桶统计实测正确率，n<5 标注样本不足），两轮 n=16 无一失败但分桶样本仍不足以解释为概率。
19. **真机验收门与耗时余量（P1 两项，2026-10-11）**：pytest 全离线（注入模拟执行器），
    真实模型链路由 **`scripts/acceptance_check.py`** 验收：⓪83181 可达 → ①供应商探测 →
    ②最小 -p 会话 → ③一条真实盲诊用例（判定/延迟/报告/工单断言），任一失败 exit 1。
    **演示日前必跑一次，通过再进演示**（首跑即抓到"8181/4178 后台进程被清、failed 态占用
    去重键卡死再触发"两个真实问题并已修复：failed 不占去重键，环境修复后重新注入可再次唤起）。
    AUTODIAG_BUDGET 默认 150→**240s**（实测 28.7~73.2s、历史离群 123s，150s 曾在验收实测
    146.1s 的运行上会误杀——240s 是余量不是目标值）。requirements.txt 已建（fastapi/uvicorn/
    numpy/scipy/pydantic 锁定为实机验证版本，Python 3.11）。
20. **成功门槛收紧：报告存在 ≠ 诊断成功（P0 问题2，2026-10-12）**：旧 `_settle()` 只要查到本次
    run_id 的报告就记 `done`——关联工单可以没有、执行索引落盘失败也不撤销成功；报告接口的
    `evidence_json` 默认 `"{}"` 且不做任何结构校验，等于"允许没有证据也记成功"。
    现在 `_settle()` 先跑 `_verify_run()` 五项核验：报告存在、证据结构合规（非空数组且每条含
    `step/tool/key_numbers/supports`，key_numbers 非空）、关联工单存在、AGH 工具调用 ≥5、
    业务记录↔AGH 会话双向关联。全过才是 `done`；只缺工单 → `partial`；证据/关联/工具调用/
    索引任一不成立 → `verification_failed`。明细从 `/api/autodiag/status` 的 `verification`
    字段外露（不含完整索引，status 每 2s 轮询）。配套：`/api/report/submit` 拒绝空证据（400）；
    执行索引落盘失败（含 log_dir 不存在，此前被 `_run_agh` 掩盖）会撤销 `done`；
    **自动复测关单只认 `done`**（partial/verification_failed 不替未核验的结论背书）；
    产出过报告的终态（done/partial/verification_failed）都占去重键，failed 仍不占。
    核验索引来源可注入（`index_provider`），与 runner/provider_probe 同一模式，离线单测才能
    验证门禁本身。回归：`tests/test_diagnosis_gate.py` 13 项 + 前端 agent-state 新增 5 项。
21. **采样改由独立循环驱动（P0 问题3，2026-10-12）**：此前 `GET /api/device` 自己合成信号、写特征、
    判报警并唤起诊断，`/api/channels` 与 `/api/health` 也会推进 tick 与温度一阶状态——采样频率
    因此由浏览器请求决定（页面越多越快），没人开页面时监测链路根本不跑。
    现在 `phmlite/sampler.py` 的 `PeriodicSampler` 起一个 daemon 线程，按 `PHM_SAMPLE_INTERVAL`
    （默认 3s；测试 0.2s；<=0 关闭循环退回按需采样）调用 `_simulation_tick(seq)`：一次 tick 里
    设备采样与通道/健康快照**共用同一个样本序号**，设备采样落库并喂看门狗。
    `GET /api/device|channels|health` 只读最近快照（冷启动才同步补一次）。
    报警连续判据绑定 `seq`：`alarms` 表新增 `seq` 列（migrate 自动加），同一 seq 重复判定不计数，
    报警历史与 `/api/device` 快照都带序号。`/api/channels` 的数字改为**按样本**演化而非按请求。
    **测试注意**：采样现按真实时间积分温度（τ 最长 320s），共享会话里用例会继承热积累，
    需要 `conftest.reset_simulation(app)` 复位；凑"连续 3 点"要用 `wait_samples()`/`wait_until()`
    等采样推进，不能再靠连打接口。回归：`tests/test_sampler.py` 9 项。
22. **控制面凭据（P1 问题4，2026-10-12）**：原控制面门禁是固定字符串 `X-Operator: console`——
    它同样存在于前端构建产物里，任何能发 HTTP 的人（含具备 shell 的诊断智能体加一个 `-H`）都能
    伪造；且 `/api/scenario/set` 完全不要凭据，写成功只是响应脱敏。因此原实现只做到"正常诊断
    流程不读真值"，没做到"诊断执行环境够不到真值"。
    现在 `/api/login` 校验账号后签发**随机内存会话 token**（`_OPERATOR_SESSIONS`，只存在于服务端
    内存）；`/api/scenario/set`、`/api/testtools/chaos`、`/api/scenario/get` 完整视图一律要
    `X-Operator-Token`（或 `Authorization: Bearer`），无凭据读=脱敏、写=403。账号可用
    `PHM_OPERATOR_USER`/`PHM_OPERATOR_PASSWORD` 覆盖——**盲测必须设非公开口令**，公开的
    admin/123456 只是登录账号、不是授权边界。前端 `api.js:operatorHeaders()` 从 localStorage
    现取 token（每次请求新取，登录后自动生效），不再发静态字面量。
    命令行/脚本统一走 `phmlite/operator_auth.py:login()`；脚本、测试、README 演示流程已同步。
    **仍是已知残余**：真值明文仍在 `data/scenario.json`，诊断智能体与后端同机同账号且有 shell，
    `cat` 仍可读到——真正隔离需评测与诊断分处不同进程/主机/账号，属架构变更，未实现。
    回归：`tests/test_truth_leak.py` 新增"写接口需凭据""凭据是随机会话值"2 项 +
    前端 `tests/operator-auth.test.js` 2 项。
23. **复测关单依据不足（P1 问题5，2026-10-12）**：旧 `_observe_recovery_locked` 只要
    `fault.type=none` 就自增计数——`severe_alarms` 传进来了却**从未被读**，docstring 写的
    "连续 N 次无严重报警"实际不存在；于是"解除仿真注入"被当成了"设备已修好"，关单备注还写
    "本次处置闭环完成"。（"三次采样可能被短时间多次请求触发"这一半已在 P0 问题3 修掉：
    `observe` 现在只由采样循环每 tick 调一次。）
    现在复测判据三条：① 本次采样确实**无严重报警**（读测量）；② 连续 `RETEST_REQUIRED=3`
    个采样；③ 跨过 `AUTODIAG_RETEST_WINDOW`（演示默认 30s，真实运维应显著更长）。
    关单依据落 `tasks.close_kind`（migrate 自动加列）：自动复测=`sim_recovery`，人工关单
    （`/api/task/close`）=`manual`；备注与播报文案统一为"**仿真恢复验证通过（未验证实际维修）**"，
    不再出现"处置闭环完成"。`/api/autodiag/status` 的 `retest` 增加 `window_s`/`elapsed_s`。
    **口径**：解除仿真注入 ≠ 设备已修好；真实运维模式必须是"维修确认 → 时间窗内复测 → 授权关单"。
    回归：`tests/test_task_lifecycle.py` 新增 4 项（测量判据/时间窗/状态外露/关单依据可区分）
    + 前端 `agent-state` 2 项 + `copy-disclosure` 1 项禁用词扫描。
24. **部署定位：本机演示 + 边界硬约束（P1 问题6，2026-10-12）**：登录是演示账号、业务路由没有
    完整会话鉴权与逐路由权限、写操作仍是 GET——这类系统**只能当本机 Demo**，不能挂公网。
    现在把边界做成硬约束，而不是靠自觉：
    ① `scripts/run_server.py` 支持 `--host/--port`，绑定非回环地址需显式 `PHM_ALLOW_NON_LOOPBACK=1`，
    否则启动即 `SystemExit` 并打印整改清单；
    ② 服务端 `local_only_middleware` 拒绝一切非回环来源（403），放行远端客户端要**另一个独立开关**
    `PHM_ALLOW_REMOTE_CLIENTS=1`；
    ③ `/api/task/close` 也纳入控制面凭据（P1 问题4 的会话 token）。
    **实测教训（值得记住）**：初版把"允许绑定"和"放行远端客户端"合成一个开关，结果为了绑
    `0.0.0.0` 就得把请求守卫一起关掉——局域网 IP 直连拿到 200。是 uvicorn 访问日志
    （`INFO: 172.29.240.1:... "GET /api/device" 200 OK`）把它抓出来的；单测没覆盖到，
    因为单测只验了谓词、没验"两个开关的关系"。现已拆开并补对应回归。
    注册顺序：`local_only_middleware` 在 `run_log_middleware` 之前注册 → run_log 在外层，
    **被拒请求同样留痕**（访问日志已验证 403 有记录）。
    README 新增「部署定位：本机演示（不可直接对外）」与「对外暴露前必须补齐」5 条清单。
    回归：`tests/test_local_boundary.py` 6 项。
25. **AGH 无人值守授权：yolo 是否必要（P1 问题7，2026-10-13 调研）**：便携运行时被注入
    `session.setYolo(true)`（`runPrint` 内 `openSession` 之后，一行），让 `-p` 免逐次审批。
    读运行时代码得到三条硬事实：① `agnes.mjs:49284` 在 yolo 时把 `needsAsk` 直接置 false；
    ② `command_policy` 只在 `needsAsk` 为真时求值（`:279464`）——**所以只要 yolo 在，白名单
    就是摆设**；③ `allow` 规则要求绝对路径 + 整条命令全匹配（`:254429`、`:279348`），
    且 shell 命令串会被折叠成单空格（`:279334`）。
    据此做了三件事：**(a)** 产出补丁原件 `vendor/agh-runtime-patch.diff`——取出 zip 里的原始
    `runtime/agnes.mjs` 套用后与实机**sha256 完全一致**，证明实机与供应商版只差这一行；
    **(b)** 新增受限方案 `phmlite/agh_policy.py` + `setup_agh_runtime.py --restricted-shell`：
    撤掉 yolo，改用逐条枚举的白名单（读接口 `device|trend|spectrum|charfreq|equipment-map`，
    写接口仅 `report/submit|task/create`，命令统一绝对路径 curl + `-G --data-urlencode`）；
    **(c)** 提示词与白名单同源并在下发前自检，漂移即失败（`tests/test_agh_policy.py` 18 项）。
    **本轮未启用**：撤 yolo 会改变智能体的命令形态，必须有一次完整真机验收才能确认诊断链路
    仍能走完；按"决赛前不轻易动 AGH"，保持默认现状，切换步骤写在 `setup_agh.md` 第 10.6 节。
    残余：白名单不约束写接口载荷内容，shell 工具仍在；真正的"受限工具"需要写 AGH 扩展。
26. **算法与验证深度（审查四，2026-10-13）**：4.1 指出合成器只有 none/bpfo/bpfi 且三者共用
    同一条冲击串公式，测试数据/规则基线/智能体共享同一套物理假设。本轮：
    ① `dsp.synthesize` 扩到 7 种机理（新增 `bpfo_bpfi` 混合、`unbalance` 1X 占优、
    `looseness` 2X/3X 谐波、`unknown` 仅宽频能量），`scenario` 白名单与前端注入面板同步；
    ② 可分性由**测量**证实而非自称：判据是包络谱特征频率的**峰/底噪比**（宽带能量会抬高底噪，
    绝对幅值会把"底噪变高"误判成"有峰"——实测踩到），实测轴承类 50~290 倍、非轴承类 ≤6.1 倍；
    ③ 实测抓到一个真问题：`looseness` 早期混入 0.5X（属油膜涡动而非松动）在 BSF 造出 23.4 倍
    伪峰，足以误判成滚动体缺陷，已移除并留回归；
    ④ 评分器修两个先天盲区：混合故障此前记成 `abnormal-untyped`、**否定表述被骗**
    （"BPFI/BSF/FTF 区无主导峰"会让外圈故障被判成混合）——现支持 `bpfo_bpfi`/`undetermined`
    与同分句前后窗口否定检测，回归用**库里 28 条真实结论**锚定；
    ⑤ 新增 `is_hedged()` 区分确证结论与倾向性结论（真实报告 #21/#27 属后者）；
    ⑥ 历史数据来源标记：`eigen_history.origin = seed|live`，`/api/trend` 返回
    `seeded_points`/`live_points`，**模拟历史不再与真实采样混同**；
    ⑦ 规则基线升级为峰/底噪比并**能判混合**（更强的对照才诚实）；盲测矩阵从 8 例扩到 19 例
    （噪声 4 档/严重程度梯度/转速 4 档/混合/非轴承/未知/部分数据缺失），
    `/api/testtools/chaos` 支持 trend/charfreq 503。
    **未做**：外部真实数据集（CWRU/Paderborn）完全未引入——方案与非声称口径见
    `evidence/validation-coverage.md` 第四节；工具超时与模型限流用例未做；
    新增机理在智能体上的**端到端表现尚未实测**。回归：`tests/test_fault_physics.py` 11 项 +
    评分器/基线 5 项 + 趋势来源 1 项。
27. **第三轮审查 6 项整改（2026-10-13 当轮）**：① **P0-1 报警与频谱同源**——`/api/device` 用
    `POINT_SEED*1000003+seq`，而 waveform/spectrum 用固定种子重新合成，两者不是同一段数据；
    新增 `WaveformSnapshot` 缓存本 tick 波形，三接口同源并各带 `sample_seq` 与波形自身摘要。
    ② **P0-2 统计口径**——准确率分母不再排除 `no-report`/`unknown`（失败样例不能消失），
    闭环率要求 `done`+报告+工单+核验，误报率分母只用阴性样本；旧口径保留为对照字段。
    ③ **P0-3 复测读实测等级**——`observe(..., measured=points)`；此前只看"新建报警行"，
    而报警需连续 3 点才落行，单次 severe 越限会被当成正常并提前关单。
    ④ **P1-4 趋势口径**——两种提示词都要求区分 `origin=seed`（模拟历史）与 `live`（真实采样）。
    ⑤ **P1-5 工具成功≠业务成功**——取证命令统一带 `-f`（HTTP≥400 → curl 非零 → 工具报错），
    证据索引给 `write_calls_ok`，`_verify_run` 新增 `write_call_unverified` 门禁。
    ⑥ **P1-6 报警事件分层**——新增 `incidents` 表与 `alarm.incident_id/kind`，持续越限只算一起
    事件（归档里 1314 条报警不是 1314 起故障），`/api/alarm/history` 返回事件汇总。
    **未做**：本轮无真机重跑；6 项里 3 项改变了智能体看到的接口语义与结论口径，必须重跑
    验收 + 盲测。回归：`tests/test_review_fixes.py` 8 项 + 评分器 4 项 + 提示词 2 项。
16. **诊断准确性盲测基准（P0 问题4，2026-10-11 建成并首跑）**：`scripts/blind_benchmark.py`
    8 用例覆盖 正常/噪声干扰/BPFO/BPFI/边界越限两形态/变频/工具失败（操作台
    `/api/testtools/chaos?target=spectrum` 注入 503）。隐藏真值评分：混淆矩阵/准确率/误报率/
    任务完成率/延迟分布 + 规则算法基线（包络谱±3Hz）对比；没测出的指标标 not_measured 不填数。
    首轮实测（evidence/blind-benchmark-20261011-0433.json/.md）：8/8 全对、误报 0、完成率 1.0、
    延迟 28.7~73.2s；**规则基线同轮也全对——单一清晰故障集上准确率无分离（如实报告），
    智能体的实测差异在工具失败鲁棒性（降级 73.2s 完成）与可审计交付物**。要证"高于规则"需
    复合/弱故障用例，合成器目前仅 none/bpfo/bpfi（属后续工作）。重跑：起服务后
    `D:/Python311/python.exe scripts/blind_benchmark.py`（约 10~15 分钟，避开 Agnes 限流时段）。
15. **AGH 持久会话会劣化，run_prompt 已用 --standalone**：-p 默认复用工作区持久会话，
    daemon 重启后旧会话内核句柄失效 → 所有 turn 1000ms 超时中止（报"no turn/end row arrived"）；
    且 Agnes 网关限流时静默空返回。现状：每次诊断独立会话（run_id ↔ session 1:1，证据链更干净）+
    空回复判失败 + 8s 退避重试一次（AUTODIAG_RETRY_BACKOFF 可调）。演示日若连续失败，
    先重启 AGH serve → 手动 `-p "只回复ok"` 暖机 → 隔半分钟再演示（网关限流冷却）。
13. **证据关联（P0，2026-10-11 修复）**：证据链两半（业务库 run_id 记录 ↔ AGH 会话事件）必须能互相指认。
    旧 export_evidence.py 抓"sessions list 首行"曾把冒烟会话打包成诊断证据（run_20260930_183606 的
    agh_session.jsonl 搜不到 demo-001，该目录已加 README 声明作废）。现行机制：
    诊断提示词与 report/task 的 curl 参数强制携带 run_id → 会话事件必含 run_id 字符串 →
    phmlite/agh_evidence.py 以此定位 turn 窗口生成执行索引（<run_id>.index.json：session_key/turn/
    模型/逐条 toolUseId/时间/命令摘要/状态），autodiag 完成后自动落盘；
    export_evidence.py 按 run_id 定位 + 双向对账（报告↔report/submit、工单↔task/create），
    任一校验失败拒绝导出。注意 AGH -p 会话按工作区复用（多 turn 同一 session_key），
    关联粒度是 turn 不是 session。**最终提交引用 evidence/run_20261012_232142/**
    （run_id=auto-1011-155052-3c11ce3d，9 项校验全 PASS，含原始 Prompt 原文）。
    2026-10-12 修掉的另一个 P0：旧 export_evidence.py 自写 turn 切片、漏掉 turn/start
    之前的 user/message，导致 run_20261011_033539 包内没有 Prompt 却标 run_id_in_prompt=true
    （评委无法独立核查"智能体没见过注入真值"）。现改为与 agh_evidence._split_turns 共用切片，
    并加"包内含原始 Prompt"硬门禁——缺 Prompt 一律拒绝导出。

## 六、任务规格（本次要做的三个增量，按优先级）

### 任务② 报警自动唤起诊断（最高价值——"智能体驱动全流水线"的关键一跳）✅ 已完成（2026-10-03）
**现状**：severe 报警后需人工在 AGH 工作台输入指令才启动诊断。
**目标**：注入故障后**零人工介入**——系统自动唤起 AGH 诊断会话完成"取证→结论→工单"全链路，吉祥物第一人称播报进度（"检测到驱动端轴承振动严重越限，我已自主启动取证诊断…"）。
**实现要点**：
- 触发器：phmlite 侧 maybe_alarm 落 severe 报警时（或独立 watcher），去重（同一故障只唤起一次，报警恢复后重置）；
- 唤起方式：调用 AGH CLI 非交互模式发起诊断（参考 scripts/setup_agh.md 与 evidence/run_20260930_183606/agh_session.jsonl 的会话结构；AGH daemon 需在运行）；
- 前端：吉祥物气泡播报"已自主启动诊断"，专家诊断页状态机照常走（取证中→交叉验证→已出结论）；
- 验收：注入 bpfo/severity≥0.8 → 不碰键盘 → 90 秒内自动出结论+工单，全程无人工输入。
- 演示兜底：AGH daemon 不在线时自动降级为"本地规则诊断"并明示（对照实验也用得上，见下）。

**落地与实测（2026-10-03）**：
- `phmlite/autodiag.py`：看门狗挂 /api/device 采样后（`observe()`：fault=none 恢复重置 + 同一 fault.type 去重），
  severe 报警 → 后台线程健康检查 4178 → AGH CLI `-p`（会话级 yolo 补丁）跑"device→包络谱→charfreq→report→task"；
  健康检查失败/CLI 异常 → 本地规则诊断降级（包络谱±3Hz 比对，结论明示【本地规则诊断·AGH离线降级】）；
  状态机+播报事件走 `GET /api/autodiag/status`；
- `GlobalAgent.jsx`：轮询 status 按事件序号增量第一人称播报 + 自身轮询 /api/device（任意页面链路都推进）；
  气泡 CSS 去 nowrap（长播报换行，不再被视口裁切）；
- 测试：`tests/test_autodiag.py` 6 个（去重/恢复重置/降级落库/规则判据/状态端点），pytest 40 绿；
- **实测**：浏览器 UI 注入 bpfo/0.8 → 报警 +4s → AGH 会话中文结论（BPFO 134.77Hz 命中 135.09Hz±3Hz）+ 报告 + 工单，
  注入→终态 **48~57 秒**（三轮回测 81.7s/57.2s/48s，均 ≤90s）；bpfi 复注同理 48s；
  停 AGH serve 再注入 → **10 秒**降级出结论（明示口径）；
- 演示开关：环境变量 `PHM_AUTODIAG=0` 关闭自动唤起（对照实验可复用）。

### 任务① 常驻巡检播报（智能体在"平时"也可见）✅ 已完成（2026-10-03）
- GlobalAgent 每 3~5 分钟一次巡检：读 /api/channels → 第一人称播报一句（"巡检完毕：16 路正常，轴承温度 45.1℃"）——**规则引擎拼句，零 LLM 调用**；
- 通道跨档时升级播报并换动作；对话面板打开时不打扰。
- **落地**：GlobalAgent.jsx `patrolSentence()` 规则拼句（平安报 bow 动作；跨档/越限 spin/tilt 升级动作+前缀"注意，通道跨档！"），
  默认 240s 一轮，URL 参数 `?patrol=毫秒` 可调（测试/彩排用）；对话面板打开或页面隐藏时静默（但仍刷新档位基线）。
  实测两分支均已现场验证（截图）：平安报句式符合规格示例；升级报正确报出越限通道。
  已知演示特性：故障注入后冷风温度慢衰减（τ=320s），复位后约 1~2 分钟内巡检仍会报"冷风温度注意档"，属真实状态非误报。

### 任务③ 对话即操作（自然语言操作台）✅ 已完成（2026-10-03）
- /api/agent/chat 增加受限工具输出：模型可返回 actions:[{type,...}]（白名单：查询通道/趋势、注入演示故障、生成报告、切换视图），服务端校验白名单后执行并把结果喂回模型组织成短句；
- 前端对话面板把 actions 派发到既有 API/视图；
- 验收："注入一个外圈故障演示一下"→ 系统真实注入 + 吉祥物播报 + 自动诊断链路启动。
- **落地与实测（2026-10-03）**：
  - `agnes_chat.py`：系统提示词加【操作协议】（回复末行 `ACTIONS:[...]`，仅 6 种操作）；`ask()` 返回 {reply, actions}；
    `_parse_actions()` 剥离并容错解析（未闭合行剥掉不算操作）；`summarize()` 把执行结果二次组织成 ≤60 字短句；
  - `server.py`：`_execute_agent_action()` 白名单执行器（inject_fault 仅 bpfo/bpfi + severity 收敛 0~1；
    switch_view 仅 4 个合法视图 id；单轮最多执行 4 个操作）；响应带 actions 给前端派发；
  - `GlobalAgent.jsx`：switch_view 派发 `agent:view` 事件（Stage.jsx 监听切 3D 视图）；
    inject/recover 由吉祥物气泡第一人称补播报；
  - 测试：`tests/test_agent_actions.py` 6 项（协议解析容错+白名单越权拒绝+收敛），pytest 46 绿；
  - **实测**：聊天"注入一个外圈故障演示一下"→ 真实注入 bpfo/0.8 → 数字孪生立刻飘红 →
    自动诊断链 42s 出结论（报告#9+工单#9）；"恢复正常运行吧"→ recover_fault 执行；
    "切热力图+查越限通道"→ switch_view+query_channels 组合执行。

### 附加（演示对照实验，强烈建议）
- 复用顶部 `agent/manual` 模式开关；人工备用模式隐藏吉祥物并暂停聊天、主动播报和自主诊断，基础监测与阈值报警继续工作。不要另建第二套开关。
- 2026-10-06 的 A/B 实测记录见 `evidence/agent-switch-ab-2026-10-06.md`：人工基线通过；智能体组真实生成报告与智能体工单，但本次耗时约 123 秒，90 秒时限未通过。
- 系统设置页现在提供形象切换与跨形象共用昵称；偏好在当前浏览器本地保存。

## 七、验证与提交规范

- 浏览器验证：?v=随机数 破缓存；关注 .ga-bot data-phase、.s3d-card 数量、控制台红字（AgentAvatar3D 失败已大声报错）；
- pytest：D:\Python311\python.exe -m pytest tests/ -q（当前 34 绿，不许倒退）；
- 提交：中文 commit，推送走代理 `git -c http.proxy=http://127.0.0.1:7897 push origin main`；
- 每完成一个任务：更新本文档勾选状态。

## 八、任务状态

- [x] 任务② 报警自动唤起诊断（2026-10-03，实测 48~57s ≤90s 零人工闭环 + 离线降级 10s）
- [x] 任务① 常驻巡检播报（2026-10-03，规则拼句零 LLM，?patrol= 可调周期）
- [x] 任务③ 对话即操作（2026-10-03，6 操作白名单，注入→自动诊断链 42s 闭环）
- [ ] 附加：智能体开关 A/B 对照已执行；人工基线通过，A 组闭环完成但 90 秒时限未通过，仍需改善时延（详见实测记录）
- [x] 登录视频机制（等用户新素材替换 public/login-bg.mp4）
- [x] 系统设置页形象切换/共用昵称（2026-10-06，偏好保存在当前浏览器）
