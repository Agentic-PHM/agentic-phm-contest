"""SQLite 数据层：特征历史、报警判定（四档阈值 + 连续 N 点）、任务、报告、工具调用留痕。"""
import sqlite3
import threading
from datetime import datetime, timedelta

RMS_NOTICE = 4.5
RMS_SEVERE = 7.1
KURT_NOTICE = 4.0
KURT_SEVERE = 6.0
ACCURING_NUM = 3

# 调相机通道阈值（行业惯例/旧系统口径），随 /api/channels 下发
SHAFT_NOTICE, SHAFT_SEVERE = 80.0, 120.0      # 轴振 μm
PEDESTAL_NOTICE, PEDESTAL_SEVERE = 4.5, 7.1   # 瓦振 mm/s（与 RMS 同口径）
RPM_NOTICE, RPM_SEVERE = 3010.0, 3040.0       # 电机转速 rpm（额定 2980，越限 +1%/+2%）
TEMP_NOTICE, TEMP_SEVERE = 65.0, 75.0         # 轴承温度 °C
# 部件级温度通道阈值（热力图视图用；对齐 tiaoxiangji_equipment_map 语义绑定，
# 与振动阈值同为"行业惯例值/演示配置，可按现场整定值在后台替换"）
SHAFT_TEMP_NOTICE, SHAFT_TEMP_SEVERE = 65.0, 80.0   # 轴温 DE/NDE °C（equipment_map 轴承温度 normal/warning 档）
COOL_AIR_NOTICE, COOL_AIR_SEVERE = 40.0, 50.0       # 冷风温度 °C（equipment_map 冷却系统 normal/critical 档）
OIL_TEMP_NOTICE, OIL_TEMP_SEVERE = 50.0, 60.0       # 油温 °C（演示配置：参照同风格自定）


def band_level(value, notice, severe):
    """通用阈值分档（值越高越差）：≥severe→severe，≥notice→notice，否则 normal。"""
    if value >= severe:
        return "severe"
    if value >= notice:
        return "notice"
    return "normal"

_DDL = """
CREATE TABLE IF NOT EXISTS eigen_history(point TEXT, ts TEXT, rms REAL, kurtosis REAL, pp REAL,
                                         level TEXT, origin TEXT DEFAULT 'live');
CREATE TABLE IF NOT EXISTS alarms(id INTEGER PRIMARY KEY AUTOINCREMENT, equip TEXT, point TEXT, ts TEXT,
                                  level TEXT, kpi TEXT, value REAL, msg TEXT, seq INTEGER,
                                  incident_id INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY AUTOINCREMENT, equip TEXT, point TEXT,
                                     level TEXT, started_ts TEXT, last_ts TEXT, ended_ts TEXT,
                                     alarm_count INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, desc TEXT,
                                 level TEXT, source TEXT, status TEXT DEFAULT 'open', run_id TEXT);
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, equip TEXT,
                                   conclusion TEXT, confidence REAL, evidence TEXT, component TEXT);
CREATE TABLE IF NOT EXISTS run_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT,
                                   tool TEXT, args TEXT, latency_ms REAL);
"""


