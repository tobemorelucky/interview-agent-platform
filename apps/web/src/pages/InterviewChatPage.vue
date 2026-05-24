<script setup lang="ts">
import { ref, nextTick, watch, onUnmounted } from "vue"
import {
  createSession,
  listSessions,
  getSession,
  bindResume,
  deleteSession,
  sendMessageStream,
  getCurrentQuestion,
  getQuestions,
  getQuestionDetail,
} from "../api/interview"
import { uploadResume, getResume as apiGetResume } from "../api/resume"
import { ApiError } from "../api/client"
import type { InterviewSession, InterviewMessage, InterviewQuestion, InterviewQuestionSummary } from "../types/interview"
import { sourceLabel, sourceColor } from "../types/interview"
import { statusLabel, stageLabel } from "../types/resume"

// ── State ──
const sessions = ref<InterviewSession[]>([])
const activeSessionId = ref<number | null>(null)
const messages = ref<InterviewMessage[]>([])
const currentQuestion = ref<InterviewQuestion | null>(null)
const questionList = ref<InterviewQuestionSummary[]>([])
const answerVisible = ref(false)
const standardAnswer = ref("")
const questionBudget = ref(20)
const generatedCount = ref(0)
const inputText = ref("")
const loading = ref(false)
const sending = ref(false)
const streamingContent = ref("")
const streamingSource = ref("")
const streaming = ref(false)

// Resume upload state
const uploadingResume = ref(false)
const resumePollTimer = ref<ReturnType<typeof setInterval> | null>(null)
const resumeProcessingStage = ref("")
const resumeStageMessage = ref("")

let abortController: AbortController | null = null
const chatContainer = ref<HTMLElement | null>(null)

// ── Computed ──
const activeSession = () => sessions.value.find((s) => s.id === activeSessionId.value)
const isActiveResumeReady = () => activeSession()?.resume_status === "COMPLETED"
const isActiveResumeFailed = () => activeSession()?.resume_status === "FAILED"
const hasActiveResume = () => Boolean(activeSession()?.resume_id)

// ── Methods ──

async function loadSessions() {
  try {
    sessions.value = await listSessions()
    const active = activeSession()
    if (active?.resume_id && active.resume_status !== "COMPLETED" && active.resume_status !== "FAILED") {
      startResumePoll(active.resume_id, active.id)
    }
  } catch (e) {
    console.error("Failed to load sessions", e)
  }
}

