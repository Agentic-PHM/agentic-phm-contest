import React from 'react'
import { Skeleton } from 'antd'
import { HeartOutlined } from '@ant-design/icons'
import { scoreColor } from './util.js'

/**
 * 底部健康因子摘要条：
 * - 取 GET /api/health 的 components 中得分最弱的 3 项，展示真实值（如"轴振2X 98.2"），按分数段配色。
 * - "查看 AI 工作流 →"为占位链接：智能体工作流独立页由另一任务（Task D/E）接入 App.jsx 导航。
 *   这里选择 window.dispatchEvent(new CustomEvent('nav', { detail: 'workflow' })) 与 App 解耦——
 *   App 侧未来监听 'nav' 事件即可完成跳转；当前无监听时点击无副作用。
 */
export default function HealthSummary({ health }) {
  const comps = health?.components || []
  const weakest = [...comps].sort((a, b) => Number(a.score) - Number(b.score)).slice(0, 3)

  const openWorkflow = () => {
    // 占位跳转：Task D/E 在 App.jsx 增加"智能体工作流"页后监听 nav 事件生效
    window.dispatchEvent(new CustomEvent('nav', { detail: 'workflow' }))
  }

  return (
    <footer className="ov-glass ov-footer">
      <span className="footer-title">
        <HeartOutlined style={{ color: '#00c7ff' }} />
        健康因子摘要
      </span>
      <span className="footer-note">最弱 3 项通道</span>
      {health ? (
        weakest.map((c) => {
          const color = scoreColor(c.score)
          return (
            <span className="footer-item" key={c.name}>
              <i className="pcard-dot" style={{ background: color, boxShadow: `0 0 8px ${color}` }} />
              {c.name}
              <b style={{ color }}>{Number(c.score).toFixed(1)}</b>
            </span>
          )
        })
      ) : (
        <Skeleton.Input active size="small" style={{ width: 340 }} />
      )}
      <a className="wf-link" onClick={openWorkflow} title="智能体工作流独立页接入后生效（Task D/E）">
        查看 AI 工作流 →
      </a>
    </footer>
  )
}
