# 旋转机械自主诊断策略（P-101）

## 工具（全部 GET，用 web_fetch 或 curl 调用，base=http://127.0.0.1:8181）
/api/device  /api/trend?point&kpi&hours  /api/waveform?point&n  /api/spectrum?point&type&top
/api/charfreq?rpm  /api/alarm/history?equip  /api/task/create?title&desc&level&source
/api/equipment-map  /api/report/submit?run_id&equip&conclusion&confidence&evidence_json[&component_id]

## 设备部件语义表（9 部件；完整映射（锚点/全部阈值/说明）可调 GET /api/equipment-map，下表阈值为 ISO 典型演示配置）
| 部件 id | 中文名 | 绑定测点 | 严重阈值口径 | 机理特征 |
|---|---|---|---|---|
| bearing_de | 驱动端轴承 | 轴振1X/1Y、轴温DE | 振动≥120μm、轴温≥95℃ | BPFO/BPFI/BSF/FTF 特征峰 |
| bearing_nde | 非驱动端轴承 | 轴振2X/2Y、轴温NDE | 振动≥120μm、轴温≥95℃ | BPFO/BPFI/BSF/FTF 特征峰 |
| rotor | 转子 | 转速、轴振1X | 转速≥3300rpm（110% 超速） | 1X 不平衡 / 2X 不对中 / 0.5X 涡动 |
| shaft | 主轴/联轴器 | 键相 | 键相丢失=数据失效 | 2X 联轴器不对中 |
| stator | 定子（机壳+绕组） | 定子铁芯温度（预留） | ≥150℃ | —— |
| bedplate | 底座 | 瓦振1X/1Y | ≥7.1mm/s（ISO 10816-3） | 基础松动、共振 |
| cooling_system | 冷却系统 | 冷风温度 | ≥50℃ | 温升异常→冷却效率下降 |
| lubrication_system | 润滑油系统 | 润滑油压、油温 | 油压≤0.06MPa、油温≥55℃ | 油压低→烧瓦 / 油温高→黏度下降 |
| terminal_box | 接线柜/控制柜 | 柜内温度（预留） | ≥65℃ | —— |

## 取证边界（P0 防真值泄漏）
你只能通过上表取证接口读取**测量值**（特征值/波形/频谱/趋势/特征频率/部件映射）。
禁止访问 /api/scenario/*（仿真控制通道，其 fault 字段已脱敏且与诊断无关）、
/api/agent/*（人机对话通道）、/api/autodiag/*（调度内部状态）。
结论必须且只能来自取证证据（谱峰±3Hz 比对、趋势、峭度），不得引用任何"已知注入信息"。

## 诊断假设表（按序取证，每步记录"假设→证据→支持/排除"）
| 假设 | 判据 |
|---|---|
| 轴承外圈 BPFO | 包络谱在 BPFO±3Hz 有显著峰，且随负载/时间增长 |
| 轴承内圈 BPFI | 包络谱在 BPFI±3Hz（可能有 1×fr 边带） |
| 滚动体 BSF / 保持架 FTF | 包络谱在 BSF/FTF±3Hz |
| 不对中 | 2×fr 幅值 > 1×fr 的 1/3，径向为主 |
| 不平衡 | 1×fr 占优、相位稳定 |
| 误报 | 趋势无恶化 + 频谱无特征峰 + 峭度≈3 |

## 流程（≥3 连续步骤，覆盖规划→调用→反馈→验证）
1. 规划：读 /api/device 确认等级；列出候选假设。
2. 取证：/api/trend（是否持续恶化）→ /api/spectrum type=fft（1×/2×）→ type=envelope（特征频率）→
   /api/charfreq 换算理论频率与谱峰比对（±3Hz 容差）→ /api/alarm/history 佐证；
   可调 GET /api/equipment-map 获取部件-测点-阈值映射，把证据对准到具体部件。
3. 交叉验证：时域（峭度>4）与频域（特征峰）与趋势（连续上升）三者至少两项一致才可下结论。
4. 结论：conclusion + confidence + 逐条证据（引用接口返回数值）。
5. 行动：/api/task/create（level=severe 时标题含"停机检查"）。
6. 验证：复查 /api/device 与报告落库结果一致，向用户汇报差异。

## 非完美路径（必须真实执行，不许跳过）
- 证据不足两项一致 → 不下结论，明确列出"还缺什么证据"并继续取证；
- 某工具超时/报错 → 换等价工具（waveform+本地计算替代 spectrum）重试一次并记录；
- 全部假设被排除 → 结论"误报"，给出排除依据。

## 输出
最终必调 /api/report/submit，evidence_json 为数组：[{step, tool, key_numbers, supports}]；
若结论能定位到具体部件，把该部件 id 作为 component_id 参数随报告提交（如 component_id=bearing_de，见设备部件语义表），定位不到则省略。
