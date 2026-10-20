import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')

test('creation reads the server catalog and sends all three interviewer choices', () => {
  const page = read('../src/pages/InterviewChatPage.vue')
  const api = read('../src/api/interview.ts')
  for (const label of ['岗位专业 Skill', '面试轮次', '面试官风格']) assert.ok(page.includes(`aria-label="${label}"`))
  assert.ok(page.includes('getInterviewerOptions()'))
  assert.ok(page.includes('undefined, interviewerSelection.value'))
  assert.ok(api.includes('/interview/interviewer-options'))
  assert.ok(api.includes('...interviewer'))
})

test('session display uses the frozen selection and preserves old sessions', () => {
  const page = read('../src/pages/InterviewChatPage.vue')
  assert.ok(page.includes('profileContext.value?.interviewer_config'))
  assert.ok(page.includes('本场面试官配置（已固定）'))
  assert.ok(page.includes('原有通用面试策略'))
  assert.ok(page.includes('表达提示：{{ item.interviewer_guidance }}'))
  assert.ok(page.includes('严格不等于更难或额外扣分'))
  assert.ok(!page.includes('await handleCreateSession()')) // recommendations allow selection before creation
})
