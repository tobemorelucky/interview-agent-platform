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

export async function listExperienceTasks(params?: {
  status?: string
  offset?: number
  limit?: number
}): Promise<{ items: ExperienceCollectionTask[]; total: number }> {
  return client.get("/admin/experience/tasks", { params })
}

export async function createExperienceTask(data: {
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
