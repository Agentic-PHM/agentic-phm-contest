/**
 * 【3D构图】默认视图模式 —— F0 已实现。
 *
 * 职责：标准整机数字孪生视图。
 * - enter：清空基础状态色（框架在切出上一模式时已做位姿/可见性防御性复位）。
 * - update(ctx, channels)：按部件绑定通道的最差 level 设置"基础"自发光——
 *   normal 不发光；notice 淡黄；severe 淡红。与右下参数卡片、部件标注卡片
 *   同源（/api/channels + equipment_map 绑定），全页一个数据口径。
 *   悬停/选中高亮（青色）由 Stage3D 框架处理，优先级高于本基础色。
 * - exit：清空全部基础状态色。
 *
 * 无任何硬编码业务数值；阈值/等级判定全部来自接口与 equipment_map。
 */
import { worstPartLevel } from '../partCards.js'

/** level → 基础自发光（强度刻意压低，只做"呼吸感"提示，不干扰读模） */
const LEVEL_TINT = {
  notice: { color: 0xfaad14, intensity: 0.14 },
  severe: { color: 0xff4d4f, intensity: 0.26 },
}

export default {
  id: 'compose',
  label: '3D构图',

  enter(ctx) {
    ctx.resetPartsTint()
  },

  update(ctx) {
    ctx.parts.forEach((part) => {
      const level = worstPartLevel(part.comp, ctx.channelsByName)
      const tint = LEVEL_TINT[level]
      ctx.setPartTint(part.id, tint ? tint.color : null, tint ? tint.intensity : 0)
    })
  },

  exit(ctx) {
    ctx.resetPartsTint()
  },
}
