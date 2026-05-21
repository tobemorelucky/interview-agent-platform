import { defineStore } from "pinia";
import { ref } from "vue";
import {
  createSession,
  getSessions,
  getSession,
  chatStream,
} from "../api/qa";
import type { ChatSession, ChatMessage, Citation, RagStage } from "../types/qa";
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

  // RAG pipeline progress
  const ragStage = ref<RagStage | null>(null);
  const ragHitCount = ref<number>(0);
  const ragTopK = ref<number>(0);

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
    ragStage.value = null;
    ragHitCount.value = 0;
    ragTopK.value = 0;
    return session;
  }

  async function selectSession(sessionId: number) {
    error.value = null;
    const detail = await getSession(sessionId);
    currentSession.value = detail;
    messages.value = detail.messages;
    citations.value = [];
    streamingContent.value = "";
    ragStage.value = null;
    ragHitCount.value = 0;
    ragTopK.value = 0;
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
    ragStage.value = null;
    ragHitCount.value = 0;
    ragTopK.value = 0;

    let fullContent = "";
    const collectedCitations: Citation[] = [];

    try {
      await chatStream(currentSession.value.id, content, {
        onStatus(stage) {
          ragStage.value = stage as RagStage;
        },
        onRetrieval(info) {
          ragTopK.value = info.top_k;
          ragHitCount.value = info.hit_count;
        },
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
      ragStage.value = null;
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
    ragStage,
    ragHitCount,
    ragTopK,
    fetchSessions,
    newSession,
    selectSession,
    sendMessage,
    clearError,
  };
});
