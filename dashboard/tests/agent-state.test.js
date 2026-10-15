import test from 'node:test'
import assert from 'node:assert/strict'
import * as agentState from '../src/agentState.js'
import { getAgentAvailability, getClosureStatus, getDiagnosisSnapshot } from '../src/agentState.js'

test('global mascot is rendered only after agent mode is confirmed', () => {
  assert.equal(typeof agentState.shouldRenderGlobalAgent, 'function')
  assert.equal(agentState.shouldRenderGlobalAgent({ mode: 'agent' }), true)
  assert.equal(agentState.shouldRenderGlobalAgent({ mode: 'agent' }, '后端不可达'), false)
  assert.equal(agentState.shouldRenderGlobalAgent({ mode: 'agent' }, null), true)
  assert.equal(agentState.shouldRenderGlobalAgent({ mode: 'manual' }), false)
  assert.equal(agentState.shouldRenderGlobalAgent(null), false)
  assert.equal(agentState.shouldRenderGlobalAgent({ mode: 'unknown' }), false)
})

test('severe alarm copy reports only verified AGH activity for the current mode', () => {
  assert.equal(typeof agentState.getSevereAlarmAgentStatus, 'function')
  assert.match(agentState.getSevereAlarmAgentStatus({ mode: 'manual' }, { state: 'idle' }), /人工备用模式.*智能体未启动/)
  assert.match(agentState.getSevereAlarmAgentStatus({
    mode: 'agent', agh_available: false, key_configured: true,
  }, { state: 'idle' }), /AGH.*离线.*未由智能体诊断/)
  assert.match(agentState.getSevereAlarmAgentStatus({
    mode: 'agent', agh_available: true, key_configured: true, provider_ready: true, provider_status: 'ready',
  }, { state: 'running', run_id: 'current-run' }), /AGH.*正在自主取证诊断.*current-run/)
})

test('operator-selected manual mode disables agent features even when AGH is healthy', () => {
  const status = getAgentAvailability({
    mode: 'manual', agh_available: true, key_configured: true, provider_ready: true,
  })
  assert.equal(status.code, 'manual')
  assert.equal(status.ready, false)
  assert.match(status.detail, /吉祥物、聊天与主动播报已关闭/)
  assert.match(status.detail, /监测与报警继续运行/)
})

test('AGH offline, missing Key, and failed provider probe are distinct states', () => {
  assert.equal(getAgentAvailability({ mode: 'agent', agh_available: false, key_configured: true }).code,
    'runtime_offline')
  assert.equal(getAgentAvailability({ mode: 'agent', agh_available: true, key_configured: false }).code,
    'key_missing')
  assert.equal(getAgentAvailability({
    mode: 'agent', agh_available: true, key_configured: true, provider_status: 'unavailable',
  }).code, 'model_unavailable')
})

test('agent readiness requires an explicit successful provider probe', () => {
  assert.equal(getAgentAvailability({
    mode: 'agent', agh_available: true, key_configured: true, provider_status: 'unverified',
  }).ready, false)
  assert.equal(getAgentAvailability({
    mode: 'agent', agh_available: true, key_configured: true,
    provider_status: 'ready', provider_ready: true,
  }).ready, true)
})

test('missing API responses remain in loading state instead of claiming the agent is offline', () => {
  const snapshot = getDiagnosisSnapshot(
    { enabled: true, state: 'idle' }, null, [],
  )
  assert.equal(snapshot.state, 'loading')
})

test('live diagnosis state comes from autodiag, never the age of a report or alarm', () => {
  const stale = { id: 1, run_id: 'old', ts: '2000-01-01T00:00:00', conclusion: '旧结论' }
  const snapshot = getDiagnosisSnapshot(
    { enabled: true, state: 'running', run_id: 'new', report_id: null },
    { mode: 'agent', agh_available: true, key_configured: true, provider_ready: true },
    [stale],
  )
  assert.equal(snapshot.state, 'running')
  assert.equal(snapshot.currentReport, null)
  assert.equal(snapshot.historicalReport, stale)
})

test('a completed run only displays its own report and preserves a newer report as history', () => {
  const current = { id: 2, run_id: 'current', ts: '2026-10-05T11:00:00', conclusion: '当前结论' }
  const newer = { id: 3, run_id: 'other', ts: '2026-10-05T12:00:00', conclusion: '其他记录' }
  const snapshot = getDiagnosisSnapshot(
    { enabled: true, state: 'done', run_id: 'current', report_id: 2 },
    { mode: 'agent', agh_available: true, key_configured: true, provider_ready: true },
    [newer, current],
  )
  assert.equal(snapshot.state, 'done')
  assert.equal(snapshot.currentReport, current)
  assert.equal(snapshot.historicalReport, newer)
})

test('report id takes precedence over a reused run id when selecting current evidence', () => {
  const old = { id: 10, run_id: 'auto-duplicate', conclusion: '旧报告' }
  const current = { id: 11, run_id: 'auto-duplicate', conclusion: '当前报告' }
  const snapshot = getDiagnosisSnapshot(
    { enabled: true, state: 'done', run_id: 'auto-duplicate', report_id: 11 },
    { mode: 'agent', agh_available: true, key_configured: true, provider_ready: true },
    [old, current],
  )
  assert.equal(snapshot.currentReport, current)
  assert.equal(snapshot.historicalReport, old)
})

