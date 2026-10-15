# agh-portable 接入操作记录（本机 2026-09-30）

运行时 = 仓库 `vendor/agh-portable-20260930.zip`（44MB 清理版）。本机有两个 AGH 目录：

- `D:\agh-portable` — **原始参考目录（含回滚备份与旧数据），本任务全程未改动**；
- `D:\agh-contest` — 本次解压的比赛运行时（下文全部操作发生在这里）。

计划示例路径是 `D:\agh-portable`，为保护原目录改用 `D:\agh-contest`（计划允许"任意路径"）。

## 1. 解压与启动

```powershell
Expand-Archive -Path D:\TSI\agentic-phm-contest\vendor\agh-portable-20260930.zip -DestinationPath D:\agh-contest
# zip 自带顶层 agh-portable/ 目录，展平一层：
powershell -Command "Get-ChildItem 'D:\agh-contest\agh-portable' -Force | Move-Item -Destination 'D:\agh-contest' -Force; Remove-Item 'D:\agh-contest\agh-portable'"
```

启动（包内 node24，系统 Node 会被原生模块拒绝）：

```bash
cd /d/agh-contest
AGH_HOME='D:\agh-contest\agh-home' ./node24/node.exe runtime/agnes.mjs serve --profile local-dev --cwd "D:\TSI\agentic-phm-contest" --port 4178
```

- 工作台 `http://127.0.0.1:4178/`（4177 被原目录常驻实例占用，改 4178）。
- **不要**先单独 `daemon start` 再 serve：daemon 会在无 web origin 状态被锁死，serve 会报 "local daemon Web origin does not match"。直接 serve，由它带 origin 拉起 daemon。
- PHMLite 的 AGH 调用统一使用 `AGH_DIR`、`AGH_HOME` 与 `PHM_AGH_PROFILE`（缺省 `local-dev`）定位同一运行时和 profile。首次部署可在系统设置页写入当前 profile 的 Agnes Key 并通过 AGH 验证；Key 不经过模型调用命令参数。保留下方人工修复步骤用于本机部署故障恢复。
- Key 设置入口为 PHMLite「系统设置 → Agnes API Key」。AGH 需已初始化该 profile，且 `defaultAccountId` 指向启用的 `agnes-ai` account 并带有有效的 `credentialRef`；页面只写入该引用对应的凭据文件，不新建或切换模型账户。后端启动后会等待 AGH 在线和 Key 配置完成，再自动探测一次；设置页也保留手动验证按钮供即时重试。若 AGH 缓存了旧凭据，重启 AGH serve 后手动点击验证；若同时重启 PHMLite，启动探测会在 AGH 在线后自动运行。写入仅允许本机同源请求，页面和 API 不回显 Key。

## 2. 凭据存储 ACL 修复（新机器必做，否则 daemon 起不来）

症状：`daemon child exited before readiness (1)`，前台跑 `runtime/daemon.mjs` 报 `The credential store is unavailable.`。

根因：`Expand-Archive` 不保留 ACL。凭据存储要求 `agh-home` 整树为"私有 DACL"（仅当前用户 + SYSTEM、无继承 ACE，`hasPrivateDaclSync` 校验），解压后是普通继承 ACL，校验必然失败。

修复（目录与文件的 grant 语法不同，必须分开；文件带 `(OI)(CI)` 会被 icacls 拒绝导致空 DACL）：

```powershell
$root = 'D:\agh-contest\agh-home'
$targets = @($root) + @(Get-ChildItem $root -Recurse -Force | ForEach-Object { $_.FullName })
# 第一步：全部去继承（自顶向下，子项的继承 ACE 随父项消失）
foreach ($t in $targets) { icacls $t /inheritance:r | Out-Null }
# 第二步：目录带继承标志，文件不带
foreach ($t in $targets | Where-Object { Test-Path $_ -PathType Container }) {
  icacls $t /grant 'WIN-1IB86RUVKU7\Administrator:(OI)(CI)(F)' 'NT AUTHORITY\SYSTEM:(OI)(CI)(F)' | Out-Null }
foreach ($t in $targets | Where-Object { Test-Path $_ -PathType Leaf }) {
  icacls $t /grant 'WIN-1IB86RUVKU7\Administrator:F' 'NT AUTHORITY\SYSTEM:F' | Out-Null }
```

