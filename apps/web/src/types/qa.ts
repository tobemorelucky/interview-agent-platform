export interface Citation {
  chunk_id: number
  doc_id: number
  title: string
  content: string
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
