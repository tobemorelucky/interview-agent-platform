import { test } from 'node:test'
import assert from 'node:assert/strict'
import { fitLabels, eligibilityLabels, interviewTarget } from '../src/types/job-matching.ts'
import { readFileSync } from 'node:fs'

test('matching uses qualitative bands, and no evidence is not inability', () => {
  assert.deepEqual(Object.keys(fitLabels), ['STRONG_MATCH', 'GOOD_MATCH', 'EXPLORATORY', 'UNCERTAIN'])
  assert.equal(eligibilityLabels.UNKNOWN, '暂无法判断')
  assert.doesNotMatch(Object.values(fitLabels).join(''), /%|\d/)
  const page = readFileSync(new URL('../src/pages/JobMatchingPage.vue', import.meta.url), 'utf8')
  for (const term of ['DEMO / REPLAY', '没证据 ≠ 不会', '当前岗位库覆盖不足', '历史推荐', '开始模拟面试']) assert.ok(page.includes(term))
  assert.ok(page.includes('run.excluded_candidates'))
})

test('recommendation handoff passes only public refs, never historical inputs', () => {
  assert.deepEqual(interviewTarget({ company_id: 12, job_id: 24, profile_version_id: 5, snapshot: 'old' }), { company_id: '12', job_id: '24' })
  const page = readFileSync(new URL('../src/pages/InterviewChatPage.vue', import.meta.url), 'utf8')
  assert.ok(page.includes('t.job_id === jobId && t.company_id === companyId'))
  assert.ok(page.includes('selectedJobId.value = jobId'))
  assert.ok(page.includes('undefined, interviewerSelection.value'))
  assert.ok(!page.includes('await handleCreateSession()')) // user confirms skill/round/style before creation
})
