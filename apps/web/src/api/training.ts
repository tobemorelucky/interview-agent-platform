import client from './client'
import type { TrainingCandidateSet } from '../types/training-candidates'
export type TrainingMode = 'FULL' | 'WEAKNESS' | 'RECHECK'
export interface TrainingTarget {
  knowledge_id: string; label: string; state: string; revision: number | null; revoked: boolean;
  available_modes: TrainingMode[]; independent_sessions: number; reason_tags: string[];
  observations: { session_id: number; assisted: boolean; state: string; observed_at: string }[]
}
export interface TrainingBoard {
  /** A private JD has no public company/job ids; exactly one source is set. */
  company_id: number | null; job_id: number | null; job_target_id: number | null;
  source_label: string | null;
  company_name: string; job_title: string; skill_id?: string;
  targets: TrainingTarget[]; coverage: { reviewed_question_count: number; fallback: string };
  recommendations?: { knowledge_id: string }[]
}
export interface TrainingFeedback {
  available: boolean; status: string; message?: string;
  company_id?: number | null; job_id?: number | null; job_target_id?: number | null; skill_id?: string;
  targets: { knowledge_id: string; label: string; before_state: string; after_state: string;
    effective_evaluations: number; independent_evaluations: number; conflict_status: string; result: string; next_action: string }[];
  questions?: { question_id: number; question: string; strategy: string; knowledge_id: string }[]
  memory_usage?: { run_id: number; model_input: string; selection: string; controller_verdict: string }[]
}
export const trainingBoard = async (body: object) => await client.post('/interview/training/workbench', body) as unknown as TrainingBoard
export const trainingFeedback = async (id: number) => await client.get(`/interview/training/sessions/${id}`) as unknown as TrainingFeedback
/** Read-only projection; nothing is created and no Coach report is regenerated. */
export const trainingCandidates = async (body: object, sessionId?: number) =>
  await client.post(`/interview/training/candidates${sessionId ? `?session_id=${sessionId}` : ''}`, body) as unknown as TrainingCandidateSet
