import client from "./client";

export interface UserMemoryItem {
  id: number
  user_id: number
  memory_type: string
  scope: string
  key?: string | null
  content: string
  summary?: string | null
  metadata_json?: Record<string, unknown> | null
  confidence: number
  importance: number
  source_type?: string | null
  source_id?: number | null
  status: string
  visibility: string
  created_at?: string | null
  updated_at?: string | null
  last_accessed_at?: string | null
  expires_at?: string | null
}

export interface UserSkillProfile {
  id: number
  user_id: number
  skill_name: string
  skill_category?: string | null
  level_score: number
  confidence: number
  evidence_count: number
  weakness_summary?: string | null
  strength_summary?: string | null
  last_evaluated_at?: string | null
  metadata_json?: Record<string, unknown> | null
  created_at?: string | null
  updated_at?: string | null
}

export interface UserMemoryEvent {
  id: number
  user_id: number
  memory_item_id?: number | null
  event_type: string
  actor_type: string
  actor_id?: number | null
  before_json?: Record<string, unknown> | null
  after_json?: Record<string, unknown> | null
  reason?: string | null
  created_at?: string | null
}

export async function listMemoryItems(params?: {
  memory_type?: string
  scope?: string
  status?: string
  keyword?: string
  offset?: number
  limit?: number
}): Promise<{ items: UserMemoryItem[]; total: number }> {
  return client.get("/memory/items", { params })
}

export async function createMemoryItem(data: {
  memory_type: string
  scope?: string
  key?: string
  content: string
  summary?: string
  confidence?: number
  importance?: number
  visibility?: string
}): Promise<UserMemoryItem> {
  return client.post("/memory/items", data)
}

export async function updateMemoryItem(
  id: number,
  data: Partial<UserMemoryItem>
): Promise<UserMemoryItem> {
  return client.patch(`/memory/items/${id}`, data)
}

export async function deleteMemoryItem(id: number): Promise<void> {
  return client.delete(`/memory/items/${id}`)
}

export async function searchMemoryItems(data: {
  query: string
  memory_types?: string[]
  limit?: number
}): Promise<{ items: UserMemoryItem[]; total: number }> {
  return client.post("/memory/search", data)
}

export async function listSkillProfiles(): Promise<{ items: UserSkillProfile[]; total: number }> {
  return client.get("/memory/skills")
}

export async function listMemoryEvents(params?: {
  offset?: number
  limit?: number
}): Promise<{ items: UserMemoryEvent[]; total: number }> {
  return client.get("/memory/events", { params })
}
