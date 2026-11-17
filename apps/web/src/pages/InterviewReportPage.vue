<script setup lang="ts">
import { knowledgeStateLabel } from '../types/adaptive-interview'
import { computed, ref, watch } from "vue"
import { displayLabel, publicName, userError } from "../i18n/zh-CN"
import { useRoute, useRouter } from "vue-router"
import ReferenceAnswerView from '../components/ReferenceAnswer.vue'
import TrainingFeedbackView from '../components/TrainingFeedback.vue'
import DesignTraceView from '../components/DesignTrace.vue'
import {
  approveMemoryProposal, correctMemoryProposal, generateInterviewReport, getInterviewReport,
  getSessionReport, listInterviewReports, listMemoryProposals, rejectMemoryProposal,
} from "../api/interview-report"
import { trainingCandidates } from "../api/training"
import { candidateEmptyLabels, candidateModeLabels, type TrainingCandidateSet } from "../types/training-candidates"
import {
  REPORT_DIMENSION_LABELS, confidenceLabel, proposalCanDecide, reportGenerationStatus, reportModeLabel,
  type CoachFinding, type FinalInterviewReport, type InterviewReportDetail, type InterviewReportListItem,
  type MemoryProposal, type ReportEvaluation,
} from "../types/interview-report"

const route = useRoute(), router = useRouter()
const reports = ref<InterviewReportListItem[]>([])
const detail = ref<InterviewReportDetail | null>(null)
const proposals = ref<MemoryProposal[]>([])
const loading = ref(false), generating = ref(false), deciding = ref<number | null>(null)
const error = ref(""), notice = ref("")
const correction = ref<{ id: number; content: string; reason: string } | null>(null)
const decision = ref<{ proposal: MemoryProposal; action: "approve" | "reject" } | null>(null)
// Round 16: a separate read-time rule projection, never written into the report.
const ruleCandidates = ref<TrainingCandidateSet | null>(null)
const ruleCandidateError = ref("")
let ruleCandidateSequence = 0
// A lost response must retry the same explicit regeneration request, not mint another generation.
const regenerateRequest = ref<{ sessionId: number; key: string } | null>(null)
let loadSequence = 0
const sessionId = computed(() => positiveId(route.query.session_id))
const reportId = computed(() => positiveId(route.params.id))
const selectedReport = computed(() => detail.value?.report || detail.value?.current_report || null)
// A private JD never becomes a company record. The frozen snapshot is the only
// place that says so, and the page must not present the pasted company name as
// an employer the candidate actually interviewed with.
const privateTarget = computed(() => {
  const session = detail.value?.session as
    { job_target_id?: number | null; job_target_version?: number | null } | undefined
  return session?.job_target_id
    ? { id: session.job_target_id, version: session.job_target_version ?? null }
    : null
})
const analysis = computed(() => selectedReport.value?.coach_analysis)
const TRAINING_MODE_LABEL: Record<string, string> = {
  FULL: '完整模拟面试', WEAKNESS: '薄弱项专项训练', RECHECK: '冲突 / 遗忘独立复测',
}
function trainingModeLabel(mode: string) { return TRAINING_MODE_LABEL[mode] || mode }
// The link only preselects; TrainingPage still asks the server whether the
// target and mode are legal right now.
const trainingLink = computed(() => {
  const suggested = analysis.value?.suggested_training
  if (!suggested) return { path: '/training' }
  const query: Record<string, string> = { mode: suggested.mode, knowledge_id: suggested.knowledge_id }
  const session = detail.value?.session
  if (session?.job_target_id) query.job_target_id = String(session.job_target_id)
  else if (session?.job_id) query.job_id = String(session.job_id)
  return { path: '/training', query }
})


const uniqueFindings = (items: CoachFinding[]) => items.filter((item, index, all) => all.findIndex(other => other.statement.trim() === item.statement.trim()) === index)
const findingSections = computed(() => [
  { label: "本场表现优势", items: analysis.value?.strengths || [] },
  { label: "本场待改进表现", items: analysis.value?.weaknesses || [] },
  { label: "重复出现的问题", items: analysis.value?.recurring_issues || [] },
  { label: "岗位对齐分析", items: analysis.value?.job_alignment || [] },
].map(section => ({ ...section, items: uniqueFindings(section.items) })))
const countFields = [
  { key: "answered_count", label: "有效回答" }, { key: "evaluated_count", label: "有效评价" },
  { key: "skipped_count", label: "跳过" }, { key: "follow_up_count", label: "补问" },
  { key: "deep_dive_count", label: "深挖" }, { key: "dynamic_question_count", label: "动态题" },
] as const

