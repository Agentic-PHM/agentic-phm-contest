"""phm-lite 工具 API：全 GET 工具接口 + POST 登录；每次调用写 run_log 留痕。"""
import asyncio
import logging
import os
import secrets
import threading
import time
from contextlib import asynccontextmanager
from contextlib import suppress
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from phmlite import autodiag
from phmlite.agent_settings import (AgentSettingsError, get_agnes_key_status,
                                    require_local_same_origin, save_agnes_api_key)
from phmlite.agh_runtime import AGHError, probe_provider
from phmlite.operation_mode import (OperationModeError, agh_runtime_available,
                                    get_mode, get_status as get_operation_status,
                                    invalidate_provider_probe, record_provider_probe, set_mode)
from phmlite.db import (COOL_AIR_NOTICE, COOL_AIR_SEVERE, DB, OIL_TEMP_NOTICE,
                        OIL_TEMP_SEVERE, PEDESTAL_NOTICE, PEDESTAL_SEVERE, RMS_NOTICE,
                        RMS_SEVERE, RPM_NOTICE, RPM_SEVERE, SHAFT_NOTICE, SHAFT_SEVERE,
                        SHAFT_TEMP_NOTICE, SHAFT_TEMP_SEVERE, TEMP_NOTICE, TEMP_SEVERE,
                        band_level)
from phmlite.dsp import (COOL_AIR_BASE, OIL_TEMP_BASE, PART_TEMP_DE_BASE, PART_TEMP_MODEL,
                         PART_TEMP_NDE_BASE, characteristic_frequencies, channel_sample,
                         envelope_spectrum, fault_severity, fft_spectrum, kurtosis,
                         linear_forecast, noise_value, part_temp_next, peak_to_peak, rms,
                         synthesize, temperature_next)
from phmlite import scenario as scenario_module
from phmlite.scenario import Scenario
from phmlite.sampler import PeriodicSampler
from phmlite.seed import seed_history

EQUIP = "P-101"
POINTS = ("DE", "NDE")
FS = 16000
N = 16384
BEARING_N_BALLS = 8
BEARING_D_OVER_D = 0.32
BEARING_CONTACT_ANGLE_DEG = 0
POINT_SEED = {"DE": 42, "NDE": 43}
ADMIN_USER = "admin"          # 演示登录账号（拍板：admin/123456）
ADMIN_PASSWORD = "123456"
CHANNEL_SEED = 2026           # 通道噪声种子基值，随 tick 递推：轮询间数字持续演化
NOMINAL_RPM = 2980            # 额定转速（健康评分基线）
DE_BASE_RMS = 2.1             # DE/NDE 正常 RMS 基线（与 seed_history 标定一致）
TEMP_BASE_FALLBACK = 45.0     # 旧场景文件缺省温度参数（与 scenario.DEFAULTS 一致）
TEMP_GAIN_FALLBACK = 45.0
_CHAN_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)
#: 视为"本机"的来源地址（P1 问题6：本机 Demo 边界）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})

