<script setup lang="ts">
import { ref, onMounted, nextTick, watch, computed } from "vue";
import { useQaStore } from "../stores/qa";

const qa = useQaStore();

const inputMessage = ref("");
const chatEl = ref<HTMLElement | null>(null);
const expandedCitations = ref(false);

const ragStageLabel = computed(() => {
  const map: Record<string, string> = {
    analyzing_query: "正在理解问题...",
    embedding_query: "正在生成查询向量...",
    retrieving: "正在检索知识库...",
    generating: "正在生成回答...",
  };
  return map[qa.ragStage ?? ""] ?? "";
});

onMounted(async () => {
  await qa.fetchSessions();
});

async function handleNewSession() {
  await qa.newSession();
  inputMessage.value = "";
  expandedCitations.value = false;
}

async function handleSelectSession(id: number) {
  await qa.selectSession(id);
  expandedCitations.value = false;
}

async function handleSend() {
  const msg = inputMessage.value.trim();
  if (!msg || qa.isStreaming) return;
  inputMessage.value = "";
  expandedCitations.value = false;

  if (!qa.currentSession) {
    await qa.newSession();
  }
  await qa.sendMessage(msg);
  scrollToBottom();
}

function scrollToBottom() {
  nextTick(() => {
    if (chatEl.value) {
      chatEl.value.scrollTop = chatEl.value.scrollHeight;
    }
  });
}

watch(() => qa.streamingContent, () => {
  scrollToBottom();
});
</script>

<template>
  <div class="qa-layout">
    <!-- Sessions sidebar -->
    <aside class="sessions-panel">
      <button class="btn-new-session" @click="handleNewSession">+ 新建会话</button>
      <div v-if="qa.sessions.length === 0" class="empty-sessions">暂无会话</div>
      <ul class="session-list">
        <li
          v-for="s in qa.sessions"
          :key="s.id"
          :class="['session-item', { active: qa.currentSession?.id === s.id }]"
          @click="handleSelectSession(s.id)"
        >
          <span class="session-title">{{ s.title || '新会话' }}</span>
        </li>
      </ul>
    </aside>

    <!-- Chat area -->
    <div class="chat-area">
      <div v-if="!qa.currentSession" class="chat-placeholder">
        <p>选择左侧会话或新建一个会话开始提问</p>
      </div>
      <template v-else>
        <div ref="chatEl" class="chat-messages">
          <div v-if="qa.messages.length === 0 && !qa.isStreaming" class="chat-empty">
            发送你的第一个面试问题
          </div>

          <div
            v-for="m in qa.messages"
            :key="m.id ?? m.created_at ?? m.content"
            :class="['message', m.role]"
          >
            <div class="message-content">{{ m.content }}</div>
            <!-- Historical citations: collapsed by default -->
            <div
              v-if="m.citations_json && m.citations_json.length > 0"
              class="citations"
            >
              <div
                class="citations-toggle"
                @click="expandedCitations = !expandedCitations"
              >
                已检索到 {{ m.citations_json.length }} 个来源
                <span class="toggle-arrow">{{ expandedCitations ? '▾' : '▸' }}</span>
              </div>
              <div v-if="expandedCitations" class="citations-list">
                <div v-for="c in m.citations_json" :key="c.chunk_id" class="citation-card">
                  <div class="citation-header">
                    <strong>{{ c.title }}</strong>
                    <span class="citation-type">{{ c.source_type }}</span>
                    <span v-if="c.score != null" class="citation-score">
                      相关度 {{ (c.score * 100).toFixed(1) }}%
                    </span>
                  </div>
                  <p class="citation-preview">{{ c.preview }}</p>
                </div>
              </div>
            </div>
          </div>

          <!-- Streaming: RAG progress indicator -->
          <div v-if="qa.isStreaming && !qa.streamingContent" class="rag-progress">
            <div class="rag-spinner"></div>
            <span class="rag-status-text">{{ ragStageLabel }}</span>
            <span v-if="qa.ragHitCount > 0" class="rag-result">
              已召回 {{ qa.ragHitCount }} 个片段
            </span>
          </div>

          <!-- Streaming message -->
          <div v-if="qa.isStreaming && qa.streamingContent" class="message assistant streaming">
            <div class="message-content">{{ qa.streamingContent }}</div>
            <!-- Live citations: collapsed by default -->
            <div
              v-if="qa.citations.length > 0"
              class="citations"
            >
              <div
                class="citations-toggle"
                @click="expandedCitations = !expandedCitations"
              >
                已检索到 {{ qa.citations.length }} 个来源
                <span class="toggle-arrow">{{ expandedCitations ? '▾' : '▸' }}</span>
              </div>
              <div v-if="expandedCitations" class="citations-list">
                <div v-for="c in qa.citations" :key="c.chunk_id" class="citation-card">
                  <div class="citation-header">
                    <strong>{{ c.title }}</strong>
                    <span class="citation-type">{{ c.source_type }}</span>
                    <span v-if="c.score != null" class="citation-score">
                      相关度 {{ (c.score * 100).toFixed(1) }}%
                    </span>
                  </div>
                  <p class="citation-preview">{{ c.preview }}</p>
                </div>
              </div>
            </div>
          </div>

          <div v-if="qa.error" class="chat-error">{{ qa.error }}</div>
        </div>

        <div class="chat-input-bar">
          <input
            v-model="inputMessage"
            type="text"
            placeholder="输入面试问题..."
            :disabled="qa.isStreaming"
            @keyup.enter="handleSend"
          />
          <button :disabled="qa.isStreaming || !inputMessage.trim()" @click="handleSend">
            {{ qa.isStreaming ? "生成中..." : "发送" }}
          </button>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.qa-layout {
  display: flex;
  height: calc(100vh - 56px - 48px);
  gap: 0;
  margin: -24px;
}

