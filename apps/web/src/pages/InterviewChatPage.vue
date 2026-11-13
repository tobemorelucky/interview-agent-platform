<script setup lang="ts">
import { ref, computed, nextTick, watch, onUnmounted } from "vue"
import { canReviewQuestion, followUpOrdinal } from '../types/match-presentation'
import { pendingStage } from '../types/interview-processing'
import { applySkillRecommendation, chooseSkill, type SkillChoice } from '../types/skill-selection'
import ReferenceAnswerView from '../components/ReferenceAnswer.vue'
import DesignTraceView from '../components/DesignTrace.vue'
import type { DesignPlan, DesignTrace } from '../types/interview'
import type { ReferenceAnswer, InterviewerOptions, InterviewerSelection, SkillRecommendation } from '../types/interview'
import { displayLabel, publicName, dimensionLabels, userError } from "../i18n/zh-CN"
import { useRoute, useRouter } from "vue-router"
import {
  createSession,
  createSessionForTarget,
  listSessions,
  getSession,
  bindResume,
  deleteSession,
  sendMessageStream,
  getQuestions,
  getQuestionDetail,
  consolidateInterviewMemory,
  getInterviewTargets,
  getInterviewerOptions,
  startInterview,
  type InterviewTarget,
} from "../api/interview"
import { uploadResume, getResume as apiGetResume } from "../api/resume"
import { getTarget, listTargets, skillRecommendations } from "../api/job-target"
import { resolvePrivateEntryTarget } from "./interviewEntry"
import type { JobTarget, TargetSkillRecommendation } from "../types/job-target"
import { ApiError } from "../api/client"
import type { InterviewSession, InterviewMessage, InterviewQuestionSummary } from "../types/interview"
import { sourceLabel, sourceColor } from "../types/interview"
import { statusLabel, stageLabel } from "../types/resume"

const route = useRoute(), router = useRouter()
const entryRoute = route, entryRouter = router
// Snapshot the one-click hand-off before the router.replace() clears the query.
const entrySkillId = String(entryRoute.query.recommended_skill_id || '')
const entrySkillSource = String(entryRoute.query.recommendation_source || '')
const entryRecommendationKey = String(entryRoute.query.recommendation_key || '')
const entryTargetId = Number(entryRoute.query.job_target_id || 0)

// ── State ──
const sessions = ref<InterviewSession[]>([])
const targets = ref<InterviewTarget[]>([])
const targetsLoading = ref(true), sessionsLoading = ref(true)
const selectedJobId = ref<number | null>(null)
// Private user-supplied targets are a separate source from published jobs.
const jobTargets = ref<JobTarget[]>([])
const selectedTargetId = ref<number | null>(null)
const createBudget = ref(6)
const interviewerOptions = ref<InterviewerOptions | null>(null)
const interviewerOptionsError = ref('')
const creatingSession = ref(false)
const designInterview = ref(false), activeDesign = ref(false)
const adaptiveInterview = ref(false)
watch(adaptiveInterview, value => { if (value) designInterview.value = false })
watch(designInterview, value => { if (value) adaptiveInterview.value = false })
const designTrace = ref<DesignTrace>()
const designPlan = ref<DesignPlan>({ deduplication: 'NONE', capacity_guard: 'NONE', capacity: 1 })
const skillChoice = ref<SkillChoice>({ skill_id: '', source: null })
const roundAndStyle = ref<Pick<InterviewerSelection, 'interview_round' | 'interviewer_style'>>({ interview_round: 'technical_screen', interviewer_style: 'standard' })
const interviewerSelection = computed<InterviewerSelection>(() => ({ ...roundAndStyle.value, interviewer_skill_id: skillChoice.value.skill_id,
  design_interview: designInterview.value,
  adaptive_interview: adaptiveInterview.value,
  interviewer_selection_source: skillChoice.value.source || 'USER_SELECTED' }))
const privateSkillRec = ref<TargetSkillRecommendation | null>(null)
let privateSkillSequence = 0
const recommendationStale = computed(() => {
  const rec = privateSkillRec.value
  if (!rec) return false
  // The server compares against the confirmed inputs; the local check is a
  // fallback if the advisory read raced with an edit.
  return !!rec.stale || (!!entryRecommendationKey && entryRecommendationKey !== rec.input_fingerprint)
})
// A private JD now uses the same recommendation shape as a published job, so
// the existing pure reducer keeps protecting an explicit manual choice.
const skillRecommendation = computed<SkillRecommendation | undefined>(() => {
  if (selectedTargetId.value) {
    const rec = privateSkillRec.value
    if (!rec) return undefined
    return {
      policy_version: rec.policy_version, status: rec.status,
      auto_select: rec.auto_select, recommended_skill_id: rec.recommended_skill_id,
      recommended_version: rec.recommended_version, reason: rec.message,
      ranking: rec.candidates.map(c => ({
        skill_id: c.skill_id, version: c.version, label: c.label, rank: c.rank,
        excluded: c.excluded,
        reason: c.matched_reason + (c.uncertain ? '（证据不确定，请人工确认）' : ''),
        evidence: c.evidence.map(e => ({
          field: e.field, requirement_id: e.requirement_id ?? null,
          term: e.matched_term || '',
          excerpt: e.verbatim_jd_evidence || e.requirement_statement || e.excerpt || '',
        })),
      })),
    }
  }
  return targets.value.find(t => t.job_id === selectedJobId.value)?.interviewer_skill_recommendation
})
watch(selectedJobId, value => { if (value != null) selectedTargetId.value = null })
watch(selectedTargetId, async value => {
  if (value != null) selectedJobId.value = null
  privateSkillRec.value = null
  const sequence = ++privateSkillSequence
  if (!value) return
  try {
    const rec = await skillRecommendations(value, entryRecommendationKey || undefined)
    if (sequence === privateSkillSequence && selectedTargetId.value === value) privateSkillRec.value = rec
  } catch { /* The manual Skill dropdown stays usable without the advisory read. */ }
})
const manualSkillMismatch = computed(() => skillChoice.value.source === 'USER_SELECTED' && !!selectedJobId.value &&
  (!skillRecommendation.value?.auto_select || skillRecommendation.value.recommended_skill_id !== skillChoice.value.skill_id))
