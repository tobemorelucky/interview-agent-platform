export interface InterviewSession {
  id: number
  user_id: number
  resume_id: number | null
  title: string | null
  status: string
  turn_count: number
  last_compressed_turn: number
  resume_filename: string | null
  resume_status: string | null
  created_at: string
  updated_at: string
}

export interface InterviewMessage {
  id: number
  role: "USER" | "ASSISTANT" | "SYSTEM"
  content: string
  metadata_json: InterviewMessageMeta | null
  turn_index: number
  created_at: string
}

export interface InterviewMessageMeta {
  retrieval_queries?: string[]
  retrieved_context?: RetrievedContextItem[]
  source?: "KB_RETRIEVED" | "LLM_GENERATED" | "HYBRID"
  evidence?: EvidenceItem[]
  compressed?: boolean
  token_estimate?: number
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

export interface InterviewSessionDetail extends InterviewSession {
  memory_summary: string | null
  messages: InterviewMessage[]
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
