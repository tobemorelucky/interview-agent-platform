import { defineStore } from "pinia";
import { ref, computed } from "vue";
import {
  createSession,
  getSessions,
  getSession,
  chatStream,
} from "../api/qa";
import type { ChatSession, ChatMessage, Citation } from "../types/qa";
import { ApiError } from "../api/client";

export const useQaStore = defineStore("qa", () => {
  const sessions = ref<ChatSession[]>([]);
  const currentSession = ref<ChatSession | null>(null);
  const messages = ref<ChatMessage[]>([]);
  const citations = ref<Citation[]>([]);
  const isStreaming = ref(false);
  const error = ref<string | null>(null);
  const streamingContent = ref("");
  const streamingMessageId = ref<number | null>(null);

  async function fetchSessions() {
    sessions.value = await getSessions();
  }

  async function newSession(title?: string) {
    const session = await createSession(title);
    sessions.value.unshift(session);
    currentSession.value = session;
    messages.value = [];
    citations.value = [];
    streamingContent.value = "";
    return session;
  }

  async function selectSession(sessionId: number) {
    error.value = null;
    const detail = await getSession(sessionId);
    currentSession.value = detail;
    messages.value = detail.messages;
    citations.value = [];
    streamingContent.value = "";
  }

  async function sendMessage(content: string) {
    if (!currentSession.value) {
      error.value = "No active session";
      return;
    }

    error.value = null;
    isStreaming.value = true;
    streamingContent.value = "";
    citations.value = [];
    streamingMessageId.value = null;

    let fullContent = "";
    const collectedCitations: Citation[] = [];

    try {
      await chatStream(currentSession.value.id, content, {
        onCitation(cs) {
          collectedCitations.push(...cs);
          citations.value = collectedCitations;
        },
        onToken(text) {
          fullContent += text;
          streamingContent.value = fullContent;
        },
        onDone(messageId) {
          streamingMessageId.value = messageId;
          // Add the user + assistant messages to the local state
          messages.value.push({
            id: 0,
            session_id: currentSession.value!.id,
            role: "user",
            content,
            citations_json: null,
            created_at: null,
          });
          messages.value.push({
            id: messageId,
            session_id: currentSession.value!.id,
            role: "assistant",
            content: fullContent,
            citations_json: collectedCitations,
            created_at: null,
          });
          // Refresh sessions to update title/order
          fetchSessions();
        },
        onError(msg) {
          error.value = msg;
        },
      });
    } catch (e) {
      if (e instanceof ApiError) {
        error.value = e.message;
      } else if (e && typeof e === "object" && "message" in e) {
        error.value = (e as { message: string }).message;
      } else {
        error.value = "流式请求失败";
      }
    } finally {
      isStreaming.value = false;
    }
  }

  function clearError() {
    error.value = null;
  }

  return {
    sessions,
    currentSession,
    messages,
    citations,
    isStreaming,
    error,
    streamingContent,
    streamingMessageId,
    fetchSessions,
    newSession,
    selectSession,
    sendMessage,
    clearError,
  };
});