.sessions-panel {
  width: 260px;
  background: #fff;
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
}

.btn-new-session {
  margin: 16px;
  padding: 8px 0;
  font-size: 14px;
  color: #409eff;
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
  border-radius: 6px;
  cursor: pointer;
}

.btn-new-session:hover {
  background: #d9ecff;
}

.empty-sessions {
  padding: 24px 16px;
  text-align: center;
  color: #999;
  font-size: 14px;
}

.session-list {
  list-style: none;
  overflow-y: auto;
  flex: 1;
}

.session-item {
  padding: 12px 16px;
  cursor: pointer;
  font-size: 13px;
  color: #555;
  border-left: 3px solid transparent;
  transition: background 0.15s;
}

.session-item:hover {
  background: #f5f7fa;
}

.session-item.active {
  background: #ecf5ff;
  border-left-color: #409eff;
  color: #1a1a2e;
}

.session-title {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-area {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #fff;
}

.chat-placeholder,
.chat-empty {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #bbb;
  font-size: 15px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.message {
  margin-bottom: 20px;
  max-width: 80%;
}

.message.user {
  margin-left: auto;
}

.message.user .message-content {
  background: #409eff;
  color: #fff;
  border-radius: 12px 12px 4px 12px;
  padding: 10px 16px;
}

.message.assistant .message-content {
  background: #f0f2f5;
  color: #333;
  border-radius: 12px 12px 12px 4px;
  padding: 10px 16px;
  white-space: pre-wrap;
  line-height: 1.6;
}

/* RAG progress indicator */
.rag-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
  background: #f5f7fa;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 13px;
  color: #666;
}

.rag-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid #d9d9d9;
  border-top: 2px solid #409eff;
  border-radius: 50%;
  animation: rag-spin 0.8s linear infinite;
}

@keyframes rag-spin {
  to { transform: rotate(360deg); }
}

.rag-status-text {
  color: #555;
}

.rag-result {
  color: #409eff;
  font-weight: 500;
  margin-left: auto;
}

/* Citations */
.citations {
  margin-top: 8px;
  border: 1px solid #eee;
  border-radius: 8px;
  overflow: hidden;
}

.citations-toggle {
  padding: 8px 12px;
  font-size: 12px;
  color: #666;
  cursor: pointer;
  background: #fafafa;
  display: flex;
  align-items: center;
  gap: 6px;
  user-select: none;
}

.citations-toggle:hover {
  background: #f0f2f5;
}

.toggle-arrow {
  font-size: 10px;
  color: #999;
}

.citations-list {
  padding: 8px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.citation-card {
  padding: 8px 10px;
  background: #fafafa;
  border-radius: 6px;
  border: 1px solid #f0f0f0;
}

.citation-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}

.citation-header strong {
  font-size: 13px;
  color: #333;
}

.citation-type {
  font-size: 11px;
  color: #999;
  background: #eee;
  padding: 1px 6px;
  border-radius: 4px;
}

.citation-score {
  font-size: 11px;
  color: #67c23a;
  margin-left: auto;
}

.citation-preview {
  font-size: 12px;
  color: #888;
  line-height: 1.5;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.chat-error {
  color: #f56c6c;
  font-size: 13px;
  padding: 8px 12px;
  background: #fef0f0;
  border-radius: 6px;
}

.chat-input-bar {
  display: flex;
  padding: 16px 24px;
  border-top: 1px solid #e8e8e8;
  gap: 12px;
}

.chat-input-bar input {
  flex: 1;
  height: 40px;
  padding: 0 14px;
  font-size: 14px;
  border: 1px solid #d9d9d9;
  border-radius: 20px;
  outline: none;
}

.chat-input-bar input:focus {
  border-color: #409eff;
}

.chat-input-bar button {
  padding: 0 24px;
  height: 40px;
  font-size: 14px;
  color: #fff;
  background: #409eff;
  border: none;
  border-radius: 20px;
  cursor: pointer;
}

.chat-input-bar button:hover {
  background: #337ecc;
}

.chat-input-bar button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
