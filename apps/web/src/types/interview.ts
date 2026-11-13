import { displayLabel } from '../i18n/zh-CN';
export interface InterviewMessage {
  id: number
  role: "USER" | "ASSISTANT" | "SYSTEM"
  content: string
  metadata_json: InterviewMessageMeta | null
  turn_index: number
  created_at: string
}

export interface RetrievedContextItem {
  chunk_id: number | null
  title: string
  preview: string
  score: number
  source_type: string
}

export interface EvidenceItem {
  chunk_id: number | null
  doc_id: number | null
  title: string
  source_type: string
  preview: string
  score: number
}

// SSE Events
export interface SSERetrievalEvent {
  hit_count: number
  source: "KB_RETRIEVED" | "LLM_GENERATED" | "HYBRID"
}

export interface SSECitationEvent {
  chunk_id: number | null
  doc_id: number | null
  title: string
  source_type: string
  preview: string
  score: number
}

export interface SSETokenEvent {
  content: string
}

export interface SSEDoneEvent {
  message_id: number
  turn_index: number
  source: string
  compressed: boolean
}

export interface SSECompressedEvent {
  compressed_turns: number
  new_last_compressed_turn: number
}

export interface SSEErrorEvent {
  code: string
  message: string
}

// Phase 3.6: Question & New SSE types

export interface ReferenceAnswer {
  answer_framework: string[]
  interview_answer: string
  bonus_points: string[]
  possible_follow_ups: string[]
}

export interface InterviewQuestion {
  assessment_topic?: string
  question_id: number
  question_index: number
  total_questions?: number
  question: string
  standard_answer?: string | null
  reference_answer?: ReferenceAnswer | null
  dimension?: string | null
  difficulty?: string | null
  source: string
  evidence?: unknown
  status?: string
  follow_up_count?: number
  is_dynamic?: boolean
  parent_question_id?: number | null
  standard_answer_hidden?: boolean
}

export interface InterviewQuestionSummary {
  assessment_topic?: string
  id: number
  question_index: number
  question: string
  dimension?: string | null
  difficulty?: string | null
  status: string
  is_dynamic?: boolean
  answer_summary?: string | null
  parent_question_id?: number | null
  question_kind?: string
}

export interface InterviewQuestionList {
  questions: InterviewQuestionSummary[]
  total: number
  current_question_index: number
  question_generation_status: string
}

export interface StartInterviewResult {
  type: string
  question_id: number
  question_index: number
  total_questions: number
  question: string
  dimension?: string | null
  difficulty?: string | null
  source: string
  evidence?: unknown
}

export interface TargetPositionResult {
  target_position_confirmed: boolean
  question_budget: number
  current_question?: {
    question_id: number
    question_index: number
    question: string
    dimension?: string | null
    difficulty?: string | null
    source: string
    evidence?: unknown
  }
}

export interface InterviewMemoryConsolidateResult {
  session_id: number
  episodic_memory_created: boolean
  episodic_memory_updated?: boolean
  preferences_created: number
  preferences_updated?: number
  skills_updated: number
  events_created: number
  episodic_memory_id?: number | null
  existing_memory_id?: number | null
}

export interface InterviewSessionDetail extends InterviewSession {
  pending_answer?: { question_id: number; idempotency_key: string; content: string; status: string; error: string | null; processing_stage?: string; processing_deadline?: string; retryable?: boolean; design_plan?: DesignPlan } | null
  memory_summary: string | null
  messages: InterviewMessage[]
  questions?: InterviewQuestion[]
  current_question_index?: number
  question_generation_status?: string
  total_questions?: number
  target_position?: string | null
  target_position_confirmed?: boolean
  interview_mode?: string
  interview_plan_json?: unknown
  question_count?: number
}

