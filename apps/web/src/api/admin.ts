import client from "./client";
import type { KbDocument, KbDocumentDetail, KbDocumentList } from "../types/qa";

export async function uploadKbDocument(file: File): Promise<KbDocument> {
  const formData = new FormData();
  formData.append("file", file);
  return client.post("/admin/kb/documents/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export async function getKbDocuments(offset = 0, limit = 50): Promise<KbDocumentList> {
  return client.get("/admin/kb/documents", { params: { offset, limit } });
}

export async function getKbDocument(id: number): Promise<KbDocumentDetail> {
  return client.get(`/admin/kb/documents/${id}`);
}

export async function deleteKbDocument(id: number): Promise<void> {
  return client.delete(`/admin/kb/documents/${id}`);
}

// ── Phase 4: Experience Keyword Presets ──

export interface ExperienceKeywordPreset {
  id: number
  preset_type: string
  name: string
  aliases_json: string[]
  enabled: boolean
  created_by?: number
  created_at?: string
  updated_at?: string
}

export async function listExperienceKeywords(params?: {
  preset_type?: string
  enabled?: boolean
  offset?: number
  limit?: number
}): Promise<{ items: ExperienceKeywordPreset[]; total: number }> {
  return client.get("/admin/experience/keywords", { params })
}

export async function createExperienceKeyword(data: {
  preset_type: string
  name: string
  aliases_json: string[]
  enabled?: boolean
}): Promise<ExperienceKeywordPreset> {
  return client.post("/admin/experience/keywords", data)
}

export async function updateExperienceKeyword(
  id: number,
  data: { name?: string; aliases_json?: string[]; enabled?: boolean }
): Promise<ExperienceKeywordPreset> {
  return client.patch(`/admin/experience/keywords/${id}`, data)
}

export async function deleteExperienceKeyword(id: number): Promise<void> {
  return client.delete(`/admin/experience/keywords/${id}`)
}

// ── Phase 4 Step 4: Collection Tasks ──

export interface ExperienceCollectionTask {
  id: number
  search_scope: string
  time_window_hours: number
  job_keywords_json: string[]
  company_keywords_json: string[]
  platforms_json: string[]
  max_results: number
  review_mode: string
  write_to_question_db: boolean
  write_to_vector_index: boolean
  update_public_summary: boolean
  status: string
  progress: number
  found_url_count: number
  fetched_count: number
  extracted_count: number
  question_count: number
  approved_count: number
  failed_count: number
  error_message?: string
  created_at?: string
  finished_at?: string
}

export interface ExperienceSourceItem {
  id: number
  task_id: number
  source_url: string
  normalized_url_hash: string
  platform?: string
  title?: string
  query_text?: string
  snippet?: string
  engine?: string
  matched_reason?: string
  filtered_reason?: string
  raw_text_char_count: number
  raw_text_preview?: string
  fetched_at?: string
  fetch_status: string
  fetch_status_label?: string
  fetch_quality?: "GOOD" | "SHORT" | "FAILED" | "PENDING" | string
  extract_status?: string
  error_message?: string
  created_at?: string
}

export interface ExperienceFetchStats {
  task_id: number
  total: number
  discovered_count: number
  fetched_count: number
  failed_count: number
  pending_count: number
  avg_raw_text_chars: number
  max_raw_text_chars: number
  min_raw_text_chars: number
  failure_reasons: Array<{ reason: string; count: number }>
  platform_stats: Array<{ platform: string; total: number; fetched: number; failed: number }>
}

export interface ExperienceSourcePreview {
  source_id: number
  title?: string
  source_url: string
  fetch_status: string
  fetch_status_label?: string
  fetch_quality?: string
  raw_text_char_count: number
  raw_text_preview: string
  message?: string
}

export async function listExperienceTasks(params?: {
  status?: string
  offset?: number
  limit?: number
}): Promise<{ items: ExperienceCollectionTask[]; total: number }> {
  return client.get("/admin/experience/tasks", { params })
}

export async function createExperienceTask(data: {
  search_scope: string
  time_window_hours: number
  job_keywords_json: string[]
  company_keywords_json: string[]
  platforms_json: string[]
  max_results?: number
  review_mode?: string
  write_to_question_db?: boolean
  write_to_vector_index?: boolean
  update_public_summary?: boolean
}): Promise<ExperienceCollectionTask> {
  return client.post("/admin/experience/tasks", data)
}

export async function getExperienceTask(id: number): Promise<ExperienceCollectionTask> {
  return client.get(`/admin/experience/tasks/${id}`)
}

export async function deleteExperienceTask(id: number): Promise<void> {
  return client.delete(`/admin/experience/tasks/${id}`)
}

export async function runExperienceTaskSearch(id: number): Promise<{
  task: ExperienceCollectionTask
  query_count: number
  query_success_count: number
  query_failed_count: number
  raw_result_count: number
  accepted_count: number
  filtered_count: number
  duplicate_count: number
  found_url_count: number
}> {
  return client.post(`/admin/experience/tasks/${id}/search`, undefined, { timeout: 180000 })
}

export async function fetchExperienceTaskSources(
  id: number,
  data?: { retry_failed?: boolean; limit?: number }
): Promise<{
  task_id: number
  total: number
  fetched_count: number
  failed_count: number
  skipped_count: number
  task: ExperienceCollectionTask
}> {
  return client.post(`/admin/experience/tasks/${id}/fetch`, data || {}, { timeout: 180000 })
}

export async function listExperienceTaskSources(
  id: number,
  params?: { offset?: number; limit?: number; fetch_status?: string }
): Promise<{ items: ExperienceSourceItem[]; total: number }> {
  return client.get(`/admin/experience/tasks/${id}/sources`, { params })
}

export async function getExperienceTaskFetchStats(id: number): Promise<ExperienceFetchStats> {
  return client.get(`/admin/experience/tasks/${id}/fetch-stats`)
}

export async function getExperienceSourcePreview(id: number): Promise<ExperienceSourcePreview> {
  return client.get(`/admin/experience/sources/${id}/preview`)
}

export async function fetchExperienceSource(
  id: number,
  data?: { force?: boolean }
): Promise<{
  source_id: number
  task_id: number
  skipped: boolean
  fetch_status: string
  error_message?: string
  raw_text_char_count: number
  item?: ExperienceSourceItem
}> {
  return client.post(`/admin/experience/sources/${id}/fetch`, data || {}, { timeout: 180000 })
}

export async function extractExperienceSource(
  id: number,
  data?: { force?: boolean }
): Promise<{
  source_id: number
  agent_run_id: number | null
  is_interview_experience: boolean
  experience_id: number | null
  question_count: number
  indexable_question_count: number
  reliability_score?: number | null
  review_status?: string | null
  risk_flags?: string[]
  quality_gate_reasons?: string[]
  status?: string
  extract_status?: string
  skipped?: boolean
  error_message?: string | null
}> {
  return client.post(`/admin/experience/sources/${id}/extract`, data || {}, { timeout: 180000 })
}

export interface AuditLog {
  id: number
  request_id?: string
  actor_user_id?: number
  actor_role?: string
  action: string
  resource_type?: string
  resource_id?: string
  ip_address?: string
  user_agent?: string
  before_json?: Record<string, unknown>
  after_json?: Record<string, unknown>
  metadata_json?: Record<string, unknown>
  status: string
  error_message?: string
  created_at?: string
}

export async function listAuditLogs(params?: {
  actor_user_id?: number
  action?: string
  resource_type?: string
  resource_id?: string
  request_id?: string
  offset?: number
  limit?: number
}): Promise<{ items: AuditLog[]; total: number }> {
  return client.get("/admin/audit/logs", { params })
}
