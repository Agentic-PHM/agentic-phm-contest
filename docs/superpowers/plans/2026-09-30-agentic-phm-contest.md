# Agentic-PHM 比赛轻量化改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在零新增安装约束下，把 TSI/PHM 现有资产改造成"阈值报警 → AGH/Agnes 自主取证诊断 → 运维任务闭环"的比赛演示系统。

**Architecture:** 新建自包含 FastAPI 服务 `phm-lite`（SQLite + scenario.json 取代 PG/Redis/Kafka/Mongo），以全 GET 接口暴露设备/趋势/波形/频谱/特征频率/报警/任务等能力供 AGH Agent 用内置工具直接调用；AGH（固定 commit 5da4073）+ Agnes 模型做诊断决策；AlgorithmWeb/tsi/tsi_dist 三仓库作为算法出处、接口契约与现有系统素材（不参与运行时）。

**Tech Stack:** Python 3.13（numpy 2.2.3 / scipy 1.17.0 / fastapi 0.115.0 / uvicorn 0.30.0 / pytest 9.0.3，系统已装）· SQLite（标准库）· AGH 运行**仓库内随附的便携包** `vendor/agh-portable-20260930.zip`（官方构建 + 本地优化补丁，自带 node24 v24.10.0，44MB，解压即用）· Agnes API（`https://api.agnes-ai.cn/v1`）· 前端 React + Ant Design（Vite 构建）。

---

## 环境事实（2026-09-30 实测，方案的既定前提）

| 事实 | 影响 |
|---|---|
| 本机无任何数据服务（5432/6379/27017 均未监听，192.168.1.251 不可达） | 数据层必须自包含：SQLite + JSON |
| `D:\TSI\AlgorithmWeb\venv` 已损坏（指向不存在的 Python311） | AlgorithmWeb 不能作为服务运行 |
| 系统 Python 3.13 已装 numpy/scipy/fastapi/uvicorn/pytest/pydantic（无 pandas） | phm-lite 零下载可建；不依赖 pandas |
| node v24.19.0 / npm / curl 可用 | AGH 可运行 |

## 关键决策（工程师不可自知的部分，全部锁定）

