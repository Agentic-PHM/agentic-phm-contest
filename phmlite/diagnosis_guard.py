"""Evidence support checks; never reads the simulation truth or generates diagnoses."""
import math
import re
from urllib.parse import parse_qs

STATUSES = ("confirmed", "provisional", "undetermined")
CODES = ("bpfo", "bpfi", "bpfo_bpfi", "unbalance", "looseness", "undetermined")

# ---- 判据常数（唯一来源；诊断提示词与两份技能文件里的数值必须与这里一致，
# ---- tests/test_skill_consistency.py 是防漂移回归阀）----
FUNDAMENTAL_TOLERANCE_HZ = 1.5   # 包络基频与理论频率的容差（Hz）
PEAK_FLOOR_RATIO = 20            # 包络特征峰/底噪 的最小显著比
UNBALANCE_DOMINANCE = 5.0        # 不平衡：A1X > 5×max(A2X, A3X)
LOOSENESS_SECOND_RATIO = 0.5     # 松动：A3X > 0.5×A1X（且 A2X > A1X）

def disclaims_selected_mechanism(conclusion, code):
    """Other named mechanisms may lack evidence without denying this diagnosis.

    Unscoped refusals stay conservative. This is a limited text consistency
    check; the independently measured spectral support is still required.
    """
    aliases = {"bpfo": ("bpfo", "外圈"), "bpfi": ("bpfi", "内圈"),
               "bpfo_bpfi": ("bpfo_bpfi", "混合"),
               "unbalance": ("unbalance", "不平衡"), "looseness": ("looseness", "松动")}
    selected = {code} if code != "bpfo_bpfi" else {"bpfo", "bpfi", "bpfo_bpfi"}
    for clause in re.split(r"[。；;，,！？!?\n]", conclusion.lower()):
        if not any(t in clause for t in ("无法确定机理", "无法确定故障", "证据不足", "待复测确认", "不能确证", "无法确证")):
            continue
        mentioned = {label for label, names in aliases.items() if any(name in clause for name in names)}
        if not mentioned or mentioned & selected:
            return True
    return False

def observations(index):
    found = {}
    for call in (index or {}).get("tool_calls", []):
        if call.get("is_error") is not False or not isinstance(call.get("response"), dict):
            continue
        payload = call["response"]
        if any(k in payload for k in ("detail", "error")):
            continue
        endpoint = call.get("endpoint")
        kind = (call.get("request_args") or {}).get("type")
        key = kind if endpoint == "/api/spectrum" else endpoint
        required = {"/api/device": "points", "/api/trend": "series", "fft": "peaks",
                    "envelope": "peaks", "/api/charfreq": "BPFO"}.get(key)
        if required is None:
            continue
        if required and (required not in payload or payload[required] in (None, [], {})):
            continue
        if "http_audit" in (index or {}):
            args = call.get("request_args") or {}
            if args.get("run_id") != index.get("run_id") or not any(
                r.get("run_id") == index.get("run_id") and r.get("tool") == endpoint and r.get("http_status") == 200
                and all(parse_qs(r.get("args") or "").get(k, [None])[-1] == v for k, v in args.items())
                for r in index.get("http_audit") or []):
                continue
        found[key] = payload
    return found

def validate_diagnosis(conclusion, status, code, index):
    if not isinstance(conclusion, str) or len(conclusion.strip()) < 4:
        return "结论须为有意义的中文观察/诊断文本"
    if status not in STATUSES or code not in CODES:
        return "必须给出diagnosis_status及受支持的diagnosis_code；未建模机理用undetermined"
    if status != "confirmed":
        if not any(t in conclusion for t in ("证据不足", "无法确定", "待复测", "待确认", "工具不可用", "需复测", "需补测", "需补充", "保留", "待验证", "倾向")):
            return "保留性报告必须说明证据不足或待确认"
        if status == "undetermined" and code != "undetermined":
            return "无法确定机理时diagnosis_code必须为undetermined"
        return None
    if code == "undetermined":
        return "未知机理不得标confirmed"
    if disclaims_selected_mechanism(conclusion, code):
        return "结论明确保留/拒判，不得标confirmed或自动创建工单"
    obs = observations(index)
    if not all(k in obs for k in ("/api/device", "/api/trend", "fft", "envelope", "/api/charfreq")):
        return "完整确证需成功取得时域、趋势、FFT、包络谱及理论频率；缺失时用provisional/undetermined"
    envelope, fft, cf = obs["envelope"], obs["fft"], obs["/api/charfreq"]
    def amplitude(spectrum, hz, tolerance=FUNDAMENTAL_TOLERANCE_HZ):
        return max((float(p["amp"]) for p in spectrum.get("peaks", [])
                    if abs(float(p["hz"]) - hz) <= tolerance), default=0)
    try:
        if code in ("bpfo", "bpfi", "bpfo_bpfi"):
            floor = float(envelope.get("noise_floor", 0))
            if not math.isfinite(floor) or floor <= 0:
                return "包络谱缺少可核验底噪，无法确认特征峰显著性"
            labels = ("BPFO", "BPFI") if code == "bpfo_bpfi" else (code.upper(),)
            # Fundamental matching avoids interpreting an outer-ring harmonic as BPFI.
            if not all(amplitude(envelope, float(cf[label])) / floor >= PEAK_FLOOR_RATIO
                       for label in labels):
                return "已测包络谱不支持所声明的基频（峰/底噪需≥20）；应保留候选并复测"
        elif code == "unbalance":
            fr = float(cf["fr"])
            if amplitude(fft, fr) <= UNBALANCE_DOMINANCE * max(
                amplitude(fft, 2 * fr), amplitude(fft, 3 * fr), 1e-9):
                return "1X相对2X/3X未显著占优，不能确证不平衡"
        elif code == "looseness":
            fr = float(cf["fr"])
            a1, a2, a3 = (amplitude(fft, harmonic * fr) for harmonic in (1, 2, 3))
            # The shipped generator adds 2X/3X, not half-speed components.
            # Weak noise near 0.5X must not become positive mechanism evidence.
            if not (a1 > 0 and a2 > a1 and a3 > LOOSENESS_SECOND_RATIO * a1):
                return "缺少2X高于1X且3X超过1X一半的组合支持，不能确证本模型的松动"
    except (ValueError, TypeError, KeyError):
        return "工具响应缺少有效频率/幅值"
    return None

def measured_evidence(index):
    """Build stored evidence from actual tool JSON, never from invented report numbers."""
    def flatten(value, prefix=""):
        out = {}
        if isinstance(value, dict):
            for key, child in value.items():
                out.update(flatten(child, prefix + str(key) + "."))
        elif isinstance(value, list):
            for i, child in enumerate(value):
                out.update(flatten(child, prefix + str(i) + "."))
        elif isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            out[prefix.rstrip(".")] = value
        return out
    steps = []
    for tool, payload in observations(index).items():
        numbers = flatten(payload)
        if numbers:
            steps.append({"step": len(steps)+1, "tool": tool, "key_numbers": numbers,
                          "supports": "本次AGH实际取得的工具观测；机理支持以诊断核验为准"})
    return steps
