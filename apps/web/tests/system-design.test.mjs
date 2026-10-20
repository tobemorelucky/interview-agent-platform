import { test } from 'node:test'
import assert from 'node:assert/strict'
import { pendingStage } from '../src/types/interview-processing.ts'
import { readFileSync } from 'node:fs'

test('design processing restores an active lease without showing idle', () => {
  assert.equal(pendingStage({ status: 'RUNNING', retryable: false, processing_stage: 'validating_design' }), 'validating_design')
  assert.equal(pendingStage({ status: 'FAILED', retryable: true, processing_stage: 'validating_design' }), 'idle')
})

test('UI contract keeps latest plan on refresh and report uses shared actual trace', () => {
  const read = p => readFileSync(new URL(p, import.meta.url), 'utf8')
  assert.ok(read('../src/pages/InterviewChatPage.vue').includes('design_revisions?.slice(-1)[0]?.plan'))
  assert.ok(read('../src/pages/InterviewReportPage.vue').includes('<DesignTraceView :trace="detail?.design_trace"'))
  const component = read('../src/components/DesignTrace.vue')
  assert.ok(component.includes('不代表真实生产环境压测'))
  assert.ok(component.includes('tool.result.accepted_count'))
})