def _now():
    return datetime.now().isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._breach = {}  # (equip, point) -> 当前连续越限计数
        self._breach_seq = {}  # (equip, point) -> 最近计入的采样序号（防同一采样重复计数）
        self._incident = {}  # (equip, point) -> {"id","level"}：当前活动故障事件（P1 问题6）

    def migrate(self):
        with self._lock:
            self._conn.executescript(_DDL)
            # 旧库兼容：reports 补 component 列（诊断智能体提交的部件 id，3D 数字孪生联动用）
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(reports)").fetchall()}
            if "component" not in cols:
                self._conn.execute("ALTER TABLE reports ADD COLUMN component TEXT")
            for column in ("diagnosis_status", "diagnosis_code"):
                if column not in cols:
                    self._conn.execute(f"ALTER TABLE reports ADD COLUMN {column} TEXT")
            log_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(run_log)").fetchall()}
            if "http_status" not in log_cols:
                self._conn.execute("ALTER TABLE run_log ADD COLUMN http_status INTEGER")
            task_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "run_id" not in task_cols:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN run_id TEXT")
            # 工单生命周期（P0 问题3）：关单时间与复测/处置备注
            if "closed_ts" not in task_cols:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN closed_ts TEXT")
            if "close_note" not in task_cols:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN close_note TEXT")
            # P1 问题5：关单依据（sim_recovery=仿真恢复验证 / manual=人工处置确认）
            if "close_kind" not in task_cols:
                self._conn.execute("ALTER TABLE tasks ADD COLUMN close_kind TEXT")
            # P1 问题3：报警绑定独立样本序号（由采样循环推进，不再由 HTTP 请求次数决定）
            alarm_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(alarms)").fetchall()}
            if "seq" not in alarm_cols:
                self._conn.execute("ALTER TABLE alarms ADD COLUMN seq INTEGER")
            # P1 问题6：报警行 ≠ 独立故障事件。旧库无法回溯归属事件，留 NULL 并在汇总里如实标出。
            if "incident_id" not in alarm_cols:
                self._conn.execute("ALTER TABLE alarms ADD COLUMN incident_id INTEGER")
            if "kind" not in alarm_cols:
                self._conn.execute("ALTER TABLE alarms ADD COLUMN kind TEXT")
            # 4.1：区分模拟历史种子与真实采样（不能把两者混同为现场故障演化）
            eigen_cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(eigen_history)").fetchall()}
            if "origin" not in eigen_cols:
                self._conn.execute("ALTER TABLE eigen_history ADD COLUMN origin TEXT")
                # 旧库无法回溯判定来源：宁可标 unknown，也不把模拟历史冒充成真实采样
                self._conn.execute("UPDATE eigen_history SET origin='unknown' WHERE origin IS NULL")
            self._conn.commit()
        return self

    def insert_eigen(self, point, ts, rms, kurtosis, pp, level, origin="live"):
        """写入一条特征历史。`origin`：live=真实采样，seed=模拟历史种子（4.1 要求两者可区分）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO eigen_history(point, ts, rms, kurtosis, pp, level, origin) "
                "VALUES(?,?,?,?,?,?,?)",
                (point, ts, rms, kurtosis, pp, level, origin))
            self._conn.commit()

    def trend(self, point, kpi, hours=24):
        if kpi not in ("rms", "kurtosis", "pp"):
            raise ValueError(f"不支持的指标: {kpi}")
        cutoff = (datetime.now() - timedelta(hours=hours)).isoformat(timespec="seconds")
        with self._lock:
            rows = self._conn.execute(
                f"SELECT ts, {kpi} AS v, origin FROM eigen_history WHERE point=? AND ts>=? ORDER BY ts",
                (point, cutoff)).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def classify(rms, kurtosis):
        """四档阈值归类：返回 (level, 触发 kpi, 触发值)。"""
        if kurtosis > KURT_SEVERE:
            return "severe", "kurtosis", kurtosis
        if rms > RMS_SEVERE:
            return "severe", "rms", rms
        if kurtosis > KURT_NOTICE:
            return "notice", "kurtosis", kurtosis
        if rms > RMS_NOTICE:
            return "notice", "rms", rms
        return "normal", "rms", rms

    def maybe_alarm(self, equip, point, rms, kurtosis, pp, seq=None):
        """连续 ACCURING_NUM 个**采样**越限才落一条报警并返回该行；未触发或中途恢复正常返回 None。

        P0 问题3：连续判据绑定采样序号 `seq`（由独立采样循环推进）。同一 `seq` 重复调用只算一次，
        因此页面轮询次数不再影响报警触发节奏。

        P1 问题6：报警行**分层到故障事件**。触发后计数归零，持续异常会每 ACCURING_NUM 个采样再落
        一行——归档证据里因此有 1314 条报警，但那不是 1314 起独立故障。现在同一持续越限复用同一个
        `incident_id` 并标 `kind='repeat'`；等级变化或恢复后再次越限才开启新事件。"""
        level, kpi, value = self.classify(rms, kurtosis)
        key = (equip, point)
        if seq is not None:  # 幂等：同一采样序号不重复计数
            last = self._breach_seq.get(key)
            if last == seq:
                return None
            self._breach_seq[key] = seq
        if level == "normal":
            self._breach[key] = 0
            self._end_incident(equip, point)
            return None
        n = self._breach.get(key, 0) + 1
        self._breach[key] = n
        if n < ACCURING_NUM:
            return None
        self._breach[key] = 0
        ts = _now()
        msg = f"{kpi}={value:.2f} 连续{ACCURING_NUM}点超过{level}阈值"
        incident_id, kind = self._incident_for(equip, point, level, ts)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO alarms(equip, point, ts, level, kpi, value, msg, seq, incident_id, kind) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (equip, point, ts, level, kpi, value, msg, seq, incident_id, kind))
            self._conn.execute(
                "UPDATE incidents SET last_ts=?, alarm_count=alarm_count+1 WHERE id=?",
                (ts, incident_id))
            self._conn.commit()
            row = self._conn.execute("SELECT * FROM alarms WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def _incident_for(self, equip, point, level, ts):
        """取/建当前活动故障事件：同等级持续 → repeat；等级变化 → 结束旧事件、开新事件。"""
        key = (equip, point)
        cur = self._incident.get(key)
        if cur and cur["level"] == level:
            return cur["id"], "repeat"
        with self._lock:
            c = self._conn.execute(
                "INSERT INTO incidents(equip, point, level, started_ts, last_ts, alarm_count) "
                "VALUES(?,?,?,?,?,0)", (equip, point, level, ts, ts))
            self._conn.commit()
            new_id = c.lastrowid
        if cur:
            self._close_incident(cur["id"], ts)
        self._incident[key] = {"id": new_id, "level": level}
        return new_id, "new"

    def _close_incident(self, incident_id, ts):
        with self._lock:
            self._conn.execute(
                "UPDATE incidents SET ended_ts=? WHERE id=? AND ended_ts IS NULL", (ts, incident_id))
            self._conn.commit()

    def _end_incident(self, equip, point):
        """恢复正常：结束当前事件（之后再次越限算新事件）。"""
        cur = self._incident.pop((equip, point), None)
        if cur:
            self._close_incident(cur["id"], _now())

    def alarm_summary(self, equip):
        """报警行数 vs **独立故障事件数**（P1 问题6）：两者不能混为一谈。
        `unclassified_rows` 是建库早于事件分层、无法回溯归属的旧报警行——如实标出而不是硬塞进事件。"""
        with self._lock:
            r = self._conn.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT incident_id) AS inc, "
                "SUM(CASE WHEN kind='repeat' THEN 1 ELSE 0 END) AS rep, "
                "SUM(CASE WHEN incident_id IS NULL THEN 1 ELSE 0 END) AS unk "
                "FROM alarms WHERE equip=?", (equip,)).fetchone()
        return {"alarm_rows": r["n"] or 0, "incidents": r["inc"] or 0,
                "repeat_rows": r["rep"] or 0, "unclassified_rows": r["unk"] or 0}

    def alarm_history(self, equip):
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM alarms WHERE equip=? ORDER BY id DESC", (equip,)).fetchall()
        return [dict(r) for r in rows]

    def create_task(self, title, desc, level, source, run_id=None):
        with self._lock:
            if source == "agent" and run_id:
                existing = self._conn.execute("SELECT * FROM tasks WHERE source='agent' AND run_id=?", (run_id,)).fetchone()
                if existing:
                    if (existing["title"], existing["desc"], existing["level"]) != (title, desc, level):
                        raise ValueError("同一run_id已存在不同工单，请核对已有响应")
                    return existing["id"]
            cur = self._conn.execute(
                "INSERT INTO tasks(ts, title, desc, level, source, run_id) VALUES(?,?,?,?,?,?)",
                (_now(), title, desc, level, source, run_id))
            self._conn.commit()
            return cur.lastrowid

    def delete_agent_run_artifacts(self, run_id):
        """Roll back partial agent output when its AGH session fails."""
        with self._lock:
            try:
                self._conn.execute("BEGIN")
                self._conn.execute("DELETE FROM reports WHERE run_id=?", (run_id,))
                self._conn.execute(
                    "DELETE FROM tasks WHERE run_id=? AND source='agent'", (run_id,))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def get_task(self, task_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def close_task(self, task_id, note="", kind=None):
        """关单（P0 问题3 工单生命周期）：仅 open 可关，写入关单时间、处置备注与**关单依据**。

        `kind` 记录这条工单是凭什么关的（P1 问题5）：
          `sim_recovery` = 仿真恢复验证通过（解除仿真注入后复测），**未验证实际维修**；
          `manual`       = 人工处置确认后关单。
        返回关单后的工单行；工单不存在或已关闭返回 None（幂等，不重复关）。"""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE tasks SET status='closed', closed_ts=?, close_note=?, close_kind=? "
                "WHERE id=? AND status='open'", (_now(), note, kind, task_id))
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            row = self._conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def task_list(self, status="all"):
        with self._lock:
            if status == "all":
                rows = self._conn.execute("SELECT * FROM tasks ORDER BY id DESC").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM tasks WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        return [dict(r) for r in rows]

    def submit_report(self, run_id, equip, conclusion, confidence, evidence, component=None,
                      diagnosis_status="legacy", diagnosis_code="undetermined"):
        with self._lock:
            existing = self._conn.execute("SELECT * FROM reports WHERE run_id=?", (run_id,)).fetchone()
            if existing:
                values = (equip, conclusion, confidence, evidence, component, diagnosis_status, diagnosis_code)
                if tuple(existing[k] for k in ("equip", "conclusion", "confidence", "evidence", "component",
                                               "diagnosis_status", "diagnosis_code")) != values:
                    raise ValueError("同一run_id已存在不同报告，请核对已有响应")
                return existing["id"]
            cur = self._conn.execute(
                "INSERT INTO reports(ts, run_id, equip, conclusion, confidence, evidence, component, diagnosis_status, diagnosis_code) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (_now(), run_id, equip, conclusion, confidence, evidence, component, diagnosis_status, diagnosis_code))
            self._conn.commit()
            return cur.lastrowid

    def get_report(self, report_id):
        with self._lock:
            row = self._conn.execute("SELECT * FROM reports WHERE id=?", (report_id,)).fetchone()
        return dict(row) if row else None

    def report_list(self, equip=None):
        with self._lock:
            if equip:
                rows = self._conn.execute(
                    "SELECT * FROM reports WHERE equip=? ORDER BY id DESC", (equip,)).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM reports ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]

    def log_tool(self, run_id, tool, args, latency_ms, http_status=None):
        with self._lock:
            self._conn.execute(
                "INSERT INTO run_log(ts, run_id, tool, args, latency_ms, http_status) VALUES(?,?,?,?,?,?)",
                (_now(), run_id, tool, args, latency_ms, http_status))
            self._conn.commit()

    def run_logs(self, run_id):
        with self._lock:
            rows = self._conn.execute("SELECT * FROM run_log WHERE run_id=? ORDER BY id", (run_id,)).fetchall()
        return [dict(r) for r in rows]
