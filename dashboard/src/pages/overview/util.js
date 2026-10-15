/**
 * Overview 页共享小工具：数值格式化、健康分档配色、最差运行等级。
 * 只做展示层处理，不引入任何业务数字常量——业务数值一律来自轮询接口。
 */

/** 业务数值统一格式化：≥1000 取整，≥100 保留 1 位，其余保留 2 位；空值显示 --。 */
export function fmtValue(v) {
  const n = Number(v)
  if (v == null || Number.isNaN(n)) return '--'
  const a = Math.abs(n)
  if (a >= 1000) return n.toFixed(0)
  if (a >= 100) return n.toFixed(1)
  return n.toFixed(2)
}

/** 健康因子分段配色（拍板口径）：≥90 绿 / 70-90 黄 / <70 红；未知时用中性灰蓝。 */
export function scoreColor(score) {
  const s = Number(score)
  if (score == null || Number.isNaN(s)) return '#8fb8dd'
  if (s >= 90) return '#52c41a'
  if (s >= 70) return '#faad14'
  return '#ff4d4f'
}

const LEVEL_RANK = { normal: 0, notice: 1, severe: 2 }

/** 设备运行状态 = 全部测点中最差的 level（与 App.jsx 顶栏同口径，数据来自 GET /api/device）。 */
export function worstLevel(points) {
  return (points || []).reduce(
    (w, p) => ((LEVEL_RANK[p.level] ?? 0) > (LEVEL_RANK[w] ?? 0) ? p.level : w),
    'normal',
  )
}