# 调相机通道目录：基线/噪声为正常运行量级，阈值取行业惯例（对齐旧系统 t_monitor_point 语义）；
# DE/NDE 振动组在采样时追加。base=健康评分基线，sigma=通道噪声标准差
CHANNEL_SPECS = [
    {"name": "轴振1X", "group": "轴振", "kind": "shaft", "base": 55.0, "sigma": 1.2,
     "unit": "μm", "notice": SHAFT_NOTICE, "severe": SHAFT_SEVERE},
    {"name": "轴振1Y", "group": "轴振", "kind": "shaft", "base": 52.0, "sigma": 1.2,
     "unit": "μm", "notice": SHAFT_NOTICE, "severe": SHAFT_SEVERE},
    {"name": "轴振2X", "group": "轴振", "kind": "shaft", "base": 57.0, "sigma": 1.2,
     "unit": "μm", "notice": SHAFT_NOTICE, "severe": SHAFT_SEVERE},
    {"name": "轴振2Y", "group": "轴振", "kind": "shaft", "base": 50.0, "sigma": 1.2,
     "unit": "μm", "notice": SHAFT_NOTICE, "severe": SHAFT_SEVERE},
    {"name": "瓦振1X", "group": "瓦振", "kind": "pedestal", "base": 2.0, "sigma": 0.06,
     "unit": "mm/s", "notice": PEDESTAL_NOTICE, "severe": PEDESTAL_SEVERE},
    {"name": "瓦振1Y", "group": "瓦振", "kind": "pedestal", "base": 1.9, "sigma": 0.06,
     "unit": "mm/s", "notice": PEDESTAL_NOTICE, "severe": PEDESTAL_SEVERE},
    {"name": "瓦振2X", "group": "瓦振", "kind": "pedestal", "base": 2.1, "sigma": 0.06,
     "unit": "mm/s", "notice": PEDESTAL_NOTICE, "severe": PEDESTAL_SEVERE},
    {"name": "瓦振2Y", "group": "瓦振", "kind": "pedestal", "base": 2.0, "sigma": 0.06,
     "unit": "mm/s", "notice": PEDESTAL_NOTICE, "severe": PEDESTAL_SEVERE},
    {"name": "电机转速", "group": "转速", "kind": "rpm", "base": float(NOMINAL_RPM),
     "sigma": 1.5, "unit": "rpm", "notice": RPM_NOTICE, "severe": RPM_SEVERE},
    {"name": "轴承温度", "group": "温度", "kind": "temp", "base": TEMP_BASE_FALLBACK,
     "sigma": 0.15, "unit": "°C", "notice": TEMP_NOTICE, "severe": TEMP_SEVERE},
    # 部件级温度通道（热力图视图）：一阶惯性模型参数（基线/增益/时间常数/漂移）收在
    # dsp.PART_TEMP_MODEL，走 temp_base/temp_gain 同款"进程内一阶趋近"思路但每路独立，
    # scenario 白名单不新增键；阈值为 db.py 常量（对齐 equipment_map，油温为演示配置）
    {"name": "轴温DE", "group": "温度", "kind": "part_temp", "base": PART_TEMP_DE_BASE,
     "sigma": 0.12, "unit": "°C", "notice": SHAFT_TEMP_NOTICE, "severe": SHAFT_TEMP_SEVERE},
    {"name": "轴温NDE", "group": "温度", "kind": "part_temp", "base": PART_TEMP_NDE_BASE,
     "sigma": 0.12, "unit": "°C", "notice": SHAFT_TEMP_NOTICE, "severe": SHAFT_TEMP_SEVERE},
    {"name": "冷风温度", "group": "温度", "kind": "part_temp", "base": COOL_AIR_BASE,
     "sigma": 0.1, "unit": "°C", "notice": COOL_AIR_NOTICE, "severe": COOL_AIR_SEVERE},
    {"name": "油温", "group": "温度", "kind": "part_temp", "base": OIL_TEMP_BASE,
     "sigma": 0.1, "unit": "°C", "notice": OIL_TEMP_NOTICE, "severe": OIL_TEMP_SEVERE},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_state(app)
    app.state.sampler.start()   # P0 问题3：无人访问页面时监测链路也持续运行
    startup_probe = None
    if os.environ.get("PHM_STARTUP_PROVIDER_PROBE", "1") != "0":
        startup_probe = asyncio.create_task(_probe_provider_when_ready())
    try:
        yield
    finally:
        app.state.sampler.stop()
        if startup_probe is not None:
            startup_probe.cancel()
            with suppress(asyncio.CancelledError):
                await startup_probe


async def _probe_provider_when_ready():
    """Wait for the local AGH runtime and configured credential, then probe once."""
    last_key_error = None
    while True:
        if await asyncio.to_thread(agh_runtime_available):
            try:
                key_status = await asyncio.to_thread(get_agnes_key_status)
            except AgentSettingsError as exc:
                category = getattr(exc, "category", "settings_unavailable")
                if category != last_key_error:
                    _LOGGER.warning("启动时读取 Agnes 凭据状态失败，类别=%s", category)
                    last_key_error = category
            except Exception:
                if last_key_error != "settings_unavailable":
                    _LOGGER.warning("启动时读取 Agnes 凭据状态失败。")
                    last_key_error = "settings_unavailable"
            else:
                last_key_error = None
                if key_status.get("configured"):
                    break
        await asyncio.sleep(2)

    try:
        await asyncio.to_thread(probe_provider)
    except (AgentSettingsError, AGHError) as exc:
        category = getattr(exc, "category", "provider_unavailable")
        record_provider_probe(False, category)
        _LOGGER.warning("AGH 启动后 Agnes 模型探测失败，类别=%s", category)
    except Exception:
        record_provider_probe(False, "provider_unavailable")
        _LOGGER.warning("AGH 启动后 Agnes 模型探测失败。")
    else:
        record_provider_probe(True)
        _LOGGER.info("AGH 启动后 Agnes 模型自动探测成功。")


app = FastAPI(title="phm-lite", lifespan=lifespan)


def _init_state(app: FastAPI):
    data_dir = Path(getattr(app.state, "data_dir", None) or "data")
    data_dir.mkdir(parents=True, exist_ok=True)
    app.state.data_dir = data_dir
    db = DB(data_dir / "phmlite.db").migrate()
    scenario = Scenario(data_dir / "scenario.json")
    scenario.ensure()
    if not db.trend("DE", "rms", hours=24):  # 种子只灌一次
        seed_history(db, points=list(POINTS), hours=24, step_minutes=5, seed=7)
    app.state.db = db
    app.state.scenario = scenario
    # 任务②：severe 报警自动唤起 AGH 诊断会话（PHM_AUTODIAG=0 可关闭；测试环境经 conftest 关闭）
    app.state.autodiag = autodiag.AutoDiag(db, data_dir)
    # P0 问题3：独立采样循环（仿真时钟）。采样周期 PHM_SAMPLE_INTERVAL 秒；<=0 关闭循环、
    # 退回请求时按需采样（仅用于特殊排查，正常演示与生产都应由循环驱动）。
    app.state.sampler = PeriodicSampler(_simulation_tick, name="sim")
    app.state.waveform = WaveformSnapshot()   # P0 问题1：波形/频谱与报警同源


def _is_loopback_request(request) -> bool:
    """请求来源是否为本机回环（P1 问题6 的判据）。无 client 信息（内部调用）按回环处理。"""
    client = getattr(request, "client", None)
    host = (getattr(client, "host", None) or "") if client else ""
    return host in LOOPBACK_HOSTS or host == ""


def _allow_request(request) -> bool:
    """是否放行该请求：回环一律放行；非回环需要**独立**的显式开关。

    两个开关刻意分开：`PHM_ALLOW_NON_LOOPBACK` 只管"允许绑定非回环地址"（启动期，
    例如挂本机反向代理时绑 0.0.0.0），`PHM_ALLOW_REMOTE_CLIENTS` 才管"允许远端客户端"。
    合成一个开关的话，为了能绑 0.0.0.0 就得把请求守卫一起关掉，守卫等于没有。"""
    if os.environ.get("PHM_ALLOW_REMOTE_CLIENTS") == "1":
        return True
    return _is_loopback_request(request)


@app.middleware("http")
async def local_only_middleware(request, call_next):
    """本机 Demo 边界（P1 问题6）：默认只服务回环地址。

    本应用是离线演示：控制台账号是固定演示账号（可经 PHM_OPERATOR_* 覆盖）、业务路由没有完整
    会话鉴权与逐路由权限、写操作仍是 GET。因此这里**硬性拒绝非回环来源**，避免"顺手绑到
    0.0.0.0"就把一个可写演示系统暴露到公网。

    确需对外提供评审链接时，必须显式设置 `PHM_ALLOW_REMOTE_CLIENTS=1`，并先补齐 README
    「对外暴露前必须补齐」清单（服务端认证、逐路由权限、CSRF 安全方法、审计留存、TLS）。
    注册顺序：本中间件在 run_log 之前注册，故 run_log 在外层——被拒的请求同样留痕。"""
    if _allow_request(request):
        return await call_next(request)
    return JSONResponse(
        status_code=403,
        content={"detail": "本服务为本机演示实例，仅接受回环地址访问；"
                           "对外暴露需显式设置 PHM_ALLOW_REMOTE_CLIENTS=1 并补齐鉴权。"})


@app.middleware("http")
async def run_log_middleware(request, call_next):
    if request.url.path.startswith("/api/"):
        t0 = time.perf_counter()
        response = await call_next(request)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        try:
            request.app.state.db.log_tool(
                run_id=request.query_params.get("run_id", "adhoc"),
                tool=request.url.path,
                args=request.url.query,
                latency_ms=latency_ms)
        except Exception:
            pass
        return response
    return await call_next(request)


class WaveformSnapshot:
    """最近一次采样循环的**同源波形快照**（审查 P0 问题1）。

    此前 `/api/device` 的指标来自 `seed=POINT_SEED*1000003+seq`（随采样序号变化）的波形，
    而 `/api/waveform` 与 `/api/spectrum` 用固定种子 `POINT_SEED` **重新合成**了另一段波形：
    两者共享故障配置，但不是同一段数据，因此不能声称"频谱在交叉验证报警所依据的那段采样"。
    现在三个接口读同一份快照，并各自返回 sample_seq / sampled_at 与该波形自身的
    rms、峭度摘要，供智能体与评测脚本核对同源性。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._seq, self._at, self._waves = None, None, {}

    def put(self, seq, at, waves):
        with self._lock:
            self._seq, self._at, self._waves = seq, at, dict(waves)

    def get(self, point):
        """返回 (seq, sampled_at, 波形)；采样未启动时第三项为 None。"""
        with self._lock:
            return self._seq, self._at, self._waves.get(point)


def _signal_digest(x):
    """该波形**自身**的摘要：让调用方核对"这段频谱"与"那次报警"是否同一份数据。"""
    return {"signal_rms": round(float(rms(x)), 4),
            "signal_kurtosis": round(float(kurtosis(x)), 4)}


def _sampled_waveform(point):
    """取采样循环缓存的那段波形；没有（采样未启动）时返回 None，由调用方如实标注来源。"""
    seq, at, x = app.state.waveform.get(point)
    return seq, at, x


def _sample(db: DB, scenario: dict, point: str, seq: int):
    """一次采样：按场景合成波形（seed 随**采样序号**递推）→ 特征 → override 覆盖 → 落库 → 报警判定。

    P0 问题3：由独立采样循环调用（见 `_simulation_tick`），不在 HTTP 请求路径上。
    返回 (测点快照, 新建报警行, 波形)——波形交给 WaveformSnapshot，供波形/频谱接口同源读取。"""
    x = synthesize(FS, N, rpm=scenario["rpm"], fault_type=scenario["fault"]["type"],
                   severity=scenario["fault"]["severity"], noise=scenario["noise"],
                   seed=POINT_SEED[point] * 1000003 + seq)
    r, k, pp = rms(x), kurtosis(x), peak_to_peak(x)
    override = scenario.get("override") or {}
    if override.get("rms") is not None:
        r = float(override["rms"])
    if override.get("kurtosis") is not None:
        k = float(override["kurtosis"])
    level, _, _ = db.classify(r, k)
    db.insert_eigen(point, time.strftime("%Y-%m-%dT%H:%M:%S"), r, k, pp, level)
    alarm = db.maybe_alarm(EQUIP, point, r, k, pp, seq=seq)
    # 报告的 rms/峭度可能被 override 覆盖；另附波形自身的摘要，二者不一致时不会被误当成同源
    return ({"name": point,
             "current": {"rms": round(r, 3), "kurtosis": round(k, 3), "pp": round(pp, 3)},
             "level": level,
             "signal": _signal_digest(x)}, alarm, x)


def _channels_snapshot(seq, waves=None):
    """调相机多通道一次采样（由采样循环调用）：fault.type/severity 统一驱动全部振动通道与温度；
    噪声按**采样序号**递推（样本间持续演化）；温度一阶趋近目标值并按真实时间演化。
    只读快照不落库——特征历史与报警仍由设备采样写入，避免双写。
    行内 "_base" 为健康评分基线，对外输出时剥除。

    `waves` 为本 tick 已合成的波形（P0 问题1）：DE/NDE 振动组直接复用，既保证与报警同源，
    也省掉每个 tick 重复合成 2 段 16384 点波形。"""
    with _CHAN_LOCK:
        st = app.state
        sc = st.scenario.get()
        sev = fault_severity(sc)
        # 轴承温度（进程内状态）：target = 基线 + k·severity，一阶趋近（注入后缓升、恢复后缓降）
        tbase = float(sc.get("temp_base", TEMP_BASE_FALLBACK))
        tgain = float(sc.get("temp_gain", TEMP_GAIN_FALLBACK))
        now = time.monotonic()
        prev = getattr(st, "temp_state", None) or {"T": tbase, "ts": now}
        t_val = temperature_next(prev["T"], tbase + tgain * sev, now - prev["ts"])
        st.temp_state = {"T": t_val, "ts": now}
        # 部件级温度（热力图视图）：每路独立一阶惯性状态，时间常数各不相同——
        # 注入 severity 后目标抬升、实测缓升，恢复后缓降；与"轴承温度"同为
        # 进程内状态（/api/channels 只读不落库，服务重启从基线重新演化的既有预期）
        prev_pt = getattr(st, "part_temp_state", None) or \
            {"T": {name: m["base"] for name, m in PART_TEMP_MODEL.items()}, "ts": now}
        dt_pt = now - prev_pt["ts"]
        temps = {name: part_temp_next(prev_pt["T"][name], name, sev, dt_pt, t_now=now)
                 for name in PART_TEMP_MODEL}
        st.part_temp_state = {"T": temps, "ts": now}
        rows = []
        for i, spec in enumerate(CHANNEL_SPECS):
            seed = (CHANNEL_SEED + seq) * 131 + i
            if spec["kind"] == "rpm":
                value = channel_sample("rpm", float(sc["rpm"]), sev, spec["sigma"], seed)
            elif spec["kind"] == "temp":
                value = t_val + noise_value(spec["sigma"], seed)
            elif spec["kind"] == "part_temp":
                value = temps[spec["name"]] + noise_value(spec["sigma"], seed)
            else:
                value = channel_sample(spec["kind"], spec["base"], sev, spec["sigma"], seed)
            rows.append({"_base": tbase if spec["kind"] == "temp" else spec["base"],
                         "name": spec["name"], "group": spec["group"],
                         "value": round(value, 2), "unit": spec["unit"],
                         "level": band_level(value, spec["notice"], spec["severe"]),
                         "notice": spec["notice"], "severe": spec["severe"]})
        for point in POINTS:  # 保留 DE/NDE 振动组（RMS 口径，与设备采样**同一段波形**）
            x = (waves or {}).get(point)
            if x is None:
                x = synthesize(FS, N, rpm=sc["rpm"], fault_type=sc["fault"]["type"],
                               severity=sc["fault"]["severity"], noise=sc["noise"],
                               seed=POINT_SEED[point] * 1000003 + seq)
            r, k = rms(x), kurtosis(x)
            level, _, _ = st.db.classify(r, k)
            rows.append({"_base": DE_BASE_RMS, "name": f"{point}振动", "group": f"{point}振动",
                         "value": round(r, 3), "unit": "mm/s", "level": level,
                         "notice": RMS_NOTICE, "severe": RMS_SEVERE})
        return rows


def _simulation_tick(seq):
    """采样循环的一次 tick（P0 问题3）：设备采样 + 通道/健康快照共用同一个样本序号。

    设备采样落库并做报警判定，再把严重报警喂给自动唤起看门狗（去重/恢复重置在 autodiag
    内部处理，非阻塞）。返回值即只读接口直接对外暴露的快照。"""
    db, sc = app.state.db, app.state.scenario.get()
    points, severe, waves = [], [], {}
    for p in POINTS:
        pt, alarm, x = _sample(db, sc, p, seq)
        points.append(pt)
        waves[p] = x
        if alarm and alarm["level"] == "severe":
            severe.append(alarm)
    sampled_at = datetime.now().isoformat(timespec="seconds")
    # P0 问题1：波形快照供 /api/waveform 与 /api/spectrum 同源读取
    app.state.waveform.put(seq, sampled_at, waves)
    watchdog = getattr(app.state, "autodiag", None)
    if watchdog:
        # 审查 P0 问题3（复测口径）：把**每个样本的实测等级**一并传入。复测要判断的是"当前测量
        # 是否仍然严重"，而 `severe` 只是"本次新建的报警行"——单次严重越限若未达到连续报警条件，
        # 该样本已经严重异常但 severe 为空，只看它会把异常当成正常并提前关单。
        watchdog.observe(sc, severe, measured=points)
    return {
        "device": {"name": EQUIP, "points": points, "sample_seq": seq,
                   "sampled_at": sampled_at},
        "channels": _channels_snapshot(seq, waves),
    }


def _latest_channels():
    """最近一次通道快照（只读，不再推进仿真状态）。"""
    return app.state.sampler.snapshot()["value"]["channels"]


@app.get("/api/device")
def api_device(run_id: str = Query(None)):
    """只读：返回采样循环最近一次设备快照。

    P0 问题3：此前本接口自己合成信号、写特征、判报警并唤起诊断，采样频率因此由浏览器
    请求决定（页面越多采样越快）；现在采样由独立循环按固定周期驱动，这里只读。"""
    return app.state.sampler.snapshot()["value"]["device"]


@app.get("/api/autodiag/status")
def api_autodiag_status(run_id: str = Query(None)):
    """自动唤起诊断状态机：吉祥物按 events 增量播报，页面按 state 展示进度。"""
    watchdog = getattr(app.state, "autodiag", None)
    if not watchdog:
        return {"enabled": False, "state": "idle", "events": []}
    return watchdog.status()


class OperationModeBody(BaseModel):
    mode: str = Field(..., max_length=16)


def _operation_status():
    try:
        try:
            key_configured = bool(get_agnes_key_status().get("configured"))
        except AgentSettingsError:
            key_configured = False
        status = get_operation_status(
            app.state.data_dir,
            agh_available=agh_runtime_available(),
            key_configured=key_configured,
        )
        watchdog = getattr(app.state, "autodiag", None)
        status["automatic_diagnosis_enabled"] = bool(watchdog and watchdog.enabled)
        return status
    except OperationModeError as exc:
        raise HTTPException(status_code=503, detail="运行模式状态无法读取，请检查本机配置。") from None


def _require_active_agh_run(run_id):
    """Only let the currently running AGH diagnosis write agent artifacts."""
    watchdog = getattr(app.state, "autodiag", None)
    status = watchdog.status() if watchdog else {}
    if (not run_id or get_mode(app.state.data_dir) != "agent"
            or status.get("state") != "running" or status.get("mode") != "agh"
            or status.get("run_id") != run_id):
        raise HTTPException(status_code=409,
                            detail="当前没有匹配的 AGH 自动诊断会话，未写入智能体产物。")


@app.get("/api/operation/mode")
def api_operation_mode_status():
    """Fast runtime status plus the operator-selected, persistent mode."""
    return _operation_status()


@app.post("/api/operation/mode")
async def api_operation_mode_set(body: OperationModeBody, request: Request):
    try:
        require_local_same_origin(request)
    except AgentSettingsError as exc:
        raise _settings_error_response(exc) from None
    if body.mode not in ("agent", "manual"):
        raise HTTPException(status_code=400, detail="运行模式只支持 agent 或 manual。")
    if body.mode == "agent":
        if not agh_runtime_available():
            record_provider_probe(False, "runtime_unavailable")
            raise HTTPException(status_code=503, detail="AGH 运行时离线，仍保持当前运行模式。")
        try:
            probe_provider()
        except (AgentSettingsError, AGHError) as exc:
            record_provider_probe(False, getattr(exc, "category", "provider_unavailable"))
            raise _settings_error_response(exc) from None
        except Exception:
            record_provider_probe(False, "provider_unavailable")
            raise HTTPException(status_code=503, detail="Agnes 模型验证失败，仍保持当前运行模式。") from None
        record_provider_probe(True)
    try:
        set_mode(app.state.data_dir, body.mode)
    except OperationModeError:
        raise HTTPException(status_code=503, detail="运行模式无法安全保存。") from None
    return _operation_status()


def _settings_error_response(exc):
    """Map internal failures to fixed public text; never reflect input or CLI output."""
    category = getattr(exc, "category", "")
    if category == "request_forbidden":
        return HTTPException(status_code=403, detail="Key 设置仅允许本机同源请求。")
    if category == "key_invalid":
        return HTTPException(status_code=400, detail="请输入有效的 Agnes API Key。")
    messages = {
        "profile_invalid": "AGH profile 配置无效。",
        "profile_unavailable": "找不到有效的 AGH profile 配置。",
        "credential_invalid": "AGH Agnes 凭据引用无效。",
        "credential_store_missing": "AGH Agnes 凭据目录不存在，请先初始化 AGH profile。",
        "acl_failed": "AGH 凭据文件 ACL 设置失败。",
        "credential_write_failed": "无法安全保存 Agnes API Key。",
        "runtime_missing": "未找到 AGH 运行时，请检查 AGH_DIR。",
        "runtime_unavailable": "AGH 运行时当前不可用。",
        "timeout": "AGH 连接验证超时。",
        "cli_failed": "AGH 调用失败，请检查运行时日志。",
        "provider_unavailable": "Agnes 模型连接验证失败，请检查 Key 和 AGH profile。",
    }
    status_code = 503 if category in {
        "profile_invalid", "profile_unavailable", "credential_invalid", "credential_store_missing",
        "runtime_missing", "runtime_unavailable", "timeout", "provider_unavailable", "cli_failed",
    } else 500
    return HTTPException(status_code=status_code,
                         detail=messages.get(category, "AGH 设置操作失败，请检查本机配置。"))


@app.get("/api/settings/agnes-key")
def api_agnes_key_status():
    try:
        status = get_agnes_key_status()
        return {"configured": bool(status.get("configured")), "profile": str(status.get("profile", ""))}
    except AgentSettingsError as exc:
        raise _settings_error_response(exc) from None
    except Exception:
        raise HTTPException(status_code=500, detail="AGH 设置状态读取失败。") from None


@app.post("/api/settings/agnes-key")
async def api_agnes_key_save(request: Request):
    try:
        require_local_same_origin(request)
    except AgentSettingsError as exc:
        raise _settings_error_response(exc) from None
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Key 请求格式无效。") from None
    api_key = payload.get("api_key") if isinstance(payload, dict) else None
    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 8192:
        raise HTTPException(status_code=400, detail="请输入有效的 Agnes API Key。")
    try:
        save_agnes_api_key(api_key)
        invalidate_provider_probe()
        return {"ok": True}
    except AgentSettingsError as exc:
        raise _settings_error_response(exc) from None
    except Exception:
        raise HTTPException(status_code=500, detail="无法安全保存 Agnes API Key。") from None


@app.post("/api/settings/agnes-key/probe")
async def api_agnes_key_probe(request: Request):
    try:
        require_local_same_origin(request)
        probe_provider()
        record_provider_probe(True)
        return {"ok": True, "message": "Agnes 模型连接验证成功。"}
    except (AgentSettingsError, AGHError) as exc:
        record_provider_probe(False, getattr(exc, "category", "provider_unavailable"))
        raise _settings_error_response(exc) from None
    except Exception:
        record_provider_probe(False, "provider_unavailable")
        raise HTTPException(status_code=503, detail="AGH 连接验证失败。") from None


@app.get("/api/channels")
def api_channels(run_id: str = Query(None)):
    """只读：返回采样循环最近一次通道快照（P0 问题3——此前每次调用都会推进仿真状态）。"""
    return {"channels": [{k: v for k, v in c.items() if not k.startswith("_")}
                         for c in _latest_channels()]}


@app.get("/api/health")
def api_health(run_id: str = Query(None)):
    """健康因子 0-100：各通道按到严重阈值的归一距离打分（基线→100 分、严重阈值→0 分）取均值。

    只读：取采样循环的最近快照（P0 问题3——此前每次调用都会推进仿真状态）。"""
    rows = _latest_channels()
    comps = []
    for c in rows:
        span = c["severe"] - c["_base"]
        raw = 100.0 * (c["severe"] - c["value"]) / span if span > 0 else \
            (0.0 if c["value"] >= c["severe"] else 100.0)
        comps.append({"name": c["name"], "score": round(min(100.0, max(0.0, raw)), 1)})
    return {"score": round(sum(c["score"] for c in comps) / len(comps), 1),
            "components": comps}


@app.get("/api/forecast")
def api_forecast(point: str = Query("DE"), hours: int = Query(2, ge=1, le=24 * 30),
                 run_id: str = Query(None)):
    """对近期 RMS 序列线性拟合外推：估算到达注意/严重阈值的剩余时间（小时区间估算值）。"""
    series = app.state.db.trend(point, "rms", hours)
    out = linear_forecast([s["v"] for s in series], _hours_axis(series),
                          RMS_NOTICE, RMS_SEVERE)
    out["point"] = point
    return out


def _hours_axis(series):
    """把 ISO 时间序列换算成自首点起的小数小时轴。"""
    ts = [datetime.fromisoformat(s["ts"]) for s in series]
    return [(t - ts[0]).total_seconds() / 3600.0 for t in ts]


@app.get("/api/trend")
def api_trend(point: str = Query("DE"), kpi: str = Query("rms"),
              hours: int = Query(24, ge=1, le=24 * 30), run_id: str = Query(None)):
    if "trend" in getattr(app.state, "chaos", set()):
        # 4.2 部分数据缺失演练：趋势工具不可用，考智能体是否用等价证据替代或如实拒绝
        raise HTTPException(status_code=503, detail="chaos: trend 工具不可用（演练注入）")
    series = app.state.db.trend(point, kpi, hours)
    seeded = sum(1 for s in series if s.get("origin") == "seed")
    legacy = sum(1 for s in series if s.get("origin") == "unknown")
    live = len(series) - seeded - legacy
    return {"point": point, "kpi": kpi,
            # 4.1：明确区分模拟历史与真实采样，避免把种子数据当成现场故障演化。
            # legacy(unknown)=建库早于来源标记的历史行：宁可标未判定，也不冒充真实采样
            "seeded_points": seeded, "live_points": live, "unclassified_points": legacy,
            "series": [{"ts": s["ts"], "v": round(s["v"], 4), "origin": s.get("origin") or "unknown"}
                       for s in series]}


@app.get("/api/waveform")
def api_waveform(point: str = Query("DE"), n: int = Query(4096, ge=16, le=N),
                 run_id: str = Query(None)):
    """只读：返回**采样循环正在使用的那一份**波形（审查 P0 问题1）。

    `source=sampled` 表示与 `/api/device` 的 rms/峭度、以及报警判定出自同一段波形，
    `sample_seq` 与 `signal_rms/signal_kurtosis` 可直接用于核对；
    `source=unsampled_adhoc` 表示采样未启动时的现场合成（此时**不保证**与报警同源，如实标注）。"""
    seq, at, x = _sampled_waveform(point)
    source = "sampled"
    if x is None:
        sc = app.state.scenario.get()
        x = synthesize(FS, N, rpm=sc["rpm"], fault_type=sc["fault"]["type"],
                       severity=sc["fault"]["severity"], noise=sc["noise"],
                       seed=POINT_SEED.get(point, 42))
        source = "unsampled_adhoc"
    step = max(1, N // n)
    values = [round(float(v), 4) for v in x[::step][:n]]
    return {"fs": FS, "n": len(values), "values": values, "point": point,
            "sample_seq": seq, "sampled_at": at, "source": source, **_signal_digest(x)}


@app.get("/api/spectrum")
def api_spectrum(point: str = Query("DE"), type: str = Query("envelope", pattern="^(fft|envelope)$"),
                 top: int = Query(12, ge=1, le=50), run_id: str = Query(None)):
    if "spectrum" in getattr(app.state, "chaos", set()):
        # 盲测工具失败用例（操作台经 /api/testtools/chaos 注入）：验证诊断智能体的降级重试
        raise HTTPException(status_code=503, detail="chaos: spectrum 工具不可用（演练注入）")
    sc = app.state.scenario.get()
    seq, at, x = _sampled_waveform(point)
    source = "sampled"
    if x is None:
        x = synthesize(FS, N, rpm=sc["rpm"], fault_type=sc["fault"]["type"],
                       severity=sc["fault"]["severity"], noise=sc["noise"],
                       seed=POINT_SEED.get(point, 42))
        source = "unsampled_adhoc"
    freqs, amps = envelope_spectrum(x, FS) if type == "envelope" else fft_spectrum(x, FS)
    peaks = []
    idx, props = _peaks(amps)
    order = sorted(range(len(idx)), key=lambda i: props["peak_heights"][i], reverse=True)[:top]
    for i in order:
        peaks.append({"hz": round(float(freqs[idx[i]]), 3),
                      "amp": round(float(props["peak_heights"][i]), 6)})
    # 审查 P0 问题1：附上波形来源与摘要——智能体可据此证明频谱与报警判据来自同一段采样
    return {"type": type, "fr": round(sc["rpm"] / 60.0, 3), "peaks": peaks,
            "point": point, "sample_seq": seq, "sampled_at": at, "source": source,
            **_signal_digest(x)}


def _peaks(amps):
    from scipy import signal
    idx, props = signal.find_peaks(amps, height=0)
    return idx, props


@app.get("/api/charfreq")
def api_charfreq(rpm: float = Query(None, gt=0), run_id: str = Query(None)):
    if "charfreq" in getattr(app.state, "chaos", set()):
        # 4.2 部分数据缺失演练：理论特征频率不可用，无法做 ±3Hz 比对
        raise HTTPException(status_code=503, detail="chaos: charfreq 工具不可用（演练注入）")
    rpm = rpm or app.state.scenario.get()["rpm"]
    return characteristic_frequencies(rpm, BEARING_N_BALLS, BEARING_D_OVER_D,
                                      BEARING_CONTACT_ANGLE_DEG)


@app.get("/api/alarm/history")
def api_alarm_history(equip: str = Query(EQUIP), run_id: str = Query(None)):
    """报警历史。P0 问题3：附带样本序号 seq——报警由采样序号推进，可据此核对采样节奏。

    P1 问题6：附带事件分层信息。`kind=new` 是新故障事件的首条报警，`kind=repeat` 是同一
    持续越限的重复提醒；`summary` 给出报警行数与**独立故障事件数**——两者不能混为一谈。"""
    rows = [{"ts": r["ts"], "level": r["level"], "kpi": r["kpi"], "value": r["value"],
             "msg": r["msg"], "seq": r.get("seq"), "incident_id": r.get("incident_id"),
             "kind": r.get("kind") or "unclassified"}
            for r in app.state.db.alarm_history(equip)]
    return {"rows": rows, "summary": app.state.db.alarm_summary(equip)}


@app.get("/api/task/create")
def api_task_create(title: str = Query(...), desc: str = Query(""),
                    level: str = Query("notice"), source: str = Query("agent"),
                    run_id: str = Query(None, max_length=100)):
    try:
        mode = get_mode(app.state.data_dir)
    except OperationModeError:
        raise HTTPException(status_code=503, detail="运行模式状态无法读取，未创建工单。") from None
    if source not in ("agent", "manual"):
        raise HTTPException(status_code=400, detail="工单来源无效。")
    if mode == "manual" and source == "agent":
        raise HTTPException(status_code=409, detail="当前为人工备用模式，不能创建智能体工单。")
    if source == "agent":
        _require_active_agh_run(run_id)
    return {"id": app.state.db.create_task(title, desc, level, source,
                                            run_id=run_id if source == "agent" else None)}


@app.get("/api/task/list")
def api_task_list(status: str = Query("all"), run_id: str = Query(None)):
    return {"rows": app.state.db.task_list(status)}


@app.get("/api/testtools/chaos")
def api_testtools_chaos(target: str = Query("spectrum"), value: str = Query("on",
                        pattern="^(on|off)$"), run_id: str = Query(None),
                        request: Request = None):
    """盲测专用故障注入（控制面）：让指定工具对智能体不可用，考其降级重试能力。

    P1 问题4：门禁从固定字符串 `X-Operator: console` 改为控制台会话凭据。
    4.2：target 从只有 spectrum 扩展到 trend / charfreq，用于覆盖"部分数据缺失"
    （智能体需用等价证据替代或如实拒绝下结论）。"""
    _require_operator(request)
    allowed = ("spectrum", "trend", "charfreq")
    if target not in allowed:
        raise HTTPException(status_code=400, detail=f"支持的 target: {'/'.join(allowed)}")
    chaos = getattr(app.state, "chaos", None)
    if chaos is None:
        chaos = app.state.chaos = set()
    (chaos.add if value == "on" else chaos.discard)(target)
    return {"target": target, "chaos": value, "active": sorted(chaos)}


@app.get("/api/task/close")
def api_task_close(id: int = Query(...), note: str = Query("", max_length=300),
                   run_id: str = Query(None), request: Request = None):
    """关单（P0 问题3 工单生命周期）：处置确认+复测结论写入备注；工单不存在/已关闭返回 404。

    P1 问题5：这是**人工处置确认**关单，依据记为 manual，与自动复测的 sim_recovery 可区分。
    P1 问题6：关单属控制面操作（诊断智能体不调用它），因此要求控制台会话凭据。"""
    _require_operator(request)
    closed = app.state.db.close_task(id, note=note, kind=autodiag.RETEST_KIND_MANUAL)
    if not closed:
        raise HTTPException(status_code=404, detail="工单不存在或已关闭。")
    return {"id": closed["id"], "status": closed["status"],
            "closed_ts": closed["closed_ts"], "close_note": closed["close_note"]}


@app.get("/api/equipment-map")
def api_equipment_map(run_id: str = Query(None)):
    """设备部件语义映射（9 部件：id/中文名/锚点/绑定测点/阈值/机理特征），诊断智能体与 3D 数字孪生共用。"""
    p = _equipment_map_path()
    if not p.exists():
        raise HTTPException(status_code=404, detail="data/equipment_map.json 缺失")
    return FileResponse(p, media_type="application/json")


class AgentChatBody(BaseModel):
    message: str = Field(..., max_length=500)
    history: list[dict] = Field(default_factory=list, max_length=8)
    run_id: str = Field("global-agent", max_length=100)


def _agent_chat_error_response(exc):
    category = getattr(exc, "category", "")
    if category == "invalid_response":
        return HTTPException(status_code=502, detail="AGH 返回格式无效，请重试。")
    messages = {
        "runtime_missing": "未找到 AGH 运行时，请检查 AGH_DIR。",
        "runtime_unavailable": "AGH 运行时当前不可用。",
        "timeout": "AGH 模型调用超时。",
        "cli_failed": "AGH 模型调用失败，请检查 AGH 运行时状态。",
        "provider_unavailable": "Agnes 模型不可用，请检查 Key 和 AGH profile。",
    }
    return HTTPException(status_code=503,
                         detail=messages.get(category, "AGH 智能体暂不可用，请检查运行时和模型配置。"))


@app.post("/api/agent/chat")
def api_agent_chat(body: AgentChatBody):
    """聊天和操作总结均经 AGH；服务端执行的操作仍受现有白名单校验。"""
    from phmlite import agnes_chat

    try:
        if get_mode(app.state.data_dir) == "manual":
            raise HTTPException(status_code=409, detail="当前为人工备用模式，智能体聊天不可用。")
    except OperationModeError:
        raise HTTPException(status_code=503, detail="运行模式状态无法读取，智能体聊天不可用。") from None
    if not agh_runtime_available():
        record_provider_probe(False, "runtime_unavailable")
        raise HTTPException(status_code=503, detail="AGH 运行时离线，智能体聊天不可用。")

    history = [
        {"role": str(item.get("role"))[:16], "content": str(item.get("content"))[:600]}
        for item in body.history
        if isinstance(item, dict) and item.get("content")
    ][:8]
    try:
        out = agnes_chat.ask(body.message, history)
    except AGHError as exc:
        record_provider_probe(False, getattr(exc, "category", "provider_unavailable"))
        raise _agent_chat_error_response(exc) from None
    except Exception:
        raise HTTPException(status_code=503, detail="AGH 智能体暂不可用，请检查运行时和模型配置。") from None
    record_provider_probe(True)
    try:
        if get_mode(app.state.data_dir) == "manual":
            raise HTTPException(status_code=409, detail="系统已切换到人工备用模式，未执行智能体操作。")
    except OperationModeError:
        raise HTTPException(status_code=503, detail="运行模式状态无法读取，未执行智能体操作。") from None
    actions = out.get("actions") or []
    if actions:
        results = []
        for a in actions[:4]:  # 单轮最多执行 4 个操作，防刷
            try:
                results.append(_execute_agent_action(a))
            except Exception as e:  # noqa: BLE001 —— 单个操作失败不拖垮整条回复
                results.append({"type": str(a.get("type")), "ok": False, "error": str(e)[:120]})
        try:
            out["reply"] = agnes_chat.summarize(body.message, out["reply"], results)
        except AGHError as exc:
            record_provider_probe(False, getattr(exc, "category", "provider_unavailable"))
            raise HTTPException(
                status_code=503,
                detail="AGH 未能完成操作结果确认；操作可能已执行，请刷新状态确认。",
            ) from None
        except Exception:
            record_provider_probe(False, "provider_unavailable")
            raise HTTPException(
                status_code=503,
                detail="AGH 未能完成操作结果确认；操作可能已执行，请刷新状态确认。",
            ) from None
        out["actions"] = results
    return {"reply": out["reply"], "actions": out.get("actions", [])}


def _execute_agent_action(a):
    """任务③白名单执行器：只放行 6 种操作，参数收紧；越权/非法参数抛异常。"""
    if not isinstance(a, dict):
        raise ValueError("操作必须是对象")
    t = a.get("type")
    if t == "query_channels":
        rows = _latest_channels()   # P0 问题3：读采样循环快照，不再由对话请求推进仿真
        bad = [c for c in rows if c["level"] != "normal"]
        return {"type": t, "ok": True, "total": len(rows),
                "abnormal": [{"name": c["name"], "value": c["value"], "unit": c["unit"],
                              "level": c["level"]} for c in bad[:6]]}
    if t == "query_trend":
        point = str(a.get("point") or "DE")
        kpi = str(a.get("kpi") or "rms")
        hours = int(a.get("hours") or 2)
        series = app.state.db.trend(point, kpi, max(1, min(hours, 24 * 30)))
        vals = [round(s["v"], 3) for s in series]
        recent = vals[-10:]
        return {"type": t, "ok": True, "point": point, "kpi": kpi, "n": len(vals),
                "first": vals[0] if vals else None, "last": vals[-1] if vals else None,
                "recent": recent}
    if t == "inject_fault":
        fault = str(a.get("fault") or "")
        severity = float(a.get("severity", 0.8))
        if fault not in ("bpfo", "bpfi"):
            raise ValueError("演示故障仅支持 bpfo/bpfi")
        severity = min(max(severity, 0.0), 1.0)
        app.state.scenario.set("fault.type", fault)
        app.state.scenario.set("fault.severity", severity)
        return {"type": t, "ok": True, "fault": fault, "severity": severity}
    if t == "recover_fault":
        app.state.scenario.set("fault.type", "none")
        app.state.scenario.set("fault.severity", 0.0)
        return {"type": t, "ok": True}
    if t == "latest_report":
        rows = app.state.db.report_list()
        r = rows[0] if rows else None
        return {"type": t, "ok": True, "report": None if not r else {
            "id": r["id"], "ts": r["ts"], "conclusion": r["conclusion"][:120],
            "confidence": r["confidence"], "component": r.get("component")}}
    if t == "switch_view":
        view = str(a.get("view") or "")
        if view not in ("compose", "explode", "thermal", "vibration"):
            raise ValueError("视图仅支持 compose/explode/thermal/vibration")
        return {"type": t, "ok": True, "view": view}  # UI 切换由前端派发
    raise ValueError(f"不支持的操作: {t}")


def _equipment_map_path():
    """equipment_map 供数路径：优先运行数据目录（测试/隔离环境可覆盖），否则仓库随附副本。"""
    candidates = []
    data_dir = getattr(app.state, "data_dir", None)
    if data_dir:
        candidates.append(Path(data_dir) / "equipment_map.json")
    candidates.append(Path(__file__).resolve().parents[1] / "data" / "equipment_map.json")
    for c in candidates:
        if c.exists():
            return c
    return candidates[-1]


@app.get("/api/report/submit")
def api_report_submit(run_id: str = Query(..., max_length=100), equip: str = Query(EQUIP),
                      conclusion: str = Query(...), confidence: float = Query(..., ge=0, le=1),
                      evidence_json: str = Query(""), component_id: str = Query(None)):
    """component_id 可选：诊断结论定位到的部件 id（如 bearing_de，见 /api/equipment-map），定位不到则省略。

    P0 问题2：证据不再是可省略的装饰。evidence_json 必须是非空数组、每条含
    step/tool/key_numbers/supports，否则拒绝落库——此前 evidence_json="{}" 也能记成功，
    于是"查到报告即 done"把成功门槛压到了没有证据。"""
    try:
        if get_mode(app.state.data_dir) == "manual":
            raise HTTPException(status_code=409, detail="当前为人工备用模式，不能提交智能体诊断报告。")
        _require_active_agh_run(run_id)
    except OperationModeError:
        raise HTTPException(status_code=503, detail="运行模式状态无法读取，未提交诊断报告。") from None
    steps, problem = autodiag.parse_evidence_steps(evidence_json)
    if problem:
        raise HTTPException(
            status_code=400,
            detail=f"诊断证据不合规（{problem}）：每条证据需含 step/tool/key_numbers/supports，"
                   f"key_numbers 须为非空数组。")
    return {"id": app.state.db.submit_report(run_id, equip, conclusion, confidence,
                                             evidence_json, component=component_id),
            "evidence_steps": len(steps)}


@app.get("/api/report/list")
def api_report_list(equip: str = Query(None), run_id: str = Query(None)):
    return {"rows": app.state.db.report_list(equip)}


class LoginBody(BaseModel):
    username: str
    password: str


# ---- 控制面凭据（P1 问题4）----
# 此前控制面门禁是固定字符串 `X-Operator: console`：它同样存在于前端构建产物里，任何能发 HTTP
# 的人（包括诊断智能体加一个 `-H`）都能伪造，等于没有边界。现在改为登录签发的随机会话 token，
# 只存在于服务端内存与登录者本地；控制台写接口一律凭它授权，不再是"写成功只脱敏响应"。
_OPERATOR_SESSIONS = set()
_OPERATOR_LOCK = threading.Lock()


def _operator_credentials():
    """控制台账号：可用 PHM_OPERATOR_USER/PASSWORD 覆盖演示默认值。

    盲测/演示环境应设置非公开口令——公开的 admin/123456 只能算演示登录，不构成授权边界。"""
    return (os.environ.get("PHM_OPERATOR_USER", ADMIN_USER),
            os.environ.get("PHM_OPERATOR_PASSWORD", ADMIN_PASSWORD))


def _issue_operator_token():
    token = secrets.token_hex(16)
    with _OPERATOR_LOCK:
        _OPERATOR_SESSIONS.add(token)
    return token


def _operator_token(request):
    """从 X-Operator-Token 或 Authorization: Bearer 取控制台会话凭据。"""
    if request is None:
        return ""
    token = (request.headers.get("x-operator-token") or "").strip()
    if token:
        return token
    auth = (request.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _is_operator(request) -> bool:
    token = _operator_token(request)
    if not token:
        return False
    with _OPERATOR_LOCK:
        return token in _OPERATOR_SESSIONS


def _require_operator(request):
    """控制面写接口/完整场景视图的硬门禁：无凭据一律 403。"""
    if not _is_operator(request):
        raise HTTPException(status_code=403, detail="需要控制台会话凭据：请先登录获取 token。")


@app.post("/api/login")
def api_login(body: LoginBody):
    """控制台登录：校验账号后签发随机会话 token（控制面凭据，见 _require_operator）。"""
    user, password = _operator_credentials()
    if secrets.compare_digest(body.username.encode(), user.encode()) and \
            secrets.compare_digest(body.password.encode(), password.encode()):
        return {"token": _issue_operator_token(), "name": "管理员"}
    raise HTTPException(status_code=401, detail="用户名或密码错误")


def _coerce(key: str, value: str):
    """value 自动识别：int → float → true/false → 字符串。

    可空键（如 override.rms）额外接受 ""/null/none 表示**清除**：HTTP 只能传字符串，
    而旧实现只认数字，于是 override 一旦设上就撤不掉（用例复位/演示恢复都会卡住）。"""
    if scenario_module.allows_none(key) and value.strip().lower() in ("", "null", "none"):
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def _scenario_view(data: dict, request: Request) -> dict:
    """scenario 对外视图：默认脱敏 fault 真值（P0——诊断智能体对本接口与仿真控制台同权访问，
    不得读到注入标签）；仅持有控制台会话凭据者可见完整场景（P1 问题4：凭据不再是可伪造的
    固定字符串）。"""
    if _is_operator(request):
        return data
    return {**data, "fault": {"type": "hidden", "severity": None}, "truth_redacted": True}


@app.get("/api/scenario/set")
def api_scenario_set(key: str = Query(...), value: str = Query(...), run_id: str = Query(None),
                     request: Request = None):
    """写仿真场景（控制面）：必须持控制台会话凭据。

    P1 问题4：此前不带任何凭据也能改真值，只是响应被脱敏——"正常流程不读真值"并不等于
    "诊断执行环境够不到真值"。现在无凭据一律 403。"""
    _require_operator(request)
    try:
        return _scenario_view(app.state.scenario.set(key, _coerce(key, value)), request)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/scenario/get")
def api_scenario_get(run_id: str = Query(None), request: Request = None):
    return _scenario_view(app.state.scenario.get(), request)


@app.get("/")
def index():
    for candidate in ("dashboard/dist/index.html", "phmlite/static/index.html"):
        p = Path(candidate)
        if p.exists():
            # index.html 不缓存：前端重新构建后浏览器必须立刻拿到新哈希的资源清单
            resp = FileResponse(p)
            resp.headers["Cache-Control"] = "no-cache"
            return resp
    return {"name": "phm-lite", "docs": "/docs"}


# 前端构建产物托管（构建完成后生效；API 路由注册在前，优先匹配）
_UI_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "dist"
if _UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")
