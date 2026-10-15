"""F2 防漂移回归：两份技能文件同源一致、判据数值与 diagnosis_guard 常数一致、
接口集合不超出提示词允许范围。任何一侧单独改动都会在这里失败。"""
import json
import re
from pathlib import Path

from phmlite import diagnosis_guard as guard

REPO = Path(__file__).resolve().parents[1]
SKILL = REPO / "agent" / "skills" / "diagnosis.md"
SKILL_AGH = REPO / ".agh" / "skills" / "diagnosis" / "SKILL.md"

# 技能文件允许出现的接口（与诊断提示词的取证清单一致，不得超出）
ALLOWED_ENDPOINTS = {"/api/device", "/api/trend", "/api/spectrum", "/api/charfreq",
                     "/api/report/submit", "/api/task/create"}
FORBIDDEN_STRINGS = ("1/3", "±3Hz", "±3 Hz", "evidence_json 为数组")
# 文档里以"禁止"口径枚举的接口不算越界；出现任何其它未知接口才算
DOCUMENTED_FORBIDDEN_ENDPOINTS = {"/api/scenario", "/api/agent", "/api/autodiag",
                                  "/api/waveform", "/api/alarm/history", "/api/equipment-map"}


def _body(path):
    """SKILL.md 去掉 YAML frontmatter；diagnosis.md 原样。统一去首部空行。"""
    text = Path(path).read_text(encoding="utf-8")
    return re.sub(r"\A---\n.*?\n---\n", "", text, flags=re.S).lstrip("\n")


def test_two_skill_files_share_identical_body():
    assert _body(SKILL) == _body(SKILL_AGH), "agent/ 与 .agh/ 两份技能正文不一致（需同步）"


def test_skill_no_longer_contains_stale_rules():
    body = _body(SKILL)
    for token in FORBIDDEN_STRINGS:
        assert token not in body, f"技能文件仍含过时判据/字段: {token}"
    # 过时的 95℃ 轴温口径（db.py 权威值 80）不应再出现
    assert "95℃" not in body


def test_skill_criteria_numbers_match_guard_constants():
    body = _body(SKILL)
    assert f"±{guard.FUNDAMENTAL_TOLERANCE_HZ}Hz" in body, "容差须与 guard 常数一致"
    assert f"≥ {guard.PEAK_FLOOR_RATIO}" in body, "峰/底噪比须与 guard 常数一致"
    assert f"{guard.UNBALANCE_DOMINANCE:.0f}×max" in body, "不平衡倍数须与 guard 常数一致"
    assert f"{guard.LOOSENESS_SECOND_RATIO}×A1X" in body, "松动比例须与 guard 常数一致"


def test_skill_endpoints_within_prompt_allowlist():
    body = _body(SKILL)
    mentioned = {ep.rstrip("/") for ep in re.findall(r"/api/[a-z_/-]+", body)}
    extra = mentioned - ALLOWED_ENDPOINTS - DOCUMENTED_FORBIDDEN_ENDPOINTS
    assert not extra, f"技能文件出现了未知接口（既非允许也非文档化禁止）: {sorted(extra)}"
    assert mentioned <= ALLOWED_ENDPOINTS | DOCUMENTED_FORBIDDEN_ENDPOINTS


def test_guard_constants_are_the_single_source():
    """guard 模块常数存在且数值与提示词口径一致（防止有人只改文档不改源）。"""
    assert guard.FUNDAMENTAL_TOLERANCE_HZ == 1.5
    assert guard.PEAK_FLOOR_RATIO == 20
    assert guard.UNBALANCE_DOMINANCE == 5.0
    assert guard.LOOSENESS_SECOND_RATIO == 0.5
    assert guard.STATUSES == ("confirmed", "provisional", "undetermined")
    assert set(guard.CODES) == {"bpfo", "bpfi", "bpfo_bpfi", "unbalance",
                                "looseness", "undetermined"}
