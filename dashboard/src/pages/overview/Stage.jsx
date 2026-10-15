import React, { useCallback, useEffect, useState } from 'react'
import { Progress, Tag } from 'antd'
import { usePolling } from '../../api.js'
import Stage3D from './stage3d/Stage3D.jsx'
import { MODE_LIST } from './stage3d/modes/index.js'

/**
 * 中央"空间舞台"（F0 升级：静态底图 → 3D 数字孪生）：
 * - 底层 Stage3D：three.js 场景（GLB 模型 + 部件标注卡片 + 悬停/点击联动 + 视图模式调度），
 *   加载中/失败时回退原有静态 PNG（/equip-base.png）+ 进度条 / 提示标签。
 * - 右上角四个视图页签由装饰按钮升级为真实切换（3D构图/结构拆图/热力图/振动云图），
 *   模式实现见 stage3d/modes/（接入契约见 modes/index.js 头部注释）；
 *   3D 场景未就绪时页签禁用。
 * - 底部 children 槽位（ParamCards 小参数卡片）保持不动。
 * - 本组件自带两路轮询：/api/channels（3s）供 3D 层卡片数值，/api/report/list（8s）取最新
 *   报告的 component 字段传给 3D 做诊断结论联动高亮——Overview.jsx 按约定不可改动，
 *   其同名轮询只进了 ParamCards；两路轮询打同一接口，数据口径一致。
 */
export default function Stage({ children }) {
  const { data: channels } = usePolling('/api/channels', 3000)
  const { data: reports } = usePolling('/api/report/list', 8000)
  // 诊断结论联动：接口按时间倒序，第一条即最新报告；component 为落库的部件 id（可为 null）
  const diagnosedId = reports?.rows?.[0]?.component || null
  const [modeId, setModeId] = useState(MODE_LIST[0]?.id || 'compose')
  const [phase, setPhase] = useState('loading') // loading | ready | error
  const [progress, setProgress] = useState(0)

  const handleStatus = useCallback((s) => {
    setPhase(s.phase)
    if (typeof s.progress === 'number') setProgress(s.progress)
  }, [])

  // 任务③对话即操作：吉祥物聊天面板可远程切换视图（agent:view 事件，白名单校验在 ModeList 内）
  useEffect(() => {
    const onView = (e) => {
      const id = String(e.detail || '')
      if (MODE_LIST.some((m) => m.id === id)) setModeId(id)
    }
    window.addEventListener('agent:view', onView)
    return () => window.removeEventListener('agent:view', onView)
  }, [])

  const notReady = phase !== 'ready'

  return (
    <section className="ov-stage">
      <div className="stage-halo" aria-hidden="true" />
      <div className="stage-ring" aria-hidden="true" />

      {/* 3D 场景层：始终挂载（内部自管理加载/兜底/销毁），画布铺满舞台底层 */}
      <Stage3D modeId={modeId} channels={channels} diagnosedId={diagnosedId} onStatus={handleStatus} />

      {/* 静态底图兜底层：仅在加载中/失败时渲染；pointer-events 已放行给画布（stage3d.css） */}
      <div className="stage-body">
        {notReady && (
          <div className="stage-fallback">
            <img
              className="stage-equip"
              src="/equip-base.png"
              alt="调相机组结构剖视图"
              draggable={false}
            />
            {phase === 'loading' && (
              <div className="stage-loading">
                <Progress
                  percent={progress}
                  size="small"
                  strokeColor="#00c7ff"
                  trailColor="rgba(0, 199, 255, 0.15)"
                />
                <span>3D 数字孪生场景加载中…</span>
              </div>
            )}
            {phase === 'error' && <Tag color="warning">3D 场景加载失败，已回退静态视图</Tag>}
          </div>
        )}
      </div>

      <div className="stage-cards">{children}</div>

      {/* 视图模式页签：真实切换（模式实现见 stage3d/modes/） */}
      <div className="stage-tabs">
        {MODE_LIST.map((m) => {
          const active = m.id === modeId
          return (
            <button
              key={m.id}
              type="button"
              title={notReady ? '3D 场景未就绪，暂不可切换' : `${m.label}视图`}
              aria-pressed={active}
              disabled={notReady}
              className={`stage-tab is-live${active ? ' is-active' : ''}`}
              onClick={() => {
                if (!notReady) setModeId(m.id)
              }}
            >
              {m.label}
            </button>
          )
        })}
      </div>

    </section>
  )
}
