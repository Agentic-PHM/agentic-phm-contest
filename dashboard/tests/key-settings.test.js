import test from 'node:test'
import assert from 'node:assert/strict'
import { getKeyStatusView } from '../src/keySettingsState.js'

test('Key status uses only the configured boolean and never returns secret-shaped fields', () => {
  assert.deepEqual(getKeyStatusView({
    configured: true,
    profile: 'local-dev',
    api_key: 'must-not-render',
    value: 'must-not-render',
  }), { configured: true, label: 'Agnes Key 已配置' })
})

test('missing Key has a clear status without inspecting or exposing a value', () => {
  assert.deepEqual(getKeyStatusView({ configured: false, value: 'must-not-render' }), {
    configured: false,
    label: '尚未配置 Agnes Key',
  })
})

test('unavailable status endpoint is not mistaken for a missing Key', () => {
  assert.deepEqual(getKeyStatusView(null, true), {
    configured: null,
    label: '无法读取 AGH 凭据状态',
  })
})