test('failed and manual states do not let old reports appear current', () => {
  const old = { id: 8, run_id: 'old', conclusion: '历史结论' }
  const operation = { mode: 'agent', agh_available: true, key_configured: true, provider_ready: true }
  const failed = getDiagnosisSnapshot(
    { enabled: true, state: 'failed', failure_reason: 'runtime_unavailable' }, operation, [old],
  )
  const manual = getDiagnosisSnapshot(
    { enabled: true, state: 'done', report_id: 8, run_id: 'old' }, { ...operation, mode: 'manual' }, [old],
  )
  assert.equal(failed.state, 'failed')
  assert.equal(failed.currentReport, null)
  assert.equal(failed.historicalReport, old)
  assert.equal(manual.state, 'manual')
  assert.equal(manual.currentReport, null)
  assert.equal(manual.historicalReport, old)
})

test('a completed report without its run-linked work order is not shown as a closed workflow', () => {
  const closure = getClosureStatus(null)
  assert.equal(closure.status, 'failed')
  assert.match(closure.desc, /没有关联工单/)
})

test('P0 问题3：工单状态与文案严格一致——open 不得显示已完成，closed 才算关单', () => {
  const open = getClosureStatus({ id: 7, status: 'open' })
  assert.equal(open.status, 'running')
  assert.match(open.desc, /待处理/)
  assert.doesNotMatch(open.desc, /已完成|已关闭|闭环/)

  const closed = getClosureStatus({ id: 7, status: 'closed', close_note: '自动复测通过' })
  assert.equal(closed.status, 'done')
  assert.match(closed.desc, /已关闭/)
})

// ---- P0 问题2：报告存在 ≠ 诊断成功，partial / verification_failed 不得显示为完成 ----

const AGENT_OP = { mode: 'agent', agh_available: true, key_configured: true, provider_ready: true, provider_status: 'ready' }

test('P0 问题2：partial 只显示部分闭环，并给出缺什么', () => {
  const snap = getDiagnosisSnapshot({
    state: 'partial', run_id: 'r1', report_id: 9, task_id: null,
    failure_reason: 'task_missing',
    verification: { problems: ['task_missing'], messages: ['本次运行没有关联工单'] },
  }, AGENT_OP, [])
  assert.equal(snap.state, 'partial')
  assert.match(snap.message, /未完成完整闭环/)
  assert.match(snap.message, /没有关联工单/)
  assert.doesNotMatch(snap.message, /已完成|闭环完成/)
})

test('P0 问题2：verification_failed 显示核验未通过及原因', () => {
  const snap = getDiagnosisSnapshot({
    state: 'verification_failed', run_id: 'r1', report_id: 9,
    failure_reason: 'evidence_invalid',
    verification: { problems: ['evidence_invalid'], messages: ['证据结构不合规（证据为空）'] },
  }, AGENT_OP, [])
  assert.equal(snap.state, 'verification_failed')
  assert.match(snap.message, /未通过完整性核验/)
  assert.match(snap.message, /证据结构不合规/)
})

test('P0 问题2：缺失核验明细时退回失败类别文案，不静默说成功', () => {
  const snap = getDiagnosisSnapshot({
    state: 'verification_failed', run_id: 'r1', report_id: 9, failure_reason: 'index_failed',
  }, AGENT_OP, [])
  assert.match(snap.message, /执行索引无法建立/)
})

test('P0 问题2：报警播报对 partial / verification_failed 不宣称已完成', () => {
  const partial = agentState.getSevereAlarmAgentStatus(AGENT_OP, { state: 'partial', failure_reason: 'task_missing' })
  assert.match(partial, /部分闭环|没有生成关联工单/)
  assert.doesNotMatch(partial, /已完成本次诊断/)

  const failed = agentState.getSevereAlarmAgentStatus(AGENT_OP, {
    state: 'verification_failed', failure_reason: 'evidence_invalid',
  })
  assert.match(failed, /未通过完整性核验/)
  assert.doesNotMatch(failed, /已完成本次诊断/)
})

test('P0 问题2：done 语义未被放宽——仍然只在完整通过时使用', () => {
  const snap = getDiagnosisSnapshot({
    state: 'done', run_id: 'r1', report_id: 9, task_id: 3,
    verification: { ok: true, problems: [], messages: [] },
  }, AGENT_OP, [{ id: 9, run_id: 'r1' }])
  assert.equal(snap.state, 'done')
  assert.equal(snap.currentReport.id, 9)
})

// ---- P1 问题5：仿真恢复 ≠ 维修完成，关单依据必须可区分 ----

test('P1 问题5：自动复测关单不得宣称维修完成', () => {
  const sim = agentState.getCloseReason({ close_kind: 'sim_recovery' })
  assert.match(sim, /仿真恢复验证通过/)
  assert.match(sim, /未验证实际维修/)
  assert.doesNotMatch(sim, /维修完成|已修好|已完成|闭环完成/)

  const manual = agentState.getCloseReason({ close_kind: 'manual' })
  assert.match(manual, /人工处置确认/)
  assert.doesNotMatch(manual, /仿真/)

  assert.equal(agentState.getCloseReason({}), '已关闭')
  assert.equal(agentState.getCloseReason(null), '已关闭')
})

test('P1 问题5：工单闭环状态带上关单依据', () => {
  const closure = getClosureStatus({ id: 5, status: 'closed', close_kind: 'sim_recovery' })
  assert.equal(closure.status, 'done')
  assert.match(closure.desc, /仿真恢复验证通过/)
  assert.doesNotMatch(closure.desc, /闭环完成|已完成/)
})
