import React, { useCallback, useEffect, useRef, useState } from 'react'
import { fetchJson, postJson, usePolling, LEVEL_TEXT } from '../api.js'
import { getAgentAvailability, getDiagnosisSnapshot } from '../agentState.js'
import { AGENT_AVATARS, AGENT_PREFERENCES_EVENT, readAgentPreferences } from '../avatarSettings.js'
import AgentAvatar3D from '../pages/overview/stage3d/AgentAvatar3D.jsx'
import './global-agent.css'

const POS_KEY = 'agentPos2' // v2：形象放大后重置回左下角

/** 登场问候（按时间段） */
function timeGreeting() {
  const h = new Date().getHours()
  if (h < 6) return '夜间值守中；请以系统状态栏和实时监视数据为准。'
  if (h < 12) return '早上好；我会按当前系统状态为你提供协助。'
  if (h < 18) return '下午好；设备状态和智能体可用性请查看状态栏。'
  return '晚上好；设备监测状态以实时数据为准。'
}

/** 定时主动问候（长时间停留时随机冒一句） */
const TIPS = [
  '规则巡检持续检查设备通道；严重越限会记录报警。',
  '当前异常档位来自阈值比较，可在实时监视页查看。',
  '自主诊断是否可用，请看页面顶部的 AGH 状态。',
  '基础监测和阈值报警持续运行。',
]

/** 任务①巡检播报：规则引擎拼句（零 LLM）。normal=平安报，跨档/越限=升级报。 */
function patrolSentence(chs, prevLevels) {
  const bad = chs.filter((c) => c.level !== 'normal')
  const bearing = chs.find((c) => c.name === '轴承温度')
  const temp = bearing ? `，轴承温度 ${bearing.value.toFixed(1)}℃` : ''
  let escalated = false
  for (const c of chs) {
    const prev = prevLevels[c.name] || 'normal'
    if ((prev === 'normal' && c.level !== 'normal') || (prev === 'notice' && c.level === 'severe')) {
      escalated = true
    }
    prevLevels[c.name] = c.level
  }
  if (bad.length) {
    const names = bad
      .map((c) => `${c.name}升到${LEVEL_TEXT[c.level]}档（${c.value}${c.unit}）`)
      .join('、')
    return { escalated, text: `${escalated ? '注意，通道跨档！' : ''}规则巡检发现异常：${names}。` }
  }
  return { escalated, text: `规则巡检结果：${chs.length} 路当前均在阈值内${temp}。` }
}

const BOT_W = 340
const BOT_H = 404 // 形象 360 + 名牌约 44

/**
 * 全局常驻 IP 形象（桌面宠物形态，参照 Codex Pets）：
 * - 无边框，3D 模型直接浮在页面上；可拖动到任意位置（拖动与点击互不误触）；
 * - 待机时呼吸起伏+小幅张望（不转圈）；登场/问候时播放动作+气泡；
 * - 点击弹出咨询对话（/api/agent/chat，Agnes + 平台部件知识，短句通俗回复）。
 */
