import type { SkillRecommendation } from './interview.ts'

export interface SkillChoice { skill_id: string; source: 'AUTO_RECOMMENDED' | 'USER_SELECTED' | null }

// Pure reducer: late catalog/job responses cannot overwrite explicit user intent.
export function applySkillRecommendation(current: SkillChoice, recommendation: SkillRecommendation | null | undefined, available: string[]): SkillChoice {
  if (current.source === 'USER_SELECTED') return current
  const id = recommendation?.auto_select ? recommendation.recommended_skill_id : null
  return id && available.includes(id) ? { skill_id: id, source: 'AUTO_RECOMMENDED' } : { skill_id: '', source: null }
}

export function chooseSkill(skill_id: string): SkillChoice {
  return { skill_id, source: skill_id ? 'USER_SELECTED' : null }
}
