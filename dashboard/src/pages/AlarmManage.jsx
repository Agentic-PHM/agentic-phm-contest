import React, { useMemo, useState } from 'react'
import { Button, Card, Table, Tag, Typography } from 'antd'
import { usePolling } from '../api.js'

export default function AlarmManage() {
  const { data } = usePolling('/api/alarm/history?equip=P-101', 5000)
  const [acked, setAcked] = useState(() => new Set())
  const [muted, setMuted] = useState(() => new Set())

  const columns = useMemo(
    () => [
      { title: '时间', dataIndex: 'ts', width: 180 },
      {
        title: '等级',
        dataIndex: 'level',
        width: 90,
        render: (l) => <Tag color={l === 'severe' ? 'red' : 'orange'}>{l === 'severe' ? '严重' : '注意'}</Tag>,
      },
      { title: '指标', dataIndex: 'kpi', width: 100 },
      { title: '触发值', dataIndex: 'value', width: 110, render: (v) => v?.toFixed(2) },
      { title: '描述', dataIndex: 'msg' },
      {
        title: '操作',
        key: 'op',
        width: 160,
        render: (_, r) => {
          const id = `${r.ts}|${r.kpi}|${r.value}`
          return (
            <span>
              <Button
                size="small"
                type={acked.has(id) ? 'default' : 'primary'}
                disabled={acked.has(id)}
                onClick={() => setAcked((s) => new Set(s).add(id))}
              >
                {acked.has(id) ? '已确认' : '确认'}
              </Button>
              <Button
                size="small"
                style={{ marginLeft: 8 }}
                danger={!muted.has(id)}
                disabled={muted.has(id)}
                onClick={() => setMuted((s) => new Set(s).add(id))}
              >
                {muted.has(id) ? '已抑制' : '抑制'}
              </Button>
            </span>
          )
        },
      },
    ],
    [acked, muted],
  )

  return (
    <Card
      title="预警记录（确认/抑制为本地操作状态，仅当前页面生效）"
      extra={
        <Typography.Text type="secondary">共 {data?.rows?.length || 0} 条</Typography.Text>
      }
    >
      <Table rowKey={(r) => `${r.ts}|${r.kpi}|${r.value}`} columns={columns} dataSource={data?.rows || []} pagination={{ pageSize: 10 }} size="middle" />
    </Card>
  )
}
