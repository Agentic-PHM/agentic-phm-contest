# vendor/ — 随仓库分发的运行时资产

## agh-portable-20260930.zip（44MB）

AGH 智能体运行时便携包（源自 `D:\agh-portable`，2026-09-30 清理版）。解压即用，自带 Node（`node24\node.exe` v24.10.0），无需安装任何依赖。

**打包时剔除了以下内容（原 `D:\agh-portable` 均有保留）：**

| 剔除项 | 原因 |
|---|---|
| `dbconn/` | Redis 审计工具，与运行时无关，且含内网信息 |
| `runtime/*.bak-preretry` | 补丁回滚备份 |
| `agh-home/data/audit|checkpoints|daemon*|doctor-*|artifacts` | 旧机器会话历史/进程状态（隐私+陈旧） |
| `agh-home/cache|locks` | 可再生缓存 |
| `agh-home/secrets/agnes-ai/` 旧凭据 | **旧 Agnes Key，不得扩散**；已留 README 占位 |

**保留的关键内容**：`runtime/`（已含 hook-max-tokens 修复与 retry 补丁的构建产物）、`node24/`、`setup-agh.ps1`、`agh-ext/hook-max-tokens/`（扩展源码）、`agh-home/profiles|data/profiles|data/resource-control|data/tables`（扩展注册与配置——**新机器无需重新装扩展**）。

**新机器部署三步**（详见 `docs/superpowers/plans/2026-09-30-agentic-phm-contest.md` Task 7）：

1. 解压本 zip 到任意路径（示例 `D:\agh-portable`）；
2. `powershell -ExecutionPolicy Bypass -File .\setup-agh.ps1`（失败且日志见 `E_SYSTEM_NATIVE_UNAVAILABLE` = 用错 Node，必须用包内 node24）；
3. 按计划 Task 7：注入 `.env` 中的比赛 Agnes Key → `consent LOCAL` → 冒烟验证（回复非空 + 会话落盘）。

⚠️ 本 zip 与 `.env` 同属私有仓库资产；**仓库公开/开源前必须删除本 zip 评估与否、并确认 `.env` 已清理轮换**。

## agh-runtime-patch.diff — 我方对运行时唯一的改动（一行）

P1 问题7 要求"保留供应商运行时版本、补丁 Diff、授权目的"，此文件即该 Diff 原件（此前只有
`setup_agh_runtime.py` 里的字符串与文档描述，没有可独立审阅的补丁）。

| 项 | 值 |
|---|---|
| 供应商包 | `agh-portable-20260930.zip`，sha256 `1323bdad177159e389c0c247823280d69dfbebc31cbb144ffa074760d929a2e4` |
| 目标文件 | `runtime/agnes.mjs`（16,015,494 字节） |
| 原始 sha256 | `E91AC7C58AB21B54F2B350D271372EB1D8FF881FB5629BC2813D1C3C51931270` |
| 打补丁后 sha256 | `746DB1E8F690D2824E3E49F730E6B9C0110717D1433470FC6394DB2D2E79BA7D`（+125 字节） |
| 补丁位置 | `runPrint()` 内 `openSession()` 之后 |
| 改动内容 | 新增一行 `await session.setYolo(true);` |

**验证方式**（2026-10-13 实测）：把 zip 里的原始 `runtime/agnes.mjs` 取出，`git apply -p1` 本补丁，
产物与当前实机文件 **sha256 完全一致** —— 证明实机运行时与供应商版**只差这一行**，没有未记录的改动。
注意套用时要用 `git -c core.autocrlf=false apply`，否则 CRLF 转换会让校验失败（不是补丁问题）。

```bash
git -c core.autocrlf=false apply -p1 ../vendor/agh-runtime-patch.diff   # 在 AGH_DIR 下
# 还原：删除该行，或重解压 zip 覆盖 runtime/agnes.mjs
```

**授权目的与边界**：`-p` 非交互模式下 shell 工具免逐次人工审批，否则无人值守诊断会卡在审批门。
这是**广泛的 Shell 执行授权**，不是"只允许几个工业接口"——调研结论与受限替代见
`scripts/setup_agh.md` 第 10.6 节（`--restricted-shell`）。
