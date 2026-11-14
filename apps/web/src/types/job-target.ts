/** Private JD targets: user-supplied text, never a public company/job record. */
export type TargetStatus = 'DRAFT' | 'PARSED' | 'CONFIRMED' | 'FAILED'
export type Importance = 'REQUIRED' | 'PREFERRED' | 'UNKNOWN'
/** Why the server gave a requirement its strength; the UI shows it verbatim. */
export type RequirementBasis =
  | 'EXPLICIT_MANDATORY' | 'LISTED_UNDER_REQUIREMENTS' | 'EXPLICIT_PREFERRED'
  | 'DUTY_SECTION' | 'NOT_STATED' | 'USER_PROVIDED'

export interface JobTargetRequirement {
  id: number
  statement: string
  importance: Importance
  basis?: RequirementBasis
  evidence?: string | null
  topic?: string | null
  source?: string
}

export interface JobTarget {
  id: number
  company_name: string
  job_title: string
  source_type: string
  source_label: string
  source_reference?: string | null
  content_fingerprint: string
  parse_status: TargetStatus
  parse_mode: 'LLM' | 'RULE' | null
  parse_error?: string | null
  version: number
  requirement_count: number
  confirmed_at?: string | null
  /** Round 22 (L2): lifecycle timestamp; archived targets keep their history. */
  archived_at?: string | null
  created_at?: string | null
  updated_at?: string | null
  raw_text?: string
  requirements?: JobTargetRequirement[]
  structured?: { responsibilities?: string[] }
  /** Only present on a create response: the same text already existed. */
  reused?: boolean
}

export interface JobTargetMatchRow {
  id: string
  statement: string
  status: 'SUPPORTED' | 'PARTIAL' | 'NO_EVIDENCE' | 'NOT_APPLICABLE'
  message: string
  supports: { candidate_fact_id?: number; category?: string; explanation?: string }[]
}

export interface JobTargetMatch {
  target_id: number
  /** Per-requirement: the strongest support wins, so one row may hold weaker facts. */
  counts: Record<string, number>
  /** Per cited fact: explains why a row can show PARTIAL while counts.PARTIAL is 0. */
  support_counts: Record<string, number>
  applicable_requirements: number
  evidence_band: string
  rows: JobTargetMatchRow[]
  disclaimer: string
}

export const statusLabels: Record<TargetStatus, string> = {
  DRAFT: '待解析', PARSED: '待确认', CONFIRMED: '已确认', FAILED: '解析失败',
}
export const evidenceLabels: Record<string, string> = {
  SUPPORTED: '有材料支持', PARTIAL: '部分支持', NO_EVIDENCE: '无材料证据', NOT_APPLICABLE: '非技术条件',
}
export const importanceLabels: Record<Importance, string> = {
  REQUIRED: '硬性要求', PREFERRED: '加分项', UNKNOWN: '原文未标明',
}
export const basisLabels: Record<RequirementBasis, string> = {
  EXPLICIT_MANDATORY: '原文写明必须或最低门槛（必须 / 至少 / 及以上）',
  LISTED_UNDER_REQUIREMENTS: '列在任职要求里，原文没有写必须',
  EXPLICIT_PREFERRED: '原文写明优先或加分',
  DUTY_SECTION: '出现在职责描述里，不是任职资格',
  NOT_STATED: '原文没有可识别的小节，性质未标明',
  USER_PROVIDED: '由你添加或修改',
}

/** Round 16: deterministic Skill suggestions for one confirmed private JD. */
export interface SkillEvidenceHit {
  field: string
  requirement_id?: number | null
  matched_term?: string | null
  excerpt?: string | null
  requirement_statement?: string | null
  verbatim_jd_evidence?: string | null
  evidence_status: string
}

export interface SkillRecommendationCandidate {
  skill_id: string
  version: string
  label: string
  rank: number
  ranking_points: number
  /** Points from requirements, duties and capability words (never the title). */
  content_points: number
  title_points: number
  signals: string[]
  excluded: boolean
  contextual: boolean
  eligible: boolean
  uncertain: boolean
  matched_reason: string
  reason_kind: string
  reason_note: string
  evidence: SkillEvidenceHit[]
}

export interface TargetSkillRecommendation {
  projection: string
  target_id: number
  target_version: number
  parse_status: TargetStatus
  confirmed_at?: string | null
  content_fingerprint: string
  input_fingerprint: string
  source: string
  model_calls: number
  skill_registry_read_only: boolean
  ranking_semantics: string
  disclaimer: string
  policy_version: string
  status: string
  auto_select: boolean
  recommended_skill_id: string | null
  recommended_version: string | null
  message: string
  reason?: string | null
  /** Set when the title and the requirements point at different directions. */
  ambiguity_reason?: 'TITLE_CONFLICTS_WITH_REQUIREMENTS' | 'TITLE_ONLY_LEADER' | 'CLOSE_CANDIDATES' | null
  /** Minimum content points before any skill can be selected automatically. */
  content_threshold: number
  needs_manual_confirmation: boolean
  candidates: SkillRecommendationCandidate[]
  empty_reason?: string | null
  confidence_note: string
  /** Set when this response was checked against an earlier hand-off key. */
  stale?: boolean
  stale_reason?: string | null
}

export const evidenceStatusLabels: Record<string, string> = {
  VERBATIM_FROM_JD: 'JD 原文逐字证据',
  USER_ADDED_NO_VERBATIM_QUOTE: '你手动补充的要求，无逐字原文片段',
  STRUCTURED_DUTY_NOT_VERBATIM_CHECKED: '解析出的职责描述，未做逐字校验',
  USER_SUPPLIED_TITLE_NOT_A_JD_QUOTE: '岗位标题命中，不是 JD 原文引用',
  REQUIREMENT_NOT_FOUND_IN_CONFIRMED_VERSION: '该要求不在当前已确认版本中',
  FIELD_NOT_VERBATIM_VERIFIABLE: '该字段没有可核对的逐字原文',
}
export const recommendationStatusLabels: Record<string, string> = {
  RECOMMENDED: '有明确领先方向', AMBIGUOUS: '多个方向接近，请手动选择',
  NO_APPLICABLE_SKILL: '没有充分适用的专业规则', INSUFFICIENT_DATA: '已确认要求不足',
  NOT_CONFIRMED: 'JD 尚未确认',
}
export const recommendationEmptyLabels: Record<string, string> = {
  JD_NOT_CONFIRMED: '该 JD 尚未确认；只有已确认版本才提供正式推荐。',
  NO_REGISTERED_SKILL: '当前没有合法注册的可用 Skill，不能给出推荐方向。',
  NO_ELIGIBLE_CANDIDATE: '已注册 Skill 与这份 JD 没有充分命中的证据，不编造相关性；请手动选择。',
}
