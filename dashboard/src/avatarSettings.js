export const AGENT_AVATARS = [
  { id: 'agent-1', name: '卡通机器人', url: '/agents/agent-1.glb', zoom: 1.6 },
  { id: 'agent-2', name: '粉色机器人', url: '/agents/agent-2.glb', zoom: 1.6 },
  { id: 'agent-3', name: '小机器人·壹', url: '/agents/agent-3.glb', zoom: 1.6 },
  { id: 'agent-4', name: '小机器人·贰', url: '/agents/agent-4.glb', zoom: 1.6 },
  { id: 'agent-5', name: '圆滚滚·壹', url: '/agents/agent-5.glb', yaw: 270, zoom: 1.6 },
  { id: 'agent-6', name: '圆滚滚·贰', url: '/agents/agent-6.glb', yaw: 270, zoom: 1.6 },
  { id: 'agent-7', name: '小机器人·叁', url: '/agents/agent-7.glb', yaw: 270, zoom: 3.5 },
]

export const AGENT_AVATAR_KEY = 'agentAvatarIdx'
export const AGENT_NICKNAME_KEY = 'agentNickname'
export const AGENT_PREFERENCES_EVENT = 'agent:preferences-updated'
export const DEFAULT_AGENT_NICKNAME = '调相助手'
const MAX_NICKNAME_LENGTH = 20

function browserStorage() {
  return typeof localStorage === 'undefined' ? null : localStorage
}

function normalizeAvatarIndex(value) {
  const index = Number.parseInt(value, 10)
  return Number.isInteger(index) && index >= 0 && index < AGENT_AVATARS.length ? index : 0
}

function normalizeNickname(value) {
  if (typeof value !== 'string') return DEFAULT_AGENT_NICKNAME
  const nickname = value.trim().slice(0, MAX_NICKNAME_LENGTH)
  return nickname || DEFAULT_AGENT_NICKNAME
}

export function readAgentPreferences(storage = browserStorage()) {
  if (!storage) return { avatarIndex: 0, nickname: DEFAULT_AGENT_NICKNAME }
  return {
    avatarIndex: normalizeAvatarIndex(storage.getItem(AGENT_AVATAR_KEY)),
    nickname: normalizeNickname(storage.getItem(AGENT_NICKNAME_KEY)),
  }
}

export function writeAgentPreferences(updates, storage = browserStorage()) {
  const current = readAgentPreferences(storage)
  const next = {
    avatarIndex: Object.hasOwn(updates, 'avatarIndex') ? normalizeAvatarIndex(updates.avatarIndex) : current.avatarIndex,
    nickname: Object.hasOwn(updates, 'nickname') ? normalizeNickname(updates.nickname) : current.nickname,
  }
  if (!storage) return next

  storage.setItem(AGENT_AVATAR_KEY, String(next.avatarIndex))
  storage.setItem(AGENT_NICKNAME_KEY, next.nickname)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AGENT_PREFERENCES_EVENT))
  }
  return next
}