async function selectSession(id: number, force = false) {
  if (!force && activeSessionId.value === id) return
  activeSessionId.value = id
  loading.value = true
  messages.value = []
  streamingContent.value = ""
  streamingSource.value = ""
  streaming.value = false
  // Phase 3.6: Clear question state when switching sessions
  currentQuestion.value = null
  questionList.value = []
  answerVisible.value = false
  standardAnswer.value = ""
  questionBudget.value = 20
  generatedCount.value = 0
  try {
    const detail = await getSession(id)
    messages.value = detail.messages || []
    const index = sessions.value.findIndex((s) => s.id === id)
    if (index >= 0) {
      sessions.value[index] = {
        ...sessions.value[index],
        ...detail,
      }
    }
    // Phase 3.6: Restore question state on refresh
    if (detail.target_position_confirmed) {
      questionBudget.value = detail.question_count || 20
      try {
        const cq = await getCurrentQuestion(id)
        if (cq && cq.question) currentQuestion.value = cq
      } catch { /* question endpoint may not be ready */ }
      try {
        const ql = await getQuestions(id)
        questionList.value = ql.questions || []
        generatedCount.value = ql.total || 0
      } catch { /* questions endpoint may not be ready */ }
    }

    // If resume processing, start polling
    if (detail.resume_id && detail.resume_status !== "COMPLETED" && detail.resume_status !== "FAILED") {
      startResumePoll(detail.resume_id, id)
    } else {
      if (resumePollTimer.value) clearInterval(resumePollTimer.value)
      resumePollTimer.value = null
      uploadingResume.value = false
      resumeProcessingStage.value = ""
      resumeStageMessage.value = ""
    }
  } catch (e) {
    console.error("Failed to load session", e)
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

async function handleCreateSession() {
  try {
    const session = await createSession()
    sessions.value.unshift(session)
    await selectSession(session.id)
  } catch (e) {
    if (e instanceof ApiError) alert(e.message)
  }
}

async function handleDeleteSession(id: number) {
  if (!confirm("确定要删除这个面试会话吗？")) return
  try {
    await deleteSession(id)
    sessions.value = sessions.value.filter((s) => s.id !== id)
    if (activeSessionId.value === id) {
      activeSessionId.value = null
      messages.value = []
    }
  } catch {
    alert("删除失败")
  }
}

async function handleUploadResume() {
  const sessionId = activeSessionId.value
  if (!sessionId) return

  const input = document.createElement("input")
  input.type = "file"
  input.accept = ".pdf,.docx,.txt"
  input.onchange = async (e) => {
    const file = (e.target as HTMLInputElement).files?.[0]
    if (!file) return
    uploadingResume.value = true
    resumeProcessingStage.value = ""
    resumeStageMessage.value = ""
    try {
      const resume = await uploadResume(file)
      if (resume.status === "FAILED") {
        alert(`简历处理任务创建失败: ${resume.error_message || "未知错误"}`)
        uploadingResume.value = false
        return
      }
      resumeProcessingStage.value = resume.processing_stage || ""
      resumeStageMessage.value = resume.stage_message || ""
      await bindResume(sessionId, resume.id)
      await loadSessions()
      await selectSession(sessionId, true)
      startResumePoll(resume.id, sessionId)
    } catch (e) {
      if (e instanceof ApiError) alert(e.message)
      else alert("上传失败")
      uploadingResume.value = false
    }
  }
  input.click()
}

function startResumePoll(resumeId: number, sessionId = activeSessionId.value) {
  if (resumePollTimer.value) clearInterval(resumePollTimer.value)
  uploadingResume.value = true
  let pollCount = 0
  resumePollTimer.value = setInterval(async () => {
    try {
      pollCount++
      const resume = await apiGetResume(resumeId)
      resumeProcessingStage.value = resume.processing_stage || ""
      resumeStageMessage.value = resume.stage_message || ""
      if (resume.status === "COMPLETED") {
        clearInterval(resumePollTimer.value!)
        resumePollTimer.value = null
        uploadingResume.value = false
        await loadSessions()
        // Finalize the already-bound session and add the welcome message.
        if (sessionId) {
          try {
            await bindResume(sessionId, resumeId)
            resumeProcessingStage.value = ""
            resumeStageMessage.value = ""
            // Reload session to show welcome message
            if (activeSessionId.value === sessionId) {
              await selectSession(sessionId, true)
            }
            // Update sessions list
            await loadSessions()
          } catch (e) {
            if (e instanceof ApiError) alert(e.message)
          }
        }
      } else if (resume.status === "FAILED") {
        clearInterval(resumePollTimer.value!)
        resumePollTimer.value = null
        uploadingResume.value = false
        resumeProcessingStage.value = resume.processing_stage || "FAILED"
        resumeStageMessage.value = resume.error_message || resume.stage_message || ""
        await loadSessions()
        if (sessionId && activeSessionId.value === sessionId) {
          await selectSession(sessionId, true)
        }
        alert(`简历处理失败: ${resume.error_message || "未知错误"}`)
      } else if (resume.status === "UPLOADED" || resume.processing_stage === "QUEUED") {
        // Warn after 20 polls (~60s) that worker may not be running
        if (pollCount === 20) {
          resumeStageMessage.value =
            "处理较慢，请确认 Celery Worker 已启动。如长时间无响应，请检查服务状态。"
        }
      }
    } catch {
      // polling error, ignore
    }
  }, 3000)
}

async function handleSend() {
  const text = inputText.value.trim()
  if (!text || sending.value || !activeSessionId.value) return

  if (!hasActiveResume()) {
    alert("请先上传并绑定一份简历")
    return
  }
  if (isActiveResumeFailed()) {
    alert("简历处理失败，请重新上传简历后再开始面试")
    return
  }
  if (!isActiveResumeReady()) {
    alert("简历还在处理中，请等待处理完成后再开始面试")
    return
  }

  // Add user message locally
  const turnIndex = (messages.value.length > 0
    ? Math.max(...messages.value.map((m) => m.turn_index))
    : 0) + (streaming.value ? 0 : 1)

  messages.value.push({
    id: Date.now(),
    role: "USER",
    content: text,
    metadata_json: null,
    turn_index: turnIndex,
    created_at: new Date().toISOString(),
  })

  inputText.value = ""
  sending.value = true
  streaming.value = true
  streamingContent.value = ""
  streamingSource.value = ""
  await scrollToBottom()

  abortController = sendMessageStream(
    activeSessionId.value,
    text,
    // onToken — accumulate for messages display
    (token) => {
      streamingContent.value += token
    },
    // onRetrieval
    (data) => {
      streamingSource.value = data.source
    },
    // onCitation
    () => {},
    // onDone
    (data) => {
      // Save accumulated content as assistant message
      if (streamingContent.value) {
        messages.value.push({
          id: data.message_id || 0,
          role: "ASSISTANT",
          content: streamingContent.value,
          metadata_json: {
            source: "QUESTION_DRIVEN",
            action: data.action,
          },
          turn_index: data.turn_index,
          created_at: new Date().toISOString(),
        })
      }
      streamingContent.value = ""
      streaming.value = false
      sending.value = false
      streamingSource.value = ""
      abortController = null
      loadSessions()
    },
    // onCompressed
    (data) => {
      console.log(`Memory compressed: ${data.compressed_turns} turns`)
    },
    // onError
    (code, message) => {
      alert(`错误 [${code}]: ${message}`)
      streamingContent.value = ""
      streaming.value = false
      sending.value = false
      abortController = null
    },
    // onStatus
    () => {},
    // Phase 3.6: New SSE callbacks
    // onEvaluation
    () => {
      if (currentQuestion.value) {
        currentQuestion.value = { ...currentQuestion.value }
      }
    },
    // onFollowUp
    (data) => {
      if (currentQuestion.value) {
        currentQuestion.value.follow_up_count = data.follow_up_count
      }
    },
    // onQuestion
    (data) => {
      currentQuestion.value = {
        question_id: data.question_id,
        question_index: data.question_index,
        total_questions: data.total_questions,
        question: data.question,
        source: data.source,
        dimension: data.dimension,
        difficulty: data.difficulty,
        evidence: data.evidence,
      }
      answerVisible.value = false
      standardAnswer.value = ""
      generatedCount.value = data.question_index + 1
      questionBudget.value = data.total_questions
    },
    // onDynamicQuestion
    (data) => {
      currentQuestion.value = {
        question_id: data.question_id,
        question_index: data.question_index,
        question: data.question,
        source: data.source,
        dimension: data.dimension,
        difficulty: data.difficulty,
        is_dynamic: true,
        parent_question_id: data.parent_question_id,
      }
      answerVisible.value = false
      standardAnswer.value = ""
      generatedCount.value = data.question_index + 1
    },
    // onQuestionTransition
    () => {
      // Frontend can show "generating next question" indicator
    },
    // onInterviewComplete
    (data) => {
      alert(`面试结束！已回答 ${data.answered_count} / ${data.question_budget || data.total_questions || questionBudget.value} 题`)
      sending.value = false
      streaming.value = false
    }
  )
}

function handleStopStream() {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  // Save partial content as message
  if (streamingContent.value) {
    messages.value.push({
      id: Date.now(),
      role: "ASSISTANT",
      content: streamingContent.value,
      metadata_json: {
        source: (streamingSource.value as "KB_RETRIEVED" | "LLM_GENERATED" | "HYBRID") || "LLM_GENERATED",
      },
      turn_index: messages.value.length > 0
        ? Math.max(...messages.value.map((m) => m.turn_index))
        : 0,
      created_at: new Date().toISOString(),
    })
  }
  streamingContent.value = ""
  streaming.value = false
  sending.value = false
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

// Phase 3.6: Simple markdown formatting for display
function formatContent(text: string): string {
  if (!text) return ""
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>")
}

// Phase 3.6: Toggle standard answer visibility
async function toggleAnswer() {
  if (!currentQuestion.value || !activeSessionId.value) return
  if (answerVisible.value) {
    answerVisible.value = false
    return
  }
  try {
    const detail = await getQuestionDetail(activeSessionId.value, currentQuestion.value.question_id)
    standardAnswer.value = detail.standard_answer || "暂无答案"
    answerVisible.value = true
  } catch {
    standardAnswer.value = "获取答案失败"
    answerVisible.value = true
  }
}

async function scrollToBottom() {
  await nextTick()
  if (chatContainer.value) {
    chatContainer.value.scrollTop = chatContainer.value.scrollHeight
  }
}

watch(streamingContent, () => {
  scrollToBottom()
})

onUnmounted(() => {
  if (resumePollTimer.value) clearInterval(resumePollTimer.value)
  if (abortController) abortController.abort()
})

// Init
loadSessions()
</script>

<template>
  <div class="interview-page">
    <!-- Left: Session List -->
    <aside class="session-sidebar">
      <div class="sidebar-header">
        <h3>面试会话</h3>
        <button class="btn-new" @click="handleCreateSession">+ 新建</button>
      </div>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-card"
          :class="{ active: s.id === activeSessionId }"
          @click="selectSession(s.id)"
        >
          <div class="session-title">{{ s.title || `面试 ${s.id}` }}</div>
          <div class="session-meta">
            <span v-if="s.resume_filename" class="resume-name">{{ s.resume_filename }}</span>
            <span v-else class="no-resume">未绑定简历</span>
            <span class="turn-count">{{ s.turn_count }} 轮</span>
          </div>
          <button
            class="btn-delete-session"
            @click.stop="handleDeleteSession(s.id)"
            title="删除会话"
          >
            ×
          </button>
        </div>
        <div v-if="sessions.length === 0" class="empty-sessions">
          暂无面试会话，点击"新建"开始
        </div>
      </div>
    </aside>

    <!-- Center: Chat -->
    <main class="chat-main">
      <!-- No session selected -->
      <div v-if="!activeSessionId" class="no-session">
        <p>选择一个面试会话或新建一个开始模拟面试</p>
      </div>

      <!-- Session active -->
      <template v-else>
        <!-- Resume status bar -->
        <div class="resume-bar" v-if="activeSession()">
          <template v-if="uploadingResume">
            <span class="uploading-indicator">⏳ 简历处理中</span>
            <span v-if="resumeProcessingStage" class="stage-text">
              {{ stageLabel(resumeProcessingStage) }}
            </span>
            <span v-if="resumeStageMessage" class="stage-msg">
              {{ resumeStageMessage }}
            </span>
          </template>
          <template v-else-if="activeSession()!.resume_filename">
            <span class="resume-bound">📄 {{ activeSession()!.resume_filename }}</span>
            <span
              v-if="activeSession()!.resume_status"
              class="resume-status"
              :class="activeSession()!.resume_status?.toLowerCase()"
            >
              {{ statusLabel(activeSession()!.resume_status!) }}
            </span>
          </template>
          <template v-else>
            <span class="no-resume-warn">⚠ 请先上传简历以开始面试</span>
          </template>
          <button
            v-if="!uploadingResume"
            class="btn-upload"
            @click="handleUploadResume"
          >
            {{ hasActiveResume() ? '更换简历' : '上传简历' }}
          </button>
        </div>

        <!-- Messages -->
        <div ref="chatContainer" class="chat-messages" v-if="!loading">
          <div v-if="messages.length === 0 && !streaming && !currentQuestion" class="chat-empty">
            <p v-if="isActiveResumeReady() && !activeSession()?.target_position_confirmed">
              简历已就绪，请确认面试岗位后开始
            </p>
            <p v-else-if="isActiveResumeReady()">
              简历已就绪，面试即将开始...
            </p>
            <p v-else-if="isActiveResumeFailed()">简历处理失败，请重新上传简历</p>
            <p v-else-if="hasActiveResume()">简历处理中，请等待处理完成</p>
            <p v-else>请先上传简历</p>
          </div>

          <!-- Phase 3.6: Question card as chat bubble -->
          <div v-if="currentQuestion && activeSession()?.target_position_confirmed" class="message-row assistant">
            <div class="message-bubble question-bubble">
              <div class="q-header">
                <span class="q-badge">Q{{ currentQuestion.question_index + 1 }} / {{ questionBudget }}</span>
                <span class="q-dimension" v-if="currentQuestion.dimension">{{ currentQuestion.dimension }}</span>
                <span class="q-difficulty" v-if="currentQuestion.difficulty">{{ currentQuestion.difficulty }}</span>
                <span class="q-source" v-if="currentQuestion.source">{{ sourceLabel(currentQuestion.source) }}</span>
                <span v-if="currentQuestion.is_dynamic" class="q-dynamic">⚡ 临时追问</span>
              </div>
              <div class="q-body">{{ currentQuestion.question }}</div>
              <button class="q-answer-btn" @click="toggleAnswer">
                {{ answerVisible ? '收起答案' : '展开参考答案' }}
              </button>
              <div v-if="answerVisible" class="q-answer">{{ standardAnswer }}</div>
            </div>
          </div>

          <div
            v-for="msg in messages"
            :key="msg.id"
            class="message-row"
            :class="msg.role.toLowerCase()"
          >
            <div class="message-bubble">
              <div class="message-content" v-html="formatContent(msg.content)"></div>
              <div class="message-meta" v-if="msg.role === 'ASSISTANT'">
                <span
                  v-if="msg.metadata_json?.source"
                  class="source-badge"
                  :style="{ background: sourceColor(msg.metadata_json.source) }"
                >
                  {{ sourceLabel(msg.metadata_json.source) }}
                </span>
              </div>
              <!-- Evidence -->
              <div
                v-if="msg.metadata_json?.evidence && msg.metadata_json.evidence.length > 0"
                class="evidence-section"
              >
                <details>
                  <summary>引用来源 ({{ msg.metadata_json.evidence.length }})</summary>
                  <div
                    v-for="(ev, i) in msg.metadata_json.evidence"
                    :key="i"
                    class="evidence-item"
                  >
                    <span class="evidence-title">{{ ev.title }}</span>
                    <span class="evidence-score">{{ (ev.score * 100).toFixed(0) }}%</span>
                    <p class="evidence-preview">{{ ev.preview }}</p>
                  </div>
                </details>
              </div>
            </div>
          </div>

          <!-- Streaming bubble -->
          <div v-if="streaming" class="message-row assistant">
            <div class="message-bubble">
              <div class="message-content" v-html="formatContent(streamingContent) || '思考中...'"></div>
              <div class="message-meta" v-if="streamingSource">
                <span
                  class="source-badge"
                  :style="{ background: sourceColor(streamingSource) }"
                >
                  {{ sourceLabel(streamingSource) }}
                </span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="loading" class="chat-loading">加载中...</div>

        <!-- Input -->
        <div class="chat-input">
          <textarea
            v-model="inputText"
            :disabled="sending || !isActiveResumeReady()"
            :placeholder="
              isActiveResumeReady()
                ? '输入你的回答... (Enter 发送, Shift+Enter 换行)'
                : isActiveResumeFailed()
                  ? '简历处理失败，请重新上传简历'
                  : hasActiveResume()
                    ? '简历处理中，请稍候'
                    : '请先上传简历'
            "
            rows="2"
            @keydown="handleKeydown"
          ></textarea>
          <button
            v-if="!streaming"
            class="btn-send"
            :disabled="!inputText.trim() || sending || !isActiveResumeReady()"
            @click="handleSend"
          >
            发送
          </button>
          <button v-else class="btn-stop" @click="handleStopStream">停止</button>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
.interview-page {
  display: flex;
  height: calc(100vh - 56px);
  max-width: 1200px;
  margin: 0 auto;
  background: #f5f7fa;
}

/* ── Sidebar ── */
.session-sidebar {
  width: 260px;
  min-width: 260px;
  background: #fff;
  border-right: 1px solid #e8e8e8;
  display: flex;
  flex-direction: column;
}

.sidebar-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px;
  border-bottom: 1px solid #f0f0f0;
}

.sidebar-header h3 {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a2e;
  margin: 0;
}

.btn-new {
  padding: 4px 12px;
  font-size: 13px;
  background: #409eff;
  color: #fff;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.btn-new:hover {
  background: #337ecc;
}

.session-list {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
}

.session-card {
  padding: 12px;
  margin-bottom: 6px;
  border-radius: 6px;
  cursor: pointer;
  position: relative;
  transition: background 0.15s;
}

.session-card:hover {
  background: #f0f5ff;
}

.session-card.active {
  background: #e6f0ff;
  border: 1px solid #b3d8ff;
}

.session-title {
  font-size: 14px;
  font-weight: 500;
  color: #333;
  margin-bottom: 4px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding-right: 20px;
}

.session-meta {
  font-size: 12px;
  color: #999;
}

.session-meta span {
  margin-right: 8px;
}

.no-resume {
  color: #f56c6c;
}

.resume-name {
  color: #67c23a;
}

.btn-delete-session {
  position: absolute;
  top: 8px;
  right: 8px;
  width: 20px;
  height: 20px;
  border: none;
  background: none;
  color: #ccc;
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
}

.btn-delete-session:hover {
  color: #f56c6c;
  background: #fef0f0;
}

.empty-sessions {
  text-align: center;
  padding: 24px;
  color: #999;
  font-size: 13px;
}

/* ── Chat Main ── */
.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.no-session {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
  font-size: 14px;
}

/* ── Resume Bar ── */
.resume-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  background: #fff;
  border-bottom: 1px solid #f0f0f0;
  font-size: 13px;
  flex-wrap: wrap;
}

.uploading-indicator {
  color: #409eff;
  font-weight: 500;
}

.stage-text {
  color: #409eff;
}

.stage-msg {
  color: #999;
  font-size: 12px;
}

.resume-bound {
  color: #333;
  font-weight: 500;
}

.resume-status {
  padding: 1px 8px;
  border-radius: 8px;
  font-size: 11px;
  color: #fff;
  background: #909399;
}

.resume-status.completed {
  background: #67c23a;
}

.resume-status.processing {
  background: #409eff;
}

.resume-status.failed {
  background: #f56c6c;
}

.no-resume-warn {
  color: #e6a23c;
}

.btn-upload {
  margin-left: auto;
  padding: 4px 12px;
  font-size: 12px;
  border: 1px solid #409eff;
  color: #409eff;
  background: #fff;
  border-radius: 4px;
  cursor: pointer;
}

.btn-upload:hover {
  background: #ecf5ff;
}

/* ── Messages ── */
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
}

