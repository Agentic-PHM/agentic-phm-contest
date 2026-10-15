# Agentic-PHM

调相机（同步补偿机组）智能运维演示平台，参赛主线为“监测 → 阈值报警 → AGH 智能体自主取证诊断 → 运维闭环”。
项目把基础监测与智能体能力分层：没有可用智能体时，监测和报警仍运行；诊断报告、证据链、置信度和智能体工单只在 AGH 会话实际完成后产生。

## 系统组成

- `phmlite/`：FastAPI 后端、信号处理、阈值报警、自动诊断状态机和本机设置接口。
- `dashboard/`：React + Ant Design 操作台，含监测、趋势、告警、诊断、报告、运行模式和系统设置页面。
- `agent/skills/diagnosis.md` 与 `.agh/skills/diagnosis/SKILL.md`：诊断策略及 AGH 部署副本，修改时保持同步。
- `scripts/setup_agh.md`：AGH portable runtime 初始化、profile、凭据库和本机 ACL 操作说明。

所有模型调用都经 Agnes Harness（AGH）和 Agnes 模型完成。聊天、聊天操作总结和报警自动诊断不直连模型 API；AGH 或模型不可用时，不会回退生成规则诊断结论。

## 本地启动

需要 Python 3.11、项目 Python 依赖，以及已初始化的 AGH portable runtime。首次部署先按 [`scripts/setup_agh.md`](scripts/setup_agh.md) 准备 AGH 和 profile。两个进程必须使用相同的 `AGH_DIR`、`AGH_HOME`、`PHM_AGH_PROFILE` 与 `AGH_PORT`。

以下命令以 Windows + Git Bash 为例，将路径替换为本机目录。在两个终端设置相同环境：

```bash
export REPO_DIR='/d/path/to/agentic-phm-contest'
export AGH_DIR='/d/path/to/agh-contest'
export AGH_HOME='D:\path\to\agh-contest\agh-home'
export PHM_AGH_PROFILE='local-dev'
export AGH_PORT='4178'
```

终端一启动 PHMLite：

```bash
cd "$REPO_DIR"
python scripts/run_server.py
```

终端二启动 AGH：

```bash
cd "$AGH_DIR"
./node24/node.exe runtime/agnes.mjs serve \
  --profile "$PHM_AGH_PROFILE" \
  --cwd "$REPO_DIR" \
  --port "$AGH_PORT"
```

打开 `http://127.0.0.1:8181/`，使用演示账号 `admin / 123456` 登录。首次使用进入“系统设置”，在密码框录入本机自己的 Agnes API Key 并保存。Key 由本机后端写入当前 AGH profile 的凭据库；页面、API 响应和应用日志不会回显 Key。后端启动后会等待 AGH 在线及 Key 配置完成，再自动探测一次 Agnes 模型；验证成功后顶部状态显示智能体就绪。需要立即重试时仍可点击“验证 Agnes 连接”。

AGH portable runtime 不包含在应用安装过程中下载或安装。Key 设置页只配置当前 profile 已存在的 Agnes account 和 credentialRef；profile 未初始化时先按部署手册完成初始化。

## 一条命令启动（演示/评委推荐）

完成一次性部署（Python 3.11 + `pip install -r requirements.txt`、AGH 运行时按
[`scripts/setup_agh.md`](scripts/setup_agh.md) 初始化）后，日常启动只需：

- Windows：双击仓库根目录 **`启动演示.bat`**（或 `python scripts/start_demo.py`）；
- 脚本会自动：启动 AGH 运行时与平台后端（已在运行则跳过）→ 模型链路暖机 → 复位演示场景 → 打开浏览器；
- 停止：双击 **`停止演示.bat`**（或 `python scripts/stop_demo.py`）；日志在 `logs/`。

测试与 CI 依赖已包含在 `requirements.txt`（pytest），无需单独安装。

## 运行模式与离线行为

