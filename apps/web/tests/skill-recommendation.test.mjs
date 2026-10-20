import { test } from 'node:test'
import assert from 'node:assert/strict'
import { applySkillRecommendation, chooseSkill } from '../src/types/skill-selection.ts'
import { interviewTarget } from '../src/types/job-matching.ts'

const rec = id => ({ auto_select: !!id, recommended_skill_id: id })
test('auto selection follows target; uncertain target clears automatic selection', () => {
  let state = { skill_id: '', source: null }
  state = applySkillRecommendation(state, rec('ai_agent_application'), ['ai_agent_application', 'python_backend'])
  assert.equal(state.source, 'AUTO_RECOMMENDED')
  state = applySkillRecommendation(state, rec('python_backend'), ['ai_agent_application', 'python_backend'])
  assert.equal(state.skill_id, 'python_backend')
  assert.equal(applySkillRecommendation(state, rec(null), ['python_backend']).skill_id, '')
})
test('manual selection survives late response and target change', () => {
  const manual = chooseSkill('python_backend')
  assert.deepEqual(applySkillRecommendation(manual, rec('java_backend'), ['python_backend', 'java_backend']), manual)
  assert.deepEqual(applySkillRecommendation(manual, rec(null), ['python_backend']), manual)
})
test('handoff carries a recommendation hint but never private historical context', () => {
  assert.deepEqual(interviewTarget({ company_id: 1, job_id: 2, interviewer_skill_recommendation: rec('python_backend') }),
    { company_id: '1', job_id: '2', recommended_skill_id: 'python_backend' })
})
