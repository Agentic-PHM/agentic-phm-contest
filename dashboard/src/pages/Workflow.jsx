import React, { useMemo } from 'react'
import { Card, Col, Descriptions, Empty, Progress, Row, Space, Table, Tag, Timeline, Typography } from 'antd'
import {
  FileDoneOutlined,
  FileSearchOutlined,
  LineChartOutlined,
  MonitorOutlined,
  NodeIndexOutlined,
  RobotOutlined,
} from '@ant-design/icons'
import { usePolling, LEVEL_COLOR, LEVEL_TEXT } from '../api.js'
import { getAgentAvailability, getCloseReason, getClosureStatus, getDiagnosisSnapshot } from '../agentState.js'
import { AGENT_TOPOLOGY_NOTE } from '../dataProvenance.js'

/**
 * 智能体工作流页（Task D）
 * 智能体阶段只由后端自动诊断状态机驱动；报警和报告时间只用于历史排序。
 * P2 口径：诊断、交叉验证、结论与工单生成是同一个 AGH 会话里的连续操作，
 * 不得把这三段描述成互相独立的智能体并行协作（该编排未经验证）。
 * 巡检/预测为规则算法面板，不属于智能体。
 */

function tsMs(ts) {
  return ts ? new Date(ts.replace(' ', 'T')).getTime() : 0
}

function evidenceSteps(raw) {
  try {
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : null
  } catch {
    return null
  }
}

/** 把小时区间格式化为可读估算区间（仅对接口返回的真实数值做单位换算）。 */
function fmtEta(hours) {
  if (!Array.isArray(hours) || hours.length !== 2) return null
  const fmt = (h) => {
    if (typeof h !== 'number') return '--'
    if (h <= 0) return '按当前趋势已越过'
    if (h >= 8760) return `${(h / 8760).toFixed(1)} 年`
    if (h >= 48) return `${(h / 24).toFixed(1)} 天`
    return `${h.toFixed(1)} 小时`
  }
  return `${fmt(hours[0])} ~ ${fmt(hours[1])}`
}

const STATUS_META = {
  running: { color: '#00c7ff', tag: 'processing', text: '运行中' },
  alert: { color: '#ff4d4f', tag: 'error', text: '越限告警' },
  done: { color: '#52c41a', tag: 'success', text: '已完成' },
  failed: { color: '#ff4d4f', tag: 'error', text: '失败' },
  unavailable: { color: '#ff4d4f', tag: 'error', text: '不可用' },
  wait: { color: '#7d95ad', tag: 'default', text: '等待触发' },
  idle: { color: '#7d95ad', tag: 'default', text: '待命' },
}

const OVERALL = {
  idle: { tag: undefined, label: '待命', color: '#52c41a' },
  loading: { tag: 'default', label: '状态读取中', color: '#7d95ad' },
  running: { tag: 'processing', label: 'AGH 诊断中', color: '#00c7ff' },
  done: { tag: 'success', label: '已出结论', color: '#52c41a' },
  // P0 问题2：报告已提交但闭环/核验未达标，不得显示为"全部完成"
  partial: { tag: 'warning', label: '仅完成部分闭环', color: '#faad14' },
  verification_failed: { tag: 'warning', label: '核验未通过', color: '#fa8c16' },
  failed: { tag: 'error', label: '诊断失败', color: '#ff4d4f' },
  unavailable: { tag: 'error', label: '智能体不可用', color: '#ff4d4f' },
  manual: { tag: 'warning', label: '人工备用模式', color: '#faad14' },
  disabled: { tag: 'default', label: '自动诊断已关闭', color: '#7d95ad' },
}

const STAGES = [
  { key: 'monitor', title: '巡检监视', executor: '规则引擎 · 实时', icon: <MonitorOutlined /> },
  { key: 'evidence', title: '故障取证', executor: '同一 AGH 会话', icon: <FileSearchOutlined /> },
  { key: 'verify', title: '交叉验证', executor: '同一 AGH 会话', icon: <NodeIndexOutlined /> },
  { key: 'close', title: '结论与工单生成', executor: '同一 AGH 会话', icon: <FileDoneOutlined /> },
]