- **智能体增强模式**：基础监测、趋势和阈值报警持续运行；AGH 与 Agnes 均通过验证后，严重报警可启动自主取证、诊断、报告和智能体工单。聊天和所有聊天操作均经 AGH。
- **人工备用模式**：由操作员在顶部明确切换，选择会在本机重启后保留。基础监测、趋势、阈值报警和设备视图继续运行；智能体聊天、自主诊断及智能体来源报告/工单暂停。已有报告显示为历史记录。
- 人工备用模式会卸载桌面吉祥物并暂停聊天与主动播报；严重报警提示会明确显示“智能体未启动”。
- **AGH 离线或模型失败**：系统显示具体不可用/失败状态，不伪造“取证中”或诊断结论，也不自动写报告或工单。AGH 恢复后不会自动切回；需由操作员选择恢复，系统会先检查运行时并验证 Agnes 模型。
- `PHM_AUTODIAG=0` 只关闭报警触发的自动诊断；它不等同于人工备用模式。人工备用模式还会关闭智能体聊天与模型操作。
- **智能体形态**：取证、交叉验证、结论与工单生成由**同一个 AGH 会话顺序执行**，不是多个智能体并行协同；界面不展示未经验证的多智能体编排。
- **成功口径**：完整诊断须同时通过机理证据支持、唯一报告/工单、成功响应ID与run_id对账、HTTP200审计、模型标识及turn成功结束。保留性/未知报告可留档，不能自动创建维修工单，也不记完整完成。在线与导出共用门禁，详见[诊断完成判据](docs/diagnosis-completion-contract.md)。
- **采样与报警口径**：仿真信号、特征入库与报警判定由后端**独立采样循环**按固定周期调用（`PHM_SAMPLE_INTERVAL`，默认 3 秒），与浏览器是否打开无关；`GET /api/device`、`/api/channels`、`/api/health` 只返回最近一次快照，不再推进仿真。报警的“连续 N 点”绑定**样本序号**（报警行带 `seq`），不由 HTTP 请求次数决定。因此采样与报警节奏不受打开页面数量、刷新频率影响；连续 3 个样本约 9 秒。
- **复测与关单口径**：工单自动关单的判据是**复测**，不是“仿真注入被解除”。复测要求：本次采样**无严重报警**（读测量，不只看场景开关）、连续 3 个样本、且跨过时间窗（`AUTODIAG_RETEST_WINDOW`，演示默认 30 秒）。关单依据写入 `tasks.close_kind`：自动复测为 `sim_recovery`，文案是“**仿真恢复验证通过，未验证实际维修**”；人工处置确认关单为 `manual`。**解除仿真注入不等于设备已修好**，真实运维模式必须先有维修确认、再经授权关单。
- 系统设置页可切换 7 款桌面形象并设置所有形象共用的昵称；形象偏好保存在当前浏览器，聊天面板不再提供切换入口。

## 演示流程

确认 AGH 状态为“智能体已就绪”，先复位持久化的演示故障。控制面写接口需要**登录签发的会话凭据**（见下节），所以先取 token 再调用：

```bash
# 取控制台会话凭据（PHM_OPERATOR_PASSWORD 可覆盖演示默认口令）
TOKEN=$(curl -s -X POST http://127.0.0.1:8181/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"123456"}' \
  | sed 's/.*"token":"\([^"]*\)".*/\1/')

curl -s -H "X-Operator-Token: $TOKEN" \
  "http://127.0.0.1:8181/api/scenario/set?key=fault.type&value=none"
```

界面上「进入实时监视 → 场景注入面板」会自动带凭据，无需手工取 token。

进入实时监视页，注入外圈故障（BPFO，强度 0.8），随后等待系统自动处理。耗时受模型响应影响；2026-10-06 一次对照运行在约 123 秒完成，超过原先 90 秒演示目标，不能据此保证每次都能在 90 秒内完成。诊断页查看当前 run 对应的报告和证据，工单由该 AGH 会话创建。若 AGH/provider 检查失败，运行会以失败结束，不会生成规则版替代报告。

人工备用对照：顶部选择“进入人工备用模式”，再次注入同一演示故障。吉祥物与聊天会消失；系统仍显示通道变化和阈值报警，但不会启动聊天诊断，也不会自动生成诊断报告或智能体工单。严重报警提示会明确显示智能体未启动。2026-10-06 实测见 [`evidence/agent-switch-ab-2026-10-06.md`](evidence/agent-switch-ab-2026-10-06.md)。

## 部署定位：本机演示（不可直接对外）

本服务被明确定义为**本机离线演示**，并把这个边界做成了硬约束，而不是靠"记得别绑 0.0.0.0"：