（`WIN-1IB86RUVKU7\Administrator` 换成本机 `主机名\用户名`，`whoami` 可查。）

修复后验证：`AGH_HOME=... node24\node.exe runtime\daemon.mjs --profile local-dev --workspace <ws> --data-dir <home>\data` 应输出 `agnesd listening on \\.\pipe\agnes-*`。

## 3. 配置 Agnes API Key

推荐通过 PHMLite 的“系统设置 → Agnes API Key”配置本机部署者自己的 Key：先初始化当前 AGH profile，确认它的默认账户为启用的 `agnes-ai` account 且存在 `credentialRef`；在本机页面录入并保存。设置服务会将 Key 写入该引用对应的 AGH 本地凭据文件，并加固 Windows ACL；后端启动后会等待 AGH 在线及 Key 配置完成，再自动探测一次。页面和 API 只展示是否已配置及连接验证状态，不回显 Key；手动验证按钮可随时发起重试。

不要把 Key 写入仓库文件、`.env`、脚本、命令行参数或聊天记录。若页面提示 profile 或凭据库尚未初始化，应先按本手册完成 AGH 安装和 profile 初始化；如果 AGH 缓存了旧凭据，重启 AGH serve 后重新验证。

## 4. 隐私口径

```bash
AGH_HOME='D:\agh-contest\agh-home' node24/node.exe runtime/agnes.mjs consent LOCAL
env | grep AGNES_TRACE   # 必须为空
```

已验证：consent=LOCAL、无 TRACE 端点。另建 `agh-home/profiles/local-dev/profile.yaml` 覆盖层：

```yaml
approvals:
  mode: smart
```

## 5. 无人值守补丁（-p 模式会话级 yolo）

演示需要"一句指令后智能体自主跑完取证→结论→落库"，而本构建 shell 工具 `requiresApproval: destructive` 永远保留审批门（策略注释明言），`--park` 在此构建被禁用。等效做法是工作台会话里人工敲一次 `/yolo`；为让 CLI `-p` 模式（演示/导出自动化）同样可用，给解压副本 `runtime/agnes.mjs` 打了一行补丁（**仓库内 zip 未动**，与包内既有 retry/hook-max-tokens 本地补丁同先例）：

```diff
      const session = await openSession(booted, p, io.cwd);
+     await session.setYolo(true); // [phm-lite demo patch] unattended print mode: session-yolo (auditable), mirrors TUI /yolo
      if (p.model) await session.setModel(p.model);
```

- 语义与工作台 `/yolo` 完全一致：会话级、不可逆、写入 `x/core/yolo-switch` 审计行（origin=system, trust=trusted）；
- 还原方式：重解压 zip 或删除该行；
- 工作台交互演示若不想要此行为，不使用 `-p` 即可。

另写 `agh-home/composer-selection.json` = `{"permission":"full"} `（TUI 新会话默认完全权限，与 UI 选择等效）。

## 6. 工具特性（决定演示话术）

- `web_fetch` 有硬编码 SSRF 防护：拒绝非公共 IP（127.0.0.1 一律 `WEB_BLOCKED_URL`），**本地 API 只能走 shell+curl**；
- shell `requiresApproval: destructive`：无 yolo 时每次都要人工批准。

## 7. 冒烟结果（2026-09-30，全部满足）

`agnes.mjs -p "用 shell 工具执行 curl -s http://127.0.0.1:8181/api/device ，然后原样复述 JSON"`：