function positiveId(value: unknown): number | null {
  const n = typeof value === "string" ? Number(value) : NaN
  return Number.isSafeInteger(n) && n > 0 ? n : null
}
function message(err: unknown): string { return err instanceof Error ? err.message : "操作失败，请刷新后重试" }
function text(value: unknown): string {
  if (typeof value === "string") return value
  if (value == null) return "暂无分析"
  // Render structured coach fields as text only; never trust generated HTML.
  return JSON.stringify(value, null, 2)
}
function support(finding: CoachFinding): ReportEvaluation[] {
  return finding.supporting_evaluations || (detail.value?.evaluations || []).filter(e => finding.supporting_evaluation_ids.includes(e.id))
}
async function load() {
  const sequence = ++loadSequence
  const selectedSession = sessionId.value, selectedId = reportId.value
  loading.value = true
  error.value = ""
  detail.value = null
  proposals.value = []
  correction.value = null
  decision.value = null
  ruleCandidates.value = null
  ruleCandidateError.value = ""
  try {
    const listing = await listInterviewReports()
    const result = selectedId ? await getInterviewReport(selectedId) : selectedSession ? await getSessionReport(selectedSession) : null
    const current = result?.report || result?.current_report
    const items = current ? (await listMemoryProposals(current.id)).items : []
    if (sequence !== loadSequence) return
    reports.value = listing.items || []
    detail.value = result
    proposals.value = items || []
    correction.value = null
    const candidateSessionId = selectedSession || Number((result?.session as { id?: number } | undefined)?.id || 0)
    void loadRuleCandidates(candidateSessionId, sequence)
  } catch (err) {
    if (sequence === loadSequence) { error.value = message(err); detail.value = null; proposals.value = [] }
  } finally { if (sequence === loadSequence) loading.value = false }
}
async function loadRuleCandidates(id: number, sequence: number) {
  const current = ++ruleCandidateSequence
  if (!id) { ruleCandidates.value = null; return }
  try {
    const data = await trainingCandidates({}, id)
    if (current === ruleCandidateSequence && sequence === loadSequence) ruleCandidates.value = data
  } catch (err) {
    if (current === ruleCandidateSequence && sequence === loadSequence) {
      ruleCandidates.value = null
      ruleCandidateError.value = message(err)
    }
  }
}
async function generate(regenerate: boolean) {
  const id = detail.value?.session.id || sessionId.value
  if (!id || generating.value) return
  if (regenerate && !confirm(regenerateRequest.value?.sessionId === id
    ? "重试本次重新生成？将复用同一请求键，保留失败尝试审计。"
    : "明确生成新一版报告？已有报告和审计将保留。")) return
  if (regenerate && regenerateRequest.value?.sessionId !== id) regenerateRequest.value = { sessionId: id, key: crypto.randomUUID() }
  generating.value = true
  error.value = ""
  notice.value = "报告生成独立进行，不会修改已完成面试的状态或评分。"
  try {
    const report = await generateInterviewReport(id, regenerate, regenerate ? regenerateRequest.value?.key : undefined)
    if (reportGenerationStatus(report) === "COMPLETED") regenerateRequest.value = null
    notice.value = reportGenerationStatus(report) === "FAILED"
      ? `报告生成失败：${userError(report.error || "REPORT_FAILED")} 面试已完成状态保持不变。`
      : "报告已生成；记忆提案仍需你逐条确认，尚未自动写入长期记忆。"
    // View this session's current successful report, not a failed replacement.
    if (reportId.value) await router.push({ path: "/interview/reports", query: { session_id: id } })
    else await load()
  } catch (err) {
    error.value = message(err)
    notice.value = "未确认生成结果。刷新查看记录；再次重试将复用同一请求键。"
  } finally { generating.value = false }
}
async function decide(proposal: MemoryProposal, action: "approve" | "reject") {
  if (!proposalCanDecide(proposal) || deciding.value !== null) return
  // Keep an explicit, accessible confirmation in the page. Browser-native
  // dialogs can disappear behind another window and interrupt refresh recovery.
  decision.value = { proposal, action }
  correction.value = null
}
async function confirmDecision() {
  const selected = decision.value
  if (!selected || deciding.value !== null) return
  const { proposal, action } = selected
  if (!proposalCanDecide(proposals.value.find(item => item.id === proposal.id) || proposal)) return
  deciding.value = proposal.id
  error.value = ""
  try {
    const result = action === "approve" ? await approveMemoryProposal(proposal.id) : await rejectMemoryProposal(proposal.id)
    proposals.value = proposals.value.map(item => item.id === result.id ? result : item)
    decision.value = null
    notice.value = action === "approve" ? "已由本人批准并写入成长记录。" : "已拒绝，没有写入长期记忆。"
  } catch (err) { error.value = message(err) }
  finally { deciding.value = null }
}
async function saveCorrection() {
  const edit = correction.value
  if (!edit || !edit.content.trim() || !edit.reason.trim() || deciding.value !== null) return
  deciding.value = edit.id
  error.value = ""
  try {
    const result = await correctMemoryProposal(edit.id, edit.content.trim(), edit.reason.trim())
    proposals.value = proposals.value.map(item => item.id === result.id ? result : item)
    correction.value = null
    notice.value = "修正文已由本人确认并写入 长期成长记忆；原始提案与修正来源保留。"
  } catch (err) { error.value = message(err) }
  finally { deciding.value = null }
}
function selectSession(id: number) { router.push({ path: "/interview/reports", query: { session_id: id } }) }
function selectGeneration(report: FinalInterviewReport) { router.push(`/interview/reports/${report.id}`) }
watch(() => route.fullPath, () => { notice.value = ""; load() }, { immediate: true })
</script>