- 默认只绑定 `127.0.0.1`（`scripts/run_server.py`）；绑定非回环地址需显式 `PHM_ALLOW_NON_LOOPBACK=1`，否则启动即拒绝并打印下面的整改清单。
- 服务端中间件**拒绝一切非回环来源的请求**（403，且被拒请求同样进 `run_log` 审计）。放行远端客户端需要**另一个独立开关** `PHM_ALLOW_REMOTE_CLIENTS=1`——两个开关刻意分开：为了能绑 `0.0.0.0` 而设的绑定开关，不得顺手把请求守卫也关掉。
- 控制台账号默认演示值 `admin/123456`（可经 `PHM_OPERATOR_USER` / `PHM_OPERATOR_PASSWORD` 覆盖），登录签发随机会话 token；控制面写接口与完整场景视图要求该 token，无凭据写入 403。

### 对外暴露前必须补齐

若要提供线上评审链接，**必须先完成**以下整改，再设置 `PHM_ALLOW_REMOTE_CLIENTS=1`：

1. **服务端身份认证**：业务路由（报告、工单、设备数据、对话）校验会话，而不是只有控制面；
2. **逐路由权限**：报告/工单/场景配置按角色授权，而不是"登录即可写"；
3. **CSRF 安全方法**：修改数据的 GET 接口（`/api/scenario/set`、`/api/task/create`、`/api/task/close`、`/api/report/submit`、`/api/testtools/chaos`）改为 POST/PATCH；
4. **操作审计留存**：谁在何时改了什么，可追溯（当前 `run_log` 只记录路径/参数/耗时）；
5. **TLS 终结与访问控制**（反向代理），并轮换全部演示凭据与 Agnes Key。

当前实现**不满足**以上任一条，因此不得直接暴露到公网。这一节也是 `run_server.py` 启动守卫打印的清单。

## 数据口径

当前演示链路使用**程序合成的仿真振动信号**，由信号合成逻辑和 `data/scenario.json` 中的演示场景驱动；不应将这些仿真输出解读为现场测量值。登录页与每页顶栏常驻「仿真数据」标识，避免被误读为现场数据。

历史与实时数据也分别标记来源：`eigen_history.origin` 为 `seed`（`seed.py` 生成的 24h **模拟历史曲线**）或 `live`（采样循环写入的真实采样）。`GET /api/trend` 会返回 `seeded_points`/`live_points` 与逐点 `origin`——**模拟历史不等于设备真实连续运行记录**，不得混同为现场故障演化。

故障机理现有 7 种（`none/bpfo/bpfi/bpfo_bpfi/unbalance/looseness/unknown`），各自的物理可分性由测量证实（见 [`evidence/validation-coverage.md`](evidence/validation-coverage.md)：判据是包络谱特征频率的**峰/底噪比**，轴承类 50~290 倍、非轴承类 ≤6.1 倍）。**尚未引入外部真实数据集（CWRU/Paderborn）**，本仓库不声称任何现场诊断准确率。

## 控制面凭据与真值可见性

- **控制面写接口需要凭据**：`/api/scenario/set`、`/api/testtools/chaos` 以及 `/api/scenario/get` 的完整视图，都要求 `X-Operator-Token`（登录签发）或 `Authorization: Bearer`。无凭据时：读场景返回脱敏视图（`fault.type = "hidden"`），写入与工具故障注入返回 403。
- 此前门禁是固定字符串 `X-Operator: console`，而它同样打在前端构建产物里——任何能发 HTTP 的人（包括具备 shell 的诊断智能体加一个 `-H`）都能伪造。现在每次登录签发一个随机会话 token，只存在于服务端内存与本机浏览器。
- **做盲测时应设置非公开口令**（`PHM_OPERATOR_USER` / `PHM_OPERATOR_PASSWORD`）。演示默认的 `admin/123456` 只是登录账号，不构成授权边界：知道口令的人仍可登录取得凭据。
- **已知残余（未消除）**：故障真值仍以明文存放在本机 `data/scenario.json`，而诊断智能体与后端**运行在同一台机器、同一个操作系统账号**下，具备 shell 能力，因此 `cat data/scenario.json` 仍可读到真值。真正的隔离需要评测与诊断分处不同进程/主机/账号（评测侧只把信号参数交给诊断侧），这属于架构变更，尚未实现——当前实现的是"正常诊断流程拿不到真值"，不是"诊断执行环境无法访问真值"。

## 开发验证

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
& 'D:\Python311\python.exe' -m pytest tests/ -q
```

前端测试与构建必须从 `dashboard/` 执行：

```bash
npm test
npm run build
```

## 凭据与发布

不要提交 `.env`、AGH `agh-home` 或任何本机凭据。当前应用的 Key 配置入口使用 AGH profile 凭据库；如果 Key 曾出现在共享或公开 Git 历史中，应先在 Agnes 侧轮换，再继续发布。
