import json
from phmlite.scenario import Scenario

def test_load_default_and_set_whitelisted(tmp_path):
    p = tmp_path / "scenario.json"
    p.write_text(json.dumps({"rpm": 2980, "fault": {"type": "none", "severity": 0.0},
                             "noise": 0.05, "override": {"rms": None, "kurtosis": None}}))
    sc = Scenario(p)
    assert sc.get()["rpm"] == 2980
    sc.set("fault.type", "bpfo");  sc.set("fault.severity", 0.8)
    sc.set("override.rms", 6.7)          # 手动降值/改值的直接入口
    d = sc.get()
    assert d["fault"] == {"type": "bpfo", "severity": 0.8} and d["override"]["rms"] == 6.7
    assert json.loads(p.read_text())["fault"]["type"] == "bpfo"   # 落盘持久

def test_set_rejects_unknown_key(tmp_path):
    sc = Scenario(tmp_path / "s.json"); sc.ensure()
    try:
        sc.set("admin.password", "x"); assert False
    except KeyError:
        pass