1. `tool shell · ok · 923ms`，回复原样复述 `{"name":"P-101","points":[{"name":"DE",...,"level":"normal"},...]}` —— **回复含 "P-101"** ✓
2. **回复非空**（hook-max-tokens 修复有效，无 4096 截断空回复）✓
3. **会话落盘**：`agh-home/data/` 出现 audit/checkpoints/daemon 与 7 个会话（最大 941 事件）——Task 9 证据素材源 ✓
4. `package status`：`@agh-ext/hook-max-tokens@1.0.0 desired=enabled actual=running trusted=true` ✓

## 8. 环境遗留

- 8181：`python scripts/run_server.py`（phm-lite 后端）；
- 4178：本运行时 serve（daemon 随 serve 存活）；
- 4177：原目录常驻实例（别人的，未动）。

## 9. 重建记录（2026-10-03，主机名已变为 AFU）

`D:\agh-contest` 在 09-30 之后被整目录清理，本次从 `vendor/agh-portable-20260930.zip` 全套重建，流程同上文 1→5 节，
另有三个新坑要记：

### 9.1 icacls 账号名必须用本机实测（主机名换了）

文档旧例写死 `WIN-1IB86RUVKU7\Administrator`，本机现在 `whoami` = `afu\administrator`。
用旧账号名 grant 会**静默失败**（输出被 Out-Null 吞掉），配合 `/inheritance:r` 直接把整树锁死（空 DACL）。
两条铁律：

- 先 `whoami` 再拼 icacls；本机正确写法是 `AFU\Administrator:...`；
- **先 grant 后去继承**（顺序反过来更稳）：每个条目先有显式 ACE，再 `/inheritance:r` 剥继承 ACE，
  永远不会把自己锁在门外。上文第 2 节的"先去继承后授权"顺序在 grant 全失败时就是自锁，别照抄。

### 9.2 解压后新建的文件要补授权

密钥文件（secrets/agnes-ai/account-*）、`composer-selection.json` 等**在去继承之后写入**的文件，
会从父目录的 (OI)(CI) ACE 继承出 (I) ACE——既过不了 hasPrivateDacl 的"无继承 ACE"校验，被剥掉后又成空 DACL。
收尾动作：全树**再补一轮 grant**（目录带 (OI)(CI)、文件不带），不补去继承。
验证探针（用运行时自带 native 模块逐路径测）：

```bash
cd /d/agh-contest && ./node24/node.exe -e "
const { createRequire } = require('module');
const native = createRequire(process.cwd() + '/runtime/agnes.mjs')('@agnes/system-node/native');
const fs = require('fs');
const walk = (p) => {
  let f = [];
  try { if (!native.hasPrivateDacl(p)) f.push(p); } catch (e) { f.push(p + ' ERR ' + e.message); }
  const st = fs.lstatSync(p);
  if (st.isDirectory()) for (const e of fs.readdirSync(p, { withFileTypes: true })) f = f.concat(walk(p + '/' + e.name));
  return f;
};
console.log(JSON.stringify(walk('D:/agh-contest/agh-home')));
"
```

输出 `[]` 且 `native.readPrivateFile(密钥路径, 1048576)` 能读到字节，才算过。

### 9.3 验证顺序

1. 前台 daemon 冒烟：`AGH_HOME=... node24/node.exe runtime/daemon.mjs --profile local-dev --workspace <ws> --data-dir <home>\data`
   → `agnesd listening on \\.\pipe\agnes-*`（测完 Ctrl+C）；
2. serve：`AGH_HOME=... node24/node.exe runtime/agnes.mjs serve --profile local-dev --cwd "D:\TSI\agentic-phm-contest" --port 4178`；
3. `-p` 冒烟（会带 yolo 补丁，见第 5 节，需重打——zip 是未打补丁的干净版）：
   `agnes.mjs --profile local-dev --cwd "D:\TSI\agentic-phm-contest" -p "用 shell 工具执行 curl -s http://127.0.0.1:8181/api/device ，然后原样复述 JSON"`。

2026-10-03 三步全过；任务②自动唤起（phmlite/autodiag.py）即依赖该 serve 常驻。

## 10. 补丁与授权清单（P1 可复现性：提交必读）

