import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('interview keeps the question prominent and the provenance secondary', () => {
  const page = read('../src/pages/InterviewChatPage.vue')
  assert.ok(page.includes('class="round-progress"'))
  assert.ok(page.includes('第 {{ (item.question_index ?? 0) + 1 }} /'))
  assert.ok(page.includes('<details class="session-context-detail">'))
  assert.ok(page.includes('aria-live="polite"'))
  assert.ok(page.includes('canReview(item) && answerVisibleMap[item.id]'))
  assert.ok(!page.includes('面试官 Agent'))
  assert.ok(!page.includes('数据库统计：'))
})

test('recommendation/report use shared modest visual styling and collapsed detail', () => {
  const css = read('../src/styles/product.css')
  assert.ok(css.includes('.matching-page .card, .report-page .card'))
  assert.ok(css.includes('.interview-page .question-bubble'))
  assert.ok(read('../src/pages/JobMatchingPage.vue').includes('查看详细依据'))
  assert.ok(read('../src/pages/InterviewReportPage.vue').includes('<summary>报告版本历史</summary>'))
})
