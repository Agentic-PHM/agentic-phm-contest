const FAILURE_TEXT = {
  runtime_unavailable: 'AGH 运行时离线',
  runtime_missing: '未找到 AGH 运行时，请检查本机配置',
  provider_unavailable: 'Agnes Key 或模型不可用',
  timeout: 'AGH 模型调用超时',
  cli_failed: 'AGH 会话执行失败',
  invalid_response: 'AGH 返回格式无效',
  missing_report: 'AGH 会话结束，但没有提交本次诊断报告',
  manual_mode: '已切换到人工备用模式，智能体诊断未启动',
  // P0 问题2：报告存在≠诊断成功，下面是"未达完整闭环/未通过核验"的具体原因
  evidence_invalid: '诊断证据结构不合规（缺少实测数值支撑）',
  task_missing: '本次诊断没有生成关联工单',
  tool_calls_insufficient: 'AGH 工具调用次数不足，取证不完整',
  linkage_failed: '业务记录与 AGH 会话无法互相指认',
  index_failed: '执行索引无法建立，证据链不可独立核查',
  index_write_failed: '执行索引落盘失败，证据链不可独立核查',
}

export function getFailureText(category) {
  return FAILURE_TEXT[category] || 'AGH 智能体会话失败，请人工复核'
}

/** 把后端核验明细摊成一句人话；缺失时退回失败类别文案。 */
export function getVerificationSummary(autodiag) {
  const messages = autodiag?.verification?.messages
  if (Array.isArray(messages) && messages.length) return messages.join('；')
  return getFailureText(autodiag?.failure_reason)
}

/** 关单依据文案（P1 问题5）：自动复测只说明"仿真恢复验证通过"，不代表实际维修已落实；
 *  人工处置确认才是运维闭环。两者必须可区分，不得都写成复测通过。 */
export function getCloseReason(task) {
  if (task?.close_kind === 'manual') return '已关闭（人工处置确认）'
  if (task?.close_kind === 'sim_recovery') return '已关闭（仿真恢复验证通过，未验证实际维修）'
  return '已关闭'
}

export function getClosureStatus(task) {
  // P0 问题3 文案纪律：诊断完成 ≠ 工单创建 ≠ 工单关闭，描述必须与工单真实状态一致
  if (!task) return { status: 'failed', desc: '本次报告没有关联工单' }
  if (task.status === 'open') {
    return { status: 'running', desc: `本次运行已生成工单 #${task.id}（待处理）` }
  }
  return { status: 'done', desc: `本次工单 #${task.id} ${getCloseReason(task)}` }
}

export function getAgentAvailability(operation) {
  if (!operation) {
    return { code: 'loading', ready: false, label: '正在读取 AGH 状态', detail: '正在读取本机运行状态。' }
  }
  if (operation.mode === 'manual') {
    return {
      code: 'manual', ready: false, label: '人工备用模式',
      detail: '吉祥物、聊天与主动播报已关闭；基础监测与报警继续运行。',
    }
  }
  if (operation.mode !== 'agent') {
    return { code: 'status_unavailable', ready: false, label: '运行状态不可用', detail: '无法确认当前运行模式。' }
  }
  if (!operation.agh_available) {
    return { code: 'runtime_offline', ready: false, label: 'AGH 运行时离线', detail: 'AGH 离线，智能体聊天和自主诊断不可用。' }
  }
  if (!operation.key_configured) {
    return { code: 'key_missing', ready: false, label: '未配置 Agnes Key', detail: '请在本机设置 Agnes API Key 后验证模型连接。' }
  }
  if (operation.provider_status === 'unavailable') {
    return { code: 'model_unavailable', ready: false, label: 'Agnes 模型连接失败', detail: '模型验证未通过；检查 Key 和 AGH profile 后重试。' }
  }
  if (operation.provider_ready && operation.provider_status === 'ready') {
    return { code: 'ready', ready: true, label: 'AGH 智能体已就绪', detail: 'AGH 运行时和 Agnes 模型均已验证。' }
  }
  return { code: 'unverified', ready: false, label: 'Agnes 模型待验证', detail: '请先验证 AGH 与 Agnes 模型连接。' }
}

export function shouldRenderGlobalAgent(operation, error = null) {
  return !error && operation?.mode === 'agent'
}

