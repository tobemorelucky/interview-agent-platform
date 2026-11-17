import type { SkillRecommendation } from './interview'
export type FitBand = 'STRONG_MATCH' | 'GOOD_MATCH' | 'EXPLORATORY' | 'UNCERTAIN'
export type Eligibility = 'SATISFIED' | 'UNKNOWN' | 'NOT_SATISFIED'
export interface MatchFilters {
  campus_types: string[]; job_families: string[]; locations: string[]; tags: string[]
  exclude_company_ids: number[]; exclude_job_ids: number[]; exclude_tags: string[]
}
export interface MatchEvidence { id: number; key: string; kind: string; excerpt: string; section: string; resume_id?: number; page?: number | null; start_offset?: number; end_offset?: number }
export interface MatchSupport {
  candidate_fact_id: number; category: string; record_id: number | null; status: string
  evidence_ids: string[]; explanation: string; evidence?: MatchEvidence[]
}
export interface MatrixRow { id: string; job_requirement_id: number; statement: string; status: string; message: string; supports: MatchSupport[] }
export interface MatchCandidate {
  interviewer_skill_recommendation?: SkillRecommendation
  id: number; company_id: number; job_id: number; company_name: string; title: string
  jd_id: number; jd_version_number: number; jd_fingerprint: string; demo: boolean
  eligibility_status: Eligibility; eligibility_reasons: { field: string; status: string; reason: string }[]
  unknown_constraints: string[]; evidence_matrix: MatrixRow[]
  matching_constraints?: Record<string, unknown>
  pre_rank: number | null; agent_rank: number | null; final_rank: number | null
  ranking_components: {
    fit_band_ceiling: FitBand
    evidence_basis?: 'NONE' | 'PARTIAL' | 'SUPPORTED'
    required_counts?: { SUPPORTED: number; PARTIAL: number; NO_EVIDENCE: number }
    preferred_supported?: number
    preferred_total?: number
  }
  recommendation: null | { id: number; fit_band: FitBand; analysis: {
    matched_strengths: MatchSupport[]; alignment_reasons: MatchSupport[]
    current_evidence_gaps: MatrixRow[]; preparation_focus: MatrixRow[]; unknowns_to_confirm: string[]
  } }
}
export interface MatchRun {
  id: number; status: string; error: string | null; producer: string; provider: string | null
  model: string | null; profile_version_id: number; policy_version: string; prompt_version: string
  input_fingerprint: string; created_at: string; candidates: MatchCandidate[]; excluded_candidates: MatchCandidate[]
  coverage: { status: string; candidate_count: number; excluded_count: number; top_n: number; window_may_be_truncated: boolean; message: string; generation_mode?: string; reused_from_run_id?: number; ranking_confidence?: 'EVIDENCE' | 'NONE' }
  snapshot: { data_mode: string; profile: { evidence: MatchEvidence[]; facts?: { id: number; label: string; category: string; evidence_state?: string | null }[] }; request: { filters: MatchFilters }; approved_preferences: { items: { id: number; content: string }[]; omitted_count: number } }
  attempts: { id: number; status: string; error: string | null; agent_run_id: number | null }[]
}
export type MatchHistory = Pick<MatchRun, 'id' | 'status' | 'producer' | 'profile_version_id' | 'created_at' | 'coverage'>
export { fitLabels, eligibilityLabels } from '../i18n/zh-CN.ts'
export function interviewTarget(candidate: Pick<MatchCandidate, 'company_id' | 'job_id' | 'interviewer_skill_recommendation'>) {
  const skill = candidate.interviewer_skill_recommendation
  return { company_id: String(candidate.company_id), job_id: String(candidate.job_id),
    ...(skill?.auto_select && skill.recommended_skill_id ? { recommended_skill_id: skill.recommended_skill_id } : {}) }
}
