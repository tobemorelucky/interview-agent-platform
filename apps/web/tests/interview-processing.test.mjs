import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { pendingStage } from '../src/types/interview-processing.ts'

test('refresh shows the persisted stage only during an active bounded lease', () => {
  assert.equal(pendingStage({ status: 'RUNNING', retryable: false }), 'evaluating')
  assert.equal(pendingStage({ status: 'RUNNING', retryable: false, processing_stage: 'generating_next_question' }), 'generating_next_question')
  assert.equal(pendingStage({ status: 'RUNNING', retryable: true }), 'idle')
  assert.equal(pendingStage({ status: 'FAILED', retryable: true }), 'idle')
  assert.equal(pendingStage(null), 'idle')
})
test('UI polls actual persistence, blocks parallel answer submit and cleans timers', () => {
  const page = readFileSync(new URL('../src/pages/InterviewChatPage.vue', import.meta.url), 'utf8')
  assert.ok(page.includes('processingAnswer.value'))
  assert.ok(page.includes('pendingStage(detail.pending_answer)'))
  assert.ok(page.includes('stopAnswerPoll()'))
  assert.ok(page.includes('原回答'))
})
test('existing match detail and history load concurrently without model regeneration', () => {
  const page = readFileSync(new URL('../src/pages/JobMatchingPage.vue', import.meta.url), 'utf8')
  const loader = page.slice(page.indexOf('async function load()'), page.indexOf('watch(() => route.params.id'))
  assert.ok(loader.includes('Promise.all'))
  assert.ok(!loader.includes('generateMatch'))
})
