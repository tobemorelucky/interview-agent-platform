import { dimensionLabels, modelModeLabel } from '../i18n/zh-CN.ts'
import type { ReferenceAnswer, DesignTrace } from './interview'
export const REPORT_DIMENSION_LABELS = dimensionLabels

export interface InterviewReportStatistics {
  answered_count: number
  evaluated_count: number
  skipped_count: number
  follow_up_count: number
  deep_dive_count: number
  dynamic_question_count: number
  dimension_averages: Record<string, number | null>
  overall_average: number | null
  mode?: string
}

export interface ReportEvaluation {
  id: number
  session_question_id: number
  question: string
  answer?: string
  evaluation: string
  scores: Record<string, number>
  covered_points?: string[]
  missing_points?: string[]
  factual_or_reasoning_issues?: string[]
  provider?: string
  model?: string
  reference_answer?: ReferenceAnswer | null
  answer_framework?: string[]
}

export interface CoachFinding {
  statement: string
  supporting_evaluation_ids: number[]
  supporting_evaluations?: ReportEvaluation[]
  occurrence_count?: number
}

export interface CoachAnalysis {
  overall_summary: string
  strengths: CoachFinding[]
  weaknesses: CoachFinding[]
  recurring_issues: CoachFinding[]
  job_alignment: CoachFinding[]
  recommended_actions: string[]
  practice_plan: string[]
}

export interface FinalInterviewReport {
  id: number
  session_id: number
  context_snapshot_id: number
  generation_number?: number
  generation_status?: string
  status?: string
  is_current?: boolean
  statistics?: InterviewReportStatistics
  coach_analysis?: CoachAnalysis | null
  summary?: string | null
  producer?: string
  provider?: string | null
  model?: string | null
  prompt_version?: string
  error?: string | null
  created_at?: string
  mode?: string
}

export interface ReportSessionInfo {
  id: number
  title?: string | null
  status: string
  company_name?: string
  job_title?: string
  created_at?: string
}

export interface InterviewReportDetail {
  design_trace?: DesignTrace
  session: ReportSessionInfo
  statistics: InterviewReportStatistics
  current_report: FinalInterviewReport | null
  report?: FinalInterviewReport | null
  history: FinalInterviewReport[]
  evaluations: ReportEvaluation[]
}

export interface InterviewReportListItem extends FinalInterviewReport {
  company_name?: string
  job_title?: string
  session_title?: string | null
}

export interface MemoryProposal {
  id: number
  session_id: number
  report_id: number
  proposal_type: string
  original_content: string
  corrected_content: string | null
  status: "PENDING" | "APPROVED" | "CORRECTED" | "REJECTED"
  confidence: "SINGLE_EVALUATION" | "REPEATED_EVALUATIONS"
  supporting_evaluation_ids: number[]
  observation_ids: number[]
  supporting_evaluations: ReportEvaluation[]
  confirmed_by: number | null
  confirmation_reason: string | null
  confirmed_at: string | null
  memory_item_id: number | null
  created_at?: string
}

export function confidenceLabel(value: string): string {
  if (value === "SINGLE_EVALUATION") return "单次回答支持信号（非概率）"
  if (value === "REPEATED_EVALUATIONS") return "多次回答支持信号（非概率）"
  return "未说明支持信号"
}

export function proposalCanDecide(proposal: Pick<MemoryProposal, "status">): boolean {
  return proposal.status === "PENDING"
}

export function reportGenerationStatus(report: FinalInterviewReport): string {
  return report.generation_status || report.status || "UNKNOWN"
}

export function reportModeLabel(report: FinalInterviewReport | null, statistics?: InterviewReportStatistics): string {
  if (report?.provider === "replay" || statistics?.mode === "replay") return modelModeLabel("replay")
  if (report?.provider) return modelModeLabel(report.provider, report.model)
  return ""
}