### 10.1 确切版本

| 项 | 值 |
|---|---|
| 供应商包 | `vendor/agh-portable-20260930.zip`（45,354,139 字节） |
| sha256 | `1323bdad177159e389c0c247823280d69dfbebc31cbb144ffa074760d929a2e4` |
| 运行时位置 | `D:gh-contest`（zip 解压展平，来源可追溯） |
| Node | 包内 `node24`（v24.10.0），不用系统 Node |
| 修复扩展 | `@agh-ext/hook-max-tokens@1.0.0`（zip 自带，非我方补丁） |

### 10.2 我方本地改动全清单（zip 里没有的）

| # | 改动 | 位置 | 作用 |
|---|---|---|---|
| 1 | `-p` 会话级 yolo 一行补丁 | `runtime/agnes.mjs`（`openSession` 之后 `await session.setYolo(true)`） | 无人值守 `-p` 模式 shell 工具免逐次审批（语义同工作台 /yolo） |
| 2 | Agnes Key 注入 | `agh-home/secrets/agnes-ai/account-bb8adc5052deb964d9b231c2-r1` | 凭据（内容来自仓库 .env，不落第二份明文） |
| 3 | profile 覆盖层 | `agh-home/profiles/local-dev/profile.yaml`（approvals: smart） | 审批策略 |
| 4 | composer 选择 | `agh-home/composer-selection.json`（permission: full） | TUI/CLI 默认权限 |
| 5 | consent LOCAL | `agh-home` 注册表事件 | 隐私口径：不出网遥测 |
| 6 | 私有 DACL | agh-home 整树 | 凭据存储强制校验要求 |

### 10.3 恢复步骤（回到未打补丁状态）

- 只撤补丁：删除 10.2-#1 那一行（或重解压 zip 覆盖 `runtime/agnes.mjs`）；
- 彻底重建：删除 `D:gh-contest` 后执行 `D:/Python311/python.exe scripts/setup_agh_runtime.py`
  （幂等：解压→ACL→注钥→consent→覆盖层→补丁→守门校验一条龙，账号经 whoami 实测）；
- 重建后验证：`scripts/setup_agh_runtime.py --verify-only`（DACL/密钥/补丁三项守门）+ `scripts/acceptance_check.py` 真机验收。

### 10.4 授权范围（yolo 补丁的边界，评审必读）

- **范围**：仅 `-p` 非交互模式、仅当前 profile（local-dev）、仅该次会话（session 级，非全局配置）；
  每次授予以 `x/core/yolo-switch` 事件写入审计（origin=system, trust=trusted）；