.chat-empty {
  text-align: center;
  padding: 60px 0;
  color: #999;
  font-size: 14px;
}

.chat-loading {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #999;
}

.message-row {
  display: flex;
  margin-bottom: 16px;
}

.message-row.user {
  justify-content: flex-end;
}

.message-row.assistant {
  justify-content: flex-start;
}

.message-bubble {
  max-width: 75%;
  padding: 12px 16px;
  border-radius: 12px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.message-row.user .message-bubble {
  background: #409eff;
  color: #fff;
  border-bottom-right-radius: 4px;
}

.message-row.assistant .message-bubble {
  background: #fff;
  color: #333;
  border-bottom-left-radius: 4px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.06);
}

.message-meta {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}

.source-badge {
  display: inline-block;
  padding: 1px 8px;
  font-size: 11px;
  color: #fff;
  border-radius: 8px;
}

/* ── Evidence ── */
.evidence-section {
  margin-top: 8px;
  font-size: 12px;
}

.evidence-section details {
  cursor: pointer;
}

.evidence-section summary {
  color: #888;
  font-size: 12px;
}

.evidence-item {
  margin-top: 6px;
  padding: 6px 8px;
  background: #fafafa;
  border-radius: 4px;
}

.evidence-title {
  font-weight: 500;
  color: #555;
}

