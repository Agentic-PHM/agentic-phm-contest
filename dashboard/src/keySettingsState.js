/** Project the settings API response into a UI-safe view with no credential fields. */
export function getKeyStatusView(status, failed = false) {
  if (failed || !status || typeof status.configured !== 'boolean') {
    return { configured: null, label: '无法读取 AGH 凭据状态' }
  }
  return status.configured
    ? { configured: true, label: 'Agnes Key 已配置' }
    : { configured: false, label: '尚未配置 Agnes Key' }
}
