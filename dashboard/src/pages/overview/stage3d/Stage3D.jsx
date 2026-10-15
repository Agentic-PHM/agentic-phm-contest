import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Button } from 'antd'
import { CloseOutlined } from '@ant-design/icons'
import { fmtValue } from '../util.js'
import { StageEngine } from './engine.js'
import { buildCardModel, chanColor, chanLevelText } from './partCards.js'
import './stage3d.css'

/**
 * 3D 数字孪生舞台（React 薄壳 + StageEngine 引擎层）：
 * - 引擎负责 three.js 场景/GLB 加载/拾取/模式调度；本组件只做状态映射与 DOM 覆盖层。
 * - 部件标注卡片：equipment_map 9 个语义部件各一张 HTML 小卡，锚定 3D 锚点，
 *   每帧由引擎重投影回调直接写 transform（不经 React，避免 60fps 重渲染）；
 *   数值部分随 /api/channels 轮询（3s）经 buildCardModel 刷新。
 * - 联动：画布悬停部件 ↔ 卡片高亮（双向）；点击部件/卡片 → 右下详情面板
 *   （全部绑定通道 值/单位/等级/注意/严重阈值），点空白/Esc/关闭钮收起。
 * - 加载失败/WebGL 不可用时通过 onStatus 上报，由 Stage.jsx 回退静态 PNG 模式。
 * - 详情面板渲染为 .s3d-host 的兄弟节点：host 的 z-index 低于底部参数卡行（stage-cards），
 *   面板若放在 host 内会被卡片行盖住，提出去才能浮在其上。
 */

/** 卡片几何常量（与 stage3d.css 尺寸对应，用于每帧防重叠推算） */
const CARD_W = 176
const CARD_H = 64
const GAP_ABOVE = 10 // 卡片底边到部件包围盒顶面的间距

/**
 * 每帧标注布局（拍板 2026-10-02 第三版："卡片悬浮在部件轮廓上方"）：
 * - 悬浮点 = 部件包围盒顶面中心（正视/侧视/背视时都在部件轮廓上方，顶视图自然重合可接受）；
 * - 引线从部件锚点（中心）连到悬浮点上的卡片，模型转动/缩放时全程跟随；
 * - 近大远小 + 距离淡出：引擎按锚点距离给出 scale/透明度依据，CSS 过渡消闪感；
 * - 卡片按距离定 z 序：近的盖住远的，无防重叠腾挪。
 */
function applyFollowLayout(host, cardEls, leaderEls, items) {
  for (const p of items) {
    const el = cardEls[p.id]
    if (!el) continue
    const xTop = p.tx ?? p.x
    const yTop = p.ty ?? p.y - GAP_ABOVE
    const yCard = Math.max(yTop - GAP_ABOVE, 6)
    el.style.transform =
      `translate3d(${xTop.toFixed(1)}px, ${yCard.toFixed(1)}px, 0) translate(-50%, -100%) scale(${(p.scale || 1).toFixed(3)})`
    let op
    if (!p.visible) {
      op = 0
    } else {
      const rr = p.dist / (p.fitDist || 1)
      op = rr <= 1.02 ? 1 : Math.max(0, 1 - (rr - 1.02) * 1.6) // 1 倍参考距离内全显，超出按比例淡出
    }
    el.style.opacity = op.toFixed(3)
    el.style.zIndex = String(2000 - Math.round((p.dist || 0) * 10))
    const ln = leaderEls ? leaderEls[p.id] : null
    if (ln) {
      if (p.visible && op > 0.05) {
        ln.setAttribute('points', `${p.x.toFixed(1)},${(p.y - 3).toFixed(1)} ${xTop.toFixed(1)},${(yCard + 2).toFixed(1)}`)
        ln.style.opacity = String(Math.min(1, op * 0.7).toFixed(3))
      } else if (ln.style.opacity !== '0') {
        ln.style.opacity = '0'
      }
    }
  }
}