watch([skillRecommendation, interviewerOptions], () => {
  skillChoice.value = applySkillRecommendation(skillChoice.value, skillRecommendation.value, interviewerOptions.value?.skills.map(s => s.id) || [])
})
function selectSkill(event: Event) { skillChoice.value = chooseSkill((event.target as HTMLSelectElement).value) }
const activeInterviewer = computed(() => profileContext.value?.interviewer_config)
const activeQuestionId = ref<number | null>(null)
const retryAnswer = ref<{ question_id: number; idempotency_key: string; content: string; design_plan?: DesignPlan } | null>(null)
const answerPersisted = ref(false)
const profileContext = ref<InterviewSession["context_snapshot"]>()
const memoryGuidance = computed(() => (profileContext.value as { memory_guided_plan?: { items: { label: string; history_summary: string }[] } } | undefined)?.memory_guided_plan?.items || [])
const trainingLabel = computed(() => ({FULL:'完整模拟面试',WEAKNESS:'薄弱项专项训练',RECHECK:'冲突 / 遗忘独立复测'} as Record<string,string>)[(profileContext.value as {training?:{mode:string}}|undefined)?.training?.mode||''])
const completion = ref<InterviewSession["completion_json"]>()
const pageError = ref("")
const streamErrorCode = ref("")
const isReplay = computed(() => messages.value.some(m => m.metadata_json?.mode === 'replay'))
const activeSessionId = ref<number | null>(null)
const messages = ref<InterviewMessage[]>([])
const questionList = ref<InterviewQuestionSummary[]>([])
const answerVisibleMap = ref<Record<number, boolean>>({})
const standardAnswerMap = ref<Record<number, string>>({})
const referenceAnswerMap = ref<Record<number, ReferenceAnswer | null>>({})
const evalCollapsed = ref<Record<number, boolean>>({})
const questionBudget = ref(20)
const generatedCount = ref(0)
const generationStage = ref<"idle" | "submitting" | "validating_design" | "generating_evaluation" | "generating_first_question" | "generating_next_question" | "evaluating">("idle")
const waitingSeconds = ref(0)
let waitingTimer: ReturnType<typeof setInterval> | null = null
watch(generationStage, stage => {
  if (waitingTimer) clearInterval(waitingTimer)
  waitingTimer = null
  waitingSeconds.value = 0
  if (stage !== 'idle') {
    const started = Date.now()
    waitingTimer = setInterval(() => { waitingSeconds.value = Math.floor((Date.now() - started) / 1000) }, 1000)
  }
})
onUnmounted(() => { if (waitingTimer) clearInterval(waitingTimer) })
let answerPoll: ReturnType<typeof setTimeout> | null = null
const processingAnswer = computed(() => ['submitting', 'validating_design', 'evaluating', 'generating_evaluation', 'generating_next_question'].includes(generationStage.value))

function stopAnswerPoll() {
  if (answerPoll) clearTimeout(answerPoll)
  answerPoll = null
}

function pollAnswer(id: number) {
  stopAnswerPoll()
  answerPoll = setTimeout(async () => {
    if (activeSessionId.value !== id || sending.value) return
    try {
      const detail = await getSession(id)
      if (activeSessionId.value !== id) return
      generationStage.value = pendingStage(detail.pending_answer)
      if (generationStage.value !== 'idle') pollAnswer(id)
      else {
        await selectSession(id, true)
        if (detail.pending_answer) pageError.value = '本轮处理已中断或超时，回答仍已保存；请安全重试原回答，不会重复提交。'
      }
    } catch (e) {
      generationStage.value = 'idle'
      pageError.value = e instanceof Error ? e.message : '读取处理状态失败，请刷新后安全重试'
    }
  }, 2500)
}
const consolidatingMemory = ref(false)
const memoryConsolidatedMap = ref<Record<number, boolean>>({})

// Phase 3.8: Stable-sorted display items (use this, not messages directly)
const displayItems = computed(() => buildDisplayItems(messages.value))
const evaluatedQuestionIds = computed(() => displayItems.value.filter(item => item.type === 'EVALUATION').map(item => item.question_id))
const canReview = (item: any) => canReviewQuestion(item.question_id, evaluatedQuestionIds.value)
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
const isActiveResumeReady = () => activeSession()?.workflow_mode === "m4" && activeSession()?.status === "IN_PROGRESS"
const isActiveResumeFailed = () => activeSession()?.resume_status === "FAILED"
const hasActiveResume = () => Boolean(activeSession()?.resume_id)

// ── Methods ──

async function loadSessions() {
  sessionsLoading.value = true
  try {
    sessions.value = await listSessions()
    const active = activeSession()
    if (active?.workflow_mode !== "m4" && active?.resume_id && active.resume_status !== "COMPLETED" && active.resume_status !== "FAILED") {
      startResumePoll(active.resume_id, active.id)
    }
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "面试历史读取失败，请重试"
  } finally { sessionsLoading.value = false }
}

async function selectSession(id: number, force = false) {
  if (String(route.query.session_id || "") !== String(id)) await router.replace({ path: "/interview", query: { session_id: id } });
  pageError.value = "";
  streamErrorCode.value = ''
  if (!force && activeSessionId.value === id) return
  stopAnswerPoll()
  generationStage.value = 'idle'
  activeSessionId.value = id
  activeQuestionId.value = null
  retryAnswer.value = null
  answerPersisted.value = false
  profileContext.value = undefined
  completion.value = undefined
  loading.value = true
  messages.value = []
  streamingContent.value = ""
  streamingSource.value = ""
  streaming.value = false
  // Phase 3.6: Clear state when switching sessions
  questionList.value = []
  answerVisibleMap.value = {}
  standardAnswerMap.value = {}
  evalCollapsed.value = {}
  questionBudget.value = 20
  generatedCount.value = 0
  try {
    const detail = await getSession(id)
    messages.value = detail.messages || []
    activeQuestionId.value = detail.active_question_id || null
    profileContext.value = detail.context_snapshot
    activeDesign.value = !!(detail.context_snapshot as unknown as { system_design?: unknown })?.system_design
    designTrace.value = detail.planner_trace_json
    const latestPlan = detail.planner_trace_json?.design_revisions?.slice(-1)[0]?.plan
    if (latestPlan) designPlan.value = { ...latestPlan }
    if (detail.pending_answer?.design_plan) designPlan.value = { ...detail.pending_answer.design_plan }
    completion.value = detail.completion_json
    if (detail.pending_answer) {
      retryAnswer.value = detail.pending_answer
      answerPersisted.value = true
      inputText.value = detail.pending_answer.content
      streamErrorCode.value = detail.pending_answer.error || ''
      generationStage.value = pendingStage(detail.pending_answer)
      if (generationStage.value !== 'idle') pollAnswer(id)
      else pageError.value = detail.pending_answer.error === 'INTERVIEW_BUDGET_EXHAUSTED'
        ? '本场时间或模型预算已不足，回答仍已保存；请新建面试继续，重试不会重置预算。'
        : '本轮处理未完成，回答已保存；可以安全重试原回答。'
    } else { inputText.value = "" }
    // Phase 3.7: Initialize eval collapsed state on refresh
    for (const msg of messages.value) {
      if (msg.metadata_json?.type === "EVALUATION") {
        if (evalCollapsed.value[msg.id] === undefined) {
          evalCollapsed.value[msg.id] = true
        }
      }
    }
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
        const ql = await getQuestions(id)
        questionList.value = ql.questions || []
        generatedCount.value = ql.total || 0
      } catch { /* ignore */ }
    }

    // If resume processing, start polling
    if (detail.workflow_mode !== "m4" && !detail.legacy && detail.resume_id && detail.resume_status !== "COMPLETED" && detail.resume_status !== "FAILED") {
      startResumePoll(detail.resume_id, id)
    } else {
      if (resumePollTimer.value) clearInterval(resumePollTimer.value)
      resumePollTimer.value = null
      uploadingResume.value = false
      resumeProcessingStage.value = ""
      resumeStageMessage.value = ""
    }
  } catch (e) {
    pageError.value = e instanceof Error ? e.message : "面试读取失败，请重试"
  } finally {
    loading.value = false
    await scrollToBottom()
  }
}

