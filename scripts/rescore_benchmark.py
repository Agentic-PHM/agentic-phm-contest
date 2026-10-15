"""离线重算已有盲测结果的标签与指标（不重跑模型）。

用途：评分器（`classify_text`）修正后，用**同一批已落库的结论原文**重新判分，
把"改动前 / 改动后"两个口径并列出来，避免"改了标准就让成绩变好"这种自证。

用法：
    python scripts/rescore_benchmark.py [结果.json]      # 省略则取 evidence 下最新一份
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blind_benchmark import classify_text, summarize  # noqa: E402


def rescore(path):
    data = json.load(open(path, encoding="utf-8"))
    before, after = data["metrics"], None
    changed = []
    for c in data["cases"]:
        if "triggered" in c:      # 旧结果里没有 case_invalid 字段：可从 triggered 推出
            c["case_invalid"] = not c["triggered"]
        text = c.get("conclusion") or ""
        if not text or c.get("said") in (None, "no-report", "harness-error"):
            continue          # 没有结论原文的（未落报告）无从重算，原样保留
        new_said = classify_text(text)
        if new_said != c.get("said"):
            changed.append((c["case"], c["said"], new_said))
        c["said_before_rescore"] = c.get("said")
        c["said"] = new_said
        c["correct"] = new_said == c["truth"]
    after = summarize(data["cases"])
    return data, before, after, changed


def _latest_source():
    """最新一份**原始**结果（排除本脚本自己写出的 -rescore.json，否则会把自己当输入）。"""
    files = [p for p in glob.glob(os.path.join("evidence", "blind-benchmark-*.json"))
             if not p.endswith("-rescore.json")]
    if not files:
        raise SystemExit("evidence/ 下没有原始盲测结果")
    return max(files, key=os.path.getmtime)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else _latest_source()
    data, before, after, changed = rescore(path)
    print(f"结果文件: {path}\n")
    print("=== 重算后标签发生变化的用例 ===")
    if not changed:
        print("  （无）")
    for case, old, new in changed:
        print(f"  {case:<24} {old} -> {new}")
    keys = ("n_scored", "unscored_rate", "accuracy_all_samples", "accuracy_all_valid_cases",
            "accuracy_scored", "invalid_case_n", "refusal_rate", "false_positive_rate",
            "full_closure_rate", "report_only_rate",
            "tool_failure_safe_rate", "tool_failure_false_conclusion_n")
    print("\n=== 指标：改动前 vs 改动后（同一批结论原文）===")
    print(f"{'指标':<34} {'重算前':>12} {'重算后':>12}")
    for k in keys:
        print(f"{k:<34} {str(before.get(k)):>12} {str(after.get(k)):>12}")
    print("\n=== 规则基线（同一批有效用例，可比口径）===")
    rb_before, rb_after = before.get("rule_baseline") or {}, after.get("rule_baseline") or {}
    for k in ("accuracy_all_samples", "accuracy_all_valid_cases", "false_positive_rate"):
        print(f"{k:<34} {str(rb_before.get(k)):>12} {str(rb_after.get(k)):>12}")
    out = path.replace(".json", "-rescore.json")
    json.dump({"source": os.path.basename(path), "metrics_before": before,
               "metrics_after": after, "changed_labels": changed, "cases": data["cases"]},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("\n已写出", out)


if __name__ == "__main__":
    main()
