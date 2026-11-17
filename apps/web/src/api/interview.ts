import client, { expireAuthorization } from "./client";
import { streamHttpError } from "./stream-http-error";
import { SSEParser } from "./sse";
import type {
  InterviewSession,
  InterviewSessionDetail,
  InterviewQuestion,
  InterviewQuestionList,
  InterviewMemoryConsolidateResult,
  StartInterviewResult,
  TargetPositionResult,
  InterviewerSelection,
  InterviewerOptions,
  SkillRecommendation,
  DesignPlan,
} from "../types/interview";

export async function createSession(
  companyId: number, jobId: number, questionCount = 20, title?: string, interviewer?: InterviewerSelection
): Promise<InterviewSession> {
  const response = await client.post("/interview/sessions", { title, company_id: companyId, job_id: jobId, question_count: questionCount, ...interviewer });
  return response as unknown as InterviewSession;
}

export async function createSessionForTarget(
  jobTargetId: number, questionCount = 20, title?: string, interviewer?: InterviewerSelection
): Promise<InterviewSession> {
  // Same endpoint and Controller; the server re-checks ownership and confirmation.
  const response = await client.post("/interview/sessions", { title, job_target_id: jobTargetId, question_count: questionCount, ...interviewer });
  return response as unknown as InterviewSession;
}

export interface InterviewTarget { company_id: number; company_name: string; job_id: number; job_title: string; interviewer_skill_recommendation?: SkillRecommendation }
export async function getInterviewerOptions(): Promise<InterviewerOptions> {
  return await client.get("/interview/interviewer-options") as unknown as InterviewerOptions;
}
export async function getInterviewTargets(): Promise<InterviewTarget[]> {
  return await client.get("/interview/targets") as unknown as InterviewTarget[];
}

export async function listSessions(): Promise<InterviewSession[]> {
  const response = await client.get("/interview/sessions");
  return response as unknown as InterviewSession[];
}

export async function getSession(
  id: number
): Promise<InterviewSessionDetail> {
  const response = await client.get(`/interview/sessions/${id}`);
  return response as unknown as InterviewSessionDetail;
}

export async function bindResume(
  sessionId: number,
  resumeId: number
): Promise<void> {
  await client.post(`/interview/sessions/${sessionId}/resume`, {
    resume_id: resumeId,
  });
}

export async function deleteSession(id: number): Promise<void> {
  await client.delete(`/interview/sessions/${id}`);
}

// Phase 3.6: New API functions

export async function setTargetPosition(
  sessionId: number,
  targetPosition: string,
  interviewMode = "comprehensive",
  questionCount = 20
): Promise<TargetPositionResult> {
  const response = await client.post(`/interview/sessions/${sessionId}/target-position`, {
    target_position: targetPosition,
    interview_mode: interviewMode,
    question_count: questionCount,
  });
  return response as unknown as TargetPositionResult;
}

export async function startInterview(sessionId: number, opening: 'MODEL' | 'REQUIREMENT' = 'MODEL'): Promise<StartInterviewResult> {
  const response = await client.post(`/interview/sessions/${sessionId}/start`, {}, { params: { opening } });
  return response as unknown as StartInterviewResult;
}

export async function getCurrentQuestion(sessionId: number): Promise<InterviewQuestion | null> {
  const response = await client.get(`/interview/sessions/${sessionId}/questions/current`);
  return response as unknown as InterviewQuestion | null;
}

export async function getQuestions(sessionId: number): Promise<InterviewQuestionList> {
  const response = await client.get(`/interview/sessions/${sessionId}/questions`);
  return response as unknown as InterviewQuestionList;
}

export async function getQuestionDetail(
  sessionId: number,
  questionId: number
): Promise<InterviewQuestion> {
  const response = await client.get(`/interview/sessions/${sessionId}/questions/${questionId}`);
  return response as unknown as InterviewQuestion;
}

export async function skipQuestion(
  sessionId: number,
  questionId: number
): Promise<unknown> {
  const response = await client.post(`/interview/sessions/${sessionId}/questions/${questionId}/skip`);
  return response;
}

export interface EndInterviewResult {
  already_completed: boolean;
  session_id: number;
  completion: { completion_reason?: string; early_terminated?: boolean; answered_count?: number; [key: string]: unknown };
  excluded: { question_id: number; answer_id: number; evaluation_id: number | null; reason: string }[];
}

