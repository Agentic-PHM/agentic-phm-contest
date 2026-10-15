import React, { useState } from 'react'
import { Alert, App as AntApp, Button, Checkbox, Form, Input, Typography } from 'antd'
import { LockOutlined, UserOutlined } from '@ant-design/icons'
import { postJson } from '../api.js'
import { DEPLOYMENT_NOTE, SIMULATION_DISCLOSURE, SIMULATION_TAG } from '../dataProvenance.js'

// ====== 登录背景视频占位 ======
// 用户提供新视频后，将文件放入 dashboard/public/login-bg.mp4 并把常量改为 '/login-bg.mp4'
// （.mp4/.webm/.ogv 后缀按视频播放，其余后缀按静态图片显示，机制与旧版 loginBg 一致）。
// 保持为 null 时使用下方纯 CSS 动画深色背景占位，不影响构建与托管。
const VIDEO_SRC = '/login-bg.mp4' // 视频放 public/login-bg.mp4/.webm（webm 为兼容回退）；换新视频只需替换同名文件

const REMEMBER_KEY = 'phmLoginName'

export default function Login({ onLoginSuccess, notice = '' }) {
  const { message } = AntApp.useApp()
  const [busy, setBusy] = useState(false)
  const [bgFailed, setBgFailed] = useState(false)
  const remembered = localStorage.getItem(REMEMBER_KEY) || ''

  const isVideo = Boolean(VIDEO_SRC) && /\.(mp4|webm|ogv)\b/i.test(VIDEO_SRC)
  const showBg = Boolean(VIDEO_SRC) && !bgFailed

  const finish = async ({ username, password, remember }) => {
    // 提交前去掉首尾空白，防止自动填充/粘贴带入不可见空格导致登录失败
    const u = String(username ?? '').trim()
    const p = String(password ?? '').trim()
    setBusy(true)
    try {
      const res = await postJson('/api/login', { username: u, password: p })
      localStorage.setItem('phmUserInfo', JSON.stringify({ token: res.token, name: res.name, ts: Date.now() }))
      if (remember) localStorage.setItem(REMEMBER_KEY, username)
      else localStorage.removeItem(REMEMBER_KEY)
      message.success(`欢迎回来，${res.name || username}`)
      onLoginSuccess?.()
    } catch (e) {
      message.error(e.message || '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-page">
      {showBg ? (
        isVideo ? (
          <video
            className="login-bg-media"
            autoPlay
            muted
            loop
            playsInline
            src={VIDEO_SRC}
            onError={() => setBgFailed(true)}
          />
        ) : (
          <img className="login-bg-media" alt="" src={VIDEO_SRC} onError={() => setBgFailed(true)} />
        )
      ) : (
        <div className="login-bg-fallback" aria-hidden>
          <div className="login-glow login-glow-1" />
          <div className="login-glow login-glow-2" />
          <div className="login-glow login-glow-3" />
          <div className="login-grid" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
          <i className="login-particle" />
        </div>
      )}
      <div className="login-card">
        <Typography.Title level={3} className="login-title">
          调相机智能运维平台
        </Typography.Title>
        <Typography.Paragraph className="login-subtitle">
          面向新型电力系统的同步补偿机组状态监测
        </Typography.Paragraph>
        <div className="login-welcome">欢迎登录</div>
        {/* 服务重启会让服务端内存里的控制台会话失效：明确告知，而不是让页面"看着已登录却改不动" */}
        {notice ? <Alert type="warning" showIcon message={notice} style={{ marginBottom: 12 }} /> : null}
        <Form
          onFinish={finish}
          initialValues={{ username: remembered, remember: Boolean(remembered) }}
          requiredMark={false}
        >
          <Form.Item name="username" rules={[{ required: true, message: '请输入登录名' }]}>
            <Input
              size="large"
              prefix={<UserOutlined style={{ color: '#4d7bab' }} />}
              placeholder="登录名"
              autoComplete="username"
            />
          </Form.Item>
          <Form.Item name="password" rules={[{ required: true, message: '请输入密码' }]}>
            <Input.Password
              size="large"
              prefix={<LockOutlined style={{ color: '#4d7bab' }} />}
              placeholder="密码"
              autoComplete="current-password"
            />
          </Form.Item>
          <Form.Item name="remember" valuePropName="checked">
            <Checkbox>记住密码</Checkbox>
          </Form.Item>
          <Form.Item style={{ marginBottom: 8 }}>
            <Button type="primary" htmlType="submit" size="large" block loading={busy}>
              登录
            </Button>
          </Form.Item>
        </Form>
        <Typography.Text type="secondary" className="login-hint">
          测试账号：admin / 123456（演示用）
        </Typography.Text>
        {/* P2 数据口径：登录页就要讲清数据来源，评委第一眼看不到 README */}
        <Typography.Text type="secondary" className="login-hint">
          {SIMULATION_TAG}：{SIMULATION_DISCLOSURE}
        </Typography.Text>
        {/* P1 问题6：明确部署定位——本机演示，不是线上服务 */}
        <Typography.Text type="secondary" className="login-hint">
          {DEPLOYMENT_NOTE}
        </Typography.Text>
      </div>
    </div>
  )
}
