import client from "./client";
import type { ChatSession, Citation, SessionDetail } from "../types/qa";

export async function createSession(title?: string): Promise<ChatSession> {
  return client.post("/qa/sessions", { title });
}

export async function getSessions(): Promise<ChatSession[]> {
  return client.get("/qa/sessions");
}

export async function getSession(id: number): Promise<SessionDetail> {
  return client.get(`/qa/sessions/${id}`);
}

export async function chatStream(
  sessionId: number,
  message: string,
  callbacks: {
    onCitation: (citations: Citation[]) => void
    onToken: (text: string) => void
    onDone: (messageId: number) => void
    onError: (message: string) => void
  }
): Promise<void> {
  const token = localStorage.getItem("access_token");

  const response = await fetch("/api/v1/qa/chat/stream", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ session_id: sessionId, message }),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({
      code: "NETWORK_ERROR",
      message: `HTTP ${response.status}`,
    }));
    throw { code: errorData.code, message: errorData.message };
  }

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n\n");
    buffer = parts.pop() || "";

    for (const part of parts) {
      if (!part.trim()) continue;
      const eventMatch = part.match(/^event:\s*(\w+)/m);
      const dataMatch = part.match(/^data:\s*(.+)/m);
      if (!eventMatch || !dataMatch) continue;

      const event = eventMatch[1];
      try {
        const data = JSON.parse(dataMatch[1]);
        switch (event) {
          case "citation":
            callbacks.onCitation(data);
            break;
          case "token":
            callbacks.onToken(data.content);
            break;
          case "done":
            callbacks.onDone(data.message_id);
            break;
          case "error":
            callbacks.onError(data.message || "未知错误");
            break;
        }
      } catch {
        // malformed data — skip
      }
    }
  }
}
