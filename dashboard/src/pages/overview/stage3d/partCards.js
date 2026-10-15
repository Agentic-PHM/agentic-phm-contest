/**
 * 3D 舞台部件标注卡片 / 部件状态的数据组装层。
 * 与 pages/overview 其余部分同口径：业务数值全部来自 /api/channels + equipment_map，
 * 这里只做"绑定匹配 + 展示格式化"，不含任何业务数字常量。
 */
import { LEVEL_COLOR, LEVEL_TEXT } from '../../../api.js'
import { fmtValue } from '../util.js'

const LEVEL_RANK = { normal: 0, notice: 1, severe: 2 }
const UNKNOWN_COLOR = '#8fb8dd'

/** level → 展示色（接口等级色，未知时中性灰蓝） */
export function chanColor(level) {
  return LEVEL_COLOR[level] || UNKNOWN_COLOR
}

/** level → 展示文案 */
export function chanLevelText(level) {
  return LEVEL_TEXT[level] || level || '--'
}

/**
 * 部件状态 = 其绑定通道（equipment_map sensors）中最差的 level；
 * 没有任何在线通道（全为预留/接口未返回）时返回 null。
 */
export function worstPartLevel(comp, channelsByName) {
  let worst = null
  for (const s of comp.sensors || []) {
    const ch = channelsByName.get(s.channel)
    if (!ch || !ch.level) continue
    if (!worst || (LEVEL_RANK[ch.level] ?? 0) > (LEVEL_RANK[worst] ?? 0)) worst = ch.level
  }
  return worst
}

/**
 * 挑选卡片代表参数（最多 max 个，默认 2）：
 * - 优先第 1 个在线通道（通常为振动），再优先挑 1 个不同单位的在线通道（通常为温度），
 *   正好对应"轴振 1X μm / 轴温 °C"这类组合；
 * - 该部件全部离线时，回退展示前 2 个绑定通道（值显示 --），保留绑定关系可见。
 */
export function pickCardSensors(comp, channelsByName, max = 2) {
  const bound = (comp.sensors || []).map((s) => ({ s, ch: channelsByName.get(s.channel) || null }))
  const live = bound.filter((b) => b.ch)
  const picked = []
  if (live.length) {
    picked.push(live[0])
    const alt = live.slice(1).find((b) => b.ch.unit !== live[0].ch.unit)
    if (alt) picked.push(alt)
    else if (live[1]) picked.push(live[1])
  } else {
    bound.slice(0, max).forEach((b) => picked.push(b))
  }
  return picked.slice(0, max)
}

/** 组装单张标注卡片的数据模型（Stage3D 渲染用） */
export function buildCardModel(comp, channelsByName) {
  const level = worstPartLevel(comp, channelsByName)
  const rows = pickCardSensors(comp, channelsByName).map(({ s, ch }) => ({
    key: s.channel,
    name: s.channel,
    value: ch ? fmtValue(ch.value) : '--',
    unit: ch?.unit || s.unit || '',
  }))
  return {
    id: comp.id,
    nameCn: comp.name_cn,
    level,
    color: chanColor(level),
    levelText: chanLevelText(level),
    rows,
  }
}
