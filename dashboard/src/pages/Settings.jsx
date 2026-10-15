import React, { useEffect, useState } from 'react'
import { Alert, App as AntApp, Button, Card, Input, Space, Tag, Typography } from 'antd'
import { CheckCircleOutlined, KeyOutlined, RobotOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { postJson, usePolling } from '../api.js'
import { getAgentAvailability } from '../agentState.js'
import {
  AGENT_AVATARS, AGENT_PREFERENCES_EVENT, DEFAULT_AGENT_NICKNAME,
  readAgentPreferences, writeAgentPreferences,
} from '../avatarSettings.js'
import { getKeyStatusView } from '../keySettingsState.js'
import AgentAvatar3D from './overview/stage3d/AgentAvatar3D.jsx'
import './settings.css'

export default function Settings() {
  const { message } = AntApp.useApp()
  const keyStatus = usePolling('/api/settings/agnes-key', 5000)
  const operation = usePolling('/api/operation/mode', 4000)
  const [apiKey, setApiKey] = useState('')
  const [saving, setSaving] = useState(false)
  const [probing, setProbing] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [probeResult, setProbeResult] = useState(null)
  const [keyJustSaved, setKeyJustSaved] = useState(false)
  const [agentPreferences, setAgentPreferences] = useState(readAgentPreferences)
  const [nicknameDraft, setNicknameDraft] = useState(agentPreferences.nickname)
  const keyView = getKeyStatusView(keyStatus.data, Boolean(keyStatus.error))
  const agentStatus = getAgentAvailability(operation.data)
  const canProbe = Boolean(keyView.configured && operation.data?.agh_available && !probing && !saving)
  const displayedAgentStatus = keyJustSaved || probeResult?.ok === false
    ? { ...agentStatus, ready: false, label: keyJustSaved ? 'Agnes Key 已更新，待重新验证' : 'Agnes 模型验证失败' }
    : agentStatus

  useEffect(() => {
    const refreshPreferences = () => {
      const next = readAgentPreferences()
      setAgentPreferences(next)
      setNicknameDraft(next.nickname)
    }
    window.addEventListener(AGENT_PREFERENCES_EVENT, refreshPreferences)
    window.addEventListener('storage', refreshPreferences)
    return () => {
      window.removeEventListener(AGENT_PREFERENCES_EVENT, refreshPreferences)
      window.removeEventListener('storage', refreshPreferences)
    }
  }, [])

  const saveKey = async () => {
    const submittedKey = apiKey.trim()
    if (!submittedKey || saving) return
    setSaving(true)
    setSaveError('')
    setProbeResult(null)
    try {
      await postJson('/api/settings/agnes-key', { api_key: submittedKey })
      setApiKey('')
      setKeyJustSaved(true)
      keyStatus.refetch()
      operation.refetch()
      message.success('Agnes Key 已保存到本机 AGH 凭据库。')
    } catch (error) {
      setSaveError(error.message || '无法安全保存 Agnes API Key。')
    } finally {
      setSaving(false)
    }
  }

  const probeProvider = async () => {
    if (!canProbe) return
    setProbing(true)
    setKeyJustSaved(false)
    setProbeResult(null)
    try {
      const result = await postJson('/api/settings/agnes-key/probe', {})
      setProbeResult({ ok: true, message: result.message || 'Agnes 模型连接验证成功。' })
      operation.refetch()
      message.success('Agnes 模型连接验证成功。')
    } catch (error) {
      setProbeResult({ ok: false, message: error.message || 'AGH 连接验证失败。' })
      operation.refetch()
    } finally {
      setProbing(false)
    }
  }

  const selectAvatar = (avatarIndex) => {
    const next = writeAgentPreferences({ avatarIndex })
    setAgentPreferences(next)
    message.success(`已切换为${AGENT_AVATARS[avatarIndex].name}。`)
  }

  const saveNickname = () => {
    const nickname = nicknameDraft.trim()
    if (!nickname) {
      setNicknameDraft(DEFAULT_AGENT_NICKNAME)
      return
    }
    const next = writeAgentPreferences({ nickname })
    setAgentPreferences(next)
    setNicknameDraft(next.nickname)
    message.success('智能体昵称已保存。')
  }

  const providerMessage = probeResult?.message || (
    keyJustSaved ? 'Key 已保存；请重新验证连接后再启用智能体模式。'
      : operation.data?.provider_ready ? 'AGH 运行时与 Agnes 模型已验证。若当前处于人工备用模式，请在顶部明确切回智能体模式。'
        : operation.data?.provider_status === 'unavailable' ? '最近一次 Agnes 模型验证未通过，请检查配置后重试。'
        : agentStatus.code === 'runtime_offline' ? agentStatus.detail
          : keyView.configured ? '尚未验证 AGH 与 Agnes 模型连接。'
            : '保存 Key 后即可进行连接验证。'
  )

  return (
    <div className="settings-page">
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card className="settings-intro">
          <Space align="start" size={14}>
            <SafetyCertificateOutlined className="settings-hero-icon" />
            <div>
              <Typography.Title level={4} style={{ marginTop: 0 }}>AGH 与 Agnes 模型设置</Typography.Title>
              <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
                Key 仅保存在本机当前 AGH profile 的凭据库中。页面不会回显 Key，也不会保存到浏览器存储。
              </Typography.Paragraph>
            </div>
          </Space>
        </Card>

        <Card title={<Space><KeyOutlined />Agnes API Key</Space>}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="settings-key-status">
              <Typography.Text type="secondary">本机配置状态</Typography.Text>
              <Tag color={keyView.configured ? 'green' : keyView.configured === false ? 'orange' : 'red'}>
                {keyView.label}
              </Tag>
            </div>
            <Input.Password
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              onPressEnter={saveKey}
              placeholder="输入 Agnes API Key"
              autoComplete="new-password"
              visibilityToggle={false}
              maxLength={8192}
              disabled={saving}
            />
            {saveError ? <Alert type="error" showIcon message={saveError} /> : null}
            <div className="settings-actions">
              <Button
                type="primary"
                icon={<KeyOutlined />}
                loading={saving}
                disabled={!apiKey.trim() || saving}
                onClick={saveKey}
              >
                保存到本机
              </Button>
              <Typography.Text type="secondary">
                保存后输入框会清空。若 AGH 仍使用旧凭据，请重启 AGH serve 后再次验证。
              </Typography.Text>
            </div>
          </Space>
        </Card>

        <Card title={<Space><CheckCircleOutlined />模型连接验证</Space>}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="settings-key-status">
              <Typography.Text type="secondary">AGH 状态</Typography.Text>
              <Tag color={displayedAgentStatus.ready ? 'green' : displayedAgentStatus.code === 'manual' ? 'orange' : 'red'}>
                {displayedAgentStatus.label}
              </Tag>
            </div>
            <Alert
              type={probeResult?.ok || (!probeResult && operation.data?.provider_ready) ? 'success' : probeResult?.ok === false || operation.data?.provider_status === 'unavailable' ? 'error' : 'info'}
              showIcon
              message={providerMessage}
            />
            <div className="settings-actions">
              <Button
                icon={<CheckCircleOutlined />}
                loading={probing}
                disabled={!canProbe}
                onClick={probeProvider}
              >
                验证 Agnes 连接
              </Button>
              {!operation.data?.agh_available
                ? <Typography.Text type="secondary">AGH 运行时在线后才能验证模型。</Typography.Text>
                : !keyView.configured
                  ? <Typography.Text type="secondary">请先保存 Agnes API Key。</Typography.Text>
                  : null}
            </div>
          </Space>
        </Card>

        <Card title={<Space><RobotOutlined />智能体形象与昵称</Space>}>
          <Typography.Paragraph type="secondary">
            形象选择与昵称会保存在当前浏览器；昵称对所有形象共用，选择后立即生效。
          </Typography.Paragraph>
          <div className="settings-avatar-layout">
            <div className="settings-avatar-preview">
              <AgentAvatar3D
                url={AGENT_AVATARS[agentPreferences.avatarIndex].url}
                yaw={AGENT_AVATARS[agentPreferences.avatarIndex].yaw || 0}
                spin
              />
              <Typography.Text strong>{AGENT_AVATARS[agentPreferences.avatarIndex].name}</Typography.Text>
            </div>
            <Space direction="vertical" size={16} className="settings-avatar-controls">
              <div>
                <Typography.Text strong>选择形象</Typography.Text>
                <div className="settings-avatar-options">
                  {AGENT_AVATARS.map((avatar, index) => (
                    <Button
                      key={avatar.id}
                      type={agentPreferences.avatarIndex === index ? 'primary' : 'default'}
                      aria-pressed={agentPreferences.avatarIndex === index}
                      onClick={() => selectAvatar(index)}
                    >
                      {avatar.name}
                    </Button>
                  ))}
                </div>
              </div>
              <div>
                <Typography.Text strong>共用昵称</Typography.Text>
                <Space.Compact style={{ display: 'flex', marginTop: 8 }}>
                  <Input
                    value={nicknameDraft}
                    onChange={(event) => setNicknameDraft(event.target.value)}
                    onPressEnter={saveNickname}
                    placeholder={DEFAULT_AGENT_NICKNAME}
                    maxLength={20}
                    showCount
                    aria-label="智能体共用昵称"
                  />
                  <Button type="primary" onClick={saveNickname} disabled={!nicknameDraft.trim()}>
                    保存昵称
                  </Button>
                </Space.Compact>
              </div>
            </Space>
          </div>
        </Card>
      </Space>
    </div>
  )
}
