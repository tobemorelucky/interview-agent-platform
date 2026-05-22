import client from "./client";
import type {
  InterviewSession,
  InterviewSessionDetail,
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
    source: string;
    compressed: boolean;
  }) => void,
  onCompressed: (data: {
    compressed_turns: number;
    new_last_compressed_turn: number;
  }) => void,
  onError: (code: string, message: string) => void,
  onStatus: (stage: string) => void
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
