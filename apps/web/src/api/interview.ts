import client from "./client";
import type {
  InterviewSession,
  InterviewSessionDetail,
  InterviewQuestion,
  InterviewQuestionList,
  StartInterviewResult,
  TargetPositionResult,
} from "../types/interview";

export async function createSession(
  title?: string
): Promise<InterviewSession> {
  const response = await client.post("/interview/sessions", { title });
  return response as unknown as InterviewSession;
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

export async function startInterview(sessionId: number): Promise<StartInterviewResult> {
  const response = await client.post(`/interview/sessions/${sessionId}/start`);
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
    score: number;
    evaluation: string;
    covered_points?: string[];
    missing_points?: string[];
    risk_tip?: string;
    action: string;
  }) => void,
  onFollowUp?: (data: {
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
    question_index: number;
    question: string;
    source: string;
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
  }) => void
): AbortController {
  const controller = new AbortController();
  const token = localStorage.getItem("access_token");

  fetch(`/api/v1/interview/sessions/${sessionId}/messages/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: token ? `Bearer ${token}` : "",
    },
    body: JSON.stringify({ content }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        const text = await response.text();
        onError("HTTP_ERROR", text);
        return;
      }
      const reader = response.body?.getReader();
      if (!reader) {
        onError("STREAM_ERROR", "无法读取响应流");
        return;
      }
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let eventType = "";
        for (const line of lines) {
          if (line.startsWith("event: ")) {
            eventType = line.slice(7).trim();
          } else if (line.startsWith("data: ")) {
            const dataStr = line.slice(6);
            try {
              const data = JSON.parse(dataStr);
              switch (eventType) {
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
                  if (onInterviewComplete) onInterviewComplete(data);
                  break;
                case "done":
                  onDone(data);
                  break;
                case "compressed":
                  onCompressed(data);
                  break;
                case "error":
                  onError(data.code, data.message);
                  break;
              }
            } catch {
              // skip parse errors for malformed lines
            }
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        onError("NETWORK_ERROR", err.message || "网络异常");
      }
    });

  return controller;
}
