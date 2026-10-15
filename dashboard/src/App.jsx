import React, { useEffect, useMemo, useState } from 'react'
import { App as AntApp, Layout, Menu, Badge, Button, Space, Tag, Tooltip, Typography } from 'antd'
import {
  AppstoreOutlined,
  DashboardOutlined,
  LineChartOutlined,
  NodeIndexOutlined,
  RobotOutlined,
  AlertOutlined,
  FileTextOutlined,
  SettingOutlined,
  LogoutOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { postJson, fetchJson, usePolling, AUTH_KEY, clearAuth, operatorHeaders,
         OperatorAuthError, LEVEL_COLOR, LEVEL_TEXT } from './api.js'
import { getAgentAvailability, shouldRenderGlobalAgent } from './agentState.js'
import { SIMULATION_DISCLOSURE, SIMULATION_TAG } from './dataProvenance.js'
import Login from './pages/Login.jsx'
import Overview from './pages/Overview.jsx'
import Realtime from './pages/Realtime.jsx'
import Trend from './pages/Trend.jsx'
import Workflow from './pages/Workflow.jsx'
import AgentDiagnosis from './pages/AgentDiagnosis.jsx'
import AlarmManage from './pages/AlarmManage.jsx'
import Report from './pages/Report.jsx'
import Settings from './pages/Settings.jsx'
import GlobalAgent from './global-agent/GlobalAgent.jsx'

const { Sider, Content, Header } = Layout

/** 读取本地会话：存在 token 才视为已登录（字段缺失或 JSON 损坏一律按未登录处理）。
 *  注意：token **存在 ≠ 有效**——服务端会话在进程内存里，服务重启即失效；
 *  有效性由 App 里的凭据自检负责，不能只看本地有没有。 */
function readAuth() {
  try {
    const info = JSON.parse(localStorage.getItem(AUTH_KEY) || 'null')
    return info && info.token ? info : null
  } catch {
    return null
  }
}

const PAGES = {
  overview: { label: '设备总览', icon: <AppstoreOutlined />, node: <Overview /> },
  realtime: { label: '实时监视', icon: <DashboardOutlined />, node: <Realtime /> },
  trend: { label: '趋势分析', icon: <LineChartOutlined />, node: <Trend /> },
  workflow: { label: '智能体工作流', icon: <NodeIndexOutlined />, node: <Workflow /> },
  agent: { label: '专家诊断', icon: <RobotOutlined />, node: <AgentDiagnosis /> },
  alarm: { label: '预警管理', icon: <AlertOutlined />, node: <AlarmManage /> },
  report: { label: '诊断报告', icon: <FileTextOutlined />, node: <Report /> },
  settings: { label: '系统设置', icon: <SettingOutlined />, node: <Settings /> },
}

const RANK = { normal: 0, notice: 1, severe: 2 }

export default function App() {
  const [user, setUser] = useState(readAuth)
  const [loginNotice, setLoginNotice] = useState('')

  // 控制台凭据自检（P1 问题4）：服务端会话在进程内存里，**服务一重启就全部失效**。
  // 而本机仍存着旧 token，页面看起来"已登录"，但读场景会静默降级成脱敏视图、写场景 403。
  // 这里主动探测一次：确认失效就清掉本地会话并请用户重新登录，不留"看着正常却改不动"的状态。
  useEffect(() => {
    if (!user) return undefined
    let alive = true
    fetchJson('/api/scenario/get', operatorHeaders())
      .then((d) => {
        if (alive && d && d.truth_redacted) throw new OperatorAuthError()
      })
      .catch((e) => {
        if (alive && e instanceof OperatorAuthError) {
          clearAuth()
          setLoginNotice('控制台凭据已失效（服务可能已重启）。请重新登录后再操作系统场景与工单。')
          setUser(null)
        }
      })
    return () => {
      alive = false
    }
  }, [user])

  // 路由守卫：未登录整屏渲染登录页，登录成功后默认进入设备总览
  if (!user) return <Login onLoginSuccess={() => { setLoginNotice(''); setUser(readAuth()) }} notice={loginNotice} />

  const logout = () => {
    localStorage.removeItem(AUTH_KEY)
    setUser(null)
  }

  return (
    <MainPage user={user} onLogout={logout} />
  )
}

function MainPage({ user, onLogout }) {
  const { message, modal } = AntApp.useApp()
  const [current, setCurrent] = useState('overview')
  const [modeBusy, setModeBusy] = useState(false)
  const [modeError, setModeError] = useState('')
  const { data: device } = usePolling('/api/device', 5000)
  const operation = usePolling('/api/operation/mode', 4000)
  const operationState = operation.error ? null : operation.data
  const agentStatus = operation.error
    ? { code: 'status_unavailable', ready: false, label: 'AGH 状态读取失败', detail: '当前无法读取运行模式；智能体界面已暂时关闭。' }
    : getAgentAvailability(operationState)

  const changeMode = async (mode) => {
    setModeBusy(true)
    setModeError('')
    try {
      await postJson('/api/operation/mode', { mode })
      operation.refetch()
      message.success(mode === 'manual'
        ? '已进入人工备用模式，吉祥物、聊天与主动播报已关闭；监测与报警继续运行。'
        : 'AGH 与 Agnes 已验证，智能体模式已恢复。')
    } catch (error) {
      setModeError(error.message || '运行模式切换失败。')
      operation.refetch()
    } finally {
      setModeBusy(false)
    }
  }

  const requestModeChange = (mode) => {
    const enteringManual = mode === 'manual'
    modal.confirm({
      title: enteringManual ? '进入人工备用模式？' : '切回智能体模式？',
      content: enteringManual
        ? '吉祥物、聊天、主动播报与自主诊断将暂停；基础监测和阈值报警继续运行，旧报告仅作为历史记录。'
        : '系统会先验证 AGH 运行时和 Agnes 模型。验证通过后才恢复智能体功能。',
      okText: enteringManual ? '进入人工模式' : '验证并恢复',
      cancelText: '取消',
      onOk: () => changeMode(mode),
    })
  }

  // 跨页导航联动：首页“AI 工作流”入口通过 window 派发 nav 事件（detail 为页面 key）跳转对应页
  useEffect(() => {
    const onNav = (e) => {
      if (e?.detail && PAGES[e.detail]) setCurrent(e.detail)
    }
    window.addEventListener('nav', onNav)
    return () => window.removeEventListener('nav', onNav)
  }, [])

  const worst = useMemo(() => {
    const pts = device?.points || []
    return pts.reduce((w, p) => (RANK[p.level] > RANK[w] ? p.level : w), 'normal')
  }, [device])

  return (
    <>
    <Layout style={{ height: '100%' }}>
      <Sider width={200} style={{ borderRight: '1px solid #16304f' }}>
        <div
          style={{
            height: 56,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            gap: 8,
            borderBottom: '1px solid #16304f',
          }}
        >
          <DashboardOutlined style={{ color: '#00c7ff', fontSize: 20 }} />
          <Typography.Text strong style={{ color: '#00c7ff', fontSize: 15 }}>
            调相机智能运维平台
          </Typography.Text>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[current]}
          onClick={(e) => setCurrent(e.key)}
          items={Object.entries(PAGES).map(([key, p]) => ({ key, icon: p.icon, label: p.label }))}
          style={{ borderInlineEnd: 'none', paddingTop: 8 }}
        />
      </Sider>
      <Layout>
        <Header
          style={{
            height: 56,
            lineHeight: '56px',
            background: '#0c2036',
            borderBottom: '1px solid #16304f',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingInline: 24,
          }}
        >
          <Space size={10} align="center">
            <Typography.Text strong style={{ fontSize: 16 }}>
              {PAGES[current].label}
            </Typography.Text>
            {/* P2 数据口径：仿真数据标识必须常驻每一页，避免被误读为现场测量值 */}
            <Tooltip title={SIMULATION_DISCLOSURE}>
              <Tag color="purple" style={{ marginInlineEnd: 0 }} aria-label={SIMULATION_DISCLOSURE}>
                {SIMULATION_TAG}
              </Tag>
            </Tooltip>
          </Space>
          <Space size={10}>
            <Typography.Text type="secondary">
              {device?.name || '--'} 运行状态
            </Typography.Text>
            <Badge
              status={worst === 'normal' ? 'success' : worst === 'notice' ? 'warning' : 'error'}
              text={
                <Tag style={{ marginInlineEnd: 0 }} color={LEVEL_COLOR[worst]}>
                  {LEVEL_TEXT[worst]}
                </Tag>
              }
            />
            <Tag color={agentStatus.ready ? 'green' : agentStatus.code === 'manual' ? 'orange' : 'red'} title={agentStatus.detail}>
              {agentStatus.label}
            </Tag>
            <Button
              size="small"
              danger={operationState?.mode !== 'manual'}
              type={operationState?.mode === 'manual' ? 'primary' : 'default'}
              loading={modeBusy}
              disabled={modeBusy || !operationState || (operationState?.mode === 'manual' &&
                (!operationState?.agh_available || !operationState?.key_configured))}
              onClick={() => requestModeChange(operationState?.mode === 'manual' ? 'agent' : 'manual')}
            >
              {operationState?.mode === 'manual' ? '切回智能体模式' : '进入人工备用模式'}
            </Button>
            <Typography.Text type="secondary">
              <UserOutlined style={{ marginInlineEnd: 6 }} />
              {user.name || '操作员'}
            </Typography.Text>
            <Button
              type="text"
              size="small"
              icon={<LogoutOutlined />}
              onClick={() => {
                onLogout()
                message.success('已退出登录')
              }}
            >
              退出
            </Button>
            {modeError ? <Typography.Text type="danger">{modeError}</Typography.Text> : null}
          </Space>
        </Header>
        <Content style={{ padding: 16, overflow: 'auto' }}>{PAGES[current].node}</Content>
      </Layout>
    </Layout>
      {shouldRenderGlobalAgent(operation.data, operation.error) ? <GlobalAgent operation={operation.data} /> : null}
    </>
  )
}
