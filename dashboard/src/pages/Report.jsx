import React, { useState } from 'react'
import { Alert, Card, Drawer, Empty, Table, Tag, Typography } from 'antd'
import { usePolling } from '../api.js'
import { getDiagnosisSnapshot } from '../agentState.js'

export default function Report() {
  const { data } = usePolling('/api/report/list', 5000)
  const { data: autodiag } = usePolling('/api/autodiag/status', 3000)
  const { data: operation } = usePolling('/api/operation/mode', 4000)
  const [current, setCurrent] = useState(null)
  const rows = data?.rows || []
  const snapshot = getDiagnosisSnapshot(autodiag, operation, rows)
  const isCurrentReport = (report) => snapshot.currentReport
    && String(snapshot.currentReport.id) === String(report.id)

  const archiveNotice = operation?.mode === 'manual'
    ? '当前为人工备用模式，列表中的报告均为历史记录；人工模式不会生成智能体诊断报告。'
    : '列表展示已归档报告。只有与后端当前自动诊断 run_id 对应的报告属于本次运行，其余均为历史记录。'

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '时间', dataIndex: 'ts', width: 170 },
    { title: 'run_id', dataIndex: 'run_id', width: 140 },
    {
      title: '记录属性', key: 'recordType', width: 150,
      render: (_, row) => <Tag color={isCurrentReport(row) ? 'green' : 'default'}>
        {isCurrentReport(row) ? '本次 AGH 诊断' : '历史记录'}
      </Tag>,
    },
    { title: '设备', dataIndex: 'equip', width: 90 },
    { title: '结论', dataIndex: 'conclusion' },
    {
      title: '置信度(自评)',
      dataIndex: 'confidence',
      width: 100,
      render: (v) => <Tag color="cyan">{(v * 100)?.toFixed(0)}%</Tag>,
    },
    {
      title: '操作',
      key: 'op',
      width: 90,
      render: (_, r) => (
        <a onClick={() => setCurrent(r)}>详情</a>
      ),
    },
  ]

  let pretty = ''
  if (current) {
    try {
      pretty = JSON.stringify(JSON.parse(current.evidence), null, 2)
    } catch {
      pretty = current.evidence
    }
  }

  return (
    <Card title="诊断报告存档" styles={{ body: { paddingTop: 16 } }}>
      <Alert
        type={snapshot.state === 'failed' || snapshot.state === 'unavailable' ? 'error' : operation?.mode === 'manual' ? 'warning' : 'info'}
        showIcon
        message={archiveNotice}
        description={snapshot.state === 'failed' ? `本次智能体诊断失败：${snapshot.message}` : undefined}
        style={{ marginBottom: 16 }}
      />
      <Table
        rowKey="id"
        columns={columns}
        dataSource={rows}
        pagination={{ pageSize: 10 }}
        size="middle"
        locale={{ emptyText: <Empty description="暂无已归档诊断报告" /> }}
      />
      <Drawer
        title={current ? `报告 #${current.id} · ${current.conclusion}` : ''}
        width={560}
        open={!!current}
        onClose={() => setCurrent(null)}
      >
        {current && (
          <>
            <Typography.Paragraph>
              <Typography.Text type="secondary">记录属性：</Typography.Text>
              <Tag color={isCurrentReport(current) ? 'green' : 'default'}>
                {isCurrentReport(current) ? '本次 AGH 诊断' : '历史记录（来源按原入库版本未验证）'}
              </Tag>
            </Typography.Paragraph>
            <Typography.Paragraph>
              <Typography.Text type="secondary">run_id：</Typography.Text>
              {current.run_id}
              <Typography.Text type="secondary" style={{ marginLeft: 16 }}>
                时间：
              </Typography.Text>
              {current.ts}
            </Typography.Paragraph>
            <Typography.Paragraph>
              <Typography.Text type="secondary">证据原始 JSON：</Typography.Text>
            </Typography.Paragraph>
            <pre
              style={{
                background: '#0c2036',
                border: '1px solid #1d3a5f',
                borderRadius: 6,
                padding: 12,
                fontSize: 12,
                maxHeight: 480,
                overflow: 'auto',
              }}
            >
              {pretty}
            </pre>
          </>
        )}
      </Drawer>
    </Card>
  )
}
