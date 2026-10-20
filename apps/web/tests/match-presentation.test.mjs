import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { presentMatch, canReviewQuestion } from '../src/types/match-presentation.ts'
import { userError } from '../src/i18n/zh-CN.ts'

const support = (id, keys = ['e1']) => ({ candidate_fact_id: id, category: 'SKILL', evidence_ids: keys, explanation: '相同匹配理由' })
const row = (id, status, supports = []) => ({ id: `req:${id}`, job_requirement_id: id, statement: id === 1 ? 'Python / Java 编程' : `要求${id}`, status, supports })
const fact = (id, label) => ({ id, label, category: 'SKILL' })
const evidence = (key, excerpt) => ({ key, excerpt, id: 1, kind: 'RESUME', section: 'skills' })
function candidate(matrix) {
  return { evidence_matrix: matrix, eligibility_reasons: [
    { field: 'locations', status: 'NOT_APPLICABLE', reason: '未设置地区筛选' },
    { field: 'education_minimum', status: 'UNKNOWN', reason: '学历待确认' }],
    recommendation: { analysis: { preparation_focus: [row(2, 'NO_EVIDENCE'), row(2, 'NO_EVIDENCE')] } } }
}

test('requirement, fact, evidence, explanation and preparation are deterministically deduplicated', () => {
  const result = presentMatch(candidate([row(1, 'SUPPORTED', [support(1), support(1), support(2)]), row(1, 'SUPPORTED', [support(1)]), row(2, 'NO_EVIDENCE'), row(2, 'NO_EVIDENCE')]),
    [fact(1, 'Java'), fact(2, 'Redis')], [evidence('e1', 'Java Redis 项目')])
  assert.equal(result.requirements.length, 2)
  assert.equal(result.requirements[0].supports.length, 2)
  assert.deepEqual(result.requirements[0].explanations, ['相同匹配理由'])
  assert.equal(result.requirements[0].evidence.length, 1)
  assert.deepEqual(result.highlights, ['Java 有相关材料依据']) // no fabricated Redis requirement match
  assert.equal(result.gaps.length, 1)
  assert.equal(result.preparation.length, 1)
  assert.deepEqual(result.risks, ['要求2：当前材料未覆盖', '硬资格待确认：学历要求'])
  assert.equal(result.eligibility.length, 1)
})

test('evidence defaults to at most three distinct excerpts and repeated facts are not rendered again', () => {
  const keys = ['a', 'b', 'c', 'd', 'copy']
  const result = presentMatch(candidate([row(1, 'SUPPORTED', [support(1, keys)]), row(3, 'PARTIAL', [support(1, keys)])]),
    [fact(1, 'Java')], keys.map(k => evidence(k, k === 'copy' ? 'a' : k)))
  assert.equal(result.requirements[0].evidence.length, 3)
  assert.equal(result.requirements[0].moreEvidence.length, 1)
  assert.equal(result.requirements[1].evidence.length, 0)
  assert.equal(result.requirements[1].supports.length, 0)
  assert.equal(result.highlights.length, 2)
})

test('highlights and risk summaries are bounded; missing historical facts remain readable', () => {
  const result = presentMatch(candidate(Array.from({ length: 8 }, (_, i) => row(i + 10, i < 5 ? 'SUPPORTED' : 'NO_EVIDENCE'))), [], [])
  assert.equal(result.highlights.length, 4)
  assert.equal(result.risks.length, 3)
  const page = readFileSync(new URL('../src/pages/JobMatchingPage.vue', import.meta.url), 'utf8')
  assert.ok(page.indexOf('<summary>查看详细依据</summary>') < page.indexOf('card.candidate.pre_rank'))
  assert.ok(page.includes("r.status !== 'NOT_APPLICABLE'"))
})

test('reference answer and technical details are unavailable before a valid evaluation for that question', () => {
  assert.equal(canReviewQuestion(1, []), false)
  assert.equal(canReviewQuestion(2, [1]), false)
  assert.equal(canReviewQuestion(undefined, [1]), false)
  assert.equal(canReviewQuestion(1, [1]), true)
  const page = readFileSync(new URL('../src/pages/InterviewChatPage.vue', import.meta.url), 'utf8')
  assert.ok(page.includes('v-if="canReview(item)" class="q-answer-btn"'))
  assert.ok(page.includes('v-if="canReview(item) && item.source_fingerprint"'))
  assert.ok(page.includes('!canReview(item)) return'))
})

test('doctorate constraint is visible even with many technical gaps; qualification stays unknown', () => {
  const input = candidate(Array.from({ length: 6 }, (_, i) => row(i + 2, 'NO_EVIDENCE')))
  input.matching_constraints = { education_minimum: '博士' }
  const result = presentMatch(input, [], [])
  assert.equal(result.eligibility[0].status, 'UNKNOWN')
  assert.equal(result.eligibility[0].required, '博士')
  assert.equal(result.risks[0], '学历要求：博士（暂无法判断）')
  assert.ok(result.risks.length <= 3)
})

test('deduplicating a fact retains all of its evidence without mutating the matrix', () => {
  const input = candidate([row(1, 'SUPPORTED', [support(1, ['a']), support(1, ['b'])])])
  const before = JSON.stringify(input)
  const result = presentMatch(input, [fact(1, 'Java')], [evidence('a', '正文a'), evidence('b', '正文b')])
  assert.deepEqual(result.requirements[0].supports[0].evidence_ids, ['a', 'b'])
  assert.equal(result.requirements[0].evidence.length, 2)
  assert.equal(JSON.stringify(input), before)
})

test('a JavaScript requirement does not become a Java highlight', () => {
  const item = { ...row(1, 'SUPPORTED', [support(1)]), statement: 'JavaScript 项目' }
  const result = presentMatch(candidate([item]), [fact(1, 'Java')], [evidence('e1', '原始依据')])
  assert.deepEqual(result.highlights, ['已有材料支持：JavaScript 项目'])
})

test('failed Coach generation uses Chinese recovery text rather than an internal code', () => {
  const result = userError('COACH_COMPUTED_NUMBER')
  assert.ok(result.includes('评分与完成状态已保留'))
  assert.ok(!result.includes('COACH_COMPUTED_NUMBER'))
  const page = readFileSync(new URL('../src/pages/InterviewReportPage.vue', import.meta.url), 'utf8')
  assert.ok(page.includes('userError(report.error || "REPORT_FAILED")'))
})
