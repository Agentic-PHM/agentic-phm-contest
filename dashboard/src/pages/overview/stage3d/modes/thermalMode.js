/**
 * 【热力图】视图模式 —— F2 实现。
 *
 * 职责：把 /api/channels 的部件级温度通道映射到 equipment_map 语义部件，按温度做
 * 蓝→绿→黄→橙→红 热力着色（部件基础自发光色，经 ctx.setPartTint 设置，悬停/选中
 * 高亮仍由框架以更高优先级处理），并在舞台左上角挂一条"色标 + 阈值刻度"图例。
 *
 * 部件 ↔ 通道绑定（equipment_map components[].id ↔ phmlite 通道名；阈值为接口
 * 随通道下发的 notice/severe，本文件不硬编码任何业务数值）：
 *   bearing_de ← 轴温DE · bearing_nde ← 轴温NDE
 *   cooling_system ← 冷风温度 · lubrication_system ← 油温
 *   stator 的"定子铁芯温度"为 equipment_map 预留通道，接口暂未提供 → 退化为用
 *   整机最弱（最热）温度做柔和着色（STATOR_RESERVED_CHANNEL，注释即预留说明）；
 *   接口日后补上该通道则自动切回正常绑定。
 *   rotor/shaft/bedplate/terminal_box 无温度绑定 → 保持原色，不做任何着色。
 *
 * 着色标尺：每个部件用它自己通道的 notice/severe 归一化热度比 r（0=冷端，1=severe），
 * 避免冷风 40/50 与轴温 65/80 两档阈值带宽差异在全局 °C 刻度上互相挤压失真：
 *   r=0（≤0.9×notice，健康基线区）→ 深蓝；r 随温度升过 绿→黄；
 *   value=notice → r=0.78（橙，即"≥notice 用橙"）；value≥severe → r=1（红+更高发光强度）。
 * 数据缺失/阈值非法的部件不着色（保持原色），绝不编造数值；channels 为 null（轮询
 * 空窗）时保持上一帧状态。updateFrame 仅做颜色/强度的平滑插值（复用对象，零新建）。
 *
 * exit：清本模式 tint（框架亦会兜底 resetPartsTint）并移除自建图例 DOM（自建自清）。
 */
import * as THREE from 'three'

/* ---- 热力标尺（纯渲染常量；业务数值全部来自接口下发的 notice/severe） ---- */
const RAMP_STOPS = [
  [0.0, 0x2f6bff], // 冷端 深蓝
  [0.38, 0x27c46f], // 绿
  [0.62, 0xf5e642], // 黄
  [0.78, 0xff9636], // 橙（value=notice 落在此处）
  [1.0, 0xff3d3d], // 红（value≥severe）
]
const RATIO_NOTICE = 0.78 // 热度比在 notice 处的取值（进入橙区）
const COOL_FACTOR = 0.9 // 冷端锚点 = notice×0.9：让各部件健康基线落在蓝→绿区
const INTENSITY_COOL = 0.3 // 冷端基础自发光强度
const INTENSITY_HOT = 0.6 // severe 红色 + 更高强度（框架选中高亮为 0.55，悬停 0.3）
const SMOOTH_RATE = 4.0 // 颜色/强度平滑趋近速率（1/s）
const STATOR_SOFT_FACTOR = 0.55 // 预留通道退化着色的柔和系数（按最弱温度打折）

/** 部件绑定：equipment_map components[].id ↔ /api/channels 温度通道名 */
const PART_TEMP_BINDING = [
  { partId: 'bearing_de', channel: '轴温DE' }, // equipment_map: Bearing_DE
  { partId: 'bearing_nde', channel: '轴温NDE' }, // equipment_map: Bearing_NDE
  { partId: 'cooling_system', channel: '冷风温度' }, // equipment_map: Cooling_System
  { partId: 'lubrication_system', channel: '油温' }, // equipment_map: Lubrication_System
]
const STATOR_PART_ID = 'stator'
const STATOR_RESERVED_CHANNEL = '定子铁芯温度' // equipment_map 预留通道：后端暂无此测点

/* ---- 模块级复用对象/状态（渲染循环内零新建；enter/exit 维护生命周期） ---- */
const STOP_COLORS = RAMP_STOPS.map(([r, hex]) => ({ r, c: new THREE.Color(hex) }))
const tmpColor = new THREE.Color()
let legendEl = null
let legendRowsEl = null
let legendTickKey = ''
let bindings = PART_TEMP_BINDING // enter 时按 ctx.parts 实际存在的部件过滤缓存
const states = new Map() // partId -> {tgt, cur, tgtI, curI, init, has, appliedHex, appliedI}

