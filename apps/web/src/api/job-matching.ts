import client from './client'
import type { MatchFilters, MatchRun, MatchHistory } from '../types/job-matching'
export async function createMatch(filters: MatchFilters, request_key: string): Promise<MatchRun> {
  return await client.post('/job-matches', { filters, request_key }) as unknown as MatchRun
}
export async function generateMatch(id: number): Promise<MatchRun> {
  return await client.post(`/job-matches/${id}/generate`, {}, { timeout: 150000 }) as unknown as MatchRun
}
export async function getMatch(id: number): Promise<MatchRun> {
  return await client.get(`/job-matches/${id}`) as unknown as MatchRun
}
export async function matchHistory(offset = 0): Promise<MatchHistory[]> {
  return await client.get('/job-matches', { params: { offset } }) as unknown as MatchHistory[]
}
