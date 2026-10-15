/**
 * 【振动云图】视图模式 —— 3D 部件按实时振幅"发光 + 呼吸脉冲"着色（F3 实现）。
 *
 * 数据链路（业务阈值一律来自数据，不硬编码）：
 * - 部件 ↔ 振动通道绑定来自 equipment_map（part.comp.sensors），只取振动类通道——
 *   按单位元数据识别（μm 位移 / mm/s 速度）；转速 rpm、温度 °C、油压 MPa、键相 -
 *   等非振动量不参与云图。
 * - 比值 r = 通道当前值 / 严重阈值。severe 优先取 /api/channels 实时下发的阈值；
 *   该字段缺失时兜底用 equipment_map 该测点的 critical 配置（同为配置数据，非编造）。
 * - 同一部件绑定多条通道时取最大 r（最坏通道主导该部件的云图颜色）。
 * - 通道缺失 / value、severe 非法 / 整体 channels 为 null → 该部件不着色，不编数值。
 * - 绑定别名兜底：equipment_map 的通道名与后端实时目录可能不同步（如 瓦振6X ↔ 实际
 *   瓦振1X/1Y/2X/2Y），精确名匹配失败时按"去掉尾缀数字/轴号的同族前缀 + 相同单位"
 *   匹配实时目录，把同族通道全部并入该部件的绑定。
 *
 * 颜色映射（断点为纯视觉常量，可硬编码；阈值语义来自通道数据）：
 *   r=0 绿 → 0.5 青 → 0.8 黄 → 0.9 橙 → ≥1.0 红（分段线性插值、全程无跳变；
 *   r≥1.0 即到达/超过严重阈值，恒为红）。与下方图例 CSS 渐变断点一一对应。
 * 强度：base = min(0.15 + r·0.55, 0.8)，经 ctx.setPartTint 设置基准自发光。
 *
 * 呼吸脉冲：onFrame 每帧 intensity = base × (0.85 + 0.15·sin(phase))，
 * phase += dt·2π·f，f = 1.2 + r·2.4 Hz —— r 越大闪得越快越亮；累积相位保证
 * 变频瞬间波形连续无跳变。每帧仍走 ctx.setPartTint(hex, intensity)（契约要求基础色
 * 由框架管理）：引擎 applyColor 优先级为 选中 > 悬停 > 基础色，脉冲因此不会盖掉
 * 悬停/选中高亮；若绕过 setPartTint 直接改材质 emissiveIntensity，反而会每帧覆盖
 * 框架高亮，故不采用。每帧零对象分配：只传 hex 整数 + 浮点强度，无 THREE 临时对象。
 *
 * 部件绑定覆盖（enter 后按实时目录解析，结果见 resolveBindings）：
 *   bearing_de  ← 轴振1X / 轴振1Y
 *   bearing_nde ← 轴振2X / 轴振2Y
 *   bedplate    ← 瓦振1X/1Y/2X/2Y（equipment_map 记名 瓦振6X/6Y，经别名兜底并入）
 *   rotor       ← 轴振1X（电机转速为 rpm 非振动量，不参与云图）
 *   shaft       ← 轴振2X（其诊断特征"2X 联轴器不对中"后端无独立通道，见 EXTRA_BINDINGS）
 *   stator / cooling_system / lubrication_system / terminal_box：无振动测点绑定，
 *   保持原色不着色。
 */

/* ═══════════ 视觉常量（非业务阈值，可硬编码） ═══════════ */

/** 色标断点：r(当前值/严重阈值) → 颜色，分段线性插值；与图例 CSS 渐变保持一致 */
const COLOR_STOPS = [
  { r: 0.0, hex: 0x22c55e }, // 绿：远离阈值
  { r: 0.5, hex: 0x00e5ff }, // 青
  { r: 0.8, hex: 0xffd400 }, // 黄
  { r: 0.9, hex: 0xff8c00 }, // 橙
  { r: 1.0, hex: 0xff2d2d }, // 红：到达严重阈值后恒为红
]

/** 振动类通道按单位元数据识别（含 um 常见转写）；rpm/°C/MPa/- 等一律不参与 */
const VIB_UNITS = new Set(['μm', 'um', 'mm/s'])

/** 强度映射 base = min(MIN + r·SPAN, MAX)（题设视觉公式） */
const INTENSITY_MIN = 0.15
const INTENSITY_SPAN = 0.55
const INTENSITY_MAX = 0.8

/** 呼吸：base × (BASE + DEPTH·sin(phase))，f = F_MIN + r·F_SPAN (Hz)（纯视觉常量） */
const BREATH_BASE = 0.85
const BREATH_DEPTH = 0.15
const BREATH_F_MIN = 1.2
const BREATH_F_SPAN = 2.4
const TAU = Math.PI * 2

/**
 * 语义补充绑定：equipment_map 只给主轴（shaft）绑了"键相"（数据有效性通道，非振动量），
 * 但其诊断特征"2X 联轴器不对中"在后端无独立通道——按任务绑定表以现存通道依序兜底，
 * 取第一个在实时目录中存在的通道；都不存在则主轴不着色，行为退化为纯 equipment_map 绑定。
 */