<template>
  <section class="report-page">
    <header class="heading"><div><h1>面试报告</h1><p>本场表现分析与行动建议，不是长期能力认证。记忆提案需本人批准。</p></div>
      <button :disabled="loading || generating" @click="load">{{ loading ? '读取中…' : '刷新' }}</button></header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="notice" class="notice" role="status">{{ notice }}</p>
    <div class="report-layout">
      <aside class="card report-list"><h2>历史报告</h2>
        <p v-if="!reports.length && !loading">完成一场模拟面试后，这里会生成复盘报告。</p>
        <button v-for="report in reports" :key="report.id" class="report-item" :class="{ selected: report.id === selectedReport?.id }" :disabled="generating" @click="selectGeneration(report)">
          <strong>{{ publicName(report.company_name) }} · {{ publicName(report.job_title) }}</strong>
          <span>第 {{ report.generation_number ?? report.id }} 版 · {{ displayLabel(reportGenerationStatus(report)) }} {{ report.is_current ? '· 当前' : '' }}</span>
          <span>{{ report.created_at }}</span>
          <span v-if="report.provider === 'replay'">DEMO · Replay 回放模式 · fixture</span>
        </button>
        <router-link to="/interview">返回模拟面试</router-link>
      </aside>
      <main class="report-detail">
        <p v-if="loading" class="card">正在读取持久化报告…</p>
        <template v-if="detail">
          <TrainingFeedbackView :session-id="detail.session.id" />
          <article class="card"><h2>{{ publicName(detail.session.company_name) }} · {{ publicName(detail.session.job_title) }}</h2>
            <p v-if="privateTarget" class="provenance" role="note">本场岗位来自你自己提供的 JD（第 {{ privateTarget.version }} 版）：用户提供、未经企业核实。公司名与岗位名按你粘贴的原文记录，不代表真实雇主或已发布的岗位。</p>
            <p>{{ displayLabel(detail.session.status) }} · {{ detail.session.title ? publicName(detail.session.title) : '模拟面试' }}</p>
            <p v-if="detail.session.completion?.completion_reason === 'USER_ENDED'" class="ended-note" role="note">
              本场由你主动提前结束（未答满 {{ detail.session.completion?.question_budget ?? '计划' }} 题）：
              已回答的 {{ detail.session.completion?.answered_count ?? '—' }} 题正常计分；
              结束请求与未回答的题目均未计入任何能力统计。
            </p>
            <p v-if="detail.statistics?.excluded_count" class="ended-note" role="note">
              {{ detail.statistics.excluded_count }} 轮被排除（{{ (detail.statistics.excluded_reasons || []).join('、') || '排除' }}），不计入平均分；原始记录仍保留。
            </p>
            <p v-if="reportModeLabel(selectedReport, detail.statistics)" class="mode">{{ reportModeLabel(selectedReport, detail.statistics) }}</p>
            <p v-if="reportId && selectedReport?.id !== detail.current_report?.id">历史版本 只读；生成新报告会保留历史与审计。</p>
            <div class="actions" v-if="detail.session.status === 'COMPLETED'">
              <button :disabled="loading || generating" @click="generate(false)">{{ generating ? '生成中…' : selectedReport ? '安全重试 / 查看当前报告' : '生成面试报告' }}</button>
              <button v-if="detail.current_report" :disabled="loading || generating" @click="generate(true)">{{ regenerateRequest?.sessionId === detail.session.id ? '重试本次重新生成' : '明确重新生成' }}</button>
              <button v-if="reportId" :disabled="generating" @click="selectSession(detail.session.id)">查看本场当前报告</button>
              <router-link :to="{ path: '/training', query: { session_id: String(detail.session.id) } }">针对本场岗位继续训练</router-link>
            </div>
          </article>
          <article class="card statistics"><h2>总体表现 · 本场数据</h2>
            <p>本场数据来自每轮已保存的有效评价，Coach 不重新评分。</p>
            <p class="overall">综合平均表现 {{ detail.statistics.overall_average ?? '—' }} / 5</p>
            <div class="stat-grid"><div v-for="field in countFields" :key="field.key"><span>{{ field.label }}</span><strong>{{ detail.statistics[field.key] }}</strong></div></div>
            <div class="dimension-grid"><div v-for="(label, key) in REPORT_DIMENSION_LABELS" :key="key"><span>{{ label }}</span><strong>{{ detail.statistics.dimension_averages[key] ?? '—' }} / 5</strong></div></div>
          </article>
          <article v-if="detail.adaptive_review" class="card"><h2>本场考察与练习重点</h2>
            <p>{{ detail.adaptive_review.semantics }}</p>
            <section v-for="target in detail.adaptive_review.targets" :key="target.label" class="findings">
              <h3>{{ target.label }} · {{ knowledgeStateLabel(target.state) }}</h3>
              <p v-if="target.missing_mechanisms.length">继续练习：{{ target.missing_mechanisms.join('；') }}</p>
              <p v-else>本场未保留待澄清缺口；这不是永久能力认证。</p>
              <details><summary>查看回答依据与补充过程</summary>
                <div v-for="(observation, index) in target.observations" :key="index">
                  <p>{{ knowledgeStateLabel(observation.state) }}<span v-if="observation.resolved_gap_count"> · 已补充先前缺口</span></p>
                  <blockquote v-for="(span, i) in observation.answer_spans" :key="i">{{ span.excerpt }}</blockquote>
                </div>
              </details>
            </section>
          </article>
          <details v-if="selectedReport" class="card ux-technical"><summary>报告技术详情</summary>
            <p>报告 ID {{ selectedReport.id }} · 版本 {{ selectedReport.generation_number ?? '—' }} · {{ displayLabel(reportGenerationStatus(selectedReport)) }}</p>
            <p>{{ selectedReport.producer }} · {{ selectedReport.provider || '—' }} / {{ selectedReport.model || '—' }} · 提示版本 {{ selectedReport.prompt_version }} · {{ selectedReport.created_at }}</p>
            <p v-if="selectedReport.error" class="error">{{ userError(selectedReport.error) }}。这不会改变已完成面试。</p>
          </details>
          <article v-if="analysis" class="card coach"><h2>Coach 分析</h2>
            <p class="preserve-lines">{{ analysis.overall_summary }}</p>
            <section v-for="section in findingSections" :key="section.label" class="findings"><h3>{{ section.label }}</h3>
              <p v-if="!section.items.length">暂无有评价支持的分析。</p>
              <article v-for="(finding, index) in section.items" :key="index" class="finding"><p>{{ finding.statement }}</p>
                <span v-if="finding.occurrence_count != null">相关评价 {{ finding.occurrence_count }} 次</span>
                <details><summary>查看评价依据</summary>
                  <div v-for="evaluation in support(finding)" :key="evaluation.id" class="support">
                    <strong>对应问题与本轮评价</strong>
                    <p>{{ evaluation.question }}</p><p>{{ evaluation.evaluation }}</p>
                    <div class="score-line" v-for="(label, key) in REPORT_DIMENSION_LABELS" :key="key">{{ label }}：{{ evaluation.scores[key] ?? '—' }} / 5</div>
                  </div>
                </details>
              </article>
            </section>
            <h3>下一场建议 · 按优先级准备</h3><ol><li v-for="(item, index) in [...new Set(analysis.recommended_actions)]" :key="index">{{ text(item) }}</li></ol>
            <h3>练习计划</h3><ol><li v-for="(item, index) in [...new Set(analysis.practice_plan)]" :key="index">{{ text(item) }}</li></ol>
            <!-- Advisory only: the training centre re-validates the target before use. -->
            <template v-if="analysis.suggested_training">
              <h3>下一场可以练什么 <small>（模型 Coach · 报告生成时的历史结论）</small></h3>
              <p>{{ trainingModeLabel(analysis.suggested_training.mode) }} · {{ analysis.suggested_training.knowledge_id.split(':').pop() }}：{{ text(analysis.suggested_training.reason) }}</p>
              <p><small>来源：模型在生成本报告时给出的建议，是按当时的输入得出的历史结论，不是已排好的训练，也不是此刻重新计算的可执行状态。下方「当前可训练候选」由规则在读取时生成，来源不同、二者不等价。</small></p>
              <router-link :to="trainingLink">去训练中心查看</router-link>
            </template>
          </article>
          <article v-else-if="generating" class="card" role="status">正在生成 Coach 分析。本场评分与完成状态已经保存；请勿重复点击「生成面试报告」，生成完成后点「刷新」查看结果。</article>
          <article v-else class="card">暂无成功的 Coach 分析。可以生成 / 安全重试；本场评分与完成状态已经保存。</article>
          <article class="card candidate-card">
            <h2>当前可训练候选 <small>（规则生成 · 读取时计算 · 0 次模型调用）</small></h2>
            <p><small>按当前正式能力状态与 training.modes() 重新计算，不改写本报告、不写入能力记忆；与上方模型在报告生成时给出的建议来源不同，二者不等价。</small></p>
            <p v-if="ruleCandidateError" role="alert">候选读取失败：{{ ruleCandidateError }}</p>
            <p v-if="!ruleCandidates && !ruleCandidateError" role="status">正在读取当前可训练候选…</p>
            <template v-if="ruleCandidates">
              <p><small>{{ ruleCandidates.semantics }}</small></p>
              <p v-if="ruleCandidates.status==='TARGET_UNAVAILABLE'" role="alert">{{ ruleCandidates.message || candidateEmptyLabels[ruleCandidates.empty_reason||''] }}</p>
              <p v-else-if="!ruleCandidates.candidates.length" role="status">{{ candidateEmptyLabels[ruleCandidates.empty_reason||''] || '当前没有可训练候选。' }}</p>
              <ul class="candidate-list">
                <li v-for="c in ruleCandidates.candidates" :key="`${c.knowledge_id}-${c.mode}`">
                  <strong>{{ c.label }}</strong> · {{ candidateModeLabels[c.mode] }} · 规则优先级 {{ c.priority }}
                  <p>{{ c.reason }}</p>
                  <p><small>正式依据：{{ c.evidence.evaluation_refs.length ? c.evidence.evaluation_refs.join('、') : '暂无正式评价引用' }} · 能力修订 {{ c.evidence.memory_revision===null?'—':c.evidence.memory_revision }}</small></p>
                  <p><small>{{ c.scope_note }}</small></p>
                  <router-link :to="c.launch">去训练中心查看（仅预选，不会自动创建训练）</router-link>
                </li>
              </ul>
              <p v-if="ruleCandidates.fallback"><small>{{ ruleCandidates.fallback.reason }}</small></p>
              <p v-if="ruleCandidates.general_only.length"><small>跨岗位通用历史（不作为当前岗位候选）：{{ ruleCandidates.general_only.map(g=>`${g.label}/${g.state}`).join('、') }}。{{ ruleCandidates.verified_note }}</small></p>
              <p><small>计算于 {{ ruleCandidates.computed_at }} · {{ ruleCandidates.modes_source || 'interview.training.modes()' }}</small></p>
            </template>
          </article>
          <article class="card proposals"><h2>本场建议记住</h2><p>长期记忆建议：尚未批准不会影响下一场，也不会修改候选人画像。批准后可在成长记录查看，下次新建面试时固定使用。表现观察只描述来源场次。</p>
            <p v-if="!proposals.length">该报告暂无记忆提案。</p>
            <article v-for="proposal in proposals" :key="proposal.id" class="proposal">
              <h3>{{ displayLabel(proposal.proposal_type) }} · {{ displayLabel(proposal.status) }}</h3>
              <p>原始提案：{{ proposal.original_content }}</p>
              <p v-if="proposal.corrected_content">本人修正文：{{ proposal.corrected_content }}</p>
              <small>本场面试报告依据 · {{ confidenceLabel(proposal.confidence) }}</small>
              <details><summary>查看支持评价</summary>
                <div v-for="evaluation in proposal.supporting_evaluations" :key="evaluation.id" class="support"><strong>对应问题与本轮评价</strong><p>{{ evaluation.question }}</p><p>{{ evaluation.evaluation }}</p></div>
              </details>
              <div v-if="proposalCanDecide(proposal)" class="actions">
                <button :disabled="deciding !== null || generating || loading" @click="decide(proposal, 'approve')">批准写入长期记忆</button>
                <button :disabled="deciding !== null || generating || loading" @click="decision = null; correction = { id: proposal.id, content: proposal.original_content, reason: '' }">修改后批准</button>
                <button class="danger" :disabled="deciding !== null || generating || loading" @click="decide(proposal, 'reject')">忽略</button>
              </div>
              <section v-if="decision?.proposal.id === proposal.id" class="correction" role="group" aria-label="确认记忆决定">
                <p>{{ decision.action === 'approve' ? '确认将这条提案写入长期成长记忆？批准后，下一场面试才会读取它。' : '确认忽略这条提案？不会写入长期记忆。' }}</p>
                <button :disabled="deciding !== null || loading || generating" @click="confirmDecision">{{ decision.action === 'approve' ? '确认批准' : '确认忽略' }}</button>
                <button :disabled="deciding !== null" @click="decision = null">取消</button>
              </section>
              <form v-if="correction?.id === proposal.id" class="correction" @submit.prevent="saveCorrection">
                <label>修正内容<textarea v-model="correction.content" required maxlength="4000" rows="3" /></label>
                <label>确认 / 修正原因<input v-model="correction.reason" required maxlength="1000" /></label>
                <button :disabled="deciding !== null">确认修正文并写入记忆</button> <button type="button" :disabled="deciding !== null" @click="correction = null">取消</button>
              </form>
              <p v-if="proposal.confirmed_at">用户确认 / 决定：本人 · {{ proposal.confirmed_at }} · {{ proposal.confirmation_reason || '无附加原因' }}</p>
              <p v-if="proposal.memory_item_id">已保存到成长记录 · <router-link to="/memory">查看成长记录</router-link></p>
            </article>
          </article>
          <DesignTraceView :trace="detail?.design_trace" />
          <article class="card"><h2>逐题复盘</h2><p>展示本轮已保存的评价，Coach 不重新评分。</p>
            <details v-for="evaluation in detail.evaluations" :key="evaluation.id" class="evaluation"><summary>{{ evaluation.question }}</summary>
              <p v-if="evaluation.answer"><strong>本人回答：</strong>{{ evaluation.answer }}</p><p>{{ evaluation.evaluation }}</p>
              <div class="dimension-grid"><div v-for="(label, key) in REPORT_DIMENSION_LABELS" :key="key"><span>{{ label }}</span><strong>{{ evaluation.scores[key] ?? '—' }} / 5</strong></div></div>
              <p v-if="evaluation.covered_points?.length">覆盖：{{ evaluation.covered_points.join('；') }}</p>
              <p v-if="evaluation.missing_points?.length">缺失：{{ evaluation.missing_points.join('；') }}</p>
              <p v-if="evaluation.factual_or_reasoning_issues?.length">问题：{{ evaluation.factual_or_reasoning_issues.join('；') }}</p>
              <h4>更好的回答思路与参考回答</h4>
              <ReferenceAnswerView :reference="evaluation.reference_answer" :fallback="evaluation.answer_framework?.join('\n')" />
            </details>
          </article>
          <details class="card"><summary>报告版本历史</summary>
            <button v-for="report in detail.history" :key="report.id" class="history-row" :disabled="generating" @click="selectGeneration(report)">第 {{ report.generation_number ?? report.id }} 版 · {{ displayLabel(reportGenerationStatus(report)) }} {{ report.is_current ? '· 当前有效' : '' }} · {{ report.created_at }} <span v-if="report.error">· {{ userError(report.error) }}</span></button>
          </details>
        </template>
        <p v-else-if="!loading" class="card">选择历史报告，或从已完成面试进入生成页。</p>
      </main>
    </div>
  </section>
