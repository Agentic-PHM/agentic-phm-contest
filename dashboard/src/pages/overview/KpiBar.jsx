import React from 'react'
import { Skeleton, Tag } from 'antd'
import { DeploymentUnitOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { LEVEL_COLOR, LEVEL_TEXT } from '../../api.js'
import { scoreColor, worstLevel } from './util.js'

/**
 * 顶部 KPI 条：
 * - 左卡"设备概况"：设备定位已拍板为"调相机"（KYJ0001 为旧系统设备编号，作机型标注；
 *   位号 name 来自 GET /api/device，运行状态取 /api/device 各测点最差 level；
 *   监测通道路数取 GET /api/channels 实际长度，均为接口实时数据）。
 * - 右卡"健康因子"：大数字来自 GET /api/health 的 score，随轮询实时变化，
 *   按分数段配色（拍板口径）：≥90 绿 / 70-90 黄 / <70 红。
 */
export default function KpiBar({ device, channelsTotal, health }) {
  const level = worstLevel(device?.points)
  const score = health ? Number(health.score) : null
  const color = scoreColor(score)

  return (
    <header className="ov-kpis">
      <div className="ov-glass kpi-device">
        <div className="kpi-head">
          <DeploymentUnitOutlined style={{ color: '#00c7ff' }} />
          设备概况
        </div>
        <div className="kpi-device-main">
          <span className="kpi-device-name">
            调相机 <em>KYJ0001</em>
          </span>
          <span className="kpi-status">
            运行状态
            {device ? (
              <Tag style={{ marginInlineEnd: 0 }} color={LEVEL_COLOR[level] || LEVEL_COLOR.normal}>
                {LEVEL_TEXT[level] || level}
              </Tag>
            ) : (
              <Skeleton.Button active size="small" style={{ width: 52 }} />
            )}
          </span>
        </div>
        <div className="kpi-device-sub">
          位号 {device?.name || '--'} · 监测通道 {channelsTotal == null ? '--' : channelsTotal} 路 ·
          数据实时联动仿真
        </div>
      </div>

      <div className="ov-glass kpi-health">
        <div>
          <div className="kpi-head">
            <SafetyCertificateOutlined style={{ color: '#00c7ff' }} />
            健康因子
          </div>
          <div className="kpi-health-sub">
            综合 {health?.components ? health.components.length : '--'} 项通道实时评分
          </div>
        </div>
        <div
          className="kpi-score"
          style={health ? { color, textShadow: `0 0 24px ${color}66` } : { color }}
        >
          {health ? (
            <>
              {score.toFixed(1)}
              <span className="kpi-score-unit">%</span>
            </>
          ) : (
            <Skeleton.Input active size="small" style={{ width: 110 }} />
          )}
        </div>
      </div>
    </header>
  )
}