const EXTRA_BINDINGS = [{ partId: 'shaft', channels: ['2X不对中', '轴振2X'] }]

/* ═══════════ 模块级运行状态（引擎按单例使用模式对象；enter 重建、exit 置空） ═══════════ */

let S = null
// S = {
//   ctx,                              // 引擎 ctx（frameTick 闭包经 S 取用）
//   parts: Map<partId, {r,hex,base,phase}>, // 当前"正在发光"的部件状态（呼吸调制数据）
//   bindings: [{partId, chans:[{name,sensor}]}]|null, // 部件→通道绑定（首个有效数据帧解析缓存）
//   frameOff: Function|null,          // onFrame 注销函数
//   legend: HTMLElement|null,         // 自建色标条
// }

/* ═══════════ 工具函数（全部无分配） ═══════════ */

function normUnit(u) {
  return String(u ?? '').trim().toLowerCase()
}

/** r → 插值后的 hex 整数（与 COLOR_STOPS / 图例渐变一致；r≥1.0 恒红） */
function colorAt(r) {
  const first = COLOR_STOPS[0]
  const last = COLOR_STOPS[COLOR_STOPS.length - 1]
  if (r <= first.r) return first.hex
  if (r >= last.r) return last.hex
  for (let i = 1; i < COLOR_STOPS.length; i++) {
    const b = COLOR_STOPS[i]
    if (r <= b.r) {
      const a = COLOR_STOPS[i - 1]
      const t = (r - a.r) / (b.r - a.r)
      const rr = ((a.hex >> 16) & 255) + (((b.hex >> 16) & 255) - ((a.hex >> 16) & 255)) * t
      const gg = ((a.hex >> 8) & 255) + (((b.hex >> 8) & 255) - ((a.hex >> 8) & 255)) * t
      const bb = (a.hex & 255) + ((b.hex & 255) - (a.hex & 255)) * t
      return ((Math.round(rr) << 16) | (Math.round(gg) << 8) | Math.round(bb))
    }
  }
  return last.hex
}

/**
 * 单通道比值 r = 值/严重阈值。
 * severe 优先取实时通道数据；非法时兜底 equipment_map 配置 critical；仍非法 → null（不着色）。
 */
function channelRatio(ch, sensor) {
  if (!ch) return null
  const v = Number(ch.value)
  let severe = Number(ch.severe)
  if (!Number.isFinite(severe) || severe <= 0) severe = Number(sensor?.critical)
  if (!Number.isFinite(v) || !Number.isFinite(severe) || severe <= 0) return null
  return Math.max(0, v / severe)
}

/**
 * 解析 部件→振动通道 绑定（equipment_map comp.sensors，只认振动单位）。
 * 精确名匹配失败时做同族前缀 + 同单位别名兜底（瓦振6X → 瓦振1X/1Y/2X/2Y）。
 * 仅在首个有效数据帧调用一次并缓存（后端通道目录在运行期不变）。
 */
function resolveBindings(ctx) {
  const byName = ctx.channelsByName
  const list = []
  ctx.parts.forEach((part, partId) => {
    // 无振动测点绑定（如 Stator/Cooling/Lubrication/Terminal_Box 只有温度/压力类）→ 不着色
    const sensors = (part.comp?.sensors || []).filter((s) => VIB_UNITS.has(normUnit(s.unit)))
    if (!sensors.length) return
    const chans = []
    const seen = new Set()
    const take = (name, sensor) => {
      if (name && byName.has(name) && !seen.has(name)) {
        seen.add(name)
        chans.push({ name, sensor })
      }
    }
    sensors.forEach((s) => {
      const nm = s.channel
      if (!nm) return
      if (byName.has(nm)) {
        take(nm, s)
        return
      }
      // 别名兜底：去掉尾缀数字/轴号得同族前缀（瓦振6X → 瓦振），按 前缀+单位 并入全部同族通道
      const prefix = nm.replace(/[0-9xyz]*$/i, '')
      if (!prefix) return
      byName.forEach((c, name) => {
        if (normUnit(c.unit) === normUnit(s.unit) && name.startsWith(prefix)) take(name, s)
      })
    })
    if (chans.length) list.push({ partId, chans })
  })
  // 语义补充绑定（见 EXTRA_BINDINGS 注释）：部件存在、尚无振动绑定时才生效
  EXTRA_BINDINGS.forEach(({ partId, channels }) => {
    if (!ctx.parts.has(partId) || list.some((b) => b.partId === partId)) return
    const name = channels.find((n) => byName.has(n))
    if (name) list.push({ partId, chans: [{ name, sensor: null }] })
  })
  return list
}

/* ═══════════ 色标条图例（纯自建 HTML，exit 自清） ═══════════ */