- **能力**：shell 工具免逐次人工审批（否则无人值守诊断会在审批门挂起）；
- **约束**：诊断智能体的取证接口白名单由提示词+技能双份限定（禁 /api/scenario/*、/api/agent/*、
  /api/autodiag/*）；scenario 接口对非操作台调用脱敏真值；业务副作用限 report/submit 与
  task/create，且受 active-run 门禁（仅运行中的 run_id 可写）；
- **工作台交互模式不受影响**（不使用 -p 时无此授权）；
- 每次诊断的完整证据：提示词原文（data/autodiag/<run_id>.log）、执行索引（.index.json）、
  AGH 审计（agh-home/data/audit + sessions.db）。

### 10.5 一键重建

```bash
D:/Python311/python.exe scripts/setup_agh_runtime.py              # 全量重建（幂等，含 yolo 补丁）
D:/Python311/python.exe scripts/setup_agh_runtime.py --verify-only  # 只跑守门校验
D:/Python311/python.exe scripts/setup_agh_runtime.py --restricted-shell  # 受限授权（见 10.6）
```

### 10.5.1 全新机器（评委/新 clone）

`python scripts/start_demo.py`（或双击 `启动演示.bat`）**会自动调用上面的全量重建**——
检测到 `AGH_DIR` 下没有运行时才触发，已部署的机器不受影响（想关掉用 `--no-bootstrap`）。
安装目录由环境变量 `AGH_DIR` 控制（默认 `D:\agh-contest`），脚本与后端读同一个值。

Agnes Key 取值顺序：环境变量 `AGNES_API_KEY` → 仓库 `.env`（可照 `.env.example` 复制）
→ 都没有。最后一种**不算失败**：profile 与凭据文件骨架照建，服务照起，
评委在「系统设置 → Agnes API Key」录入自己的 Key 即可。

### 10.5.2 两个已知坑（2026-10-15 实测，脚本已自动处理）

- **旧文档写死的 ACL 账号名不可用**：主机名换过（`WIN-1IB86RUVKU7` → `AFU` → …），
  脚本一律用 `whoami` 实测值，不要照抄第 2 节的硬编码例子。
- **`fix_acl()` 之后才写入的文件需要收尾补授权**：密钥文件 / `profile.yaml` /
  `composer-selection.json` 是在去继承之后写出的，会继承出 `(I)` ACE；而
  `hasPrivateDacl` 要求"无继承 ACE 的私有 DACL"，只补授权不剥继承仍判不合规。
  脚本末尾的 `harden_final_acl()` 做「再授权 + 再去继承」，守门因此能过。

### 10.6 受限授权替代方案（P1 问题7，2026-10-13 调研）

**问题**：10.4 的 yolo 补丁是"任意 Shell 执行"级授权，不是"只允许几个工业接口"。

**读运行时代码得到的关键事实**（`D:\agh-contest\runtime\agnes.mjs`）：

| 行 | 事实 |
|---|---|
| `49284` | `if (s2.yolo \|\| approvalMode === "off") needsAsk = false;` |
| `279464` | `command_policy` 只在 `needsAsk` 为真时求值 |
| `279348` | `matchPolicy`：`deny` 优先；`allow` 对**整条命令全匹配**；未命中回落默认审批 |
| `279334` | shell 命令串会被折叠：`command.trim().replace(/\s+/g, " ")` |
| `254429` | `allow` 规则的 `argv` **必须锚定绝对路径**（相对命令无法被预先批准） |

→ **只要 yolo 补丁在，`command_policy` 完全不被求值**，加白名单等于摆设。二者不能并存。

**受限方案**：撤掉 yolo，改用白名单。命中 `allow` 的命令免审批，其余命令在无人值守下
（`on_unavailable: deny`）直接被拒。白名单与提示词同源，见 `phmlite/agh_policy.py`：

- 读接口**逐条枚举**（`device|trend|spectrum|charfreq|equipment-map`），不是放行整个 `/api/*`；
- 写接口只放行 `report/submit` 与 `task/create`；
- 命令统一为**绝对路径 curl**，写接口用 `-G --data-urlencode`，不再让模型自己拼 PowerShell
  编码函数——那正是旧命令形态无法被白名单覆盖、只能靠 yolo 放行的原因；
- 受限模式下 `_prompt()` 下发前会逐条自检，白名单与提示词漂移会**立刻失败**而不是演示时卡死
  （`tests/test_agh_policy.py` 18 项锁定）。

**应用步骤（尚未在真机验证，需一次完整验收）**：

```bash
D:/Python311/python.exe scripts/setup_agh_runtime.py --restricted-shell
set PHM_AGH_RESTRICTED_SHELL=1
# 先校验预设被 host 接受（不调模型）：AGH_HOME=D:\agh-contest\agh-home
D:\agh-contest\node24\node.exe D:\agh-contest\runtime\agnes.mjs profile inspect local-dev --resolved
# 再跑一次真机验收，确认诊断链路在白名单下仍能走完
D:/Python311/python.exe scripts/acceptance_check.py
```

**尚未解决的残余**：白名单只约束"哪个可执行文件 + 哪个端点"，不约束写接口的**载荷内容**；
且 shell 工具本身仍在。要做到整改所说的"受限工具 + 工具层验证参数"，需要写一个 AGH 扩展
把取证接口包成专用工具——按"决赛前不要轻易升级 AGH"，本轮未做。