export function getSevereAlarmAgentStatus(operation, autodiag) {
  if (operation?.mode === 'manual') {
    return '当前为人工备用模式，智能体未启动。'
  }
  if (autodiag?.state === 'dispatching') {
    return 'AGH 正在检查运行时和模型配置，准备启动本次诊断。'
  }
  if (autodiag?.state === 'running') {
    const run = autodiag.run_id ? `（运行号 ${autodiag.run_id}）` : ''
    return `AGH 智能体正在自主取证诊断${run}。`
  }
  if (autodiag?.state === 'done') {
    return autodiag.report_id != null
      ? `AGH 已完成本次诊断，报告 #${autodiag.report_id} 已提交。`
      : 'AGH 运行已结束，但未确认提交报告；请人工复核。'
  }
  if (autodiag.state === 'failed') {
    return `${getFailureText(autodiag.failure_reason)}；本次诊断未完成，请人工复核。`
  }
  // P0 问题2：报告已提交但没走完整闭环 / 没通过核验，一律不许说成"已完成"
  if (autodiag.state === 'partial') {
    return `AGH 已提交诊断报告，但只完成部分闭环：${getVerificationSummary(autodiag)}；请人工复核。`
  }
  if (autodiag.state === 'verification_failed') {
    return `AGH 已提交诊断报告，但未通过完整性核验：${getVerificationSummary(autodiag)}；请人工复核。`
  }
  const availability = getAgentAvailability(operation)
  if (!availability.ready) {
    return `${availability.detail}本次报警未由智能体诊断。`
  }
  return 'AGH 尚无本次报警的诊断运行记录，请关注专家诊断页状态。'
}

function latest(rows) {
  return Array.isArray(rows) && rows.length ? rows[0] : null
}

function isCurrentReport(report, status) {
  if (!report || !status) return false
  if (status.report_id != null) return String(report.id) === String(status.report_id)
  return Boolean(status.run_id && report.run_id === status.run_id)
}

/** Only the backend autodiag state can describe a live run; timestamps are historical metadata. */
export function getDiagnosisSnapshot(autodiag, operation, reports = []) {
  const mostRecent = latest(reports)
  const historyOnly = { currentReport: null, historicalReport: mostRecent }

  if (operation?.mode === 'manual') {
    return { state: 'manual', message: getAgentAvailability(operation).detail, ...historyOnly }
  }
  if (!autodiag || !operation) {
    return { state: 'loading', message: '正在读取自动诊断状态。', ...historyOnly }
  }
  if (autodiag.state === 'dispatching' || autodiag.state === 'running') {
    return {
      state: 'running',
      message: autodiag.state === 'dispatching'
        ? 'AGH 正在检查运行时和 Agnes 模型配置。'
        : 'AGH 智能体正在自主取证诊断。',
      ...historyOnly,
    }
  }
  if (autodiag.state === 'failed') {
    return {
      state: 'failed', message: `${getFailureText(autodiag.failure_reason)}；请人工复核。`, ...historyOnly,
    }
  }
  if (autodiag.state === 'done') {
    const report = reports.find((row) => isCurrentReport(row, autodiag)) || null
    if (!report) {
      return {
        state: 'failed', message: `${getFailureText('missing_report')}；请人工复核。`, ...historyOnly,
      }
    }
    return {
      state: 'done', message: 'AGH 智能体已提交本次诊断报告。', currentReport: report,
      historicalReport: mostRecent && !isCurrentReport(mostRecent, autodiag) ? mostRecent : null,
    }
  }
  // P0 问题2：报告存在 ≠ 诊断成功。partial / verification_failed 如实展示，不再算 done。
  if (autodiag.state === 'partial' || autodiag.state === 'verification_failed') {
    const report = reports.find((row) => isCurrentReport(row, autodiag)) || null
    const head = autodiag.state === 'partial'
      ? '诊断报告已提交，但未完成完整闭环：'
      : '诊断报告已提交，但未通过完整性核验：'
    return {
      state: autodiag.state,
      message: `${head}${getVerificationSummary(autodiag)}。`,
      currentReport: report,
      historicalReport: mostRecent && !isCurrentReport(mostRecent, autodiag) ? mostRecent : null,
    }
  }
  if (autodiag.enabled === false) {
    return { state: 'disabled', message: '自动诊断已由系统配置关闭；监测与报警仍运行。', ...historyOnly }
  }
  const availability = getAgentAvailability(operation)
  if (!availability.ready) {
    return { state: 'unavailable', message: availability.detail, ...historyOnly }
  }
  return { state: 'idle', message: 'AGH 智能体待命；触发严重报警后才会启动自主诊断。', ...historyOnly }
}
