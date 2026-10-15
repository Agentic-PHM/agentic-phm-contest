import React from 'react'
import { Skeleton } from 'antd'
import { LEVEL_COLOR, LEVEL_TEXT } from '../../api.js'
import { fmtValue } from './util.js'

/**
 * 首页底部的通道卡（拍板 2026-10-02：只留"无空间归属"的通道——
 * 轴振/瓦振已由 3D 部件卡按部位展示，底部不再重复，避免同值两处出现）。
 * 通道的 名称/数值/单位/状态/注意与严重阈值 全部来自 GET /api/channels（3s 轮询，实时联动仿真），
 * 页面上不含任何硬编码业务数字。
 */
const REPRESENTATIVE = ['电机转速', '轴承温度', 'DE振动', 'NDE振动']

export default function ParamCards({ channels }) {
  const list = channels?.channels || []
  const picked = REPRESENTATIVE.map((name) => list.find((c) => c.name === name)).filter(Boolean)

  // 首个轮询周期未返回时显示骨架占位
  if (!channels) {
    return (
      <>
        {REPRESENTATIVE.map((name) => (
          <div className="pcard pcard-skel" key={name}>
            <Skeleton active title={{ width: '56%' }} paragraph={{ rows: 1, width: '82%' }} />
          </div>
        ))}
      </>
    )
  }

  return (
    <>
      {picked.map((c) => {
        const color = LEVEL_COLOR[c.level] || LEVEL_COLOR.normal
        return (
          // --lv 供 CSS 绘制卡片顶部微光条与左上光晕，随状态色联动
          <div className="pcard" key={c.name} style={{ '--lv': color }} title={`${c.name}（${c.group}）`}>
            <div className="pcard-name">{c.name}</div>
            <div className="pcard-value">
              {fmtValue(c.value)}
              <span className="pcard-unit">{c.unit}</span>
            </div>
            <div className="pcard-status" style={{ color }}>
              <i className="pcard-dot" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
              {LEVEL_TEXT[c.level] || c.level}
              <span className="pcard-range">
                注意 {fmtValue(c.notice)} / 严重 {fmtValue(c.severe)} {c.unit}
              </span>
            </div>
          </div>
        )
      })}
    </>
  )
}
