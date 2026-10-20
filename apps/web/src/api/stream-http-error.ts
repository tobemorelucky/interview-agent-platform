/** HTTP rejection occurs before the answer stream is accepted; never claim persistence. */
export function streamHttpError(status: number, body: string): { code: string; message: string } {
  if (status === 401) return { code: 'AUTH_TOKEN_EXPIRED', message: '登录已过期，请重新登录。本次回答尚未提交。' }
  try {
    const data = JSON.parse(body)
    const error = data.error || data
    if (typeof error.code === 'string') return { code: error.code, message: typeof error.message === 'string' ? error.message : '请求未完成，请刷新确认状态。' }
  } catch { /* Do not display HTML, provider payloads or stack traces. */ }
  return { code: 'HTTP_ERROR', message: '请求未完成，请刷新确认状态后重试。' }
}