export type InterviewerSkillId = string
export interface DesignPlan { deduplication: 'NONE' | 'CHECK_THEN_WRITE' | 'ATOMIC_KEY'; capacity_guard: 'NONE' | 'CHECK_THEN_WRITE' | 'ATOMIC_RESERVATION'; capacity: number }
export interface DesignTrace {
  design_revisions: { version: number; answer_id: number; plan: DesignPlan }[]
  design_tools: { id: string; plan_version: number; tool: string; status: string; error?: string; result?: { passed: boolean; submitted_count: number; accepted_count: number; violation?: string | null } | null }[]
}
export interface SkillRecommendation {
  policy_version: string; job_id?: number; jd_id?: number; jd_fingerprint?: string
  status: string; auto_select: boolean; recommended_skill_id: string | null; recommended_version: string | null; reason: string
  ranking: { skill_id: string; version: string; label: string; rank: number; excluded: boolean; reason: string
    evidence: { field: string; requirement_id?: number | null; term: string; excerpt: string }[] }[]
}
export type InterviewerRound = "technical_screen" | "project_deep_dive"
export type InterviewerStyle = "gentle" | "standard" | "strict"
export interface InterviewerSelection {
  adaptive_interview?: boolean
  design_interview?: boolean
  interviewer_skill_id: InterviewerSkillId
  interview_round: InterviewerRound
  interviewer_style: InterviewerStyle
  interviewer_selection_source?: 'AUTO_RECOMMENDED' | 'USER_SELECTED'
}
export interface InterviewerConfig {
  engine_version: string
  content_fingerprint: string
  skill: { id: InterviewerSkillId; label: string; version: string; domains: string[] }
  round: { id: InterviewerRound; label: string; version: string; focus: string }
  style: { id: InterviewerStyle; label: string; version: string }
  company_background: { mode: string; instruction: string; source?: { source_type: string; url: string } | null }
}
export interface InterviewerOptions {
  skills: { id: InterviewerSkillId; label: string; version: string }[]
  rounds: { id: InterviewerRound; label: string; description: string }[]
  styles: { id: InterviewerStyle; label: string; description: string }[]
}

export interface InterviewSession {
  planner_trace_json?: DesignTrace
  workflow_mode?: "legacy" | "m4"
  legacy?: boolean
  active_question_id?: number | null
  state_version?: number
  context_snapshot?: { adaptive_policy?: { version: string }; adaptive_blueprint?: { targets: { label: string }[] }; company_name: string; job_title: string; profile_version_number: number; jd_version_number: number; job_target?: { id: number; version: number }; coverage_insufficient: boolean; profile?: unknown; interviewer_config?: InterviewerConfig; interviewer_selection?: { source: 'AUTO_RECOMMENDED' | 'USER_SELECTED'; skill_id: string }; approved_memory_context?: { items: { id: number; content: string }[]; omitted_count?: number }; recent_performance_observations?: { items: { id: number; content: string }[] } }
  completion_json?: { overall_average: number | null; dimension_averages: Record<string, number | null>; answered_count: number; evaluated_count: number; mode: string }
  id: number
  user_id: number
  resume_id: number | null
  title: string | null
  status: string
  current_question_index?: number
  question_generation_status?: string
  question_generation_error?: string | null
  total_questions?: number
  turn_count?: number
  last_compressed_turn?: number
  target_position?: string | null
  target_position_confirmed?: boolean
  interview_mode?: string
  interview_plan_json?: unknown
  question_count?: number
  resume_filename?: string | null
  resume_status?: string | null
  created_at?: string
  updated_at?: string
}

export interface InterviewMessageMeta {
  [key: string]: unknown  // allow dynamic fields from SSE events
  retrieval_queries?: string[]
  retrieved_context?: RetrievedContextItem[]
  source?: string
  evidence?: EvidenceItem[] | unknown[]
  compressed?: boolean
  type?: "QUESTION" | "EVALUATION" | "FOLLOW_UP" | "DYNAMIC_QUESTION" | "POSITION_CONFIRMED" | "POSITION_SUGGESTION" | "INTERVIEW_COMPLETE"
  question_id?: number
  question_index?: number
  action?: string
  score?: number
  is_follow_up?: boolean
  covered_points?: string[]
  missing_points?: string[]
  risk_tip?: string
  target_position?: string
  dimension?: string
  difficulty?: string
  follow_up_count?: number
  max_follow_ups?: number
  source_label?: string
  parent_question_id?: number
}

// SSE evaluation event
export interface EvaluationEvent {
  score: number
  evaluation: string
  covered_points?: string[]
  missing_points?: string[]
  risk_tip?: string
  action: string
}

export function sourceLabel(source: string): string {
  return displayLabel(source)
}

export function sourceColor(source: string): string {
  switch (source) {
    case "KB_RETRIEVED": return "#67c23a"
    case "LLM_GENERATED": return "#409eff"
    case "HYBRID": return "#e6a23c"
    default: return "#909399"
  }
}
