import React, { useMemo, useState } from 'react'
import { Alert, App as AntApp, Button, Card, Col, Row, Select, Slider, Space, Table, Tag, Typography } from 'antd'
import { fetchJson, usePolling, operatorHeaders, OperatorAuthError, LEVEL_COLOR, LEVEL_TEXT, THRESH } from '../api.js'
import { getSevereAlarmAgentStatus } from '../agentState.js'

const FAULT_LABEL = {
  none: '正常运行',
  bpfo: '外圈故障（BPFO）',
  bpfi: '内圈故障（BPFI）',
  // 4.1 扩展的机理：混合故障与两类非轴承故障（谱上可区分，见 tests/test_fault_physics.py）
  bpfo_bpfi: '混合故障（外圈+内圈）',
  unbalance: '不平衡（1X 占优）',
  looseness: '基础松动（2X/3X 谐波）',
  unknown: '未建模故障（宽频能量）',
}

function LevelTag({ level }) {
  return (
    <Tag color={LEVEL_COLOR[level]} style={{ marginInlineEnd: 0 }}>
      {LEVEL_TEXT[level]}
    </Tag>
  )
}

export default function Realtime() {
  const { message } = AntApp.useApp()
  const { data: device } = usePolling('/api/device', 3000)
  // 控制台凭据：登录后挂载本页时取一次，作为 usePolling 的稳定依赖（每次渲染新建对象会触发无限重取）
  const OP = useMemo(() => operatorHeaders(), [])
  // scenario 接口默认对无凭据调用脱敏 fault 真值（P0 防泄漏），写接口还需凭据才能改（P1 问题4）
  const { data: scenario, refetch } = usePolling('/api/scenario/get', 5000, OP)
  const { data: operation } = usePolling('/api/operation/mode', 4000)
  const { data: autodiag } = usePolling('/api/autodiag/status', 3000)
  const [faultType, setFaultType] = useState('bpfo')
  const [severity, setSeverity] = useState(0.8)
  const [busy, setBusy] = useState(false)

  const worst = useMemo(() => {
    const pts = device?.points || []
    const rank = { normal: 0, notice: 1, severe: 2 }
    return pts.reduce((w, p) => (rank[p.level] > rank[w] ? p.level : w), 'normal')
  }, [device])

  // 控制面写入统一处理失败：凭据失效要说清楚，其余报 HTTP 状态，不再静默失败
  const writeScenario = async (query) => {
    setBusy(true)
    try {
      await fetchJson(`/api/scenario/set?${query}`, OP)
      refetch()
      return true
    } catch (e) {
      message.error(e instanceof OperatorAuthError
        ? '控制台凭据已失效（服务可能已重启），请重新登录后再注入。'
        : `场景写入失败：${e.message || e}`)
      return false
    } finally {
      setBusy(false)
    }
  }

  const setKey = (key, value) => writeScenario(`key=${key}&value=${value}`)

  const inject = async () => {
    if (await writeScenario(`key=fault.type&value=${faultType}`)) {
      await writeScenario(`key=fault.severity&value=${severity}`)
    }
  }

  const recover = async () => {
    if (await writeScenario('key=fault.type&value=none')) {
      await writeScenario('key=fault.severity&value=0')
    }
  }

  const columns = [
    { title: '测点', dataIndex: 'name', key: 'name' },
    {
      title: 'RMS (mm/s)',
      dataIndex: ['current', 'rms'],
      render: (v) => (
        <Typography.Text style={{ color: v > THRESH.rms.severe ? '#ff4d4f' : v > THRESH.rms.notice ? '#faad14' : undefined }}>
          {v?.toFixed(3)}
        </Typography.Text>
      ),
    },
    {
      title: '峭度',
      dataIndex: ['current', 'kurtosis'],
      render: (v) => (
        <Typography.Text
          style={{
            color: v > THRESH.kurtosis.severe ? '#ff4d4f' : v > THRESH.kurtosis.notice ? '#faad14' : undefined,
          }}
        >
          {v?.toFixed(3)}
        </Typography.Text>
      ),
    },
    { title: '峰峰值 (mm/s)', dataIndex: ['current', 'pp'], render: (v) => v?.toFixed(3) },
    { title: '等级', dataIndex: 'level', render: (l) => <LevelTag level={l} /> },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {worst === 'severe' && (
        <Alert
          type="error"
          showIcon
          message="严重报警：振动特征持续越限"
          description={`RMS/峭度连续 3 点超过严重阈值。${getSevereAlarmAgentStatus(operation, autodiag)}`}
        />
      )}
      {scenario?.truth_redacted ? (
        <Alert type="error" showIcon
               message="控制台凭据已失效：当前显示的是脱敏视图，场景注入与关单都会被拒绝。请退出后重新登录。" />
      ) : null}
      {worst === 'notice' && (
        <Alert type="warning" showIcon message="注意级报警：振动特征连续 3 点超过注意阈值" />
      )}
      <Card title="测点特征值（后端独立采样，本页 3s 轮询只读快照）">
        <Table
          rowKey="name"
          columns={columns}
          dataSource={device?.points || []}
          pagination={false}
          size="middle"
        />
      </Card>
      <Card title="场景注入（手动仿真）">
        <Row gutter={24}>
          <Col span={8}>
            <Typography.Text type="secondary">当前场景：</Typography.Text>
            <Tag color={scenario?.fault?.type === 'none' ? 'green' : 'red'}>
              {FAULT_LABEL[scenario?.fault?.type] || '--'} · 强度 {scenario?.fault?.severity ?? '--'}
            </Tag>
          </Col>
          <Col span={8}>
            <Typography.Text type="secondary">故障类型</Typography.Text>
            <div>
              <Select
                value={faultType}
                onChange={setFaultType}
                style={{ width: '100%' }}
                options={[
                  { value: 'bpfo', label: '外圈故障（BPFO）' },
                  { value: 'bpfi', label: '内圈故障（BPFI）' },
                  { value: 'bpfo_bpfi', label: '混合故障（外圈+内圈）' },
                  { value: 'unbalance', label: '不平衡（1X 占优）' },
                  { value: 'looseness', label: '基础松动（2X/3X 谐波）' },
                  { value: 'unknown', label: '未建模故障（宽频能量）' },
                ]}
              />
            </div>
          </Col>
          <Col span={8}>
            <Typography.Text type="secondary">故障强度 {severity.toFixed(1)}</Typography.Text>
            <Slider min={0} max={1} step={0.1} value={severity} onChange={setSeverity} />
          </Col>
        </Row>
        <Space style={{ marginTop: 16 }}>
          <Button type="primary" loading={busy} onClick={inject}>
            注入故障
          </Button>
          <Button loading={busy} onClick={recover}>
            恢复正常
          </Button>
        </Space>
      </Card>
    </Space>
  )
}
