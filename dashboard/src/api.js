import { useEffect, useState } from 'react'

/** 本机会话键（与 App.jsx 共用）。 */
export const AUTH_KEY = 'phmUserInfo'

/** 清掉本机会话：控制台凭据被服务端否定时用（P1 问题4 的会话是服务端进程内存，
 *  服务一重启就全部失效，浏览器里的旧 token 会静默降级成"智能体视角"）。 */
export function clearAuth() {
  try {
    localStorage.removeItem(AUTH_KEY)
  } catch {
    /* 无 localStorage（如 SSR/测试）时忽略 */
  }
}

/** 控制台凭据被拒（403）或已被服务端遗忘。 */
export class OperatorAuthError extends Error {
  constructor(message) {
    super(message || '控制台凭据已失效，请重新登录。')
    this.name = 'OperatorAuthError'
  }
}

/** 控制台会话凭据（P1 问题4）：控制面写接口与完整场景视图需要它。
 *
 * 此前是固定字符串 `X-Operator: console`——它同样打在前端构建产物里，任何能发 HTTP 的人
 * （包括诊断智能体加一个 `-H`）都能伪造，等于没有边界。现在发的是登录签发的会话 token，
 * 只存在于服务端内存与本机 localStorage，每次请求现取（登录后自动生效）。 */
export function operatorHeaders() {
  try {
    const info = JSON.parse(localStorage.getItem(AUTH_KEY) || 'null')
    return info && info.token ? { 'X-Operator-Token': info.token } : {}
  } catch {
    return {}
  }
}

/** GET 请求封装（headers 可选，如控制台会话凭据）。 */
export async function fetchJson(path, headers) {
  const r = await fetch(path, { headers })
  if (!r.ok) {
    // 带控制台凭据却被拒 → 会话已失效（服务重启），抛可识别的错误让上层提示重新登录
    if (r.status === 403 && headers && headers['X-Operator-Token']) {
      throw new OperatorAuthError()
    }
    throw new Error(`HTTP ${r.status}`)
  }
  return r.json()
}

/** POST 请求封装（登录等需提交 body 的接口）：失败时优先抛后端 detail 文案；后端不可达时抛固定提示。 */
export async function postJson(path, body) {
  let r
  try {
    r = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error('后端服务未启动')
  }
  if (!r.ok) {
    let detail = `HTTP ${r.status}`
    try {
      const d = await r.json()
      if (d && typeof d.detail === 'string') detail = d.detail
    } catch {
      /* 非 JSON 响应体，保留 HTTP 状态码提示 */
    }
    throw new Error(detail)
  }
  return r.json()
}

/** 轮询 Hook：设备/趋势/报告等统一 5s（个别页面 3s）；refetch() 立即拉取一次。headers 可选（操作台令牌）。 */
export function usePolling(path, ms = 5000, headers) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [tick, setTick] = useState(0)

  useEffect(() => {
    let alive = true
    const run = async () => {
      try {
        const d = await fetchJson(path, headers)
        if (alive) {
          setData(d)
          setError(null)
        }
      } catch (e) {
        if (alive) setError(String(e.message || e))
      }
    }
    run()
    const id = setInterval(run, ms)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [path, ms, tick, headers])

  return { data, error, refetch: () => setTick((t) => t + 1) }
}

export const LEVEL_COLOR = { normal: '#52c41a', notice: '#faad14', severe: '#ff4d4f' }
export const LEVEL_TEXT = { normal: '正常', notice: '注意', severe: '严重' }

export const THRESH = {
  rms: { notice: 4.5, severe: 7.1 },
  kurtosis: { notice: 4.0, severe: 6.0 },
  pp: {},
}
