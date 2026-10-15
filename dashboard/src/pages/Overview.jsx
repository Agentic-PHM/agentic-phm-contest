import React from 'react'
import { Spin } from 'antd'
import { usePolling } from '../api.js'
import KpiBar from './overview/KpiBar.jsx'
import Stage from './overview/Stage.jsx'
import ParamCards from './overview/ParamCards.jsx'
import HealthSummary from './overview/HealthSummary.jsx'
import './overview/Overview.css'

/**
 * 设备总览（Task B 重构版，拍板方案）：
 *   顶部 KPI 条（设备概况 + 健康因子大数字）
 *   → 中央"空间舞台"（调相机新底图居中 + 底部光晕环纯 CSS + 右上角装饰视角页签 + 小参数卡片）
 *   → 底部健康因子摘要（最弱 3 项 + "查看 AI 工作流"占位链接）。
 *
 * 数据源（全部实时轮询，页面无任何硬编码业务数字）：
 *   GET /api/device    5s —— 设备位号 name、各测点 level（→ 运行状态取最差等级）
 *   GET /api/channels  3s —— 代表通道 名称/数值/单位/等级/阈值（→ 舞台小参数卡片）
 *   GET /api/health    3s —— 健康因子 score 与 components（→ 大数字 + 底部最弱 3 项）
 *
 * 布局基调参照《设备总览理想图》但按拍板精简：首页只留 概况 + 健康因子 + 设备图；
 * 工作流移独立页（首页仅占位链接），能耗等无数据源模块一律不放。
 */
export default function Overview() {
  const { data: device, error: deviceErr } = usePolling('/api/device', 5000)
  const { data: channelRes, error: channelErr } = usePolling('/api/channels', 3000)
  const { data: health, error: healthErr } = usePolling('/api/health', 3000)

  // 首次连接阶段（三路都还没有数据且已有请求报错）显示整页加载占位；
  // 之后某一接口瞬时失败时保留最近一次数据，仅受影响区域退化为占位符。
  const booting = !device && !channelRes && !health && (deviceErr || channelErr || healthErr)

  if (booting) {
    return (
      <div className="ov-boot">
        <Spin size="large" />
        <div className="ov-boot-text">正在连接数据服务…</div>
      </div>
    )
  }

  return (
    <div className="ov-page">
      <KpiBar
        device={device}
        channelsTotal={channelRes ? channelRes.channels.length : null}
        health={health}
      />
      <Stage>
        <ParamCards channels={channelRes} />
      </Stage>
      <HealthSummary health={health} />
    </div>
  )
}