function buildLegend() {
  const el = document.createElement('div')
  el.className = 's3d-vib-legend'
  el.style.cssText = [
    'position:absolute',
    'left:14px',
    'bottom:14px',
    'z-index:6',
    'padding:8px 10px 7px',
    'border-radius:8px',
    'background:rgba(6,14,26,0.72)',
    'border:1px solid rgba(0,199,255,0.22)',
    'font:500 10px/1.4 system-ui,"Microsoft YaHei",sans-serif',
    'color:#8fb8d8',
    'pointer-events:none',
    'user-select:none',
  ].join(';')
  // 渐变断点与 COLOR_STOPS 一一对应：绿0% → 青50% → 黄80% → 橙90% → 红100%（=严重阈值）
  el.innerHTML = [
    '<div style="margin-bottom:5px;letter-spacing:0.1em;color:#9fc7ff;">振幅 / 严重阈值</div>',
    '<div style="position:relative;width:190px;height:8px;border-radius:4px;overflow:hidden;',
    'box-shadow:0 0 10px rgba(0,199,255,0.2);',
    'background:linear-gradient(90deg,#22c55e 0%,#00e5ff 50%,#ffd400 80%,#ff8c00 90%,#ff2d2d 100%);"></div>',
    '<div style="position:relative;width:190px;height:13px;margin-top:2px;white-space:nowrap;">',
    '<span style="position:absolute;left:0;">0</span>',
    '<span style="position:absolute;left:50%;transform:translateX(-50%);">50%</span>',
    '<span style="position:absolute;left:80%;transform:translateX(-50%);">80%</span>',
    '<span style="position:absolute;left:100%;transform:translateX(-100%);">100%·严重阈值</span>',
    '</div>',
  ].join('')
  return el
}

/* ═══════════ 生命周期 ═══════════ */

function enter(ctx) {
  S = { ctx, parts: new Map(), bindings: null, frameOff: null, legend: null }
  if (ctx?.container) {
    S.legend = buildLegend()
    ctx.container.appendChild(S.legend)
  }
  // 呼吸脉冲走框架帧回调：切模式/卸载时框架自动清理，exit 里再主动注销一次（双保险）
  if (ctx?.onFrame) S.frameOff = ctx.onFrame(frameTick)
}

function update(ctx, channels) {
  if (!S || !ctx || !ctx.parts) return
  const rows = channels?.channels
  if (!rows || !rows.length) {
    clearParts() // 通道缺失/为 null → 不着色、不编数值
    return
  }
  if (!S.bindings) S.bindings = resolveBindings(ctx)

  S.bindings.forEach((b) => {
    // 取该部件全部绑定通道的最大比值（最坏通道主导云图颜色）
    let r = null
    b.chans.forEach(({ name, sensor }) => {
      const rr = channelRatio(ctx.channelsByName.get(name), sensor)
      if (rr !== null && (r === null || rr > r)) r = rr
    })
    if (r === null) {
      // 该部件所有绑定通道当前无有效数据 → 撤色，不编数值
      if (S.parts.has(b.partId)) {
        ctx.setPartTint(b.partId, null)
        S.parts.delete(b.partId)
      }
      return
    }
    const hex = colorAt(r)
    const base = Math.min(INTENSITY_MIN + r * INTENSITY_SPAN, INTENSITY_MAX)
    let st = S.parts.get(b.partId)
    if (!st) {
      st = { r, hex, base, phase: 0 }
      S.parts.set(b.partId, st)
    } else {
      st.r = r // phase 保留：数据刷新时呼吸波形连续
      st.hex = hex
      st.base = base
    }
    ctx.setPartTint(b.partId, hex, base) // 仅在数据到来时设一次基准色；呼吸由帧回调叠加
  })
}

/** 呼吸脉冲（onFrame 每帧）：amplitude = base × (0.85 + 0.15·sin(phase))，f 随 r 加快 */
function frameTick(dt) {
  if (!S) return
  const ctx = S.ctx
  if (!ctx?.setPartTint) return
  S.parts.forEach((st, partId) => {
    st.phase += dt * TAU * (BREATH_F_MIN + st.r * BREATH_F_SPAN)
    const breath = BREATH_BASE + BREATH_DEPTH * Math.sin(st.phase)
    // 零分配：只传 hex 整数 + 浮点强度；引擎侧选中/悬停高亮优先级更高，不会被脉冲盖掉
    ctx.setPartTint(partId, st.hex, st.base * breath)
  })
}

/** 清除本模式对全部部件的着色（不动部件位姿/可见性——框架职责） */
function clearParts() {
  if (!S) return
  const ctx = S.ctx
  S.parts.forEach((_st, partId) => ctx?.setPartTint?.(partId, null))
  S.parts.clear()
}

/** exit / dispose 共用：注销帧回调、移除图例、清自建状态。部件基础色由框架兜底 resetPartsTint */
function teardown() {
  if (!S) return
  if (S.frameOff) {
    try {
      S.frameOff()
    } catch (_) {
      /* 框架已批量清理时的重复注销忽略 */
    }
  }
  S.legend?.remove()
  S = null
}

export default {
  id: 'vibration',
  label: '振动云图',
  enter,
  update,
  exit: teardown,
  dispose: teardown, // 整个 Stage3D 卸载时同样清理（幂等：S 为空直接返回）
}