async function handleCreateSession() {
  if (creatingSession.value || !interviewerOptions.value || !skillChoice.value.skill_id) return
  creatingSession.value = true
  try {
    const session = selectedTargetId.value
      ? await createSessionForTarget(selectedTargetId.value, createBudget.value, undefined, interviewerSelection.value)
      : await (async () => {
          const target = targets.value.find(t => t.job_id === selectedJobId.value)
          if (!target) throw new ApiError('INVALID_COMPANY_JOB', '请选择已发布岗位或我的目标岗位；需先完成候选人画像')
          return await createSession(target.company_id, target.job_id, createBudget.value, undefined, interviewerSelection.value)
        })()
    sessions.value.unshift(session)
    await selectSession(session.id)
    await handleStart()
  } catch (e) {
    if (e instanceof ApiError) alert(e.message)
  } finally { creatingSession.value = false }
}

async function handleStart() {
  if (!activeSessionId.value) return
  generationStage.value = "generating_first_question"
  try {
    await startInterview(activeSessionId.value)
    await loadSessions()
    await selectSession(activeSessionId.value, true)
  } catch (e) { alert(e instanceof ApiError ? e.message : "首题生成失败，可以重试开始") }
  finally { generationStage.value = "idle" }
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

async function handleConsolidateMemory(sessionId = activeSessionId.value) {
  if (!sessionId || consolidatingMemory.value) return
  if (!confirm("是否将本场面试表现沉淀为长期记忆，用于后续个性化面试？")) return
  consolidatingMemory.value = true
  try {
    const result = await consolidateInterviewMemory(sessionId)
    memoryConsolidatedMap.value[sessionId] = true
    alert(
      `已更新你的面试记忆和技能画像。新增偏好 ${result.preferences_created} 条，更新技能 ${result.skills_updated} 项。`
    )
  } catch (e) {
    if (e instanceof ApiError) alert(e.message)
    else alert("记忆沉淀失败")
  } finally {
    consolidatingMemory.value = false
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
  if (!text || sending.value || processingAnswer.value || !activeSessionId.value) return

  if (!isActiveResumeReady() || !activeQuestionId.value) {
    alert("请开始新的模拟面试；历史会话为只读")
    return
  }
  if (retryAnswer.value && retryAnswer.value.content !== text) { alert("上一回答处理失败，请先重试原回答，不能替换已提交内容"); return }
  const answerContext = retryAnswer.value || { question_id: activeQuestionId.value, idempotency_key: crypto.randomUUID(), content: text,
    ...(activeDesign.value ? { design_plan: { ...designPlan.value } } : {}) }
  pageError.value = ''
  streamErrorCode.value = ''
  if (!retryAnswer.value) answerPersisted.value = false
  retryAnswer.value = answerContext

  // Add user message locally
  const turnIndex = (messages.value.length > 0
    ? Math.max(...messages.value.map((m) => m.turn_index))
    : 0) + (streaming.value ? 0 : 1)

  if (!messages.value.some(m => m.role === "USER" && m.metadata_json?.idempotency_key === answerContext.idempotency_key)) messages.value.push({
    id: Date.now(),
    role: "USER",
    content: text,
    metadata_json: { idempotency_key: answerContext.idempotency_key },
    turn_index: turnIndex,
    created_at: new Date().toISOString(),
  })

  inputText.value = ""
  sending.value = true
  generationStage.value = 'submitting'
  stopAnswerPoll()
  streaming.value = true
  streamingContent.value = ""
  streamingSource.value = ""
  await scrollToBottom()

  abortController = sendMessageStream(
    activeSessionId.value,
    text,
    // onToken — only accumulate when not in generation stage (ignore LLM JSON tokens)
    (token) => {
      if (generationStage.value === "generating_first_question" || generationStage.value === "generating_next_question") return
      streamingContent.value += token
    },
    // onRetrieval
    (data) => {
      streamingSource.value = data.source
    },
    // onCitation
    () => {},
    // onDone — clear streaming state. SSE events already built the UI.
    () => {
      retryAnswer.value = null
      generationStage.value = "idle"
      streamingContent.value = ""
      streaming.value = false
      sending.value = false
      streamingSource.value = ""
      abortController = null
      loadSessions()
      if (activeSessionId.value) selectSession(activeSessionId.value, true)
    },
    // onCompressed
    (data) => {
      console.log(`Memory compressed: ${data.compressed_turns} turns`)
    },
    // onError
    (code, message) => {
      pageError.value = userError(code, message)
      generationStage.value = "idle"
      streamErrorCode.value = code
      streamingContent.value = ""
      streaming.value = false
      sending.value = false
      abortController = null
      inputText.value = answerContext.content
      if (code === 'AUTH_TOKEN_EXPIRED') {
        // A 401 rejects the request before persistence. Keep the draft, not a fake saved answer.
        messages.value = messages.value.filter(m => m.metadata_json?.idempotency_key !== answerContext.idempotency_key)
        retryAnswer.value = null
        answerPersisted.value = false
      } else if (activeSessionId.value) {
        // Read actual persistence/lease state after an error, rather than claim success.
        const id = activeSessionId.value
        getSession(id).then(detail => {
          if (activeSessionId.value !== id || sending.value) return
          if (detail.pending_answer) {
            answerPersisted.value = true
            retryAnswer.value = detail.pending_answer
          }
          generationStage.value = pendingStage(detail.pending_answer)
          if (generationStage.value !== 'idle') pollAnswer(id)
        }).catch(() => {})
      }
    },
    // onStatus
    (stage) => { if (['evaluating', 'validating_design', 'generating_evaluation', 'generating_first_question', 'generating_next_question'].includes(stage)) generationStage.value = stage as typeof generationStage.value },
    // Phase 3.6 final: New SSE callbacks
    // onEvaluation — add collapsible evaluation message
    (data) => {
      activeQuestionId.value = data.question_id || null
      const mid = Date.now()
      evalCollapsed.value[mid] = true  // default collapsed
      messages.value.push({
        id: mid,
        role: "ASSISTANT",
        content: data.evaluation || "",
        metadata_json: {
          type: "EVALUATION",
          ...data,
          question_id: data.question_id,
          score: data.score,
          missing_points: data.missing_points || [],
          risk_tip: data.risk_tip,
          covered_points: data.covered_points || [],
          action: data.action,
        },
        turn_index: messages.value.length,
        created_at: new Date().toISOString(),
      })
    },
    // onFollowUp — add as question bubble
    (data) => {
      const mid = Date.now()
      activeQuestionId.value = data.question_id || null
      messages.value.push({
        id: mid,
        role: "ASSISTANT",
        content: data.question || "",
        metadata_json: {
          type: "FOLLOW_UP",
          ...data,
          question_id: data.question_id,
          follow_up_count: data.follow_up_count,
          max_follow_ups: data.max_follow_ups,
        },
        turn_index: messages.value.length,
        created_at: new Date().toISOString(),
      })
    },
    // onQuestion — add as question bubble
    (data) => {
      const mid = Date.now()
      activeQuestionId.value = data.question_id
      messages.value.push({
        id: mid,
        role: "ASSISTANT",
        content: data.question,
        metadata_json: {
          type: "QUESTION",
          ...data,
          question_id: data.question_id,
          question_index: data.question_index,
          dimension: data.dimension,
          difficulty: data.difficulty,
          source: data.source,
          source_label: data.source,
          evidence: data.evidence as unknown[],
        },
        turn_index: messages.value.length,
        created_at: new Date().toISOString(),
      })
      generatedCount.value = (data.question_index ?? 0) + 1
      questionBudget.value = data.total_questions
    },
    // onDynamicQuestion — add as dynamic question bubble
    (data) => {
      const mid = Date.now()
      messages.value.push({
        id: mid,
        role: "ASSISTANT",
        content: data.question,
        metadata_json: {
          type: "DYNAMIC_QUESTION",
          question_id: data.question_id,
          question_index: data.question_index,
          dimension: data.dimension,
          difficulty: data.difficulty,
          source: data.source,
          parent_question_id: data.parent_question_id,
        },
        turn_index: messages.value.length,
        created_at: new Date().toISOString(),
      })
      generatedCount.value = (data.question_index ?? 0) + 1
    },
    // onQuestionTransition — noop
    () => {},
    // onInterviewComplete — show as chat card, not alert
    (data) => {
      const mid = Date.now()
      const answered = data.answered_count || 0
      const budget = data.question_budget || data.total_questions || questionBudget.value || 0
      messages.value.push({
        id: mid, role: "ASSISTANT",
        content: `面试结束。已回答 ${answered} 题，计划 ${budget} 题。全场均分 ${data.overall_average ?? data.avg_score ?? '—'} / 5。`,
        metadata_json: { type: "INTERVIEW_COMPLETE", answered_count: answered, question_budget: budget },
        turn_index: messages.value.length, created_at: new Date().toISOString(),
      })
      sending.value = false
      streaming.value = false
      activeQuestionId.value = null
    },
    answerContext
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
  generationStage.value = 'idle'
  if (activeSessionId.value) pollAnswer(activeSessionId.value)
  if (retryAnswer.value) inputText.value = retryAnswer.value.content
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
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\n/g, "<br>")
}

// Phase 3.8: Stable-sorted display items for refresh-proof rendering
function buildDisplayItems(msgs: InterviewMessage[]) {
  const sorted = [...msgs].sort((a, b) => {
    const ta = a.turn_index ?? 0
    const tb = b.turn_index ?? 0
    if (ta !== tb) return ta - tb
    const oa = a.role === "USER" ? 0 : (a.metadata_json as any)?.display_order ?? 99
    const ob = b.role === "USER" ? 0 : (b.metadata_json as any)?.display_order ?? 99
    if (oa !== ob) return oa - ob
    const ca = new Date(a.created_at || 0).getTime()
    const cb = new Date(b.created_at || 0).getTime()
    if (ca !== cb) return ca - cb
    return (a.id as number) - (b.id as number)
  })
  const items: any[] = []
  for (const msg of sorted) {
    const t = msg.metadata_json?.type
    if (msg.role === "USER") {
      items.push({ id: msg.id, type: "USER", content: msg.content, raw: msg })
    } else if (t === "QUESTION" || t === "FOLLOW_UP" || t === "DYNAMIC_QUESTION") {
      const meta = msg.metadata_json as any
      items.push({
        id: msg.id, type: t, content: msg.content,
        question_id: meta.question_id, question_index: meta.question_index,
        dimension: meta.dimension, difficulty: meta.difficulty,
        source: meta.source || meta.source_label, evidence: meta.evidence,
        source_fingerprint: meta.source_fingerprint, question_kind: meta.question_kind,
        follow_up_count: meta.follow_up_count, max_follow_ups: meta.max_follow_ups,
        interviewer_guidance: meta.interviewer_guidance,
        is_dynamic: t === "DYNAMIC_QUESTION", parent_question_id: meta.parent_question_id,
        raw: msg,
      })
    } else if (t === "EVALUATION") {
      items.push({
        id: msg.id, type: "EVALUATION", content: msg.content,
        question_id: (msg.metadata_json as any).question_id,
        score: (msg.metadata_json as any).score,
        missing_points: (msg.metadata_json as any).missing_points || [],
        risk_tip: (msg.metadata_json as any).risk_tip,
        covered_points: (msg.metadata_json as any).covered_points || [],
        action: (msg.metadata_json as any).action,
        dimensions: Object.fromEntries(["technical_correctness", "coverage", "depth", "relevance", "communication"].map(key => [key, msg.metadata_json?.[key]])),
        mode: msg.metadata_json?.mode,
        raw: msg,
      })
    } else if (t === "INTERVIEW_COMPLETE") {
      items.push({ id: msg.id, type: "INTERVIEW_COMPLETE", content: msg.content, raw: msg })
    } else {
      items.push({ id: msg.id, type: "NORMAL", content: msg.content, role: msg.role, raw: msg })
    }
  }
  return items
}

// Phase 3.8: Toggle answer for display item (uses item.question_id directly)
async function toggleAnswerForItem(item: any) {
  if (!activeSessionId.value || !canReview(item)) return
  const itemId = item.id
  if (answerVisibleMap.value[itemId]) {
    answerVisibleMap.value[itemId] = false
    return
  }
  try {
    const detail = await getQuestionDetail(activeSessionId.value, item.question_id)
    standardAnswerMap.value[itemId] = detail.standard_answer || "暂无答案"
    referenceAnswerMap.value[itemId] = detail.reference_answer || null
    answerVisibleMap.value[itemId] = true
  } catch {
    standardAnswerMap.value[itemId] = "获取答案失败"
    answerVisibleMap.value[itemId] = true
  }
}

function toggleEvalCollapse(msgId: number) {
  // Phase 3.7: undefined/true → expand (set false); false → collapse (set true)
  if (evalCollapsed.value[msgId] === false) {
    evalCollapsed.value[msgId] = true   // collapse
  } else {
    evalCollapsed.value[msgId] = false  // expand
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
  stopAnswerPoll()
  if (resumePollTimer.value) clearInterval(resumePollTimer.value)
  if (abortController) abortController.abort()
})

// Init
getInterviewerOptions().then(data => { interviewerOptions.value = data })
  .catch(() => { interviewerOptionsError.value = '面试官配置读取失败，请刷新后重试。' })
loadSessions().then(() => { const id = Number(route.query.session_id); if (sessions.value.some(s => s.id === id)) selectSession(id); })
const privateTargets = listTargets().then(data => { jobTargets.value = data }).catch(() => { jobTargets.value = [] })
getInterviewTargets().then(async data => {
  targets.value = data
  const jobId = Number(entryRoute.query.job_id), companyId = Number(entryRoute.query.company_id)
  if (!jobId || !companyId) return
  // M6 hands off references ONLY. The existing M4 create endpoint revalidates
  // current Profile/JD/public data/Memory; no MatchRun snapshot is copied.
  await entryRouter.replace({ path: '/interview' }) // consuming the entry avoids reload creating another session
  const target = data.find(t => t.job_id === jobId && t.company_id === companyId)
  if (!target) { alert('该岗位当前已关闭或不可用，无法创建新的模拟面试；历史推荐保留。'); return }
  selectedJobId.value = jobId
  // Select the current target, then let the user choose interviewer configuration.
}).then(async () => {
  const targetId = entryTargetId
  if (!targetId) return
  entryRouter.replace({ path: '/interview' })
  // The target list loads in parallel; awaiting it (and falling back to an
  // owner-scoped read) keeps the result independent of which request resolves
  // first. Logic and its regression test live in ./interviewEntry.ts.
  const resolution = await resolvePrivateEntryTarget(targetId, {
    list: async () => { await privateTargets; return jobTargets.value },
    detail: id => getTarget(id),
  })
  if (resolution.status !== 'SELECTED') {
    alert('该目标岗位不存在、未确认或不属于当前账号，请重新选择。'); return
  }
  if (resolution.source === 'DETAIL') {
    jobTargets.value = [resolution.target, ...jobTargets.value.filter(t => t.id !== resolution.target.id)]
  }
  selectedTargetId.value = targetId
  // One-click hand-off only preselects. A non-primary direction is recorded as
  // an explicit user choice, so the async recommendation cannot overwrite it.
  if (entrySkillId) skillChoice.value = entrySkillSource === 'AUTO_RECOMMENDED'
    ? { skill_id: entrySkillId, source: 'AUTO_RECOMMENDED' }
    : chooseSkill(entrySkillId)
}).catch(e => { pageError.value = e instanceof Error ? e.message : "岗位读取失败，请重试" }).finally(() => { targetsLoading.value = false })
</script>

<template>
  <div class="interview-page">
    <p v-if="pageError" class="ux-empty" role="alert">{{ pageError }}</p>
    <details v-if="streamErrorCode" class="ux-technical"><summary>错误技术详情</summary>{{ streamErrorCode }}</details>
    <!-- Left: Session List -->
    <aside class="session-sidebar">
      <div class="sidebar-header">
        <h3>面试会话</h3>
        <button class="btn-new" :disabled="creatingSession || !interviewerOptions || (!selectedJobId && !selectedTargetId) || !skillChoice.skill_id" @click="handleCreateSession">{{ creatingSession ? '正在创建…' : '+ 新建' }}</button>
      </div>
      <div style="padding:12px;display:grid;gap:8px">
        <label>公司 / 岗位
          <select v-model="selectedJobId" aria-label="公司和岗位" style="width:100%">
            <option :value="null">请选择已发布岗位</option>
            <option v-for="t in targets" :key="t.job_id" :value="t.job_id">{{ publicName(t.company_name) }} · {{ publicName(t.job_title) }}</option>
          </select>
        </label>
        <label>我的目标岗位
          <select v-model="selectedTargetId" aria-label="我的目标岗位" style="width:100%">
            <option :value="null">不使用（只用已发布岗位）</option>
            <option v-for="t in jobTargets.filter(t => t.parse_status === 'CONFIRMED')" :key="t.id" :value="t.id">
              {{ t.company_name }} · {{ t.job_title }}（第 {{ t.version }} 版）
            </option>
          </select>
          <small v-if="!jobTargets.some(t => t.parse_status === 'CONFIRMED')">
            还没有已确认的目标岗位，可在「我的目标岗位」粘贴 JD 并确认。
          </small>
          <small v-else>私人岗位现在会按你已确认的 JD 要求给出规则推荐，仍可手动更换；来源为用户提供，未经企业核实。</small>
        </label>
        <label>面试轮数（含追问）<input v-model.number="createBudget" type="number" min="1" max="40" style="width:60px"></label>
        <label><input type="checkbox" v-model="designInterview" aria-label="系统设计验证面试"> 系统设计验证面试（限量预约场景）</label>
        <label><input type="checkbox" v-model="adaptiveInterview" aria-label="自适应面试"> 自适应面试 · 根据回答调整考察目标</label>
        <small v-if="adaptiveInterview">结合岗位要求、真实项目与已审核资料安排技术主题；回答后补具体缺口或切换主题，不形成长期能力评分。</small>
        <template v-if="interviewerOptions">
          <label>岗位专业 Skill<select :value="skillChoice.skill_id" @change="selectSkill" aria-label="岗位专业 Skill" style="width:100%">
            <option value="">请选择专业 Skill</option>
            <option v-for="option in interviewerOptions.skills" :key="option.id" :value="option.id">{{ option.label }}</option>
          </select></label>
          <p v-if="skillRecommendation" role="status">{{ skillRecommendation.reason }}</p>
          <small>{{ skillChoice.source === 'USER_SELECTED' ? '用户手动选择' : skillChoice.source === 'AUTO_RECOMMENDED' ? '根据岗位规则自动推荐' : '推荐依据不足，请手动选择' }}</small>
          <small v-if="skillRecommendation">推荐来源：确定性规则匹配（0 次模型调用），不是模型置信度或百分比。</small>
          <p v-if="recommendationStale" role="alert">这份 JD 的推荐输入已更新，此前的推荐方向可能已过期；请回到「我的目标岗位」重新查看后再使用。</p>
          <p v-if="manualSkillMismatch">已保留你的手动选择；与当前岗位推荐方向可能不同，请确认。</p>
          <details v-if="skillRecommendation"><summary>查看推荐依据与其他方向</summary>
            <div v-for="item in skillRecommendation.ranking" :key="item.skill_id"><p>{{ item.label }}：{{ item.excluded ? '不适用' : item.reason }}</p>
              <small v-for="e in item.evidence.slice(0, 3)" :key="`${e.field}-${e.term}`">对应 JD 依据：{{ e.excerpt }}<br></small>
            </div>
          </details>
          <label>面试轮次<select v-model="roundAndStyle.interview_round" aria-label="面试轮次" style="width:100%">
            <option v-for="option in interviewerOptions.rounds" :key="option.id" :value="option.id">{{ option.label }}</option>
          </select></label>
          <label>面试官风格<select v-model="roundAndStyle.interviewer_style" aria-label="面试官风格" style="width:100%">
            <option v-for="option in interviewerOptions.styles" :key="option.id" :value="option.id">{{ option.label }}</option>
          </select></label>
          <small>{{ interviewerOptions.styles.find(option => option.id === interviewerSelection.interviewer_style)?.description }} 严格不等于更难或额外扣分。</small>
          <small>设置后点击“新建”。从岗位推荐进入时也可先调整本场配置。</small>
        </template>
        <p v-else :role="interviewerOptionsError ? 'alert' : 'status'">{{ interviewerOptionsError || '正在读取面试官配置…' }}</p>
        <router-link to="/profile">先查看 / 完成我的画像</router-link>
        <p v-if="targetsLoading">正在读取岗位…</p>
        <p v-else-if="!targets.length && !pageError">当前岗位库暂无可用岗位。</p>
      </div>
      <div class="session-list">
        <div
          v-for="s in sessions"
          :key="s.id"
          class="session-card"
          :class="{ active: s.id === activeSessionId }"
          @click="selectSession(s.id)"
        >
          <div class="session-title">{{ s.title ? publicName(s.title) : '模拟面试' }}</div>
          <div class="session-meta">
            <span v-if="s.resume_filename" class="resume-name">{{ s.resume_filename }}</span>
            <span v-else class="no-resume">{{ s.legacy ? '历史只读' : displayLabel(s.status) }}</span>
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
        <div v-if="sessionsLoading" class="empty-sessions">正在读取面试记录…</div>
        <div v-else-if="sessions.length === 0 && !pageError" class="empty-sessions">
          还没有模拟面试记录。选择岗位后开始第一场面试。
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
        <div class="resume-bar" v-if="activeSession()" style="flex-wrap:wrap">
          <template v-if="activeSession()?.workflow_mode === 'm4'">
            <strong>{{ publicName(profileContext?.company_name) }} · {{ publicName(profileContext?.job_title) }}</strong>
            <p v-if="trainingLabel">本场训练：{{trainingLabel}} · 目标与起始状态已固定</p>
            <span v-if="activeInterviewer">{{ activeInterviewer.skill.label }} / {{ activeInterviewer.round.label }} / {{ activeInterviewer.style.label }}</span>
            <details v-if="activeInterviewer" class="session-context-detail"><summary>本场面试官配置（已固定）</summary>
              <p v-if="profileContext?.interviewer_selection">选择来源：{{ profileContext.interviewer_selection.source === 'AUTO_RECOMMENDED' ? '根据岗位自动推荐' : '用户手动选择' }}</p>
              <p>{{ activeInterviewer.round.focus }}</p><p>考察领域：{{ activeInterviewer.skill.domains.join('、') }}</p>
              <p>{{ activeInterviewer.company_background.instruction }}</p>
              <small>Skill {{ activeInterviewer.skill.version }} / 策略 {{ activeInterviewer.style.version }}；后续配置更新不改变本场。</small>
            </details>
            <span v-else>原有通用面试策略</span>
            <details v-if="profileContext?.adaptive_policy" class="session-context-detail"><summary>自适应考察范围（本场固定）</summary>
              <p>{{ profileContext.adaptive_blueprint?.targets.map(t => t.label).join('、') }}</p>
              <p>依据本轮回答调整追问；表现观察不等于稳定技能事实，跨场次推断可在成长记录中查看和更正。</p>
            </details>
            <span class="round-progress">第 {{ Math.min(questionList.find(q => q.id === activeQuestionId)?.question_index != null ? questionList.find(q => q.id === activeQuestionId)!.question_index + 1 : questionList.length || 1, questionBudget) }} / {{ questionBudget }} 轮</span>
            <span v-if="activeQuestionId">当前题型：{{ displayLabel(questionList.find(q => q.id === activeQuestionId)?.dimension, '技术 / 项目') }} · 难度：{{ displayLabel(questionList.find(q => q.id === activeQuestionId)?.difficulty) }}</span>
            <span v-if="questionList.find(q => q.id === activeQuestionId)?.assessment_topic">考察主题：{{ questionList.find(q => q.id === activeQuestionId)?.assessment_topic }}</span>
            <details class="session-context-detail"><summary>本场资料详情</summary><p>画像第 {{ profileContext?.profile_version_number }} 版 / 岗位描述第 {{ profileContext?.jd_version_number }} 版（本场固定）</p><p v-if="profileContext?.coverage_insufficient">题库覆盖有限，本场会结合真实项目和岗位要求出题。</p></details>
            <span v-if="isReplay || completion?.mode === 'replay'">DEMO · Replay 回放模式（不是 Live 模型验收）</span>
            <button v-if="activeSession()?.status === 'READY'" :disabled="generationStage !== 'idle'" @click="handleStart">开始 / 重试首题</button>
          </template>
          <template v-else><span>历史会话只读，不猜测关联画像或岗位</span></template>
          <template v-if="false">
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
          </template>
        </div>

        <!-- Messages -->
        <div v-if="retryAnswer && !sending && !processingAnswer && streamErrorCode !== 'INTERVIEW_BUDGET_EXHAUSTED'" class="resume-bar">{{ answerPersisted ? '已保存本题回答；可以安全重试，不会重复评分或推进。' : '本题处理尚未确认完成，请保留原回答安全重试；保存状态以刷新后的结果为准。' }}</div>
        <div v-if="streamErrorCode === 'AUTH_TOKEN_EXPIRED'" class="resume-bar">
          本次回答尚未提交，草稿保留在输入框。
          <a href="/login" target="_blank" rel="noopener">重新登录（新窗口）</a>
          登录后可回到本页提交，不必丢弃草稿。
        </div>
        <details v-if="profileContext" class="resume-bar"><summary>本场使用的历史记忆（开始时固定）</summary>
          <p>已批准的准备目标 / 偏好，不作为学历或技能证明。本场开始后不动态刷新。</p>
          <ul v-if="profileContext.approved_memory_context?.items.length"><li v-for="memory in profileContext.approved_memory_context.items" :key="memory.id">{{ memory.content }}</li></ul>
          <p v-else>本场未使用已批准的历史记忆。</p>
          <div v-if="memoryGuidance.length"><h4>本场能力复测参考（系统推断）</h4><p v-for="item in memoryGuidance" :key="item.label">{{ item.label }}：{{ item.history_summary }}</p><p>只作为选题参考，不作为评分事实；开始后不会变化。</p></div>
          <details v-if="profileContext.recent_performance_observations?.items.length"><summary>近期练习观察（不是长期事实）</summary><ul><li v-for="observation in profileContext.recent_performance_observations.items" :key="observation.id">{{ observation.content }}</li></ul></details>
          <router-link to="/memory">查看和管理成长记录</router-link>
        </details>
        <p v-if="generationStage !== 'idle'" role="status" aria-live="polite" class="resume-bar processing-status"><span class="processing-dot" aria-hidden="true"></span>{{ displayLabel(generationStage, '正在处理本轮回答…') }} · 当前阶段已等待 {{ waitingSeconds }} 秒。{{ waitingSeconds >= 20 ? '仍在处理；完成后会自动更新，也可刷新查看已保存状态。' : '请稍候；刷新后可恢复处理状态。' }}</p>
        <div ref="chatContainer" class="chat-messages" v-if="!loading">
          <!-- Phase 3.8: Loading card for question generation stages -->
          <div v-if="generationStage === 'generating_first_question' && !streaming" class="message-row assistant">
            <div class="message-bubble" style="background:#f0f7ff;text-align:center;padding:20px;">
              ⏳ 正在根据你的岗位和简历生成第一题...
            </div>
          </div>
          <div v-if="generationStage === 'generating_next_question' && !streaming" class="message-row assistant">
            <div class="message-bubble" style="background:#f0f7ff;text-align:center;padding:20px;">
              ⏳ 正在根据你的回答生成下一题...
            </div>
          </div>

          <div v-if="messages.length === 0 && !streaming && generationStage === 'idle'" class="chat-empty">
            <p v-if="activeSession()?.workflow_mode === 'm4'">画像与岗位已固定，请开始面试</p>
            <p v-else-if="isActiveResumeReady()">简历已就绪，面试即将开始...</p>
            <p v-else-if="isActiveResumeFailed()">简历处理失败，请重新上传简历</p>
            <p v-else-if="hasActiveResume()">简历处理中，请等待处理完成</p>
            <p v-else>请先上传简历</p>
          </div>

          <!-- Phase 3.8: Render from stable-sorted displayItems -->
          <template v-for="item in displayItems" :key="item.id">
            <!-- QUESTION / FOLLOW_UP / DYNAMIC_QUESTION bubbles -->
            <!-- QUESTION / FOLLOW_UP / DYNAMIC_QUESTION bubbles -->
            <div v-if="item.type === 'QUESTION' || item.type === 'FOLLOW_UP' || item.type === 'DYNAMIC_QUESTION'" class="message-row assistant">
              <div class="message-bubble question-bubble">
                <div class="speaker-label">面试官</div>
                <div class="q-header">
                  <span class="q-badge" v-if="item.type === 'QUESTION'">
                    第 {{ (item.question_index ?? 0) + 1 }} / {{ item.question_budget || questionBudget }} 轮
                  </span>
                  <span class="q-badge followup" v-else-if="item.type === 'FOLLOW_UP'">
                    追问 {{ followUpOrdinal(questionList, item.question_id) ?? item.follow_up_count ?? '' }}{{ item.max_follow_ups ? ' / ' + item.max_follow_ups : '' }}
                  </span>
                  <span class="q-badge dynamic" v-else>动态问题</span>
                  <span class="q-dimension" v-if="item.dimension">{{ displayLabel(item.dimension, "技术 / 项目") }}</span>
                  <span class="q-difficulty" v-if="item.difficulty">{{ displayLabel(item.difficulty) }}</span>
                </div>
                <div class="q-body">{{ item.content }}</div>
                <p v-if="item.interviewer_guidance" class="q-dimension">表达提示：{{ item.interviewer_guidance }}</p>
                <div v-if="item.question_kind === 'DEEP_DIVE'" class="q-dimension">技术深挖</div>
                <details v-if="canReview(item) && item.evidence?.evidence?.length">
                  <summary>查看候选人依据</summary>
                  <div v-for="e in item.evidence.evidence" :key="e.key">
                    {{ displayLabel(e.kind) }} · {{ displayLabel(e.section) }}
                    <blockquote>{{ e.excerpt }}</blockquote>
                  </div>
                </details>
                <details v-if="canReview(item) && item.source_fingerprint" class="ux-technical"><summary>题目技术详情</summary>公共原题指纹：{{ item.source_fingerprint }}</details>
                <button v-if="canReview(item)" class="q-answer-btn" @click="toggleAnswerForItem(item)">
                  {{ answerVisibleMap[item.id] ? '收起答案' : '展开参考答案' }}
                </button>
                <ReferenceAnswerView v-if="canReview(item) && answerVisibleMap[item.id]" :reference="referenceAnswerMap[item.id]" :fallback="standardAnswerMap[item.id]" />
              </div>
            </div>

            <!-- EVALUATION: collapsible card -->
            <div v-else-if="item.type === 'EVALUATION'" class="message-row assistant">
              <div class="message-bubble eval-bubble" @click="toggleEvalCollapse(item.id)">
                <div class="eval-summary">
                  面试官点评 · 得分 {{ item.score ?? '?' }}/5
                  <span class="eval-toggle">{{ evalCollapsed[item.id] !== false ? '▸ 展开' : '▾ 收起' }}</span>
                </div>
                <div v-if="evalCollapsed[item.id] === false" class="eval-detail">
                  <div class="eval-text" v-html="formatContent(item.content)"></div>
                  <div v-if="item.mode === 'replay'">DEMO · Replay 回放模式 · fixture</div>
                  <div v-for="(label, key) in dimensionLabels" :key="key">{{ label }}：{{ item.dimensions?.[key] ?? '—' }} / 5</div>
                  <div v-if="item.covered_points?.length"><strong>已覆盖要点：</strong>{{ item.covered_points.join('；') }}</div>
                  <p v-if="item.action">下一步：{{ displayLabel(item.action) }}</p>
                  <div v-if="item.missing_points?.length" class="eval-missing">
                    <strong>缺失点:</strong>
                    <ul><li v-for="mp in item.missing_points" :key="mp">{{ mp }}</li></ul>
                  </div>
                  <div v-if="item.risk_tip" class="eval-risk">
                    <strong>风险提示:</strong> {{ item.risk_tip }}
                  </div>
                </div>
              </div>
            </div>

            <!-- INTERVIEW_COMPLETE card -->
            <div v-else-if="item.type === 'INTERVIEW_COMPLETE'" class="message-row assistant">
              <div class="message-bubble complete-bubble">
                <div class="complete-text">🎉 {{ item.content }}</div>
                <template v-if="completion">
                  <p>本场复盘：已评价 {{ completion.evaluated_count }} 轮，综合平均表现 {{ completion.overall_average ?? '—' }} / 5</p>
                  <div v-for="(label, key) in dimensionLabels" :key="key">{{ label }}：{{ completion.dimension_averages[key] ?? '—' }}</div>
                </template>
                <router-link
                  v-if="activeSession()?.workflow_mode === 'm4' && activeSessionId"
                  :to="{ path: '/interview/reports', query: { session_id: activeSessionId } }"
                >生成 / 查看面试报告与记忆提案</router-link>
                <button
                  v-if="activeSession()?.legacy && activeSessionId && !memoryConsolidatedMap[activeSessionId]"
                  class="memory-save-btn"
                  :disabled="consolidatingMemory"
                  @click="handleConsolidateMemory(activeSessionId)"
                >
                  {{ consolidatingMemory ? '正在沉淀...' : '沉淀为长期记忆' }}
                </button>
                <div v-else-if="activeSession()?.legacy" class="memory-saved-note">已更新你的面试记忆和技能画像</div>
              </div>
            </div>

            <!-- USER messages -->
            <div v-else-if="item.type === 'USER'" class="message-row user">
              <div class="message-bubble">
                <div class="speaker-label">我的回答</div>
                <div class="message-content">{{ item.content }}</div>
              </div>
            </div>

            <!-- Normal/legacy messages -->
            <div v-else class="message-row" :class="item.role?.toLowerCase() || 'assistant'">
              <div class="message-bubble">
                <div class="message-content" v-html="formatContent(item.content)"></div>
              </div>
            </div>
          </template>

          <!-- Streaming bubble -->
          <div v-if="streaming" class="message-row assistant">
            <div class="message-bubble">
              <div class="message-content" v-html="formatContent(streamingContent) || displayLabel(generationStage, '正在处理本轮回答…')"></div>
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
        <DesignTraceView v-if="activeDesign" :trace="designTrace" />
        <div class="chat-input">
          <fieldset v-if="activeDesign" :disabled="sending || processingAnswer || !!retryAnswer || activeSession()?.status === 'COMPLETED'">
            <legend>本轮方案声明（与回答一起提交）</legend>
            <label>请求去重<select v-model="designPlan.deduplication" aria-label="请求去重"><option value="NONE">无去重</option><option value="CHECK_THEN_WRITE">先查后写（非原子）</option><option value="ATOMIC_KEY">原子业务键去重</option></select></label>
            <label>名额保护<select v-model="designPlan.capacity_guard" aria-label="名额保护"><option value="NONE">无保护</option><option value="CHECK_THEN_WRITE">先查名额再写入（非原子）</option><option value="ATOMIC_RESERVATION">原子条件扣减</option></select></label>
            <label>容量<input v-model.number="designPlan.capacity" type="number" min="1" max="20" aria-label="预约容量"></label>
          </fieldset>
          <textarea
            v-model="inputText"
            :disabled="sending || processingAnswer || !isActiveResumeReady()"
            :placeholder="
              activeSession()?.legacy ? '历史面试只读，请新建模拟面试'
                : activeSession()?.status === 'COMPLETED' ? '面试已结束，请新建会话继续练习'
                : isActiveResumeReady() ? '输入你的回答…（回车发送，Shift+回车换行）'
                : '画像与岗位已固定，请开始面试'
            "
            rows="2"
            @keydown="handleKeydown"
          ></textarea>
          <button
            v-if="!streaming"
            class="btn-send"
            :disabled="!inputText.trim() || sending || processingAnswer || streamErrorCode === 'INTERVIEW_BUDGET_EXHAUSTED' || !isActiveResumeReady()"
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
.speaker-label {font-size:12px;color:#667085;margin-bottom:8px;}
.message-row.user .speaker-label {color:#fff;}
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
  max-height: 220px;
  overflow-y: auto;
}
.question-bubble { contain: layout; }
.followup { background: #e6a23c !important; }
.dynamic { background: #f56c6c !important; }

/* Evaluation collapsible card */
.eval-bubble {
  background: #fdf6ec !important;
  border: 1px solid #faecd8 !important;
  cursor: pointer;
  max-width: 90% !important;
}
.eval-summary {
  font-size: 13px;
  color: #916000;
  font-weight: 600;
}
.eval-toggle { float: right; color: #909399; font-weight: 400; font-size: 12px; }
.eval-detail { margin-top: 10px; }
.eval-text { font-size: 14px; color: #303133; line-height: 1.6; }
.eval-missing { margin-top: 8px; font-size: 13px; color: #916000; }
.eval-missing ul { margin: 4px 0 0 16px; }
.eval-risk { margin-top: 8px; font-size: 13px; color: #f56c6c; }
.complete-bubble { background: #f0f9eb !important; border: 1px solid #c2e7b0 !important; text-align: center; }
.complete-text { font-size: 15px; color: #67c23a; font-weight: 600; }
.memory-save-btn {
  margin-top: 12px;
  padding: 6px 14px;
  border: 1px solid #67c23a;
  border-radius: 6px;
  background: #fff;
  color: #3d8f28;
  cursor: pointer;
}
.memory-save-btn:disabled {
  opacity: 0.65;
  cursor: wait;
}
.memory-saved-note {
  margin-top: 10px;
  font-size: 13px;
  color: #529b2e;
}
@media (max-width: 700px) {
  .interview-page { flex-direction: column; height: auto; }
  .session-sidebar { width: 100%; min-width: 0; max-height: 320px; flex-shrink: 0; }
  .chat-main { min-height: 620px; height: 72vh; flex: none; }
  .message-bubble { max-width: 95%; overflow-wrap: anywhere; }
}
</style>
