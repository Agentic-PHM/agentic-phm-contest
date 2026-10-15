# 旋转机械自主诊断策略（P-101，与 phmlite/autodiag._prompt 及 phmlite/diagnosis_guard 同源）

> 本文件是诊断技能的**唯一事实来源**：判据数值必须与 `phmlite/diagnosis_guard.py` 的常数一致
> （容差 1.5Hz、峰/底噪 ≥20、不平衡 5 倍、松动 0.5 比例），tests/test_skill_consistency.py 是防漂移回归阀。

## 取证接口（仅此 6 个；禁止 /api/scenario/*、/api/agent/*、/api/autodiag/*、/api/waveform、/api/alarm/history、/api/equipment-map）

| 接口 | 用途 |
|---|---|
| /api/device?run_id=… | 时域：各测点 RMS/峭度/峰峰值与等级 |
| /api/trend?point=…&kpi=rms&hours=24&limit=12&run_id=… | 24h 趋势；series 只返回最近 12 点（limit=12 是设计如此，不算工具缺失） |
| /api/spectrum?point=…&type=fft&top=20&run_id=… | FFT：1×/2×/3× 转频幅值（不平衡/松动判据） |
| /api/spectrum?point=…&type=envelope&top=20&run_id=… | 包络谱：轴承特征频率峰值与 noise_floor |
| /api/charfreq?run_id=… | 理论特征频率（BPFO/BPFI/fr 等） |
| /api/report/submit、/api/task/create | 提交结论与工单（写接口，见"输出"） |

## 建模标签与确证判据（数值=diagnosis_guard 常数）

| diagnosis_code | confirmed 判据 |
|---|---|
| bpfo | 包络谱在 BPFO 基频 ±1.5Hz 内的峰幅值 / noise_floor ≥ 20（不需要 BPFI 支持也不因缺 BPFI 否定） |
| bpfi | 包络谱在 BPFI 基频 ±1.5Hz 内的峰幅值 / noise_floor ≥ 20（不需要 BPFO 支持；不能把 BPFO 二倍频当成 BPFI） |
| bpfo_bpfi | BPFO 与 BPFI 两个基频分别满足上述条件 |
| unbalance | FFT：A1X > 5×max(A2X, A3X)（不需要出现轴承特征峰） |
| looseness | FFT：A2X > A1X 且 A3X > 0.5×A1X（本模型松动合成器只加 2X/3X，不含半频） |
| undetermined | 存在异常但机理无法确定（未知宽频异常等）；不得猜未建模机理 |

注意：2X 分量本来就有本底（正常信号 2X/1X≈0.4），**单凭 2X/1X 比值不能判不对中**；
不对中/BSF/FTF 不在已建模标签内，遇到时用 undetermined 并列出所需证据。
排除轴承缺陷不等于证明其它机理。

## 诊断状态（diagnosis_status）

- confirmed：证据完整且支持所声明机理（满足上表判据）。
- provisional：**仅限工具返回错误或缺少必要接口**时使用——趋势 limit=12 是设计如此、
  单次采样足以确证，都不得作为降级理由。
- undetermined：存在异常但机理无法确定。

## 输出

- 提交 /api/report/submit：参数 run_id、equip=P-101、conclusion（中文结论文本，
  写明支持判据）、confidence（0~1 自评）、diagnosis_status、diagnosis_code。
  **不要传 evidence_json**——后端只从本 run 的 AGH 实际 HTTP 响应生成实测证据。
- 仅 confirmed 报告成功后创建 /api/task/create（level=severe，source=agent，带 run_id）；
  provisional/undetermined 提交报告后停止，不创建工单。
- curl 必须一整行（不要反引号续行、不要换行分隔）；URL 用字面量，不要变量拼 URL。