function StageNode({ stage, status, desc }) {
  const meta = STATUS_META[status] || STATUS_META.idle
  const nodeClass = status === 'alert' ? 'wf-node wf-node-alert'
    : status === 'running' ? 'wf-node wf-node-running'
      : status === 'failed' || status === 'unavailable' ? 'wf-node wf-node-failed' : 'wf-node'
  return (
    <div
      className={nodeClass}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: '50%',
            flex: 'none',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: 18,
            color: meta.color,
            background: `radial-gradient(circle at 35% 30%, ${meta.color}22, #0a1a2f 80%)`,
            border: `1px solid ${meta.color}66`,
          }}
        >
          {stage.icon}
        </div>
        <div style={{ minWidth: 0 }}>
          <div>
            <Typography.Text strong style={{ fontSize: 15 }}>
              {stage.title}
            </Typography.Text>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {stage.executor}
          </Typography.Text>
        </div>
      </div>
      <div style={{ marginTop: 10 }}>
        <Tag color={meta.tag} style={{ marginInlineEnd: 0 }}>
          {meta.text}
        </Tag>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12, minHeight: 34 }}>
        {desc}
      </Typography.Paragraph>
    </div>
  )
}

function LinkLine({ on }) {
  return <div className={on ? 'wf-link wf-link-on' : 'wf-link'} />
}

