import React, { useMemo } from 'react'
import { Alert, Card, Col, Descriptions, Empty, Progress, Row, Space, Spin, Table, Tag, Timeline, Typography } from 'antd'
import { RobotOutlined } from '@ant-design/icons'
import { usePolling } from '../api.js'
import { getAgentAvailability, getDiagnosisSnapshot } from '../agentState.js'

const AVATAR = {
  loading: { color: '#7d95ad', label: '读取中', tag: 'default' },
  idle: { color: '#52c41a', label: '待命', tag: 'default' },
  running: { color: '#00c7ff', label: 'AGH 诊断中', tag: 'processing' },
  done: { color: '#52c41a', label: '本次诊断完成', tag: 'success' },
  // P0 问题2：只有报告、闭环或核验没达标时如实降级显示
  partial: { color: '#faad14', label: '仅完成部分闭环', tag: 'warning' },
  verification_failed: { color: '#fa8c16', label: '核验未通过', tag: 'warning' },
  failed: { color: '#ff4d4f', label: '诊断失败', tag: 'error' },
  manual: { color: '#faad14', label: '人工备用模式', tag: 'warning' },
  unavailable: { color: '#ff4d4f', label: '智能体不可用', tag: 'error' },
  disabled: { color: '#7d95ad', label: '自动诊断已关闭', tag: 'default' },
}

function evidenceSteps(raw) {
  try {
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : null
  } catch {
    return null
  }
}

export default function AgentDiagnosis() {
  const { data: reports } = usePolling('/api/report/list', 3000)
  const { data: tasks } = usePolling('/api/task/list?status=all', 5000)
  const { data: autodiag } = usePolling('/api/autodiag/status', 2000)
  const { data: operation } = usePolling('/api/operation/mode', 4000)
  const snapshot = getDiagnosisSnapshot(autodiag, operation, reports?.rows || [])
  const availability = getAgentAvailability(operation)
  const state = snapshot.state
  const currentReport = snapshot.currentReport
  const historyReport = snapshot.historicalReport
  const currentTask = autodiag?.task_id == null ? null
    : (tasks?.rows || []).find((task) => String(task.id) === String(autodiag.task_id)) || null

  const steps = useMemo(
    () => (currentReport ? evidenceSteps(currentReport.evidence) : null),
    [currentReport],
  )

  const personaText = state === 'done' && currentReport
    ? `诊断完成：${currentReport.conclusion}（自评置信度 ${(currentReport.confidence * 100).toFixed(0)}%）${currentTask ? `，工单 #${currentTask.id} 已生成。` : '。'}`
    : snapshot.message

  const taskColumns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '时间', dataIndex: 'ts', width: 170 },
    { title: '标题', dataIndex: 'title' },
    { title: '级别', dataIndex: 'level', width: 90, render: (l) => <Tag color={l === 'severe' ? 'red' : 'orange'}>{l === 'severe' ? '严重' : '注意'}</Tag> },
    { title: '来源', dataIndex: 'source', width: 90 },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (s) => <Tag color={s === 'open' ? 'processing' : 'green'}>{s === 'open' ? '待处理' : '已关闭'}</Tag>,
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card styles={{ body: { padding: 16 } }}>
        <Row gutter={16} align="middle">
          <Col flex="64px">
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: '50%',
                background: `radial-gradient(circle at 35% 30%, ${AVATAR[state]?.color || '#7d95ad'}cc, #0f2540 75%)`,
                border: `2px solid ${AVATAR[state]?.color || '#7d95ad'}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: state !== 'idle' ? `0 0 18px ${AVATAR[state]?.color || '#7d95ad'}66` : 'none',
              }}
            >
              <RobotOutlined style={{ fontSize: 26, color: '#e6f7ff' }} />
            </div>
          </Col>
          <Col flex="auto">
            <Space size={8} align="center">
              <Typography.Text strong style={{ color: '#00c7ff' }}>
                Agnes 诊断智能体
              </Typography.Text>
              <Tag color={AVATAR[state]?.tag || 'default'}>{AVATAR[state]?.label || '状态未知'}</Tag>
            </Space>
            <div style={{ marginTop: 4, fontSize: 15 }}>
              {personaText}
            </div>
            {state === 'unavailable' ? <Typography.Text type="secondary">当前状态：{availability.label}</Typography.Text> : null}
          </Col>
        </Row>
      </Card>

      {state === 'done' && currentReport ? (
        <Card title="诊断结论">
          <Descriptions column={2} size="small">
            <Descriptions.Item label="结论" span={2}>
              <Typography.Text strong style={{ fontSize: 16 }}>
                {currentReport.conclusion}
              </Typography.Text>
            </Descriptions.Item>
            <Descriptions.Item label="置信度（模型自评，未做统计校准）">
              <Progress percent={Math.round(currentReport.confidence * 100)} size="small" style={{ width: 160 }} />
            </Descriptions.Item>
            <Descriptions.Item label="run_id / 时间">
              {currentReport.run_id} · {currentReport.ts}
            </Descriptions.Item>
          </Descriptions>
        </Card>
      ) : null}

      {steps ? (
        <Card title="证据链（由报告 evidence_json 渲染）">
          <Timeline
            items={steps.map((s) => ({
              color: s.supports === false || s.supports === '排除' ? 'gray' : 'cyan',
              children: (
                <div>
                  <Typography.Text strong>
                    步骤 {s.step} · {s.tool}
                  </Typography.Text>
                  <div style={{ color: '#9fc6e8', fontSize: 13 }}>
                    关键数值：{typeof s.key_numbers === 'string' ? s.key_numbers : JSON.stringify(s.key_numbers)}
                  </div>
                  <div style={{ fontSize: 13 }}>
                    判定：
                    <Tag color={s.supports ? 'cyan' : 'default'}>
                      {typeof s.supports === 'boolean' ? (s.supports ? '支持假设' : '排除') : String(s.supports)}
                    </Tag>
                  </div>
                </div>
              ),
            }))}
          />
        </Card>
      ) : state === 'running' ? (
        <Card>
          <Spin tip="AGH 会话正在运行；证据链将在本次报告提交后展示…" />
        </Card>
      ) : (
        <Alert type={state === 'failed' || state === 'unavailable' ? 'error' : state === 'manual' ? 'warning' : 'info'}
          showIcon message={personaText} />
      )}

      {historyReport ? (
        <Card title={<Space>历史报告（不代表当前运行）<Tag color="default">历史</Tag></Space>}>
          <Typography.Text strong>{historyReport.conclusion}</Typography.Text>
          <div><Typography.Text type="secondary">{historyReport.run_id} · {historyReport.ts}</Typography.Text></div>
        </Card>
      ) : null}

      <Row gutter={16}>
        <Col span={12}>
          <Card title="运维任务">
            <Table rowKey="id" columns={taskColumns} dataSource={(tasks?.rows || []).slice(0, 5)} pagination={false} size="small" />
          </Card>
        </Col>
        <Col span={12}>
          <Card title="执行轨迹摘要">
            {steps ? (
              <Timeline
                items={steps.map((s) => ({
                  children: (
                    <Typography.Text style={{ fontSize: 13 }}>
                      调用 {s.tool}
                      {s.key_numbers ? '' : ''}
                    </Typography.Text>
                  ),
                }))}
              />
            ) : (
              <Empty description="尚无执行轨迹" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>
    </Space>
  )
}