const hexCss = (n) => `#${n.toString(16).padStart(6, '0')}`
const fmtNum = (v) => String(v)

/** 热度比 r∈[0,1]：以通道自身 notice/severe 为档位锚点（非法/缺失阈值按冷端处理） */
function heatRatio(value, notice, severe) {
  if (!(severe > notice) || !(value > notice * COOL_FACTOR)) return 0
  if (value < notice) return (RATIO_NOTICE * (value - notice * COOL_FACTOR)) / (notice - notice * COOL_FACTOR)
  return RATIO_NOTICE + (1 - RATIO_NOTICE) * Math.min((value - notice) / (severe - notice), 1)
}

/** r → 颜色：在停靠点间线性插值，写入调用方复用的 out（不新建对象） */
function rampColor(r, out) {
  let i = 1
  while (i < STOP_COLORS.length - 1 && STOP_COLORS[i].r < r) i += 1
  const a = STOP_COLORS[i - 1]
  const b = STOP_COLORS[i]
  out.copy(a.c).lerp(b.c, b.r === a.r ? 0 : (r - a.r) / (b.r - a.r))
  return out
}

/** 记录部件目标色（update 里算好，updateFrame 平滑逼近后经 setPartTint 应用） */
function setTarget(ctx, partId, color, intensity) {
  if (!ctx.parts.has(partId)) return
  let st = states.get(partId)
  if (!color) {
    // 数据缺失 → 不着色（保持原色），立即清除
    if (st) st.has = false
    ctx.setPartTint(partId, null, 0)
    return
  }
  if (!st) {
    st = { tgt: new THREE.Color(), cur: new THREE.Color(), tgtI: 0, curI: 0,
      init: false, has: true, appliedHex: -1, appliedI: -1 }
    states.set(partId, st)
  }
  st.has = true
  st.tgt.copy(color)
  st.tgtI = intensity
}

/** 图例：蓝→红渐变条 + 各绑定通道的注意/严重阈值刻度（全部来自接口数据） */
function buildLegend() {
  const el = document.createElement('div')
  el.className = 's3d-thermal-legend'
  // 页签在右上、详情面板在右下、参数卡在底部 → 图例挂左上角；样式内联（自建自清，不动全局 CSS）
  el.style.cssText =
    'position:absolute;top:12px;left:16px;z-index:3;pointer-events:none;' +
    'min-width:172px;padding:8px 12px 9px;border-radius:9px;' +
    'background:linear-gradient(165deg,rgba(18,49,82,.78),rgba(8,21,39,.9));' +
    'border:1px solid rgba(0,199,255,.22);backdrop-filter:blur(6px);' +
    'box-shadow:0 5px 16px rgba(0,0,0,.38);font-size:11px;line-height:1.5;color:#cfe6ff;'
  const title = document.createElement('div')
  title.textContent = '部件温度热力 · °C'
  title.style.cssText = 'letter-spacing:1px;color:#7fd0ff;margin-bottom:5px;'
  const bar = document.createElement('div')
  bar.style.cssText =
    `height:8px;border-radius:4px;background:linear-gradient(90deg,` +
    `${RAMP_STOPS.map(([r, hex]) => `${hexCss(hex)} ${Math.round(r * 100)}%`).join(',')});`
  const ends = document.createElement('div')
  ends.style.cssText = 'display:flex;justify-content:space-between;color:#5f7f9e;margin:2px 0 4px;'
  ends.innerHTML = '<span>冷</span><span>注意 → 严重</span><span>热</span>'
  const rows = document.createElement('div')
  el.append(title, bar, ends, rows)
  legendRowsEl = rows // 阈值刻度行容器（exit 随图例一起移除）
  return el
}

/** 阈值刻度行：首次拿到带 notice/severe 的通道数据时填充（数据不变不重画） */
function refreshLegend(ctx) {
  if (!legendEl || !legendRowsEl) return
  const rows = bindings
    .map(({ channel }) => ({ channel, ch: ctx.channelsByName.get(channel) }))
    .filter((x) => x.ch && Number.isFinite(x.ch.notice) && Number.isFinite(x.ch.severe))
  if (!rows.length) return
  const key = rows.map((x) => `${x.channel}:${x.ch.notice}/${x.ch.severe}`).join('|')
  if (key === legendTickKey) return
  legendTickKey = key
  legendRowsEl.innerHTML = ''
  rows.forEach(({ channel, ch }) => {
    const row = document.createElement('div')
    row.style.cssText = 'display:flex;justify-content:space-between;gap:12px;white-space:nowrap;'
    const name = document.createElement('span')
    name.textContent = channel
    name.style.color = '#8fb8dd'
    const val = document.createElement('span')
    val.textContent = `注意 ${fmtNum(ch.notice)} / 严重 ${fmtNum(ch.severe)}`
    row.append(name, val)
    legendRowsEl.append(row)
  })
  // 预留通道提示（equipment_map：定子铁芯温度，后端暂无测点）
  if (!ctx.channelsByName.get(STATOR_RESERVED_CHANNEL)) {
    const row = document.createElement('div')
    row.style.cssText = 'color:#5f7f9e;white-space:nowrap;margin-top:1px;'
    row.textContent = `${STATOR_RESERVED_CHANNEL} · 预留`
    legendRowsEl.append(row)
  }
}