- **D1 三仓库用法（2026-09-30 用户决定更新）**：三仓库仅作**内部参考**，不出现在任何对外材料中。**仓库代码、README、提交材料一律不写"基于某项目改造"之类表述，方法以团队自主设计呈现**；`docs/` 计划文档含开发过程记录，公开前需评估处理（README 已标注）。
- **D2 手动仿真**：`data/scenario.json` 是唯一场景开关。手动改值（或 `GET /api/scenario/set`）即完成"注入异常"：`fault.type=bpfo, severity=0.8` → 波形合成叠加 BPFO 冲击串 → RMS/峭度越限 → 报警。无 MQTT、无 PyQt。
- **D3 工具接口全 GET**：Agent 用 AGH 内置工具（web_fetch / shell+curl）即可调用，不写 AGH 插件、不引 MCP 依赖。
- **D4 AGH 运行时 = 仓库自带的便携包 `vendor/agh-portable-20260930.zip`**（2026-09-30 用户决定：官方原版 agent 体验过弱；便携包为优化版——内置 `@agh-ext/hook-max-tokens` 扩展修复网关 4096 默认截断导致的空回复、含 retry 补丁、自带 node24 v24.10.0。**已清入仓库**：剔除 dbconn 审计工具/*.bak-preretry 备份/旧会话数据/旧 Agnes Key，扩展注册与配置保留，新机器免装扩展；清单一见 `vendor/README.md`。原始参考目录 `D:\agh-portable` 含回滚备份）。**必须用包内 node24 启动**：系统 node v24.19 的 libuv 1.52 会被原生模块拒绝（`E_SYSTEM_NATIVE_UNAVAILABLE`）。Web 工作台 `http://127.0.0.1:4177/`（setup-agh.ps1 启动）。`consent=LOCAL`（轨迹只留本地）；**绝不设置** `AGNES_TRACE_ENDPOINT`/`AGNES_TRACE_TOKEN`。合规锚点：执行记录格式与官方一致（`agh-home/data/` 下 audit/profiles/checkpoints），官方仓库 main 树 `5da4073` 保留为行为审计参照基线。
- **D5 Agnes Key**（用户确认私有仓库暂存、开源前删除）：记于 `.env`，`AGNES_API_KEY=<redacted>`，`AGNES_BASE_URL=https://api.agnes-ai.cn/v1`（AGH 源码内置该 base_url，见 `packages/ai/src/adapters/pi/providers/agnes-ai.ts`）。README 与本计划均标注"开源前必删"。
- **D6 演示设备与故障**：离心泵 P-101，测点 DE（驱动端水平振动）；fr=49.67Hz（2980rpm）；轴承参数 n=8、d/D=0.32、φ=0°；演示故障=外圈剥落（BPFO≈135.1Hz）；阈值 RMS 注意 4.5 / 严重 7.1 mm/s，峭度注意 4.0 / 严重 6.0，连续 3 点触发（对应原系统 accruingNum）。
- **D7 命名**：仓库目录 `D:\TSI\agentic-phm-contest`，远端 `Agentic-PHM/agentic-phm-contest`（私有，用户已在 GitHub 创建）。
- **D8 前端（2026-09-30 用户要求）**：**重写实现、但保留 tsi_dist 的大部分界面观感**——暗色大屏风格（phmV2 深蓝配色）、左侧导航沿用原页面命名（设备概览/实时监视/趋势分析/专家诊断/预警管理/诊断报告），其中"专家诊断"页升级为 Agent 诊断核心页（诊断结论+证据链+执行轨迹）。技术栈 React + Ant Design（视觉语言与原界面同源，最接近原观感）。npm 安装前端依赖属允许范围（与 AGH 同属开发依赖；"零下载"约束仅指运行时中间件）。
- **D9 后端部署位置（2026-09-30 决定）**：**默认本机**（server 监听 127.0.0.1:8181；局域网协作时可改 0.0.0.0 供同网段另一台跑 AGH 的机器访问）。多电脑协作的载体是 git 仓库 + 本计划文档，不依赖共享在线服务；比赛演示执行机=演示机，不依赖现场网络。公网服务器部署为**可选项**：仅当需要异地共享同一实例时启用，启用前置条件=接口加访问令牌、密钥不经公网仓库分发、走 HTTPS。
- **D10 智能体存在形态（2026-09-30 决定）**：**A 事件驱动为主 + C 的轻量人格感；B 常驻问答列为可选加分**。具体：平时不占界面；报警触发后专家诊断页实时呈现"正在诊断"的步骤流；Agent 以**头像 + 第一人称步骤播报**出现（"我注意到 RMS 连续 3 点越限，正在调取频谱…"），**播报文案由前端从证据步骤数据渲染，模型输出保持客观事实**（人格在前端，事实在模型，二者不混）。不做完整 IP 形象体系（无评分权重、工期不允许）。

## File Structure

```
agentic-phm-contest/
├─ docs/superpowers/plans/2026-09-30-agentic-phm-contest.md   # 本计划
├─ README.md                  # 赛前能力 vs 赛期新增声明、来源标注、开源前删 Key 提示
├─ .env                       # AGNES_API_KEY / AGNES_BASE_URL（私有仓库暂存）
├─ .gitignore                 # __pycache__/ data/*.db / agh-home/ / node_modules/
├─ phmlite/
│  ├─ __init__.py
│  ├─ dsp.py                  # 特征计算+频谱+包络+特征频率+波形合成
│  ├─ scenario.py             # scenario.json 读写/白名单 set
│  ├─ db.py                   # SQLite DDL + 查询/插入 + 报警判定
│  ├─ seed.py                 # 24h 历史种子（seeded random，可复现）
│  └─ server.py               # FastAPI 全 GET 路由 + run_log 留痕 + 静态托管 dashboard/dist
├─ dashboard/                 # 前端（React+AntD 重写，复刻 tsi_dist 主界面观感，D8）
│  ├─ package.json / vite.config.js（dev 代理 /api → 127.0.0.1:8181，build 输出 dist/）
│  └─ src/
│     ├─ App.jsx（暗色主题 + 侧边导航：设备概览/实时监视/趋势分析/专家诊断/预警管理/诊断报告）
│     └─ pages/（每个导航页一个组件；专家诊断页=Agent 结论+证据链+轨迹，比赛核心页）
├─ data/
│  └─ scenario.json           # 手动仿真开关
├─ agent/skills/diagnosis.md  # 诊断策略（接入 AGH 的 skill，内容在本计划 Task 8 给全）
├─ scripts/
│  ├─ run_server.py           # 启动入口 uvicorn
│  └─ export_evidence.py      # AGH 轨迹 + run_log + report 打包
├─ tests/
│  ├─ conftest.py             # uvicorn 子线程 fixture
│  ├─ test_dsp.py / test_scenario.py / test_db.py / test_api.py
├─ evidence/                  # run_*.json 执行证据（持续追加）
└─ vendor/                    # agh-portable-20260930.zip（AGH 便携运行时，44MB，清单一见 vendor/README.md）
```

---

### Task 1: 仓库初始化与 Agnes Key 落位（约 15 分钟）

**Files:** Create: `.gitignore`, `.env`, `README.md`, `phmlite/__init__.py`

- [x] **Step 1: 建目录与 git init**

```bash
mkdir -p /d/TSI/agentic-phm-contest/{phmlite/static,data,agent/skills,scripts,tests,evidence}
cd /d/TSI/agentic-phm-contest && git init -b main
```

- [x] **Step 2: 写 .gitignore**

```gitignore
__pycache__/
*.pyc
data/*.db
agh-home/
agnes-harness/
node_modules/
```

注意：`.env` **不**进 .gitignore（用户决定：私有仓库暂存 Key，开源前删除——README 与本计划双重标注）。

- [x] **Step 3: 写 .env**

```ini
AGNES_API_KEY=<redacted>
AGNES_BASE_URL=https://api.agnes-ai.cn/v1
```

- [x] **Step 4: 写 README.md**（已按用户 2026-09-30 决定重写：不声明任何来源，方法以自主设计呈现；保留合成数据说明与公开前删 Key/评估 docs/ 的警告。）

- [x] **Step 5: 验证环境并首次提交**（2026-09-30 完成：远端由用户预先创建为 `Agentic-PHM/agentic-phm-contest`，本地 `git remote add` + `git push -u origin main`，提交 634bffe。环境验证已在计划制定前完成。）

```bash
python -c "import numpy, scipy, fastapi, uvicorn, pytest; print('env ok')"
git add -A && git commit -m "chore: 初始化比赛仓库与 Agnes Key（私有暂存）"
git push -u origin main   # 远端 Agentic-PHM/agentic-phm-contest 已存在
```

---

### Task 2: dsp.py 信号处理核心，TDD（约 2 小时）

**Files:** Create: `phmlite/dsp.py`, `tests/test_dsp.py`。注意：代码与注释以团队自主设计表述，**不引用任何外部或内部项目来源**。

- [ ] **Step 1: 写失败测试 `tests/test_dsp.py`**

```python
import math
import numpy as np
from phmlite.dsp import (rms, peak_to_peak, kurtosis, skewness,
                         fft_spectrum, envelope_spectrum,
                         characteristic_frequencies, synthesize)

FS, N = 16000, 16384

def sine(f, a=1.0, seconds=1.024):
    t = np.arange(int(FS * seconds)) / FS
    return a * np.sin(2 * np.pi * f * t)

def test_rms_of_sine_is_amplitude_over_root2():
    assert abs(rms(sine(50, a=2.0)) - 2.0 / math.sqrt(2)) < 1e-6

def test_peak_to_peak():
    assert abs(peak_to_peak(sine(50, a=2.0)) - 4.0) < 1e-6

def test_kurtosis_of_sine_is_3():
    assert abs(kurtosis(sine(50)) - 3.0) < 0.05

def test_skewness_of_sine_is_0():
    assert abs(skewness(sine(50))) < 1e-6

def test_fft_spectrum_finds_peak_at_50hz():
    freqs, amps = fft_spectrum(sine(50, a=1.0), FS)
    peak_hz = freqs[int(np.argmax(amps))]
    assert abs(peak_hz - 50.0) < FS / N  # 分辨率内

def test_envelope_spectrum_of_bpfo_impulses_peaks_at_bpfo():
    cf = characteristic_frequencies(rpm=2980, n_balls=8, d_over_D=0.32, contact_angle_deg=0)
    x = synthesize(FS, N, rpm=2980, fault_type="bpfo", severity=1.0, noise=0.02)
    freqs, amps = envelope_spectrum(x, FS)
    mask = (freqs > cf["BPFO"] - 3) & (freqs < cf["BPFO"] + 3)
    assert mask.any() and amps[mask].max() > np.percentile(amps, 99)

def test_synthesize_bpfo_raises_kurtosis_and_rms():
    normal = synthesize(FS, N, rpm=2980, fault_type="none", severity=0, noise=0.05)
    faulty = synthesize(FS, N, rpm=2980, fault_type="bpfo", severity=0.8, noise=0.05)
    assert kurtosis(faulty) > kurtosis(normal) and kurtosis(faulty) > 5.0
    assert rms(faulty) > 2 * rms(normal)

def test_characteristic_frequencies_values():
    cf = characteristic_frequencies(2980, 8, 0.32, 0)
    fr = 2980 / 60
    assert abs(cf["fr"] - fr) < 1e-9
    assert abs(cf["BPFO"] - fr * 4 * (1 - 0.32)) < 0.01   # ≈135.09
    assert abs(cf["BPFI"] - fr * 4 * (1 + 0.32)) < 0.01   # ≈262.24
    assert abs(cf["FTF"] - fr / 2 * (1 - 0.32)) < 0.01    # ≈16.89
    assert abs(cf["BSF"] - fr / (2 * 0.32) * (1 - 0.32**2)) < 0.01  # ≈69.66
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd /d/TSI/agentic-phm-contest && python -m pytest tests/test_dsp.py -v`
Expected: FAIL，`ModuleNotFoundError: ... 'phmlite.dsp'`

- [ ] **Step 3: 实现 `phmlite/dsp.py`**

```python
"""信号处理核心：特征计算、频谱、包络解调、轴承特征频率、故障波形合成。"""
import numpy as np
from scipy import signal

def rms(x):
    x = np.asarray(x, dtype=float)
    return float(np.sqrt(np.mean(x ** 2)))

def peak_to_peak(x):
    x = np.asarray(x, dtype=float)
    return float(x.max() - x.min())

def kurtosis(x):
    x = np.asarray(x, dtype=float)
    mu, sigma = x.mean(), x.std()
    return float(np.mean((x - mu) ** 4) / sigma ** 4)   # 与 feature_calculate.calc_eig_value 同口径，正弦≈3

def skewness(x):
    x = np.asarray(x, dtype=float)
    mu, sigma = x.mean(), x.std()
    return float(np.mean((x - mu) ** 3) / sigma ** 3)

def fft_spectrum(x, fs):
    x = np.asarray(x, dtype=float)
    spec = np.abs(np.fft.rfft(x - x.mean())) / len(x) * 2
    freqs = np.fft.rfftfreq(len(x), d=1.0 / fs)
    return freqs, spec

def envelope_spectrum(x, fs):
    """Hilbert 包络解调后取幅值谱（对应 custom_operator.acc_envelop_singal）。"""
    x = np.asarray(x, dtype=float)
    env = np.abs(signal.hilbert(x - x.mean()))
    env = signal.detrend(env)          # 去直流量，突出调制成分
    return fft_spectrum(env, fs)

def characteristic_frequencies(rpm, n_balls, d_over_D, contact_angle_deg):
    fr = rpm / 60.0
    phi = math.radians(contact_angle_deg)
    k = d_over_D * math.cos(phi)
    return {
        "fr": fr,
        "BPFO": fr * n_balls / 2 * (1 - k),
        "BPFI": fr * n_balls / 2 * (1 + k),
        "BSF": fr / (2 * d_over_D) * (1 - k ** 2),
        "FTF": fr / 2 * (1 - k),
    }

def synthesize(fs, n, rpm, fault_type, severity, noise, carrier_hz=3200, decay=0.008, seed=42):
    """本底转频正弦 +（故障时）周期冲击串 + 白噪声。冲击间隔=1/特征频率，冲击=指数衰减振荡。"""
    rng = np.random.default_rng(seed)
    t = np.arange(n) / fs
    fr = rpm / 60.0
    x = 0.6 * np.sin(2 * np.pi * fr * t) + 0.25 * np.sin(2 * np.pi * 2 * fr * t)
    cf = characteristic_frequencies(rpm, 8, 0.32, 0)
    rate = {"bpfo": cf["BPFO"], "bpfi": cf["BPFI"], "none": None}[fault_type]
    if rate is not None:
        period = 1.0 / rate
        next_t = 0.1
        while next_t < t[-1]:
            idx = int(next_t * fs)
            seg = t[: n - idx]
            x[idx:] += severity * 4.0 * np.exp(-seg / decay) * np.sin(2 * np.pi * carrier_hz * seg)
            next_t += period
    x += rng.normal(0, noise, n)
    return x * (2.0 / 6.7 if fault_type != "none" else 1.0)   # 故障时 RMS 标定到演示量级（见 Task 6 校准步）
```

（实现时若 import math 缺失自行补 `import math`。标定系数在 Step 4 校准。）

- [ ] **Step 4: 跑测试并校准标定系数**

Run: `python -m pytest tests/test_dsp.py -v`
Expected: 全 PASS。若 `test_synthesize_bpfo_raises_kurtosis_and_rms` 不满足，只允许调 `synthesize` 里的冲击幅度/标定系数，**不许改测试断言**（6.7mm/s、峭度>5 是演示脚本的规定值）。

- [ ] **Step 5: 提交**

```bash
git add phmlite/dsp.py tests/test_dsp.py && git commit -m "feat(dsp): 实现特征/包络/特征频率算法与故障波形合成"
```

---

### Task 3: scenario.py 手动仿真开关，TDD（约 40 分钟）

**Files:** Create: `phmlite/scenario.py`, `data/scenario.json`, `tests/test_scenario.py`

- [ ] **Step 1: 写失败测试 `tests/test_scenario.py`**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**：`python -m pytest tests/test_scenario.py -v` → FAIL（模块不存在）

- [ ] **Step 3: 实现 `phmlite/scenario.py`**

要点（接口锁定）：`Scenario(path)`；`ensure()` 不存在则写默认值（默认值 = Task 1 场景：rpm 2980 / fault none 0 / noise 0.05 / override 全 None）；`get() -> dict`；`set(dotted_key, value)`，白名单键仅限 `rpm`、`noise`、`fault.type`(none|bpfo|bpfi)、`fault.severity`(0~1)、`override.rms`、`override.kurtosis`，非法键抛 `KeyError`；每次 set 后原子落盘（写临时文件再 replace）。

- [ ] **Step 4: 跑测试通过并提交**

```bash
python -m pytest tests/test_scenario.py -v && python -c "from phmlite.scenario import Scenario; Scenario('data/scenario.json').ensure()"
git add -A && git commit -m "feat(scenario): 手动仿真开关（白名单键+原子落盘）"
```

---

### Task 4: db.py + seed.py 历史与报警，TDD（约 1.5 小时）

**Files:** Create: `phmlite/db.py`, `phmlite/seed.py`, `tests/test_db.py`

- [ ] **Step 1: 写失败测试 `tests/test_db.py`**

```python
from phmlite.db import DB

def test_schema_and_roundtrip(tmp_path):
    db = DB(tmp_path / "t.db"); db.migrate()
    db.insert_eigen("DE", "2026-09-30T10:00:00", rms=2.1, kurtosis=3.1, pp=5.0, level="normal")
    rows = db.trend("DE", "rms", hours=24)
    assert rows and rows[-1]["v"] == 2.1

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
```

- [ ] **Step 2: 确认失败**：`python -m pytest tests/test_db.py -v` → FAIL

- [ ] **Step 3: 实现 `phmlite/db.py`**

DDL（表语义对应 `D:\TSI\tsi\database_init.sql` 的 t_eigen_data/t_threshold_warning_record/t_task，字段精简）：

```sql
CREATE TABLE IF NOT EXISTS eigen_history(point TEXT, ts TEXT, rms REAL, kurtosis REAL, pp REAL, level TEXT);
CREATE TABLE IF NOT EXISTS alarms(id INTEGER PRIMARY KEY AUTOINCREMENT, equip TEXT, point TEXT, ts TEXT,
                                  level TEXT, kpi TEXT, value REAL, msg TEXT);
CREATE TABLE IF NOT EXISTS tasks(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, title TEXT, desc TEXT,
                                 level TEXT, source TEXT, status TEXT DEFAULT 'open');
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT, equip TEXT,
                                   conclusion TEXT, confidence REAL, evidence TEXT);
CREATE TABLE IF NOT EXISTS run_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, run_id TEXT,
                                   tool TEXT, args TEXT, latency_ms REAL);
```

接口锁定：`DB(path)`；`migrate()`；`insert_eigen(point, ts, rms, kurtosis, pp, level)`；`trend(point, kpi, hours) -> list[dict]`（kpi∈rms|kurtosis|pp，dict 键 `ts`/`v`）；`maybe_alarm(equip, point, rms, kurtosis, pp) -> dict|None`——连续 `ACCURING_NUM=3` 次超阈值才插入 alarms 并返回该行（阈值常量 `RMS_NOTICE=4.5, RMS_SEVERE=7.1, KURT_NOTICE=4.0, KURT_SEVERE=6.0`，语义对应 tsi 的 ThresholdModel 四档 + accruingNum）；`alarm_history(equip)`；`create_task/get_task`；`submit_report/get_report`；`log_tool(run_id, tool, args, latency_ms)`。

- [ ] **Step 4: 实现 `phmlite/seed.py`** 并加一条测试

```python
def test_seed_is_deterministic(tmp_path):
    from phmlite.seed import seed_history
    db = DB(tmp_path / "a.db"); db.migrate()
    seed_history(db, points=["DE", "NDE"], hours=24, step_minutes=5, seed=7)
    a = db.trend("DE", "rms", hours=24)
    db2 = DB(tmp_path / "b.db"); db2.migrate()
    seed_history(db2, points=["DE", "NDE"], hours=24, step_minutes=5, seed=7)
    assert [r["v"] for r in a] == [r["v"] for r in db2.trend("DE", "rms", hours=24)]
    assert all(1.5 < r["v"] < 3.5 for r in a)   # 全程正常区
```

`seed_history(db, points, hours, step_minutes, seed=7)`：`np.random.default_rng(seed)` 生成围绕 rms≈2.1（±0.4）、峭度≈3.1、pp≈5.0 的正态扰动，时间戳从当前时刻回推，`level="normal"`。

- [ ] **Step 5: 全部通过后提交**

```bash
python -m pytest tests/test_db.py -v && git add -A && git commit -m "feat(db): SQLite 历史/报警/任务/报告/调用留痕 + 24h 确定性种子"
```

---

### Task 5: server.py 全 GET 工具 API，TDD（约 2.5 小时）

**Files:** Create: `phmlite/server.py`, `scripts/run_server.py`, `tests/conftest.py`, `tests/test_api.py`

- [ ] **Step 1: 写 conftest（子线程起真服务，用 urllib 访问——不依赖 httpx）**

```python
import threading, time, urllib.request, json as _json
import pytest, uvicorn
from phmlite.server import app

@pytest.fixture(scope="session")
def base_url(tmp_path_factory):
    data_dir = tmp_path_factory.mktemp("data")
    app.state.data_dir = data_dir            # server 启动时读它，测试隔离
    config = uvicorn.Config(app, host="127.0.0.1", port=8201, log_level="error")
    server = uvicorn.Server(config)
    th = threading.Thread(target=server.run, daemon=True); th.start()
    for _ in range(50):
        try:
            urllib.request.urlopen("http://127.0.0.1:8201/api/device", timeout=1); break
        except Exception:
            time.sleep(0.2)
    yield "http://127.0.0.1:8201"
    server.should_exit = True

def api(base_url, path):
    with urllib.request.urlopen(base_url + path, timeout=10) as r:
        return _json.loads(r.read())
```

- [ ] **Step 2: 写失败测试 `tests/test_api.py`**（响应键名全系统唯一事实源，后续任务不得偏离）

```python
from conftest import api

def test_device_shape(base_url):
    d = api(base_url, "/api/device")
    assert d["name"] == "P-101"
    pt = d["points"][0]
    assert pt["name"] == "DE" and set(pt["current"]) == {"rms", "kurtosis", "pp"} and "level" in pt

def test_trend_and_waveform(base_url):
    tr = api(base_url, "/api/trend?point=DE&kpi=rms&hours=24")
    assert len(tr["series"]) > 100 and {"ts", "v"} <= set(tr["series"][0])
    w = api(base_url, "/api/waveform?point=DE&n=4096")
    assert w["fs"] == 16000 and len(w["values"]) == 4096

def test_spectrum_and_charfreq(base_url):
    sp = api(base_url, "/api/spectrum?point=DE&type=envelope&top=12")
    assert sp["type"] == "envelope" and sp["peaks"] and {"hz", "amp"} <= set(sp["peaks"][0])
    cf = api(base_url, "/api/charfreq?rpm=2980")
    assert abs(cf["BPFO"] - 135.09) < 0.1

def test_scenario_set_triggers_alarm_then_task_and_report(base_url):
    api(base_url, "/api/scenario/set?key=fault.type&value=bpfo")       # 手动注入（字符串值）
    api(base_url, "/api/scenario/set?key=fault.severity&value=0.8")
    for _ in range(3):                                                  # 采样 3 次（模拟连续触发）
        api(base_url, "/api/device")
    d = api(base_url, "/api/device")
    assert d["points"][0]["level"] in ("notice", "severe")
    assert api(base_url, "/api/alarm/history?equip=P-101")["rows"]
    t = api(base_url, "/api/task/create?title=t&desc=d&level=severe&source=agent")
    r = api(base_url, "/api/report/submit?run_id=r1&equip=P-101&conclusion=c&confidence=0.9&evidence_json=%7B%7D")
    assert t["id"] >= 1 and r["id"] >= 1
    assert api(base_url, "/api/scenario/set?key=fault.type&value=none")["fault"]["type"] == "none"
```

- [ ] **Step 3: 确认失败**：`python -m pytest tests/test_api.py -v` → FAIL

- [ ] **Step 4: 实现 `phmlite/server.py`**

路由与响应（全 GET；每次工具调用写 run_log，作为 evidence 对账的 phm-lite 侧）：

| 路由 | 响应键 |
|---|---|
| `/` | static/index.html（StaticFiles 挂 /static 或直接 FileResponse） |
| `/api/device` | `{name:"P-101", points:[{name:"DE", current:{rms,kurtosis,pp}, level:"normal|notice|severe"}]}`；每次调用 = 一次"采样"：按 scenario 合成波形→dsp 算特征→（override 优先）→insert_eigen→maybe_alarm |
| `/api/trend?point&kpi&hours=24` | `{point, kpi, series:[{ts,v}]}` |
| `/api/waveform?point&n=4096` | `{fs:16000, n, values:[...]}`（按 n 截断/等距降采样） |
| `/api/spectrum?point&type=fft|envelope&top=12` | `{type, fr, peaks:[{hz,amp}]}`（峰值按幅值取 top，映射回各峰频率） |
| `/api/charfreq?rpm` | `{fr,BPFO,BPFI,BSF,FTF}`（轴承参数取自 scenario） |
| `/api/alarm/history?equip` | `{rows:[{ts,level,kpi,value,msg}]}` |
| `/api/task/create?title&desc&level&source` | `{id}` |
| `/api/task/list?status=all` | `{rows:[{id,ts,title,desc,level,source,status}]}` |
| `/api/report/submit?run_id&equip&conclusion&confidence&evidence_json` | `{id}` |
| `/api/report/list?equip` | `{rows:[{id,ts,run_id,equip,conclusion,confidence}]}` |
| `/api/scenario/set?key&value` | 更新后完整 scenario dict（value 自动识别 float/int/str，true/false→bool） |
| `/api/scenario/get` | scenario dict |

实现要点：`app.state.data_dir` 缺省 `pathlib.Path("data")`；DB/scenario 惰性初始化并挂 app.state；首次启动自动 `migrate()+seed_history()+scenario.ensure()`；`override.rms/kurtosis` 非 null 时直接覆盖计算值（这就是"手动降低某些值"的第二入口）；所有查询参数用 pydantic/Query 校验。

`scripts/run_server.py`：`uvicorn.run("phmlite.server:app", host="127.0.0.1", port=8181, factory=False)`。

- [ ] **Step 5: 全绿后手动冒烟并提交**

```bash
python -m pytest tests/ -v
python scripts/run_server.py &  sleep 3 && curl -s "http://127.0.0.1:8181/api/charfreq?rpm=2980"
git add -A && git commit -m "feat(api): phm-lite 全 GET 工具接口（采样/趋势/波形/频谱/报警/任务/报告/场景）"
```

---

### Task 6: 前端重写——复刻 tsi_dist 主界面观感（约 1 天，人工验收）

**Files:** Create: `dashboard/`（`package.json`、`vite.config.js`、`index.html`、`src/main.jsx`、`src/App.jsx`、`src/api.js`、`src/pages/Overview.jsx|Realtime.jsx|Trend.jsx|AgentDiagnosis.jsx|AlarmManage.jsx|Report.jsx`）；Modify: `phmlite/server.py`（挂静态目录）

- [ ] **Step 1: 脚手架**

```bash
cd /d/TSI/agentic-phm-contest/dashboard
npm init -y && npm i react react-dom antd && npm i -D vite @vitejs/plugin-react
```

`vite.config.js` 锁定：`plugins:[react()]`；`server.proxy={'/api':'http://127.0.0.1:8181'}`；`build.outDir='dist'`。

- [ ] **Step 2: 应用壳（App.jsx）**

AntD `ConfigProvider` 设 `theme.darkAlgorithm`，全局变量对齐原系统暗色大屏基调（深蓝底色、青色 `#00c7ff` 系高亮、告警红/注意橙）。侧边导航六项，**沿用原系统页面命名**：设备概览 / 实时监视 / 趋势分析 / 专家诊断 / 预警管理 / 诊断报告。`src/api.js`：`fetchJson(path)` 唯一请求封装（全 GET），设备/趋势轮询统一 5s。

- [ ] **Step 3: 六个页面（每页职责与数据源锁定）**

| 页面 | 内容 | 数据源 |
|---|---|---|
| 设备概览 Overview | 设备卡（名称/测点/总体等级徽章） | `/api/device` |
| 实时监视 Realtime | 测点特征值表 + 报警横幅（notice 橙/severe 红）+ **场景注入面板**（fault.type 下拉、severity 滑条、注入/恢复按钮） | `/api/device` + `/api/scenario/set` |
| 趋势分析 Trend | rms/kurtosis/pp 多曲线，阈值线 4.5/7.1 标注 | `/api/trend` |
| 专家诊断 AgentDiagnosis | **比赛核心页**：顶部 **Agent 人格条**（圆形头像 + 第一人称状态播报，如"我注意到 RMS 连续 3 点越限，正在调取频谱…"——文案由前端按证据步骤状态机渲染：空闲/取证中/交叉验证/已出结论；**结论原文引用模型输出，播报不改编事实**）；下方诊断结论卡（conclusion/confidence）、证据链列表（step/tool/key_numbers/supports）、运维任务列表、执行轨迹摘要 | `/api/report/list` + `/api/task/list` |
| 预警管理 AlarmManage | 报警表格（等级 Tag/时间/kpi/值），确认/抑制按钮（本地 UI 状态即可） | `/api/alarm/history` |
| 诊断报告 Report | 报告列表 + 详情抽屉（完整 evidence_json） | `/api/report/list` |

- [ ] **Step 4: 构建与托管**：`npm run build`；`server.py` 增加 `app.mount("/", StaticFiles(directory="dashboard/dist", html=True), name="ui")`（API 路由注册在前）。验证：`python scripts/run_server.py` 后浏览器 `http://127.0.0.1:8181` 直接出六页应用。

- [ ] **Step 5: 验收清单（逐项人工核对并记录到 evidence/manual_check.md）**

1. 六页可导航、暗色风格统一无白块；2. 初始 RMS≈2.1 绿色；3. 实时监视页注入故障（bpfo/0.8）→ 15 秒内横幅变红、趋势页曲线过阈值线；4. 专家诊断页在 Task 9 联调后能显示 Agent 报告与证据链；5. 浏览器控制台无报错。

- [ ] **Step 6: 提交**：`git add -A && git commit -m "feat(dashboard): 重写主界面（暗色六页+场景注入+Agent 诊断页）"`

---

### Task 7: AGH（agh-portable）接入与 Agnes 冒烟（约 1.5 小时）

**Files:** Create: `scripts/setup_agh.md`（本机操作记录）；不 clone 官方仓库——运行时用仓库自带的 `vendor/agh-portable-20260930.zip`（见 D4 与 `vendor/README.md`）

- [ ] **Step 1: 解压并启动便携包**

```powershell
# 解压到任意路径（示例 D:\agh-portable；换电脑时 clone 本仓库后解压 vendor/ 下 zip 即可）
Expand-Archive -Path D:\TSI\agentic-phm-contest\vendor\agh-portable-20260930.zip -DestinationPath D:\
cd D:\agh-portable
powershell -ExecutionPolicy Bypass -File .\setup-agh.ps1
```

浏览器开 `http://127.0.0.1:4177/` 确认工作台出现。**注意**：启动失败且日志含 `E_SYSTEM_NATIVE_UNAVAILABLE` = 用了系统 node（libuv 1.52 不兼容），必须让脚本用包内 `node24\node.exe`（v24.10.0）。

- [ ] **Step 2: 注入比赛 Agnes Key 并设置隐私口径**

替换 `agh-home/secrets/agnes-ai` 中打包自带的旧凭据为 `.env` 里的比赛 Key（按 CLI 的 secret/auth 流程写入；不要把 Key 提交到本仓库——它已在 `.env` 管理）。然后：

```bash
# consent 设为 LOCAL（轨迹只落本地）；CLI 入口为 node24\node.exe runtime\agnes.mjs
node24\node.exe runtime\agnes.mjs consent LOCAL
node24\node.exe runtime\agnes.mjs consent        # 验证输出 LOCAL
env | grep AGNES_TRACE                            # 验证为空（不设上传端点）
```

（若 CLI 子命令名有出入，以 `node24\node.exe runtime\agnes.mjs --help` 实际输出为准，目标状态不变：consent=LOCAL、无 TRACE 端点。）

- [ ] **Step 3: 确认优化补丁生效**

```bash
node24\node.exe runtime\agnes.mjs package list    # 应含 @agh-ext/hook-max-tokens 且 enabled
```

- [ ] **Step 4: Agnes 模型冒烟**（Web 工作台或 CLI 会话）：提示词"用 web_fetch 访问 http://127.0.0.1:8181/api/device 并复述 JSON"。
**验证（三点）**：① 回复含 `"P-101"`；② **回复非空**（空回复=4096 截断症状复发，回查 Step 3 的扩展是否加载）；③ `D:\agh-portable\agh-home\data\`（audit/profiles）出现本会话记录——这就是 Task 9 evidence 的素材源。若模型直连外部接口报错，改用 shell 工具 `curl -s http://127.0.0.1:8181/api/device`。

- [ ] **Step 5: 提交操作记录**：`git add scripts/setup_agh.md && git commit -m "chore(agh): agh-portable 接入记录（LOCAL consent + Key 注入 + 补丁验证）"`

---

### Task 8: diagnosis.md 诊断策略接入 AGH（约 1.5 小时）

**Files:** Create: `agent/skills/diagnosis.md`

- [ ] **Step 1: 写入以下策略内容**（注册格式按 pinned AGH 的 skills 文档落位到 AGH 可加载位置；内容不变，只适配文件头格式）：

````markdown
# 旋转机械自主诊断策略（P-101）

## 工具（全部 GET，用 web_fetch 或 curl 调用，base=http://127.0.0.1:8181）
/api/device  /api/trend?point&kpi&hours  /api/waveform?point&n  /api/spectrum?point&type&top
/api/charfreq?rpm  /api/alarm/history?equip  /api/task/create?title&desc&level&source
/api/report/submit?run_id&equip&conclusion&confidence&evidence_json

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
   /api/charfreq 换算理论频率与谱峰比对（±3Hz 容差）→ /api/alarm/history 佐证。
3. 交叉验证：时域（峭度>4）与频域（特征峰）与趋势（连续上升）三者至少两项一致才可下结论。
4. 结论：conclusion + confidence + 逐条证据（引用接口返回数值）。
5. 行动：/api/task/create（level=severe 时标题含"停机检查"）。
6. 验证：复查 /api/device 与报告落库结果一致，向用户汇报差异。

## 非完美路径（必须真实执行，不许跳过）
- 证据不足两项一致 → 不下结论，明确列出"还缺什么证据"并继续取证；
- 某工具超时/报错 → 换等价工具（waveform+本地计算替代 spectrum）重试一次并记录；
- 全部假设被排除 → 结论"误报"，给出排除依据。
## 输出
最终必调 /api/report/submit，evidence_json 为数组：[{step, tool, key_numbers, supports}]。
````

- [ ] **Step 2: 验证 AGH 能加载该 skill**（会话中让模型列出可用技能/复述假设表任一行）并提交：`git add agent && git commit -m "feat(agent): 假设树诊断策略 + 全 GET 工具清单"`

---

### Task 9: 端到端闭环 + 证据导出（约 2 小时）

**Files:** Create: `scripts/export_evidence.py`；输出：`evidence/run_<ts>/`

- [ ] **Step 1: 彩排脚本（写进 README 的 Demo 分镜）**

```
① curl /api/scenario/set?key=fault.type&value=none   → 页面绿色 NORMAL
② 注入：key=fault.type=bpfo, severity=0.8 → 3 次采样后横幅报警（阈值+连续3点语义）
③ AGH 发起：会话输入"P-101 出现振动报警，请按 diagnosis 策略自主诊断，run_id=demo-001"
④ 大屏围观 Agent（专家诊断页）：人格条头像亮起 + 第一人称播报逐步推进（"我注意到 RMS 连续 3 点越限…"→"正在调取包络谱…"→"135.2Hz 与理论外圈频率吻合"）→ 趋势→FFT→包络→特征频率比对→交叉验证→结论（BPFO 外圈，confidence）
⑤ 自动 /api/task/create + /api/report/submit；页面任务/报告区出现记录
⑥ 出示 AGH 轨迹原文："去掉 Agnes，系统只能报警，不能诊断"
```

- [ ] **Step 2: 实现 `scripts/export_evidence.py`**：输入 run_id，收集 ①AGH 会话记录（`D:\agh-portable\agh-home\data\` 下 audit/profiles，按 session 时间匹配）②phm-lite `run_log` 中该 run_id 的调用行 ③reports 行 ④报警行，合并写 `evidence/run_<ts>/bundle.json` + 拷贝会话记录原件。验证：彩排后运行，bundle 包含 ≥6 条 tool 记录、1 条 report、≥1 条 alarm、AGH 记录含模型判断文本。

- [ ] **Step 3: 对照 3.1/3.2 打勾**（写进 evidence/checklist.md）：任务规划✓ 能力调用✓ 反馈处理✓ 结果验证✓；Agnes 模型名/版本/使用环节/调用方式 四字段已填。

- [ ] **Step 4: 提交**：`git add -A && git commit -m "feat(evidence): 端到端闭环彩排与证据打包"`

---

### Task 10: 提交材料冻结（约 2 小时，截止 10/15 12:00 前完成）

- [ ] 按官方命名打包：`组别-参赛编号-队伍名称-队长姓名.zip`（内含 AGH 执行记录、演示视频、验证材料、本仓库 README）
- [ ] 录屏 5 分钟按 Task 9 分镜，一镜到底不剪辑（可信度）
- [ ] **最后检查：若任何公开动作 → 先删 `.env` 中 Key 并轮换 Agnes 后台密钥**

### 可选 Task 12（加分，时间富余才做）：常驻问答面板

专家诊断页侧栏加提问框，输入经 AGH 会话转发（Agent 复用既有工具回答，如"P-101 现在健康吗？""最近 24h 有几次预警？"），回答追加显示在人格条下方。砍掉不影响主线——主线是 Task 9 的事件驱动闭环。

### 可选 Task 11（加分，时间富余才做）：AlgorithmWeb 对照验证

若日后允许装 py3.8 依赖：用 `synthesize` 同一波形分别喂 `AlgorithmWeb/feature_calculate.py` 与 `phmlite/dsp.py`，输出对照表进 `evidence/parity.md`，证明移植公式等价（这是"用了 AlgorithmWeb 仓库"的最硬证据）。

---

## Self-Review 记录

- **Spec 覆盖**：报警（T4/T5）→取证（T5 API×T8 流程）→诊断（T8）→行动（T5 task/report）→验证（T8 第6步/T9）；≥3 连续步骤与四阶段显式覆盖（T8 流程 + T9 checklist）；仅 Agnes 模型（T7 配置+T10 声明）；证据可核查（T5 run_log + T9 bundle + AGH 轨迹）；手动仿真（D2/T3/T5 override 双入口）；零下载（环境事实表，npm 仅 AGH 本体）。
- **占位符扫描**：无 TBD/TODO；唯一外部不确定点（AGH skills 注册文件头格式、CLI bin 名）已给主路径+源码对齐 fallback（T7/T8）。
- **类型一致性**：`trend→series[{ts,v}]`、`spectrum→peaks[{hz,amp}]`、`maybe_alarm→dict|None`、`Scenario.set(dotted,value)` 贯穿 T2~T5/T8 已对齐；响应键唯一事实源 = tests/test_api.py。

