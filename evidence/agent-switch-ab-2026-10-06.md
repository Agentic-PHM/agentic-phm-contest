# 智能体开关 A/B 对照实验（2026-10-06）

## 结论

- **人工备用基线：通过。** 吉祥物和聊天已隐藏；BPFO/0.8 故障仍触发阈值报警；26 秒观察期间自动诊断保持 idle、没有 run_id，报告和工单数量没有增加。
- **智能体增强组：功能闭环通过，时限未通过。** AGH 实际完成取证并提交中文结论、证据链和智能体工单；注入到终态约 123 秒，超过 90 秒目标。不得将本次结果描述为满足 90 秒时限。
- 实验完成后已将故障复位为 none，并恢复实验前的 agent 模式；AGH/provider 就绪。

## 条件与计数

两组都从实时监视页注入同一故障：`BPFO / severity=0.8`。实验过程中没有删除任何报告、工单或数据库记录。

| 阶段 | 模式 | 自动诊断 | 报告数 | 工单数 | 报警历史数 | 观察 |
|---|---|---:|---:|---:|---:|---|
| 注入前 | agent，AGH/provider ready | idle，无 run_id | 9 | 9 | 752 | 场景 none |
| A 组终态 | agent | done，mode=agh | 10 | 10 | 816 | 新增报告/工单各 1 条 |
| B 组注入前 | manual | idle，无 run_id | 10 | 10 | 828 | 故障复位后再切人工模式 |
| B 组观察 26 秒后 | manual | idle，无 run_id | 10 | 10 | 838 | 报警历史增加 10 条；报告/工单不变 |

报警历史计数包含连续采样产生的报警记录；它不是唯一故障事件数。

## A 组：智能体增强模式

- 运行号：`auto-1006-232924-858f6100`
- 终态：`done`，`mode=agh`
- 报告：#10；工单：#10
- 报告与工单均关联同一 `run_id`，工单 `source=agent`。
- 诊断结论：驱动端轴承外圈 BPFO 缺陷，置信度 0.92；包络主峰 134.77 Hz 命中理论 BPFO 135.09 Hz，偏差 0.33 Hz，并观察到谐波簇。
- 取证日志记录 `/api/device` 与包络谱/特征频率比对；结果已写入报告 evidence。
- PHMLite 运行日志记录 AGH session completed；详细 AGH 审计留存在本机 AGH audit log，本次没有把独立 audit 文件复制进 evidence 目录。
- 注入到终态观察耗时约 **122.7 秒**（后台终态事件约 120 秒）。闭环存在，但超过 90 秒时限。

## B 组：人工备用模式

- 模式：`manual`；AGH/provider 当时在线，但操作员明确选择人工备用模式。
- UI：吉祥物计数 0、聊天面板计数 0；严重报警仍显示 DE/NDE 振动 severe。
- 自动诊断在注入前后均为 `idle`，没有新 `run_id`、报告或工单。
- 报警历史从 828 增至 838；报告与工单仍为 10。
- 初次观察发现实时监视页原先硬编码“智能体已介入自主诊断”，与人工基线矛盾。已改为根据模式、AGH readiness 和 autodiag 状态生成提示；重建后在故障仍处于 manual 模式时确认显示“当前为人工备用模式，智能体未启动”。

## 收尾状态

- `/api/scenario/get`：`fault.type=none`、`severity=0`
- `/api/operation/mode`：`agent`
- `/api/autodiag/status`：`idle`
- `/api/operation/mode`：AGH 在线、Agnes Key 已配置、provider ready