export default {
  id: 'thermal',
  label: '热力图',

  enter(ctx) {
    states.clear()
    legendTickKey = ''
    // 缓存本模式可着色的部件列表（材质已由引擎逐 mesh 克隆，经 setPartTint 改基础色）
    bindings = PART_TEMP_BINDING.filter((b) => ctx.parts.has(b.partId))
    legendEl = buildLegend()
    ctx.container.appendChild(legendEl)
  },

  update(ctx, channels) {
    if (!channels) return // 轮询空窗：保持现状，不清色也不编造数值
    const byName = ctx.channelsByName
    let weakest = null // 绑定部件中最热一路的热度比（供 Stator 预留退化着色）

    bindings.forEach(({ partId, channel }) => {
      const ch = byName.get(channel)
      const ok = ch && Number.isFinite(ch.value) && Number.isFinite(ch.notice) &&
        Number.isFinite(ch.severe) && ch.severe > ch.notice
      if (!ok) {
        setTarget(ctx, partId, null, 0) // 数据缺失：不着色，保持原色
        return
      }
      const r = heatRatio(ch.value, ch.notice, ch.severe)
      setTarget(ctx, partId, rampColor(r, tmpColor),
        INTENSITY_COOL + (INTENSITY_HOT - INTENSITY_COOL) * r)
      if (!weakest || r > weakest.r) weakest = { r }
    })

    // Stator（预留）：equipment_map 绑定的"定子铁芯温度"接口暂无 → 用整机最弱温度
    // 做柔和着色；接口日后补上该通道则按正常绑定着色
    const statorCh = byName.get(STATOR_RESERVED_CHANNEL)
    if (statorCh && Number.isFinite(statorCh.value) && Number.isFinite(statorCh.notice) &&
      Number.isFinite(statorCh.severe) && statorCh.severe > statorCh.notice) {
      const r = heatRatio(statorCh.value, statorCh.notice, statorCh.severe)
      setTarget(ctx, STATOR_PART_ID, rampColor(r, tmpColor),
        INTENSITY_COOL + (INTENSITY_HOT - INTENSITY_COOL) * r)
    } else if (weakest) {
      setTarget(ctx, STATOR_PART_ID, rampColor(weakest.r, tmpColor),
        (INTENSITY_COOL + (INTENSITY_HOT - INTENSITY_COOL) * weakest.r) * STATOR_SOFT_FACTOR)
    } else {
      setTarget(ctx, STATOR_PART_ID, null, 0)
    }
    // Rotor/Shaft/Bedplate/Terminal_Box：equipment_map 无温度绑定 → 保持原色（不着色）

    refreshLegend(ctx)
  },

  /** 每帧：目标色平滑插值（复用 Color 对象；变化足够小则不重设） */
  updateFrame(ctx, dt) {
    if (!states.size) return
    const k = 1 - Math.exp(-SMOOTH_RATE * Math.max(dt, 0))
    states.forEach((st, partId) => {
      if (!st.has) return
      if (!st.init) {
        st.cur.copy(st.tgt)
        st.curI = st.tgtI
        st.init = true
      } else {
        st.cur.lerp(st.tgt, k)
        st.curI += (st.tgtI - st.curI) * k
      }
      const hex = st.cur.getHex()
      if (hex !== st.appliedHex || Math.abs(st.curI - st.appliedI) > 0.001) {
        st.appliedHex = hex
        st.appliedI = st.curI
        ctx.setPartTint(partId, hex, st.curI)
      }
    })
  },

  exit(ctx) {
    states.forEach((st, partId) => ctx.setPartTint(partId, null, 0)) // 清 tint（框架亦兜底）
    states.clear()
    legendEl?.remove() // 自建图例自清
    legendEl = null
    legendRowsEl = null
    legendTickKey = ''
  },

  dispose(ctx) {
    this.exit(ctx)
  },
}
