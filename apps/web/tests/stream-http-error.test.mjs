import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { streamHttpError } from '../src/api/stream-http-error.ts'

test('401 is login expiry, not a persisted answer or model failure', () => {
  assert.equal(streamHttpError(401, '{"detail":"invalid token"}').code, 'AUTH_TOKEN_EXPIRED')
  assert.match(streamHttpError(401, '').message, /尚未提交/)
  const page = readFileSync(new URL('../src/pages/InterviewChatPage.vue', import.meta.url), 'utf8')
  assert.ok(page.includes("code === 'AUTH_TOKEN_EXPIRED'"))
  assert.ok(page.includes('answerPersisted.value = true'))
  assert.ok(page.includes('answerPersisted ?'))
  const store = readFileSync(new URL('../src/stores/auth.ts', import.meta.url), 'utf8')
  assert.ok(store.includes('watch(authExpiredAt, logout'))
})

test('structured HTTP errors preserve business codes without rendering arbitrary response bodies', () => {
  assert.deepEqual(streamHttpError(409, '{"error":{"code":"CONFLICT","message":"状态已变化"}}'), { code: 'CONFLICT', message: '状态已变化' })
  assert.equal(streamHttpError(500, '<html>Traceback secret</html>').message.includes('secret'), false)
})
