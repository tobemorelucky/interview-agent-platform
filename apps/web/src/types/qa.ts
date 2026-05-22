export interface Citation {
  chunk_id: number
  doc_id: number
  title: string
  source_type: string
  preview: string
  score: number | null
}

export interface ChatMessage {
  id: number
  session_id: number
  role: "user" | "assistant" | "system"
  content: string
  citations_json: Citation[] | null
  created_at: string | null
}

export interface ChatSession {
  id: number
  user_id: number
  title: string | null
  created_at: string | null
  updated_at: string | null
}

export interface SessionDetail extends ChatSession {
  messages: ChatMessage[]
}

export interface KbDocument {
  id: number
  title: string
  source_type: string
  status: string
  chunk_count: number
  error_message: string | null
  task_id: string | null
  processing_started_at: string | null
  processing_finished_at: string | null
  uploaded_by: number | null
  created_at: string | null
  indexed_at: string | null
}

export interface KbChunk {
  id: number
  document_id: number
  chunk_index: number
  content: string
  embedding_status: string
  token_count: number | null
  created_at: string | null
}

export interface KbDocumentDetail extends KbDocument {
  chunks: KbChunk[]
}

export interface KbDocumentList {
  items: KbDocument[]
  total: number
}

/** RAG pipeline stage exposed via SSE status events. */
export type RagStage =
  | "analyzing_query"
  | "embedding_query"
  | "retrieving"
  | "generating"

export interface RetrievalInfo {
  top_k: number
  hit_count: number
}