/** User-owned, idempotent end. Repeats return the committed summary. */
export async function endInterview(sessionId: number): Promise<EndInterviewResult> {
  const response = await client.post(`/interview/sessions/${sessionId}/end`);
  return response as unknown as EndInterviewResult;
}

export async function consolidateInterviewMemory(
  sessionId: number,
  force = false
): Promise<InterviewMemoryConsolidateResult> {
  const response = await client.post(`/interview/sessions/${sessionId}/memory/consolidate`, {
    force,
  });
  return response as unknown as InterviewMemoryConsolidateResult;
}

/**
 * Send a message in streaming mode using fetch + SSE.
 * Returns an AbortController so the caller can cancel.
 */
export function sendMessageStream(
  sessionId: number,
  content: string,
  onToken: (token: string) => void,
  onRetrieval: (data: { hit_count: number; source: string }) => void,
  onCitation: (citations: unknown[]) => void,
  onDone: (data: {
    message_id: number;
    turn_index: number;
    source?: string;
    compressed?: boolean;
    action?: string;
  }) => void,
  onCompressed: (data: {
    compressed_turns: number;
    new_last_compressed_turn: number;
  }) => void,
  onError: (code: string, message: string) => void,
  onStatus: (stage: string) => void,
  // Phase 3.6: New SSE event callbacks
  onEvaluation?: (data: {
    question_id?: number;
    score: number;
    evaluation: string;
    covered_points?: string[];
    missing_points?: string[];
    risk_tip?: string;
    action: string;
  }) => void,
  onFollowUp?: (data: {
    question_id?: number;
    question: string;
    follow_up_count: number;
    max_follow_ups: number;
  }) => void,
  onQuestion?: (data: {
    question_id: number;
    question_index: number;
    total_questions: number;
    question: string;
    source: string;
    dimension?: string;
    difficulty?: string;
    evidence?: unknown;
  }) => void,
  onDynamicQuestion?: (data: {
    question_id: number;
    question_index?: number;
    question: string;
    source?: string;
    dimension?: string;
    difficulty?: string;
    parent_question_id?: number;
    reason?: string;
  }) => void,
  onQuestionTransition?: (data: {
    from_index: number;
    to_index: number;
    preview?: string;
  }) => void,
  onInterviewComplete?: (data: {
    summary?: string;
    answered_count: number;
    question_budget?: number;
    total_questions?: number;
    avg_score?: number;
    dimension_averages?: Record<string, number>;
    overall_average?: number;
  }) => void,
  answerContext?: { question_id: number; idempotency_key: string; design_plan?: DesignPlan }
): AbortController {
  const controller = new AbortController();
  const token = localStorage.getItem("access_token");

  fetch(`/api/v1/interview/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify({ content, ...answerContext }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        if (response.status === 401) expireAuthorization();
        const error = streamHttpError(response.status, text);
        onError(error.code, error.message);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        onError("STREAM_ERROR", "无法读取响应流");
        return;
      }
      const decoder = new TextDecoder();
      const parser = new SSEParser();
      let receivedDone = false;

      while (true) {
        const { done, value } = await reader.read();
        if (done) { parser.push(decoder.decode()); parser.finish(); break; }
        for (const parsed of parser.push(decoder.decode(value, { stream: true }))) {
              const data = parsed.data as any;
              switch (parsed.event) {
                case "status":
                  onStatus(data.stage);
                  break;
                case "retrieval":
                  onRetrieval(data);
                  break;
                case "citation":
                  onCitation(data);
                  break;
                case "token":
                  onToken(data.content);
                  break;
                case "evaluation":
                  if (onEvaluation) onEvaluation(data);
                  break;
                case "follow_up":
                  if (onFollowUp) onFollowUp(data);
                  break;
                case "question":
                  if (onQuestion) onQuestion(data);
                  break;
                case "dynamic_question":
                  if (onDynamicQuestion) onDynamicQuestion(data);
                  break;
                case "question_transition":
                  if (onQuestionTransition) onQuestionTransition(data);
                  break;
                case "interview_complete":
                case "complete":
                  if (onInterviewComplete) onInterviewComplete(data);
                  break;
                case "done":
                  receivedDone = true;
                  onDone(data);
                  break;
                case "compressed":
                  onCompressed(data);
                  break;
                case "error":
                  receivedDone = true;
                  onError(data.code, data.message);
                  break;
              }
        }
      }
      if (!receivedDone) onError("STREAM_INTERRUPTED", "连接中断，请使用同一回答重试；不会重复提交");
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        onError("NETWORK_ERROR", err.message || "网络异常");
      }
    });

  return controller;
}
