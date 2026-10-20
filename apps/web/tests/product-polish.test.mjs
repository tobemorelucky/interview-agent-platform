import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { pendingStage } from '../src/types/interview-processing.ts'
const read = name => readFileSync(new URL(name, import.meta.url), 'utf8')
test('evaluation phase survives refresh and reference is not shown before grading', () => {
  assert.equal(pendingStage({status:'RUNNING',retryable:false,processing_stage:'generating_evaluation'}),'generating_evaluation')
  const page=read('../src/pages/InterviewChatPage.vue')
  assert.ok(page.includes('canReview(item) && answerVisibleMap[item.id]'))
  assert.ok(page.includes('当前阶段已等待'))
  assert.ok(page.includes('开始时固定'))
})
test('report and interview reuse the four-part reference without extra model requests', () => {
  const component=read('../src/components/ReferenceAnswer.vue')
  for (const heading of ['回答框架','面试版参考回答','加分点','可能追问']) assert.ok(component.includes(heading))
  assert.ok(read('../src/pages/InterviewReportPage.vue').includes('evaluation.reference_answer'))
  assert.ok(read('../src/pages/InterviewReportPage.vue').includes('本场建议记住'))
  assert.ok(!component.includes('fetch('))
})
