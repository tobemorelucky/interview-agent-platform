import type { InterviewSessionDetail } from './interview'

export function pendingStage(pending: InterviewSessionDetail['pending_answer']): 'idle' | 'evaluating' | 'validating_design' | 'generating_evaluation' | 'generating_next_question' {
  if (!pending || pending.status !== 'RUNNING' || pending.retryable !== false) return 'idle'
  if (pending.processing_stage === 'validating_design') return 'validating_design'
  if (pending.processing_stage === 'generating_evaluation') return 'generating_evaluation'
  return pending.processing_stage === 'generating_next_question' ? 'generating_next_question' : 'evaluating'
}
