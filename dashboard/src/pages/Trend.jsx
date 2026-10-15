import React, { useEffect, useRef } from 'react'
import { Card, Space, Typography } from 'antd'
import * as echarts from 'echarts'
import { usePolling, THRESH } from '../api.js'

const KPIS = [
  { key: 'rms', label: 'RMS (mm/s)', color: '#00c7ff', th: THRESH.rms },
  { key: 'kurtosis', label: '峭度', color: '#b37feb', th: THRESH.kurtosis },
  { key: 'pp', label: '峰峰值 (mm/s)', color: '#95de64', th: {} },
]

function KpiChart({ kpi, series }) {
  const ref = useRef(null)
  const chart = useRef(null)

  useEffect(() => {
    chart.current = echarts.init(ref.current)
    const onResize = () => chart.current?.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.current?.dispose()
    }
  }, [])

  useEffect(() => {
    if (!chart.current) return
    const ts = series.map((s) => s.ts)
    const vs = series.map((s) => s.v)
    const markLine = Object.entries(kpi.th).map(([lv, v]) => ({
      yAxis: v,
      name: lv === 'notice' ? `注意 ${v}` : `严重 ${v}`,
      lineStyle: { color: lv === 'notice' ? '#faad14' : '#ff4d4f', type: 'dashed' },
      label: { formatter: `{b}`, color: lv === 'notice' ? '#faad14' : '#ff4d4f' },
    }))
    chart.current.setOption({
      grid: { left: 56, right: 20, top: 36, bottom: 56 },
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: ts, axisLabel: { color: '#7ba7cc' } },
      yAxis: { type: 'value', scale: true, axisLabel: { color: '#7ba7cc' } },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 10 }],
      series: [
        {
          name: kpi.label,
          type: 'line',
          data: vs,
          showSymbol: false,
          lineStyle: { color: kpi.color, width: 1.6 },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: kpi.color + '55' },
              { offset: 1, color: kpi.color + '05' },
            ]),
          },
          markLine: { silent: true, symbol: 'none', data: markLine },
        },
      ],
    })
  }, [series, kpi])

  return <div ref={ref} style={{ height: 260 }} />
}

export default function Trend() {
  const { data: trendRms } = usePolling('/api/trend?point=DE&kpi=rms&hours=24', 5000)
  const { data: trendKurt } = usePolling('/api/trend?point=DE&kpi=kurtosis&hours=24', 5000)
  const { data: trendPp } = usePolling('/api/trend?point=DE&kpi=pp&hours=24', 5000)
  const byKey = { rms: trendRms, kurtosis: trendKurt, pp: trendPp }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card title="测点 DE · 近 24h 特征趋势（虚线为阈值线）">
        <Typography.Text type="secondary">
          数据每 5s 轮询；注入故障后曲线将在 15 秒内越过阈值线（连续 3 点触发报警）。
        </Typography.Text>
      </Card>
      {KPIS.map((kpi) => (
        <Card key={kpi.key} title={kpi.label}>
          <KpiChart kpi={kpi} series={byKey[kpi.key]?.series || []} />
        </Card>
      ))}
    </Space>
  )
}