export default function GlobalAgent({ operation }) {
  const { data: reports } = usePolling('/api/report/list', 10000)
  const { data: device } = usePolling('/api/device', 4000) // 只读快照：采样由后端独立循环推进（P0 问题3）
  const { data: autodiag } = usePolling('/api/autodiag/status', 3000)
  const availability = getAgentAvailability(operation)
  const chatReady = availability.ready

  const [preferences, setPreferences] = useState(readAgentPreferences)
  const { avatarIndex: agentIdx, nickname } = preferences
  const [phase, setPhase] = useState('idle') // idle | loading | ready | fail
  const [bubble, setBubble] = useState(null)
  const [action, setAction] = useState(null) // 'greet#序号'
  const [chatOpen, setChatOpen] = useState(false)
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [thinking, setThinking] = useState(false)
  const bubbleTimerRef = useRef(null)
  const msgListRef = useRef(null)

  useEffect(() => {
    const refreshPreferences = () => setPreferences(readAgentPreferences())
    window.addEventListener(AGENT_PREFERENCES_EVENT, refreshPreferences)
    window.addEventListener('storage', refreshPreferences)
    return () => {
      window.removeEventListener(AGENT_PREFERENCES_EVENT, refreshPreferences)
      window.removeEventListener('storage', refreshPreferences)
    }
  }, [])

  useEffect(() => {
    if (phase !== 'idle') setPhase('loading')
  }, [agentIdx])

  // 位置：默认左下角；可拖动，记忆在 localStorage
  const [pos, setPos] = useState(() => {
    try {
      const p = JSON.parse(localStorage.getItem(POS_KEY) || 'null')
      if (p && typeof p.left === 'number' && typeof p.top === 'number') return p
    } catch {}
    return { left: 18, top: Math.max((window.innerHeight || 900) - BOT_H - 14, 8) }
  })
  const dragRef = useRef(null) // { sx, sy, l, t, moved }

  const showBubble = useCallback((text, ms = 5000) => {
    setBubble({ text })
    if (bubbleTimerRef.current) clearTimeout(bubbleTimerRef.current)
    bubbleTimerRef.current = setTimeout(() => setBubble(null), ms)
  }, [])

  const play = useCallback((name) => setAction(`${name}#${Date.now()}`), [])

  // 懒加载：挂载 2 秒后再加载模型，不与页面首屏抢资源
  useEffect(() => {
    const t = setTimeout(() => setPhase((p) => (p === 'idle' ? 'loading' : p)), 2000)
    return () => clearTimeout(t)
  }, [])

  const onAvatarStatus = useCallback(
    (s) => {
      if (s === 'ok') {
        setPhase('ready')
        play('greet')
        showBubble(chatReady ? timeGreeting() : availability.detail, 5500)
      } else if (s === 'fail') {
        setPhase('fail')
      }
    },
    [play, showBubble, chatReady, availability.detail],
  )

  // 任务①常驻巡检：默认 4 分钟一次（?patrol=毫秒 可调，供测试/彩排），规则拼句零 LLM。
  // 对话面板打开或页面隐藏时不打扰（但仍刷新档位基线，保证下次跨档判断正确）。
  const prevLevelsRef = useRef({})
  useEffect(() => {
    if (!availability.ready) return undefined
    const ms = Math.max(5000, parseInt(new URLSearchParams(window.location.search).get('patrol'), 10) || 240000)
    const patrol = async () => {
      try {
        const d = await fetchJson('/api/channels')
        const chs = d.channels || []
        const { escalated, text } = patrolSentence(chs, prevLevelsRef.current)
        if (chatOpen || document.hidden) return
        showBubble(text, 7000)
        const hasBad = chs.some((c) => c.level !== 'normal')
        play(escalated ? 'spin' : hasBad ? 'tilt' : 'bow')
      } catch {
        if (!chatOpen && !document.hidden) showBubble(TIPS[Math.floor(Math.random() * TIPS.length)], 6000)
      }
    }
    const iv = setInterval(patrol, ms)
    return () => clearInterval(iv)
  }, [chatOpen, showBubble, play, availability.ready])

  // 任务②：自动唤起诊断播报——按事件序号增量播报（吉祥物第一人称）
  const announcedSeqRef = useRef(0)
  useEffect(() => {
    const evs = autodiag?.events || []
    if (!evs.length) return
    const fresh = evs.filter((e) => e.announce && e.seq > announcedSeqRef.current)
    if (!fresh.length) return
    announcedSeqRef.current = evs.reduce((m, e) => Math.max(m, e.seq), announcedSeqRef.current)
    const last = fresh[fresh.length - 1]
    showBubble(last.text, 9000)
    play(last.text.includes('诊断完成') || last.text.includes('仿真恢复验证通过')
      ? 'happy' : last.text.includes('失败') ? 'tilt' : 'nod')
  }, [autodiag, showBubble, play])

  // ---- 拖动（桌面宠物）：按住拖到任意位置；位移 >6px 视为拖动，不触发点击 ----
  const onPointerDown = (e) => {
    if (e.button !== 0) return
    dragRef.current = { sx: e.clientX, sy: e.clientY, l: pos.left, t: pos.top, moved: false }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  const onPointerMove = (e) => {
    const d = dragRef.current
    if (!d) return
    const dx = e.clientX - d.sx
    const dy = e.clientY - d.sy
    if (!d.moved && Math.abs(dx) + Math.abs(dy) > 6) d.moved = true
    if (!d.moved) return
    const vw = window.innerWidth || 1920
    const vh = window.innerHeight || 1080
    setPos({
      left: Math.min(Math.max(d.l + dx, 4), vw - BOT_W - 4),
      top: Math.min(Math.max(d.t + dy, 4), vh - 120),
    })
  }
  const onPointerUp = () => {
    const d = dragRef.current
    dragRef.current = null
    if (d?.moved) {
      setPos((p) => {
        localStorage.setItem(POS_KEY, JSON.stringify(p))
        return p
      })
    } else {
      setChatOpen((o) => !o) // 原地松手 = 点击：开/合对话
    }
  }

  const diagSnapshot = getDiagnosisSnapshot(autodiag, operation, reports?.rows || [])
  const toneState = diagSnapshot.state === 'done'
    ? { label: '本次诊断完成', tone: 'done' }
    : diagSnapshot.state === 'running'
      ? { label: 'AGH 诊断中', tone: 'busy' }
      : { label: availability.label, tone: 'idle' }

  const send = useCallback(async () => {
    const text = input.trim()
    if (!text || thinking) return
    if (!chatReady) {
      showBubble(availability.detail, 8000)
      return
    }
    setInput('')
    const history = messages.slice(-6).map((m) => ({ role: m.role, content: m.content }))
    setMessages((ms) => [...ms, { role: 'user', content: text }])
    setThinking(true)
    try {
      const data = await postJson('/api/agent/chat', { run_id: 'global-agent', message: text, history })
      if (typeof data?.reply !== 'string' || !Array.isArray(data?.actions)) {
        throw new Error('AGH 返回格式无效，未执行对话操作。')
      }
      setMessages((ms) => [...ms, { role: 'assistant', content: data.reply }])
      play('happy')
      // 任务③对话即操作：前端派发 UI 类动作（切视图）；注入类后端已执行，这里补第一人称播报
      const acts = data.actions || []
      for (const a of acts) {
        if (a.type === 'switch_view' && a.view) {
          window.dispatchEvent(new CustomEvent('agent:view', { detail: a.view }))
        }
      }
      const inj = acts.find((x) => x.type === 'inject_fault' && x.ok)
      if (inj) {
        const cn = inj.fault === 'bpfo' ? '外圈（BPFO）' : '内圈（BPFI）'
        showBubble(`好的，已注入${cn}演示故障（强度${inj.severity}），我马上自主取证诊断！`, 8000)
        play('spin')
      } else if (acts.some((x) => x.type === 'recover_fault' && x.ok)) {
        showBubble('已恢复正常运行，我继续盯着数据～', 6000)
        play('happy')
      }
    } catch (error) {
      const detail = String(error?.message || 'AGH 智能体暂不可用，请检查运行时和模型配置。')
      setMessages((ms) => [...ms, { role: 'assistant', content: `（智能体对话失败：${detail}）` }])
      showBubble(`AGH 智能体暂不可用：${detail}`, 8000)
    } finally {
      setThinking(false)
    }
  }, [input, messages, thinking, play, showBubble, chatReady, availability.detail])

  // 对话面板位置：跟随形象（左出界/下出界时收进屏幕内）
  const chatStyle = {
    left: Math.max(8, Math.min(pos.left, (window.innerWidth || 1920) - 366)),
    top:
      pos.top > 520
        ? Math.max(8, pos.top - 482)
        : Math.min(pos.top + BOT_H + 10, (window.innerHeight || 1080) - 480),
  }

  return (
    <>
      {/* 咨询对话面板 */}
      {chatOpen && (
        <div className="ga-chat" style={chatStyle} onClick={(e) => e.stopPropagation()}>
          <div className="ga-chat-head">
            <div>
              <div className="ga-chat-title">{chatReady ? 'Agnes 智能体 · 已验证' : availability.label}</div>
              {!chatReady ? <div className="ga-chat-status">{availability.detail}</div> : null}
            </div>
            <span className="ga-chat-tools">
              <button type="button" className="ga-chat-tool" onClick={() => setChatOpen(false)}>
                收起
              </button>
            </span>
          </div>
          <div
            className="ga-chat-msgs"
            ref={(el) => {
              msgListRef.current = el
              if (el) el.scrollTop = el.scrollHeight
            }}
          >
            {messages.length === 0 && (
              <div className="ga-chat-empty">
                你好呀！我是这台调相机的守护小助手～<br />
                可以问我"驱动端轴承是什么"、"峭度是什么意思"这类问题。
              </div>
            )}
            {messages.map((m, i) => (
              <div key={i} className={`ga-msg is-${m.role}`}>
                {m.content}
              </div>
            ))}
            {thinking && (
              <div className="ga-msg is-assistant">
                <span className="ga-typing">
                  <i />
                  <i />
                  <i />
                </span>
              </div>
            )}
          </div>
          <div className="ga-chat-input">
            <input
              value={input}
              placeholder={chatReady ? '向 Agnes 智能体提问…' : '智能体当前不可用'}
              disabled={!chatReady || thinking}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') send()
              }}
              onClick={(e) => e.stopPropagation()}
            />
            <button type="button" onClick={send} disabled={!chatReady || thinking || !input.trim()}>
              发送
            </button>
          </div>
        </div>
      )}

      {/* 常驻形象本体（无框、可拖动的桌面宠物） */}
      <div
        data-phase={phase}
        className={`ga-bot is-${toneState.tone}${phase === 'ready' ? ' has-model' : ''}`}
        style={{ left: pos.left, top: pos.top }}
        title="拖动我换个位置 · 点我聊天"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        {bubble && <div className="ga-bubble">{bubble.text}</div>}
        <div className="ga-avatar">
          <span className="ga-bot-fallback" aria-hidden="true">
            🤖
          </span>
          {(phase === 'loading' || phase === 'ready') && (
            <AgentAvatar3D
              url={AGENT_AVATARS[agentIdx].url}
              yaw={AGENT_AVATARS[agentIdx].yaw || 0}
              action={action}
              onStatus={onAvatarStatus}
            />
          )}
          {phase === 'loading' && <div className="ga-avatar-loading">形象加载中…</div>}
        </div>
        <div className="ga-nameplate" aria-label={`智能体昵称：${nickname}`}>
          <span className="ga-name">{nickname}</span>
          <span className={`ga-state ga-state-${toneState.tone}`}>{toneState.label}</span>
        </div>
      </div>
    </>
  )
}
