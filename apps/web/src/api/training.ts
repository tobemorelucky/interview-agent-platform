import client from './client'
export type TrainingMode = 'FULL' | 'WEAKNESS' | 'RECHECK'
export interface TrainingTarget {
  knowledge_id: string; label: string; state: string; revision: number | null; revoked: boolean;
  available_modes: TrainingMode[]; independent_sessions: number; reason_tags: string[];
  observations: { session_id: number; assisted: boolean; state: string; observed_at: string }[]
}
export interface TrainingBoard {
  company_id: number; job_id: number; company_name: string; job_title: string;
  targets: TrainingTarget[]; coverage: { reviewed_question_count: number; fallback: string }
}
export interface TrainingFeedback {
  available: boolean; status: string; message?: string; company_id?: number; job_id?: number; skill_id?: string;
  targets: { knowledge_id: string; label: string; before_state: string; after_state: string;
    effective_evaluations: number; independent_evaluations: number; conflict_status: string; result: string; next_action: string }[];
  questions?: { question_id: number; question: string; strategy: string; knowledge_id: string }[]
  memory_usage?: { run_id: number; model_input: string; selection: string; controller_verdict: string }[]
}
export const trainingBoard = async (body: object) => await client.post('/interview/training/workbench', body) as unknown as TrainingBoard
export const trainingFeedback = async (id: number) => await client.get(`/interview/training/sessions/${id}`) as unknown as TrainingFeedback