export default function Stage3D({ modeId, channels, diagnosedId, onStatus }) {
  const hostRef = useRef(null)
  const engineRef = useRef(null)
  const cardEls = useRef({})
  const leaderEls = useRef({})
  const onStatusRef = useRef(onStatus)
  onStatusRef.current = onStatus

  const [ready, setReady] = useState(false)
  const [comps, setComps] = useState([])
  const [legendNote, setLegendNote] = useState('')
  const [hoverId, setHoverId] = useState(null)
  const [selectedId, setSelectedId] = useState(null)

  /** 通道按名索引：卡片数值与详情面板共用的唯一数据源 */
  const channelsByName = useMemo(() => {
    const m = new Map()
    ;(channels?.channels || []).forEach((c) => m.set(c.name, c))
    return m
  }, [channels])

  // 引擎生命周期：随本组件挂载/卸载（卸载时引擎彻底 dispose）。
  // WebGL 创建失败等引擎级异常同样走 onError → Stage.jsx 回退静态 PNG，不拖垮整页。
  useEffect(() => {
    const host = hostRef.current
    if (!host) return undefined

    let engine
    try {
      engine = new StageEngine(host, {
        onProgress: (p) => onStatusRef.current?.({ phase: 'loading', progress: Math.max(5, Math.round(p * 100)) }),
        onReady: ({ components, legendNote: note }) => {
          setComps(components || [])
          setLegendNote(note || '')
          setReady(true)
          onStatusRef.current?.({ phase: 'ready' })
        },
        onError: (err) => {
          setReady(false)
          onStatusRef.current?.({ phase: 'error', error: String(err?.message || err) })
        },
        onHover: (id) => setHoverId(id),
        onSelect: (id) => setSelectedId(id),
        onProject: (items) => applyFollowLayout(host, cardEls.current, leaderEls.current, items),
      })
    } catch (err) {
      console.error('[Stage3D] 引擎初始化失败，回退静态视图', err)
      onStatusRef.current?.({ phase: 'error', error: String(err?.message || err) })
      return undefined
    }
    engineRef.current = engine

    return () => {
      engine.dispose()
      if (engineRef.current === engine) engineRef.current = null
    }
  }, [])

  // 模式切换（就绪后生效；切换时清掉悬停/选中，避免跨模式残留高亮）
  useEffect(() => {
    engineRef.current?.setMode(modeId)
    setSelectedId(null)
    setHoverId(null)
  }, [modeId, ready])

  // 轮询数据 → 引擎（ready 翻转时补推一次，保证首个 update 不等下一个轮询周期）
  useEffect(() => {
    engineRef.current?.setChannels(channels)
  }, [channels, ready])

  // 诊断结论联动：最新报告指向的部件推送引擎做橙红高亮（ready 翻转时补推，避免场景就绪前推送丢失）
  useEffect(() => {
    engineRef.current?.setDiagnosedPart(diagnosedId || null)
  }, [diagnosedId, ready])

  const selectedComp = ready ? comps.find((c) => c.id === selectedId) : null

  const handleCardEnter = (id) => engineRef.current?.setHover(id)
  const handleCardLeave = () => engineRef.current?.setHover(null)
  const handleCardClick = (id) => engineRef.current?.setSelect(id === selectedId ? null : id)
  const closePanel = () => engineRef.current?.setSelect(null)

  return (
    <>
      <div className="s3d-host" ref={hostRef}>
        {/* three 画布由 StageEngine 注入并铺满本层（.s3d-canvas） */}

        {ready && (
          <>
            {/* 引线层：卡片固定在顶部卡槽，引线从卡底连到部件锚点（拍板 2026-10-02：
                卡片固定、引线跟随，消除旋转时卡片漂移/闪跳） */}
            <svg className="s3d-leader" aria-hidden>
              {comps.map((comp) => (
                <polyline
                  key={comp.id}
                  ref={(el) => {
                    leaderEls.current[comp.id] = el
                  }}
                  className={
                    's3d-leader-line' +
                    (comp.id === hoverId ? ' is-hot' : '') +
                    (comp.id === selectedId ? ' is-sel' : '')
                  }
                />
              ))}
            </svg>
            <div className="s3d-cards">
              {comps.map((comp) => {
                const card = buildCardModel(comp, channelsByName)
                const cls = ['s3d-card']
                if (card.id === hoverId) cls.push('is-hot')
                if (card.id === selectedId) cls.push('is-sel')
                return (
                  <div
                    key={card.id}
                    ref={(el) => {
                      cardEls.current[card.id] = el
                    }}
                    className={cls.join(' ')}
                    title={`${card.nameCn} · 点击查看绑定测点`}
                    onMouseEnter={() => handleCardEnter(card.id)}
                    onMouseLeave={handleCardLeave}
                    onClick={() => handleCardClick(card.id)}
                  >
                    <div className="s3d-card-name">
                      <i className="s3d-dot" style={{ background: card.color, boxShadow: `0 0 8px ${card.color}` }} />
                      {card.nameCn}
                    </div>
                    {card.rows.map((r) => (
                      <div className="s3d-card-row" key={r.key}>
                        <span className="n">{r.name}</span>
                        <b>{r.value}</b>
                        <span className="u">{r.unit}</span>
                      </div>
                    ))}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>

      {/* 详情面板：host 的兄弟节点，z-index 高于底部参数卡行，避免被遮挡 */}
      {selectedComp && (
        <PartDetailPanel
          comp={selectedComp}
          channelsByName={channelsByName}
          note={legendNote}
          onClose={closePanel}
        />
      )}
    </>
  )
}

/**
 * 部件详情小面板（右下浮出）：
 * 全部绑定通道的 值/单位/level/注意/严重 —— 值与等级来自 /api/channels，
 * 接口未覆盖的通道回退 equipment_map 演示阈值（值显示 --）；
 * 底部附 equipment_map 的阈值口径说明（ISO 典型值，演示配置）。
 */
function PartDetailPanel({ comp, channelsByName, note, onClose }) {
  return (
    <div className="s3d-panel">
      <div className="s3d-panel-head">
        <span className="s3d-panel-title">{comp.name_cn}</span>
        <span className="s3d-panel-mesh">
          {comp.mesh} · {comp.id}
        </span>
        <Button
          className="s3d-panel-close"
          type="text"
          size="small"
          icon={<CloseOutlined />}
          onClick={onClose}
          aria-label="关闭部件详情"
        />
      </div>

      {(comp.sensors || []).map((s) => {
        const ch = channelsByName.get(s.channel)
        const unit = ch?.unit || s.unit || ''
        const color = chanColor(ch?.level)
        return (
          <div className="s3d-ch" key={s.channel}>
            <div className="s3d-ch-top">
              <span className="s3d-ch-name" title={s.desc}>
                {s.channel}
              </span>
              <span className="s3d-ch-val" style={{ color }}>
                {ch ? fmtValue(ch.value) : '--'}
                {unit && <em>{unit}</em>}
              </span>
            </div>
            <div className="s3d-ch-sub">
              <i className="s3d-dot" style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
              {chanLevelText(ch?.level)}
              <span className="s3d-ch-range">
                注意 {fmtValue(ch?.notice ?? s.warning)} / 严重 {fmtValue(ch?.severe ?? s.critical)} {unit}
              </span>
            </div>
          </div>
        )
      })}

      {comp.notes && <div className="s3d-panel-note">{comp.notes}</div>}
      {note && <div className="s3d-panel-legend">阈值口径：{note}</div>}
    </div>
  )
}