.evidence-score {
  margin-left: 6px;
  font-size: 11px;
  color: #67c23a;
}

.evidence-preview {
  margin: 4px 0 0;
  color: #999;
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ── Input ── */
.chat-input {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  padding: 12px 16px;
  background: #fff;
  border-top: 1px solid #f0f0f0;
}

.chat-input textarea {
  flex: 1;
  padding: 10px 12px;
  font-size: 14px;
  border: 1px solid #d9d9d9;
  border-radius: 8px;
  resize: none;
  outline: none;
  font-family: inherit;
  line-height: 1.5;
}

.chat-input textarea:focus {
  border-color: #409eff;
}

.chat-input textarea:disabled {
  background: #f5f5f5;
  cursor: not-allowed;
}

.btn-send,
.btn-stop {
  padding: 8px 20px;
  font-size: 14px;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  min-width: 60px;
}

.btn-send {
  background: #409eff;
  color: #fff;
}

.btn-send:hover:not(:disabled) {
  background: #337ecc;
}

.btn-send:disabled {
  background: #a0cfff;
  cursor: not-allowed;
}

.btn-stop {
  background: #f56c6c;
  color: #fff;
}

.btn-stop:hover {
  background: #e04545;
}

/* Phase 3.6: Question Bubble in chat */
.question-bubble {
  background: #f0f7ff !important;
  border: 1px solid #b3d8ff !important;
  max-width: 90% !important;
}
.q-header {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 10px;
  flex-wrap: wrap;
}
.q-badge {
  background: #409eff;
  color: #fff;
  padding: 2px 10px;
  border-radius: 12px;
  font-size: 13px;
  font-weight: 600;
}
.q-dimension, .q-difficulty, .q-source {
  font-size: 12px;
  color: #606266;
  background: #ecf5ff;
  padding: 2px 8px;
  border-radius: 8px;
}
.q-dynamic {
  font-size: 12px;
  color: #e6a23c;
  font-weight: 600;
}
.q-body {
  font-size: 15px;
  line-height: 1.6;
  color: #303133;
  margin-bottom: 12px;
}
.q-answer-btn {
  background: none;
  border: 1px solid #409eff;
  color: #409eff;
  padding: 4px 14px;
  border-radius: 6px;
  cursor: pointer;
  font-size: 13px;
}
.q-answer-btn:hover {
  background: #ecf5ff;
}
.q-answer {
  margin-top: 10px;
  padding: 12px;
  background: #fff;
  border-radius: 8px;
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
  color: #303133;
}
</style>