</template>

<style scoped>
.report-page { max-width: 1280px; margin: auto; color: #253047; }
.heading, .actions { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.heading { margin-bottom: 18px; }
h1 { margin: 0; font-size: 24px; } h2 { font-size: 18px; margin: 0 0 12px; } h3 { font-size: 16px; }
p { line-height: 1.6; } small, .heading p, .statistics > p:first-of-type { color: #667085; }
.report-layout { display: grid; grid-template-columns: 270px minmax(0, 1fr); align-items: start; gap: 18px; }
.report-detail { min-width: 0; } .card { padding: 20px; margin-bottom: 18px; border: 1px solid #e5e7eb; border-radius: 8px; background: white; }
.report-list { padding: 16px; } .report-item { width: 100%; box-sizing: border-box; display: flex; flex-direction: column; align-items: start; text-align: left; gap: 6px; margin-bottom: 12px; background: #f8fafc; color: #344054; border: 1px solid #e5e7eb; overflow-wrap: anywhere; }
.report-item strong, .report-item span { max-width: 100%; }
.report-item span { font-size: 12px; } .report-item.selected { border-color: #2e7d6f; background: #eaf5f1; }
button { padding: 8px 12px; border: 1px solid #d0d5dd; border-radius: 6px; background: #fff; font: inherit; cursor: pointer; color: #344054; }
.actions { justify-content: flex-start; margin-top: 14px; } .actions button:first-child { background: #2e7d6f; color: white; border-color: #2e7d6f; }
button:disabled { cursor: not-allowed; opacity: .6; } .danger { color: #b42318; }
.error { color: #b42318; background: #fff1f0; padding: 12px; } .notice { padding: 12px; background: #edf7f3; } .mode { color: #8a6113; background: #fff8e8; padding: 10px; }
.overall { font-size: 22px; font-weight: 600; color: #2e7d6f; }
.stat-grid, .dimension-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(105px, 1fr)); gap: 10px; margin-top: 14px; }
.stat-grid > div, .dimension-grid > div { display: flex; flex-direction: column; gap: 7px; padding: 12px; background: #f8fafc; border-radius: 6px; }
.stat-grid span, .dimension-grid span { color: #667085; font-size: 13px; }
.finding, .proposal { padding: 14px 0; border-top: 1px solid #eef2f6; } .support { padding: 12px; background: #f8fafc; margin-top: 8px; }
details { margin-top: 12px; } summary { cursor: pointer; line-height: 1.6; } .score-line { display: inline-block; margin: 3px 12px 0 0; font-size: 13px; }
.preserve-lines, pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; line-height: 1.7; } pre { padding: 12px; background: #f8fafc; }
.correction { display: grid; gap: 10px; margin-top: 12px; padding: 12px; background: #f8fafc; } label { display: grid; gap: 7px; }
input, textarea { width: 100%; box-sizing: border-box; border: 1px solid #d0d5dd; border-radius: 6px; padding: 8px; font: inherit; }
.evaluation { border-top: 1px solid #eef2f6; padding: 12px 0; } .history-row { display: block; text-align: left; margin: 8px 0; width: 100%; }
@media (max-width: 900px) { .report-layout { grid-template-columns: 1fr; } .report-list { max-height: 300px; overflow-y: auto; } }
</style>
