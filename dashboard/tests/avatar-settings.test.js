import test from 'node:test'
import assert from 'node:assert/strict'
import {
  AGENT_AVATARS,
  DEFAULT_AGENT_NICKNAME,
  readAgentPreferences,
  writeAgentPreferences,
} from '../src/avatarSettings.js'

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
  }
}

test('avatar preferences safely normalize invalid stored values', () => {
  const preferences = readAgentPreferences(memoryStorage({
    agentAvatarIdx: '999',
    agentNickname: '   ',
  }))
  assert.equal(preferences.avatarIndex, 0)
  assert.equal(preferences.nickname, DEFAULT_AGENT_NICKNAME)
})

test('avatar preferences persist one shared nickname and selected appearance', () => {
  const storage = memoryStorage()
  writeAgentPreferences({ avatarIndex: AGENT_AVATARS.length - 1, nickname: '小调助手' }, storage)
  assert.deepEqual(readAgentPreferences(storage), { avatarIndex: AGENT_AVATARS.length - 1, nickname: '小调助手' })
  writeAgentPreferences({ avatarIndex: 2 }, storage)
  assert.deepEqual(readAgentPreferences(storage), { avatarIndex: 2, nickname: '小调助手' })
})
