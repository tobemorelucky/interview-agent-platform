import client from "./client"
import type {
  FinalInterviewReport,
  InterviewReportDetail,
  InterviewReportListItem,
  MemoryProposal,
} from "../types/interview-report"

export async function listInterviewReports(): Promise<{ items: InterviewReportListItem[]; total: number }> {
  return await client.get("/interview-reports") as unknown as { items: InterviewReportListItem[]; total: number }
}

export async function getSessionReport(sessionId: number): Promise<InterviewReportDetail> {
  return await client.get(`/interview-reports/sessions/${sessionId}`) as unknown as InterviewReportDetail
}

export async function getInterviewReport(reportId: number): Promise<InterviewReportDetail> {
  return await client.get(`/interview-reports/${reportId}`) as unknown as InterviewReportDetail
}

export async function generateInterviewReport(sessionId: number, regenerate = false, requestKey?: string): Promise<FinalInterviewReport> {
  // An initial call plus one constrained repair can exceed the shared 30s timeout.
  return await client.post(`/interview-reports/sessions/${sessionId}/generate`,
    { regenerate, ...(requestKey ? { request_key: requestKey } : {}) }, { timeout: 150000 }) as unknown as FinalInterviewReport
}

export async function listMemoryProposals(reportId: number): Promise<{ items: MemoryProposal[]; total: number }> {
  return await client.get("/memory-proposals", { params: { report_id: reportId } }) as unknown as { items: MemoryProposal[]; total: number }
}

export async function approveMemoryProposal(id: number, reason?: string): Promise<MemoryProposal> {
  return await client.post(`/memory-proposals/${id}/approve`, { reason }) as unknown as MemoryProposal
}

export async function rejectMemoryProposal(id: number, reason?: string): Promise<MemoryProposal> {
  return await client.post(`/memory-proposals/${id}/reject`, { reason }) as unknown as MemoryProposal
}

export async function correctMemoryProposal(id: number, content: string, reason: string): Promise<MemoryProposal> {
  return await client.post(`/memory-proposals/${id}/correct`, { content, reason }) as unknown as MemoryProposal
}
