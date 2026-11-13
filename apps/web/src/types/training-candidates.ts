/** Round 16: read-time, rule-generated training candidates. Not a Coach report. */
export type CandidateMode = 'FULL' | 'WEAKNESS' | 'RECHECK'

export interface CandidateObservation {
  evaluation_id: number
  session_id: number
  observed_at: string
  assisted: boolean
  state: string
}

export interface CandidateEvidence {
  capability_source: string
  memory_revision: number | null
  independent_sessions: number
  evaluation_refs: string[]
  observations: CandidateObservation[]
  conflicts: Record<string, unknown>[]
  evidence_note: string
}

export interface TrainingCandidate {
  knowledge_id: string
  topic: string
  label: string
  skill_id: string
  mode: CandidateMode
  available_modes: string[]
  state: string
  state_revision: number | null
  state_last_observed_at?: string | null
  priority: number
  reason: string
  reason_tags: string[]
  evidence: CandidateEvidence
  capability_scope: string
  target_relevance: string
  scope_note: string
  revalidation_note: string
  launch: { path: string; query: Record<string, string> }
}

export interface GeneralCapabilityNote {
  topic: string
  label: string
  state: string
  memory_revision: number | null
  observation_count: number
  relation: string
  note: string
}

export interface CandidateFallback {
  mode: string
  reason: string
  evidence_refs: string[]
  launch: { path: string; query: Record<string, string> }
}

export interface TrainingCandidateSet {
  projection: string
  source: string
  model_calls: number
  computed_at: string
  state_fingerprint?: string
  skill_id?: string | null
  job_target_id?: number | null
  job_id?: number | null
  target_version?: number | null
  status?: string
  error_code?: string
  message?: string
  candidates: TrainingCandidate[]
  general_only: GeneralCapabilityNote[]
  empty_reason?: string | null
  semantics: string
  verified_note?: string
  disclaimer: string
  modes_source?: string
  fallback?: CandidateFallback | null
}

export const candidateModeLabels: Record<string, string> = {
  FULL: '完整模拟面试', WEAKNESS: '薄弱项专项训练', RECHECK: '冲突 / 遗忘独立复测',
}

export const candidateEmptyLabels: Record<string, string> = {
  NO_LEGAL_TARGETS: '当前岗位与专业规则没有合法考察目标，请更换专业方向。',
  NO_HISTORY_EVALUATIONS: '还没有正式面试评价，不能编造弱项或复测状态；可从完整模拟面试开始。',
  NO_ACTIONABLE_WEAKNESS_OR_RECHECK: '当前没有可训练的弱项或待复测目标（相关能力已是跨场次独立支持）。',
  TARGET_EXPIRED_OR_UNAVAILABLE: '目标版本已变化、未确认或不可用；不能把旧建议当作当前可直接执行的训练。',
}
