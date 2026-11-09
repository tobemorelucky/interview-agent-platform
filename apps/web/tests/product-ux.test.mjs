import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { userNavigation, adminNavigation, navigationActive, displayLabel, userError, modelModeLabel, dimensionLabels } from '../src/i18n/zh-CN.ts'
test('one navigation source covers the entire user journey and keeps administration separate', () => {
  assert.deepEqual(userNavigation.map(n => n.label), ['首页','我的简历','我的画像','岗位推荐','公共资料','训练中心','模拟面试','面试报告','成长记录'])
  assert.ok(adminNavigation.every(n => n.path.startsWith('/admin/')))
  assert.ok(navigationActive('/jobs/matches/12', '/jobs/matches'))
  assert.ok(navigationActive('/resumes/12', '/resumes'))
  assert.ok(navigationActive('/interview/reports/12', '/interview/reports'))
  assert.equal(navigationActive('/interview/reports/12', '/interview'), false)
})
test('raw enums and provider errors are not ordinary user labels', () => {
  assert.equal(displayLabel('UNKNOWN'), '暂无法判断')
  assert.equal(displayLabel('STRONG_MATCH'), '高度匹配')
  assert.equal(displayLabel('INTERNAL_UNKNOWN_ENUM'), '未说明')
  assert.match(userError('UNSUPPORTED_SKILL_STATE'), /简历证据/)
  assert.doesNotMatch(userError('INTERNAL_ERROR', 'Traceback password'), /Traceback|password/)
  assert.equal(Object.keys(dimensionLabels).length, 5)
})
test('DEMO Replay and actual live model labels cannot be confused', () => {
  assert.match(modelModeLabel('replay', 'fixture'), /DEMO.*Replay.*不是 Live/)
  assert.equal(modelModeLabel('openai_compatible', 'deepseek-v4-pro-ga-260813'), 'DeepSeek 实时模型')
  assert.equal(modelModeLabel(null, null), '')
})
test('report separates statistics, coach, review and memory; interview refresh pins session', () => {
  const page = readFileSync(new URL('../src/pages/InterviewReportPage.vue', import.meta.url), 'utf8')
  for (const word of ['本场数据','Coach 分析','逐题复盘','长期记忆建议','修改后批准']) assert.ok(page.includes(word))
  const interview = readFileSync(new URL('../src/pages/InterviewChatPage.vue', import.meta.url), 'utf8')
  assert.ok(interview.includes('query: { session_id: id }'))
  assert.ok(interview.includes('route.query.session_id'))
})

test('growth record does not print the same content as its summary twice', () => {
  const memory = readFileSync(new URL('../src/pages/MemoryPage.vue', import.meta.url), 'utf8')
  assert.ok(memory.includes('item.summary.trim() !== item.content.trim()'))
})
