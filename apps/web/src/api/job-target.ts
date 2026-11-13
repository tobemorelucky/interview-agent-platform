import client from './client'
import type { Importance, JobTarget, JobTargetMatch, TargetSkillRecommendation } from '../types/job-target'

export interface TargetRequirementInput {
  statement: string
  importance: Importance
  evidence?: string | null
  topic?: string | null
}

export async function listTargets(): Promise<JobTarget[]> {
  return await client.get('/job-targets') as unknown as JobTarget[]
}

export async function getTarget(id: number): Promise<JobTarget> {
  return await client.get(`/job-targets/${id}`) as unknown as JobTarget
}

export async function createTarget(body: {
  raw_text: string; company_name?: string; job_title?: string; source_reference?: string
}): Promise<JobTarget> {
  return await client.post('/job-targets', body) as unknown as JobTarget
}

export async function parseTarget(id: number): Promise<JobTarget> {
  // The model call happens server-side outside the request transaction.
  return await client.post(`/job-targets/${id}/parse`, {}, { timeout: 120000 }) as unknown as JobTarget
}

export async function updateRequirements(id: number, requirements: TargetRequirementInput[]): Promise<JobTarget> {
  return await client.patch(`/job-targets/${id}`, { requirements }) as unknown as JobTarget
}

export async function confirmTarget(id: number): Promise<JobTarget> {
  return await client.post(`/job-targets/${id}/confirm`, {}) as unknown as JobTarget
}

export async function deleteTarget(id: number): Promise<void> {
  await client.delete(`/job-targets/${id}`)
}

export async function matchTarget(id: number): Promise<JobTargetMatch> {
  return await client.get(`/job-targets/${id}/match`) as unknown as JobTargetMatch
}

export async function skillRecommendations(id: number, recommendationKey?: string): Promise<TargetSkillRecommendation> {
  // Read-only and owner-scoped. An unconfirmed JD returns an explicit empty
  // projection instead of a draft recommendation. A prior key lets the server
  // flag a stale hand-off after the JD was edited/reconfirmed.
  const query = recommendationKey ? `?recommendation_key=${encodeURIComponent(recommendationKey)}` : ''
  return await client.get(`/job-targets/${id}/skill-recommendations${query}`) as unknown as TargetSkillRecommendation
}
