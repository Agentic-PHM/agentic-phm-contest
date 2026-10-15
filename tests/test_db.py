from datetime import datetime

from phmlite.db import DB

def test_schema_and_roundtrip(tmp_path):
    db = DB(tmp_path / "t.db"); db.migrate()
    # trend 按 now-24h 过滤，写入时间必须用当前时刻（写死日期会随时间推移失效）
    now = datetime.now().isoformat(timespec="seconds")
    db.insert_eigen("DE", now, rms=2.1, kurtosis=3.1, pp=5.0, level="normal")
    rows = db.trend("DE", "rms", hours=24)
    assert rows and rows[-1]["v"] == 2.1
    assert rows[-1]["origin"] == "live"          # 4.1：默认按真实采样标记


def test_trend_distinguishes_seeded_history_from_live_samples(tmp_path):
    """4.1：模拟历史曲线与真实采样必须可区分，不能混同为现场故障演化。"""
    db = DB(tmp_path / "o.db").migrate()
    now = datetime.now().isoformat(timespec="seconds")
    db.insert_eigen("DE", now, rms=2.1, kurtosis=3.1, pp=5.0, level="normal", origin="seed")
    db.insert_eigen("DE", now, rms=6.7, kurtosis=10.5, pp=67.0, level="severe")
    rows = db.trend("DE", "rms", hours=24)
    assert [r["origin"] for r in rows] == ["seed", "live"]
    assert sum(1 for r in rows if r["origin"] == "seed") == 1

def test_maybe_alarm_requires_3_consecutive_breaches(tmp_path):
    db = DB(tmp_path / "t.db"); db.migrate()
    for v in [2.0, 5.0, 5.2]:                 # 注意阈值 4.5，连续 2 次不触发
        assert db.maybe_alarm("P-101", "DE", rms=v, kurtosis=3.0, pp=6.0) is None
    assert db.maybe_alarm("P-101", "DE", rms=5.4, kurtosis=3.0, pp=6.0) is not None  # 第 3 次 → 报警行
    assert len(db.alarm_history("P-101")) == 1

def test_task_and_report_roundtrip(tmp_path):
    db = DB(tmp_path / "t.db"); db.migrate()
    tid = db.create_task(title="更换轴承", desc="BPFO 特征明显", level="severe", source="agent")
    rid = db.submit_report(run_id="r1", equip="P-101", conclusion="外圈故障", confidence=0.86, evidence="{}")
    assert db.get_task(tid)["status"] == "open" and db.get_report(rid)["conclusion"] == "外圈故障"

def test_migrate_adds_component_column_to_legacy_db(tmp_path):
    """旧库（reports 无 component 列）迁移后自动 ALTER 补列，旧/新调用均兼容。"""
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        "CREATE TABLE reports(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, equip TEXT,"
        "conclusion TEXT, confidence REAL, evidence TEXT);"
        "INSERT INTO reports(ts, run_id, equip, conclusion, confidence, evidence)"
        "VALUES('2026-01-01T00:00:00','r0','P-101','旧结论',0.5,'{}');")
    conn.commit(); conn.close()
    db = DB(p); db.migrate()
    rid = db.submit_report(run_id="r1", equip="P-101", conclusion="新结论", confidence=0.9,
                           evidence="{}", component="bearing_de")
    rows = {r["id"]: r for r in db.report_list()}
    assert rows[rid]["component"] == "bearing_de"
    rid2 = db.submit_report(run_id="r2", equip="P-101", conclusion="无部件结论", confidence=0.9, evidence="{}")
    assert db.report_list()[0]["id"] == rid2
    assert next(r for r in db.report_list() if r["id"] == rid2)["component"] is None

def test_migrate_adds_task_run_id_to_legacy_db(tmp_path):
    import sqlite3
    p = tmp_path / "legacy-tasks.db"
    conn = sqlite3.connect(p)
    conn.executescript(
        "CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, desc TEXT,"
        "level TEXT, source TEXT, status TEXT DEFAULT 'open');"
        "INSERT INTO tasks(ts, title, desc, level, source)"
        "VALUES('2026-01-01T00:00:00','旧任务','旧记录','notice','manual');")
    conn.commit(); conn.close()

    db = DB(p); db.migrate()
    new_id = db.create_task("当前任务", "run 绑定", "severe", "agent", run_id="run-42")
    rows = {task["id"]: task for task in db.task_list()}
    assert rows[1]["run_id"] is None
    assert rows[new_id]["run_id"] == "run-42"

def test_seed_is_deterministic(tmp_path):
    from phmlite.seed import seed_history
    db = DB(tmp_path / "a.db"); db.migrate()
    seed_history(db, points=["DE", "NDE"], hours=24, step_minutes=5, seed=7)
    a = db.trend("DE", "rms", hours=24)
    db2 = DB(tmp_path / "b.db"); db2.migrate()
    seed_history(db2, points=["DE", "NDE"], hours=24, step_minutes=5, seed=7)
    assert [r["v"] for r in a] == [r["v"] for r in db2.trend("DE", "rms", hours=24)]
    assert all(1.5 < r["v"] < 3.5 for r in a)   # 全程正常区
