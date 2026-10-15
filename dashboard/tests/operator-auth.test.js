import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join, relative } from 'node:path'

/**
 * P1 问题4 回归：控制面凭据必须是登录签发的会话 token。
 *
 * 此前前端发固定字符串 `X-Operator: console`——它同样打在前端构建产物里，任何能发 HTTP 的人
 * （包括诊断智能体加一个 -H）都能伪造，既不是凭据也谈不上边界。现在只发 X-Operator-Token。
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? walk(full) : [full]
  })
}

const SOURCES = walk(SRC).filter((f) => /\.(js|jsx)$/.test(f))

test('P1-4：前端不再发送可伪造的静态控制台令牌', () => {
  const hits = []
  for (const file of SOURCES) {
    const text = readFileSync(file, 'utf8')
    if (/X-Operator['"]?\s*:\s*['"]console['"]/.test(text) || /['"]X-Operator['"]/.test(text)) {
      hits.push(relative(SRC, file))
    }
  }
  assert.deepEqual(hits, [], `仍有文件在使用静态 X-Operator 令牌：${hits.join('、')}`)
})

test('P1-4：控制台请求头来自登录会话 token', async () => {
  const store = { phmUserInfo: JSON.stringify({ token: 'session-token-abc', name: '管理员' }) }
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
  }
  const { operatorHeaders } = await import('../src/api.js')
  assert.deepEqual(operatorHeaders(), { 'X-Operator-Token': 'session-token-abc' })
  // 未登录（或会话损坏）时不给任何凭据，而不是退回固定值
  delete store.phmUserInfo
  assert.deepEqual(operatorHeaders(), {})
  store.phmUserInfo = 'not-json'
  assert.deepEqual(operatorHeaders(), {})
})

// ---- 服务重启会让服务端内存里的会话全部失效，此时旧 token 会静默降级成"智能体视角" ----

test('P1-4：带凭据却 403 时抛可识别的 OperatorAuthError', async () => {
  const { fetchJson, OperatorAuthError } = await import('../src/api.js')
  globalThis.fetch = async () => ({ ok: false, status: 403, json: async () => ({}) })
  await assert.rejects(
    () => fetchJson('/api/scenario/set?key=fault.type&value=none', { 'X-Operator-Token': 'stale' }),
    (e) => e instanceof OperatorAuthError)
  // 不带凭据的 403 仍按普通 HTTP 错误处理，不要误报成"凭据失效"
  await assert.rejects(() => fetchJson('/api/x'), (e) => !(e instanceof OperatorAuthError))
})

test('P1-4：clearAuth 只清本机会话，不动别的键', async () => {
  const store = { phmUserInfo: 'x', phmLoginName: 'admin' }
  globalThis.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
  }
  const { clearAuth } = await import('../src/api.js')
  clearAuth()
  assert.equal(store.phmUserInfo, undefined)
  assert.equal(store.phmLoginName, 'admin')
})

test('P1-4：凭据失效必须自行暴露，不得"看着已登录却改不动"', () => {
  const app = readFileSync(join(SRC, 'App.jsx'), 'utf8')
  assert.match(app, /truth_redacted/, 'App 必须探测凭据是否仍然有效')
  assert.match(app, /clearAuth/, '失效后要清掉本机会话并请用户重新登录')
  assert.match(app, /OperatorAuthError/)

  const realtime = readFileSync(join(SRC, 'pages', 'Realtime.jsx'), 'utf8')
  assert.match(realtime, /OperatorAuthError/, '场景写入失败必须给用户可见提示，不得静默失败')
  assert.match(realtime, /truth_redacted/, '脱敏视图必须在界面上标明')
  assert.match(realtime, /message\.error/, '写失败要有错误提示')
})
