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

export interface InterviewQuestion {
  question_id: number
  question_index: number
  total_questions?: number
  question: string
  standard_answer?: string | null
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
  id: number
  question_index: number
  question: string
  dimension?: string | null
  difficulty?: string | null
  status: string
  is_dynamic?: boolean
  answer_summary?: string | null
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

export interface InterviewSession {
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
  switch (source) {
    case "KB_RETRIEVED": return "知识库召回"
    case "LLM_GENERATED": return "大模型生成"
    case "HYBRID": return "混合生成"
    default: return source
  }
}

export function sourceColor(source: string): string {
  switch (source) {
    case "KB_RETRIEVED": return "#67c23a"
    case "LLM_GENERATED": return "#409eff"
    case "HYBRID": return "#e6a23c"
    default: return "#909399"
  }
}