export default function Workflow() {
  const { data: alarms } = usePolling('/api/alarm/history?equip=P-101', 3000)
  const { data: reports } = usePolling('/api/report/list', 3000)
  const { data: tasks } = usePolling('/api/task/list?status=all', 5000)
  const { data: channels } = usePolling('/api/channels', 5000)
  const { data: device } = usePolling('/api/device', 5000)
  const { data: forecast } = usePolling('/api/forecast', 10000)
  const { data: autodiag } = usePolling('/api/autodiag/status', 2000)
  const { data: operation } = usePolling('/api/operation/mode', 4000)

  const reportRows = reports?.rows || []
  const latestReport = reportRows[0]
  const latestTask = tasks?.rows?.[0]
  const chList = channels?.channels || []
  const availability = getAgentAvailability(operation)
  const snapshot = getDiagnosisSnapshot(autodiag, operation, reportRows)
  const state = snapshot.state
  const currentReport = snapshot.currentReport
  const historyReport = snapshot.historicalReport
  const currentTask = autodiag?.task_id == null ? null
    : (tasks?.rows || []).find((task) => String(task.id) === String(autodiag.task_id)) || null
  const steps = useMemo(() => (currentReport ? evidenceSteps(currentReport.evidence) : null), [currentReport])

  const abnormal = chList.filter((c) => c.level && c.level !== 'normal')
  const conf = currentReport && typeof currentReport.confidence === 'number' ? Math.round(currentReport.confidence * 100) : null

  // —— 四个阶段的真实状态 ——
  const wf = useMemo(() => {
    const monitor = {
      status: abnormal.length ? 'alert' : 'running',
      desc: abnormal.length
        ? `规则监测检测到 ${abnormal.length} 路通道越限（${abnormal.map((c) => c.name).join('、')}）；智能体状态见下方`
        : `${chList.length || 0} 路通道监视中，特征值均在阈值内`,
    }
    let evidence, verify, close
    if (state === 'idle' || state === 'loading' || state === 'disabled') {
      evidence = { status: 'idle', desc: '待命：暂无越限报警触发取证' }
      verify = { status: 'idle', desc: '待命：等待取证输出' }
      close = { status: 'idle', desc: '待命：暂无智能体结论与工单' }
      if (state === 'loading') evidence.desc = '正在读取后端自动诊断状态'
      if (state === 'disabled') evidence.desc = '系统配置已关闭自动诊断；人工监测与报警继续运行'
      if (state === 'idle' && !availability.ready) evidence.desc = availability.detail
    } else if (state === 'manual') {
      evidence = { status: 'idle', desc: '人工备用模式：智能体取证已暂停' }
      verify = { status: 'idle', desc: '人工备用模式：没有智能体交叉验证' }
      close = { status: 'idle', desc: '由操作员按人工流程处理工单' }
    } else if (state === 'unavailable') {
      evidence = { status: 'unavailable', desc: snapshot.message }
      verify = { status: 'wait', desc: '等待取证完成后交叉验证' }
      close = { status: 'wait', desc: '等待验证结论后生成工单' }
    } else if (state === 'failed') {
      evidence = { status: 'failed', desc: snapshot.message }
      verify = { status: 'wait', desc: '诊断失败，没有交叉验证结论' }
      close = { status: 'wait', desc: '诊断失败，未生成工单' }
    } else if (state === 'running') {
      evidence = { status: 'running', desc: snapshot.message }
      verify = { status: 'wait', desc: '后端未报告交叉验证阶段' }
      close = { status: 'wait', desc: '等待 AGH 会话完成并提交工单' }
    } else {
      evidence = {
        status: 'done',
        desc: steps ? `本次 AGH 报告包含 ${steps.length} 条证据（${currentReport.ts}）` : `本次 AGH 报告已落库（${currentReport.ts}）`,
      }
      verify = { status: 'done', desc: 'AGH 会话已完成；证据结论以本次报告为准' }
      close = getClosureStatus(currentTask)
    }
    return [
      { ...STAGES[0], ...monitor },
      { ...STAGES[1], ...evidence },
      { ...STAGES[2], ...verify },
      { ...STAGES[3], ...close },
    ]
  }, [state, abnormal, chList, steps, currentReport, currentTask, availability, snapshot.message])

  // 连接线点亮规则：左侧阶段已完成（done/alert）且右侧阶段运行中
  const linkOn = [
    wf[0].status === 'alert' && wf[1].status === 'running',
    wf[1].status === 'done' && wf[2].status === 'running',
    wf[2].status === 'done' && wf[3].status === 'running',
  ]

  const bannerText = (() => {
    if (state === 'manual') return '人工备用模式：吉祥物、聊天、主动播报和自主诊断已暂停；基础监测与阈值报警继续运行。'
    if (state === 'unavailable') return snapshot.message
    if (state === 'failed') return snapshot.message
    if (state === 'disabled') return '自动诊断由系统配置关闭；规则监测和阈值报警继续运行，不会生成智能体诊断结论。'
    if (state === 'loading') return snapshot.message
    if (state === 'running') return snapshot.message
    if (state === 'idle') return availability.ready
      ? '规则引擎持续监视各通道特征值；严重报警触发后，AGH 才会启动自主诊断。'
      : `规则监测与阈值报警继续运行；${availability.detail}`
    const closeText = currentTask
      ? `本次工单 #${currentTask.id} ${currentTask.status === 'open' ? '待处理' : '已关闭'}`
      : '暂无关联工单'
    return `AGH 诊断结论：${currentReport?.conclusion || '--'}${conf !== null ? `（自评置信度 ${conf}%）` : ''}；${closeText}。`
  })()

  // —— 运行轨迹：全部来自真实表数据（报警/报告/工单），按时间倒序 ——
  const events = useMemo(() => {
    const list = []
    for (const a of (alarms?.rows || []).slice(0, 3)) {
      list.push({ ts: a.ts, color: a.level === 'severe' ? 'red' : 'orange', text: `规则引擎报警：${a.msg}` })
    }
    if (latestReport) {
      const isCurrent = currentReport && String(currentReport.id) === String(latestReport.id)
      list.push({
        ts: latestReport.ts,
        color: isCurrent ? 'cyan' : 'gray',
        text: `${isCurrent ? '本次 AGH 诊断报告' : '历史报告（不代表当前运行）'} #${latestReport.id}：${latestReport.conclusion}`,
      })
    }
    if (latestTask) {
      const isCurrent = currentTask && String(currentTask.id) === String(latestTask.id)
      list.push({
        ts: latestTask.ts,
        color: 'blue',
        text: `${isCurrent ? '本次智能体工单' : latestTask.source === 'manual' ? '人工工单' : '历史工单'} #${latestTask.id} [${latestTask.status === 'open' ? '待处理' : '已关闭'}]：${latestTask.title}`,
      })
    }
    return list.sort((a, b) => tsMs(b.ts) - tsMs(a.ts)).slice(0, 6)
  }, [alarms, latestReport, latestTask, currentReport, currentTask])

  const chColumns = [
    { title: '通道', dataIndex: 'name', width: 90 },
    { title: '实时值', dataIndex: 'value', width: 110, render: (v, r) => (typeof v === 'number' ? `${v.toFixed(2)} ${r.unit || ''}` : '--') },
    {
      title: '注意 / 严重阈值',
      key: 'th',
      width: 130,
      render: (_, r) => (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {r.notice} / {r.severe}
        </Typography.Text>
      ),
    },
    {
      title: '状态',
      dataIndex: 'level',
      width: 80,
      render: (l) => (
        <Tag style={{ marginInlineEnd: 0 }} color={LEVEL_COLOR[l]}>
          {LEVEL_TEXT[l] || l}
        </Tag>
      ),
    },
  ]

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      {/* 注入工作流节点的脉冲/流动动画样式（页内私有，不进全局 theme.css） */}
      <style>
        {`@keyframes wf-pulse { 0%,100% { box-shadow: 0 0 4px rgba(0,199,255,.25); } 50% { box-shadow: 0 0 18px rgba(0,199,255,.65); } }
          @keyframes wf-pulse-red { 0%,100% { box-shadow: 0 0 4px rgba(255,77,79,.25); } 50% { box-shadow: 0 0 18px rgba(255,77,79,.6); } }
          @keyframes wf-flow { from { background-position: 0 0; } to { background-position: 24px 0; } }
          .wf-node { flex: 1; min-width: 0; border: 1px solid #1d3a5f; border-radius: 8px; background: #0f2540; padding: 14px 16px; }
          .wf-node-running { border-color: #00c7ff; animation: wf-pulse 1.8s ease-in-out infinite; }
          .wf-node-alert { border-color: #ff4d4f; animation: wf-pulse-red 1.8s ease-in-out infinite; }
          .wf-node-failed { border-color: #ff4d4f; background: rgba(80, 20, 30, .45); }
          .wf-link { width: 44px; align-self: center; height: 2px; flex: none; margin: 0 4px; background: #1d3a5f; }
          .wf-link-on { background: repeating-linear-gradient(90deg, #00c7ff 0 6px, transparent 6px 12px); animation: wf-flow .5s linear infinite; }`}
      </style>

      {/* 顶部状态横幅 */}
      <Card styles={{ body: { padding: 16 } }}>
        <Row gutter={16} align="middle">
          <Col flex="64px">
            <div
              style={{
                width: 56,
                height: 56,
                borderRadius: '50%',
                background: `radial-gradient(circle at 35% 30%, ${OVERALL[state].color}cc, #0f2540 75%)`,
                border: `2px solid ${OVERALL[state].color}`,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: state !== 'idle' ? `0 0 18px ${OVERALL[state].color}66` : 'none',
              }}
            >
              <RobotOutlined style={{ fontSize: 26, color: '#e6f7ff' }} />
            </div>
          </Col>
          <Col flex="auto">
            <Space size={8} align="center" wrap>
              <Typography.Text strong style={{ color: '#00c7ff', fontSize: 16 }}>
                调相机机组 · 智能体工作流
              </Typography.Text>
              <Tag color={OVERALL[state].tag}>{OVERALL[state].label}</Tag>
              <Typography.Text type="secondary">
                位号 {device?.name || '--'} · 在线监视通道 {chList.length} 路
              </Typography.Text>
            </Space>
            <div style={{ marginTop: 4 }}>{bannerText}</div>
          </Col>
        </Row>
      </Card>

      {/* 主视觉：四阶段工作流（节点间连线，激活阶段脉冲高亮） */}
      <Card
        title="工作流状态机"
        extra={
          <Typography.Text type="secondary">
            智能体阶段以 /api/autodiag/status 为准；时间戳只用于历史排序
          </Typography.Text>
        }
      >
        {/* P2 智能体数量口径：三段智能体阶段是同一会话的连续操作，不是并行编排。
            放在节点上方而不是卡片底部——底部会被桌面吉祥物默认位置遮挡。 */}
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12, fontSize: 12 }}>
          {AGENT_TOPOLOGY_NOTE}
        </Typography.Text>
        <div style={{ display: 'flex', alignItems: 'stretch' }}>
          {wf.map((s, i) => (
            <React.Fragment key={s.key}>
              {i > 0 && <LinkLine on={linkOn[i - 1]} />}
              <StageNode stage={s} status={s.status} desc={s.desc} />
            </React.Fragment>
          ))}
        </div>
      </Card>

      {/* 只把本次运行的智能体产物展示为当前结果 */}
      <Row gutter={16}>
        <Col span={12}>
          <Card title={<>诊断智能体（Agnes）</>} extra={<Tag color={OVERALL[state].tag}>{OVERALL[state].label}</Tag>}>
            {currentReport ? (
              <Descriptions column={2} size="small">
                <Descriptions.Item label="最新结论" span={2}>
                  <Typography.Text strong>{currentReport.conclusion}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="置信度（模型自评）">
                  {conf !== null ? <Progress percent={conf} size="small" style={{ width: 140 }} /> : '--'}
                </Descriptions.Item>
                <Descriptions.Item label="证据条数">{steps ? `${steps.length} 条` : '--'}</Descriptions.Item>
                <Descriptions.Item label="run_id / 时间" span={2}>
                  {currentReport.run_id} · {currentReport.ts}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description={snapshot.message} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
        <Col span={12}>
          <Card title={<>本次工单</>} extra={currentTask ? <Tag color={currentTask.status === 'open' ? 'processing' : 'success'}>{currentTask.status === 'open' ? '待处理' : getCloseReason(currentTask)}</Tag> : null}>
            {currentTask ? (
              <Descriptions column={2} size="small">
                <Descriptions.Item label="最新工单" span={2}>
                  <Typography.Text strong>{currentTask.title}</Typography.Text>
                </Descriptions.Item>
                <Descriptions.Item label="等级">
                  <Tag style={{ marginInlineEnd: 0 }} color={currentTask.level === 'severe' ? 'red' : 'orange'}>
                    {currentTask.level === 'severe' ? '严重' : '注意'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="状态">
                  <Tag style={{ marginInlineEnd: 0 }} color={currentTask.status === 'open' ? 'processing' : 'green'}>
                    {currentTask.status === 'open' ? '待处理' : '已关闭'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="时间 / 来源" span={2}>
                  {currentTask.ts} · 来源 {currentTask.source}
                </Descriptions.Item>
              </Descriptions>
            ) : (
              <Empty description={state === 'done' ? '本次运行没有关联工单' : '没有本次 AGH 运行的工单'} image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>

      {historyReport || (latestTask && !currentTask) ? (
        <Card title="历史记录（不代表当前智能体运行）">
          {historyReport ? <Typography.Paragraph>
            历史报告 #{historyReport.id} · {historyReport.ts}：{historyReport.conclusion}
          </Typography.Paragraph> : null}
          {latestTask && !currentTask ? <Typography.Paragraph style={{ marginBottom: 0 }}>
            {latestTask.source === 'manual' ? '人工工单' : '历史工单'} #{latestTask.id} · {latestTask.ts}：{latestTask.title}
          </Typography.Paragraph> : null}
        </Card>
      ) : null}

      {/* 算法面板（非智能体）：巡检规则引擎 + 趋势外推 */}
      <Row gutter={16}>
        <Col span={14}>
          <Card
            title={<>巡检监视 · 规则引擎面板</>}
            extra={
              <Typography.Text type="secondary">
                任一通道连续 3 点越限 → 生成报警；AGH 可用且处于智能体模式时才启动取证
              </Typography.Text>
            }
          >
            <Table
              rowKey="name"
              columns={chColumns}
              dataSource={chList}
              pagination={{ pageSize: 6, size: 'small' }}
              size="small"
              locale={{ emptyText: <Empty description="暂无通道数据" image={Empty.PRESENTED_IMAGE_SIMPLE} /> }}
            />
          </Card>
        </Col>
        <Col span={10}>
          <Card
            title={<>趋势外推 · 预测性维护面板</>}
            extra={
              forecast ? (
                <Typography.Text type="secondary">
                  {forecast.point} · {forecast.method} · {forecast.points} 个样本点
                </Typography.Text>
              ) : null
            }
          >
            {forecast ? (
              forecast.reason ? (
                <Typography.Text type="secondary">{forecast.reason}</Typography.Text>
              ) : (
                <>
                  <Descriptions column={1} size="small">
                    <Descriptions.Item label="预计触及注意阈值">{fmtEta(forecast.eta_notice_hours) || '暂无'}</Descriptions.Item>
                    <Descriptions.Item label="预计触及严重阈值">{fmtEta(forecast.eta_severe_hours) || '暂无'}</Descriptions.Item>
                    <Descriptions.Item label="拟合优度 R²">
                      {typeof forecast.r2 === 'number' ? forecast.r2.toFixed(3) : '--'}
                    </Descriptions.Item>
                  </Descriptions>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    以上为线性外推估算区间（真实趋势拟合输出，非固定话术）
                    {typeof forecast.r2 === 'number' && forecast.r2 < 0.5
                      ? '；当前拟合优度低，仅供趋势参考，不作为检修决策依据'
                      : '，随程序合成的仿真信号实时更新'}
                    。
                  </Typography.Text>
                </>
              )
            ) : (
              <Empty description="暂无预测数据" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </Card>
        </Col>
      </Row>

      {/* 运行轨迹 */}
      <Card title="运行轨迹（真实事件时间线）">
        {events.length ? (
          <Timeline
            items={events.map((e) => ({
              color: e.color,
              children: (
                <div>
                  <Typography.Text type="secondary" style={{ fontSize: 12, marginRight: 8 }}>
                    {e.ts}
                  </Typography.Text>
                  {e.text}
                </div>
              ),
            }))}
          />
        ) : (
          <Empty description="暂无事件记录" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>
    </Space>
  )
}
