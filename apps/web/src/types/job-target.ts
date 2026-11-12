/** Private JD targets: user-supplied text, never a public company/job record. */
export type TargetStatus = 'DRAFT' | 'PARSED' | 'CONFIRMED' | 'FAILED'
export type Importance = 'REQUIRED' | 'PREFERRED' | 'UNKNOWN'

export interface JobTargetRequirement {
  id: number
  statement: string
  importance: Importance
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
  created_at?: string | null
  updated_at?: string | null
  raw_text?: string
  requirements?: JobTargetRequirement[]
  structured?: { responsibilities?: string[] }
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
  counts: Record<string, number>
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
