"""P0 问题4 盲测评分器：said 标签提取、指标汇总、规则基线（纯函数，不触网）。

4.2 扩展：评分器要能表达**混合故障**与**主动拒绝**，并且不能被否定表述骗到。
下面用**真实跑出来的结论文本**（data/phmlite.db reports 表，2026-10-13 取样）做回归。
"""
from scripts.blind_benchmark import (classify_text, is_hedged, rule_baseline, summarize)

#: 真实结论文本（逐字取自库中 reports.conclusion）
REAL_BPFO = ("驱动端轴承外圈BPFO缺陷：包络谱主峰134.77Hz命中理论BPFO=135.09Hz"
             "(偏差0.33Hz在±3Hz内)且呈2x-6x谐波簇，峭度10.50远超阈值4，"
             "24h趋势恶化约3.2倍，建议停机检查并更换驱动端轴承")
REAL_BPFI = ("驱动端轴承内圈BPFI缺陷：包络谱主峰262.70Hz命中理论BPFI=262.24Hz"
             "(偏差0.45Hz在±3Hz内)并呈2x-5x谐波簇，RMS 9.17远超基线，建议停机检查并更换驱动端轴承")
REAL_HEDGED = ("驱动端轴承缺陷（倾向性，外圈BPFO待复测确认）：峭度10.51远超阈值4且等级severe，"
               "但本次包络谱工具不可用")   # 报告 #21：工具故障下的保留性结论


def test_classify_text_labels():
    assert classify_text(REAL_BPFO) == "bpfo"
    assert classify_text(REAL_BPFI) == "bpfi"
    assert classify_text("运行正常，无异常") == "normal"
    assert classify_text("存在冲击特征，有故障迹象") == "abnormal-untyped"
    assert classify_text("") == "unknown"
    assert classify_text("0.92") == "unknown"          # 历史真实缺陷：只填了数字


def test_classify_text_handles_mixed_and_refusal():
    """4.2：混合故障要能表达；无法识别要能表达"主动拒绝"；非轴承机理不能落进未评分桶。"""
    assert classify_text("外圈BPFO与内圈BPFI特征峰同时存在，判定复合缺陷") == "bpfo_bpfi"
    assert classify_text("证据不足，无法确定故障类型，建议人工检查") == "undetermined"
    assert classify_text("无法识别该频谱形态，不给出结论") == "undetermined"
    assert classify_text("1X 转频占优、无冲击成分，判定不平衡") == "unbalance"
    assert classify_text("2X/3X 谐波簇抬升，基础松动") == "looseness"
    assert classify_text("宽频能量抬升，不符合任何已知轴承特征频率，无法确定机理") == "undetermined"


def test_classify_text_is_not_fooled_by_negation():
    """真实结论会用"BPFI/BSF/FTF 区无主导峰"**排除**某频率；数关键词会把它误判成混合故障。"""
    text = ("驱动端轴承外圈BPFO缺陷：包络谱主峰134.77Hz命中BPFO，"
            "BPFI/BSF/FTF 区无主导峰，排除内圈与滚动体缺陷")
    assert classify_text(text) == "bpfo"
    assert classify_text("未见 BPFI 特征峰，判外圈 BPFO 损伤") == "bpfo"
    assert classify_text("排除 BPFO，内圈 BPFI 命中") == "bpfi"


def test_is_hedged_distinguishes_tentative_from_confirmed():
    """4.2 要求比较证据质量：倾向性结论不能被当成确证结论。"""
    assert is_hedged(REAL_HEDGED) is True
    assert is_hedged(REAL_BPFO) is False
    assert is_hedged("证据不足，无法确定") is True



def test_summarize_counts_failures_in_the_denominator():
    """P0 问题2①：无结果 / 无法评分的样例**不能**从总体准确率里消失。"""
    results = [
        {"kind": "normal", "truth": "normal", "said": "normal", "correct": True},
        {"kind": "e2e", "truth": "bpfo", "said": "bpfo", "correct": True,
         "report_id": 1, "latency_s": 30.0},
        {"kind": "e2e", "truth": "bpfi", "said": "bpfo", "correct": False,
         "report_id": 2, "latency_s": 40.0},
        {"kind": "e2e", "truth": "bpfo", "said": "no-report", "correct": False,
         "report_id": None, "latency_s": None},
    ]
    m = summarize(results)
    assert m["n_cases"] == 4 and m["n_scored"] == 3
    assert m["accuracy_all_samples"] == 0.5           # 2/4：失败与无结果都计入分母
    assert m["accuracy_scored"] == round(2 / 3, 3)    # 旧口径，保留仅为对照
    assert m["unscored_rate"] == 0.25
    assert m["confusion_matrix"]["bpfi"]["bpfo"] == 1
    assert m["latency_distribution_s"] == {"n": 2, "min": 30.0, "median": 35.0, "max": 40.0}


def test_full_closure_requires_verification_not_just_a_report():
    """P0 问题2②：只看 report_id 会把核验失败的运行也当成"完成"。"""
    base = {"kind": "e2e", "truth": "bpfo", "said": "bpfo", "correct": True,
            "report_id": 1, "task_created": True, "verification_ok": True, "latency_s": 30.0}
    good = dict(base, final_state="done")
    bad = dict(base, final_state="verification_failed", verification_ok=False, report_id=2)
    m = summarize([good, bad])
    assert m["report_only_rate"] == 1.0        # 旧口径：两份都有报告 → 100%
    assert m["full_closure_rate"] == 0.5       # 新口径：只有一份真正闭环


def test_false_positive_rate_uses_negatives_only():
    """P0 问题2③：误报率分母只能是阴性样本，不能用全部用例。"""
    results = [
        {"kind": "normal", "truth": "normal", "said": "normal", "correct": True},
        {"kind": "normal", "truth": "normal", "said": "bpfo", "correct": False},
        *[{"kind": "e2e", "truth": "bpfo", "said": "bpfo", "correct": True, "report_id": i,
           "task_created": True, "verification_ok": True, "final_state": "done",
           "latency_s": 20.0} for i in range(1, 9)],
    ]
    m = summarize(results)
    assert m["negative_samples"] == 2
    assert m["false_positive_rate"] == 0.5     # 1/2，而不是 1/10


def test_summarize_reports_refusal_and_missing_negatives():
    m = summarize([{"kind": "e2e", "truth": "undetermined", "said": "undetermined",
                    "correct": True, "report_id": None}])
    assert m["false_positive_rate"] is None    # 无阴性样本 → not_measured，不许填 0
    assert m["refusal_rate"] == 1.0
    assert m["accuracy_all_samples"] == 1.0


def test_rule_baseline_matches_envelope_truth():
    std = {"fault": "bpfo", "severity": 0.8, "rpm": 2980, "noise": 0.05}
    said, bands = rule_baseline(std)
    assert said == "bpfo" and bands["BPFO"] > bands["BPFI"]
    said, _ = rule_baseline({"fault": "bpfi", "severity": 0.8, "rpm": 2400, "noise": 0.05})
    assert said == "bpfi"
    said, _ = rule_baseline({"fault": "none", "severity": 0.0, "rpm": 2980, "noise": 0.05})
    assert said == "normal"
