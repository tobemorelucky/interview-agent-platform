<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import {
  approveExperienceReviewItem,
  getExperienceReviewItem,
  listExperienceReviewItems,
  rejectExperienceReviewItem,
  requestExperienceReviewRerun,
  updateExperienceReviewItem,
  updateExperienceReviewQuestion,
  type ExperienceReviewDetail,
  type ExperienceReviewItem,
  type ExperienceReviewQuestion,
} from "../../api/admin"

const items = ref<ExperienceReviewItem[]>([])
const total = ref(0)
const loading = ref(false)
const detailLoading = ref(false)
const saving = ref(false)
const selected = ref<ExperienceReviewDetail | null>(null)
const selectedId = ref<number | null>(null)

const filters = ref({
  review_status: "",
  company: "",
  position: "",
  platform: "",
  risk_flag: "",
  keyword: "",
  reliability_min: undefined as number | undefined,
  reliability_max: undefined as number | undefined,
})

const experienceForm = ref({
  company: "",
  position: "",
  round_name: "",
  summary: "",
  review_note: "",
})

const questionDrafts = ref<Record<number, {
  question: string
  original_answer: string
  standard_answer: string
  answer_source: string
  question_type: string
  technical_categories_text: string
  difficulty: string
  should_index: boolean
  evidence: string
  review_status: string
}>>({})

const statusOptions = [
  { value: "", label: "全部状态" },
  { value: "WAITING_REVIEW", label: "待审核" },
  { value: "NEEDS_MANUAL_CHECK", label: "需人工确认" },
  { value: "APPROVED", label: "已通过" },
  { value: "REJECTED", label: "已拒绝" },
  { value: "NEEDS_RERUN", label: "需重跑" },
]

const platformOptions = ["", "牛客", "小红书", "抖音"]
const answerSourceOptions = ["ORIGINAL", "LLM_GENERATED", "HYBRID", "NONE"]
const questionStatusOptions = ["WAITING_REVIEW", "APPROVED", "REJECTED"]

const selectedQuestions = computed(() => selected.value?.questions || [])
const reportedTopics = computed(() => {
  const anchors = selected.value?.experience.extraction_json?.source_anchors as { reported_topics?: { raw_excerpt: string }[] } | undefined
  return anchors?.reported_topics || []
})

function statusLabel(status?: string | null) {
  const found = statusOptions.find((item) => item.value === status)
  return found?.label || status || "-"
}

function platformLabel(platform?: string | null) {
  return platform || "通用搜索"
}

function scoreText(score?: number | null) {
  return score === null || score === undefined ? "-" : score.toFixed(2)
}

async function loadItems() {
  loading.value = true
  try {
    const params: Record<string, string | number | undefined> = {
      review_status: filters.value.review_status || undefined,
      company: filters.value.company || undefined,
      position: filters.value.position || undefined,
      platform: filters.value.platform || undefined,
      risk_flag: filters.value.risk_flag || undefined,
      keyword: filters.value.keyword || undefined,
      reliability_min: filters.value.reliability_min,
      reliability_max: filters.value.reliability_max,
      offset: 0,
      limit: 50,
    }
    const res = await listExperienceReviewItems(params)
    items.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}

async function openDetail(id: number) {
  selectedId.value = id
  detailLoading.value = true
  try {
    selected.value = await getExperienceReviewItem(id)
    hydrateForms()
  } finally {
    detailLoading.value = false
  }
}

function closeDetail() {
  selected.value = null
  selectedId.value = null
  questionDrafts.value = {}
}

function hydrateForms() {
  const exp = selected.value?.experience
  if (!exp) return
  experienceForm.value = {
    company: exp.company || "",
    position: exp.position || "",
    round_name: exp.round_name || "",
    summary: exp.summary || "",
    review_note: exp.review_note || "",
  }
  const drafts: typeof questionDrafts.value = {}
  for (const question of selected.value?.questions || []) {
    drafts[question.question_id] = {
      question: question.question || "",
      original_answer: question.original_answer || "",
      standard_answer: question.standard_answer || "",
      answer_source: question.answer_source || "NONE",
      question_type: question.question_type || "",
      technical_categories_text: (question.technical_categories || []).join(", "),
      difficulty: question.difficulty || "",
      should_index: Boolean(question.should_index),
      evidence: question.evidence || "",
      review_status: question.review_status || "WAITING_REVIEW",
    }
  }
  questionDrafts.value = drafts
}

async function saveExperience() {
  if (!selectedId.value) return
  saving.value = true
  try {
    selected.value = await updateExperienceReviewItem(selectedId.value, {
      company: experienceForm.value.company,
      position: experienceForm.value.position,
      round_name: experienceForm.value.round_name,
      summary: experienceForm.value.summary,
      review_note: experienceForm.value.review_note,
    })
    hydrateForms()
    await loadItems()
    alert("面经修改已保存")
  } catch (error: any) {
    alert(error?.message || "保存失败")
  } finally {
    saving.value = false
  }
}

async function saveQuestion(question: ExperienceReviewQuestion) {
  const draft = questionDrafts.value[question.question_id]
  if (!draft) return
  saving.value = true
  try {
    await updateExperienceReviewQuestion(question.question_id, {
      question: draft.question,
      original_answer: draft.original_answer || null,
      standard_answer: draft.standard_answer || null,
      answer_source: draft.answer_source,
      question_type: draft.question_type || null,
      technical_categories: draft.technical_categories_text
        .split(/[,，、]/)
        .map((item) => item.trim())
        .filter(Boolean),
      difficulty: draft.difficulty || null,
      should_index: draft.should_index,
      evidence: draft.evidence || null,
      review_status: draft.review_status,
    })
    if (selectedId.value) {
      selected.value = await getExperienceReviewItem(selectedId.value)
      hydrateForms()
    }
    alert("题目修改已保存")
  } catch (error: any) {
    alert(error?.message || "题目保存失败")
  } finally {
    saving.value = false
  }
}

async function approveItem() {
  if (!selectedId.value || !confirm("确认审核通过？本阶段不会写入题库或 Milvus。")) return
  saving.value = true
  try {
    selected.value = await approveExperienceReviewItem(selectedId.value)
    hydrateForms()
    await loadItems()
  } catch (error: any) {
    alert(error?.message || "审核通过失败")
  } finally {
    saving.value = false
  }
}

async function rejectItem() {
  if (!selectedId.value) return
  const reason = window.prompt("请输入拒绝原因")
  if (!reason) return
  saving.value = true
  try {
    selected.value = await rejectExperienceReviewItem(selectedId.value, { reason })
    hydrateForms()
    await loadItems()
  } catch (error: any) {
    alert(error?.message || "拒绝失败")
  } finally {
    saving.value = false
  }
}

async function requestRerun() {
  if (!selectedId.value) return
  const reason = window.prompt("请输入退回重跑原因", "抽取结果不完整")
  if (reason === null) return
  saving.value = true
  try {
    selected.value = await requestExperienceReviewRerun(selectedId.value, {
      reason,
      force: true,
    })
    hydrateForms()
    await loadItems()
  } catch (error: any) {
    alert(error?.message || "退回重跑失败")
  } finally {
    saving.value = false
  }
}

onMounted(loadItems)
</script>

<template>
  <div class="review-page">
    <header class="page-head">
      <div>
        <h2>面经审核</h2>
        <p>查看、编辑和审核 Agent 生成的候选面经；审核通过不会立即入题库或写入 Milvus。</p>
      </div>
      <button class="primary" @click="loadItems" :disabled="loading">{{ loading ? "刷新中..." : "刷新" }}</button>
    </header>

    <section class="filters">
      <label>
        状态
        <select v-model="filters.review_status">
          <option v-for="item in statusOptions" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </label>
      <label>
        公司
        <input v-model="filters.company" placeholder="腾讯" />
      </label>
      <label>
        岗位
        <input v-model="filters.position" placeholder="后端" />
      </label>
      <label>
        平台
        <select v-model="filters.platform">
          <option v-for="item in platformOptions" :key="item" :value="item">{{ item || "全部平台" }}</option>
        </select>
      </label>
      <label>
        风险标签
        <input v-model="filters.risk_flag" placeholder="low_source_quality" />
      </label>
      <label>
        关键词
        <input v-model="filters.keyword" placeholder="标题 / 摘要 / URL" />
      </label>
      <label>
        最低可信度
        <input v-model.number="filters.reliability_min" type="number" min="0" max="1" step="0.05" />
      </label>
      <label>
        最高可信度
        <input v-model.number="filters.reliability_max" type="number" min="0" max="1" step="0.05" />
      </label>
      <button class="primary" @click="loadItems">筛选</button>
    </section>

    <section class="list-panel">
      <div class="panel-title">
        <h3>审核队列</h3>
        <span>共 {{ total }} 条</span>
      </div>
      <div v-if="items.length" class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>公司 / 岗位</th>
              <th>来源</th>
              <th>题目数</th>
              <th>可信度</th>
              <th>状态</th>
              <th>风险标签</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.experience_id">
              <td>#{{ item.experience_id }}</td>
              <td>
                <strong>{{ item.company || "未知公司" }}</strong>
                <div class="muted">{{ item.position || "未知岗位" }}</div>
              </td>
              <td>
                <span>{{ platformLabel(item.platform) }}</span>
                <a v-if="item.source_url" :href="item.source_url" target="_blank" rel="noopener noreferrer">原文</a>
              </td>
              <td>{{ item.question_count }}</td>
              <td>{{ scoreText(item.reliability_score) }}</td>
              <td><span class="tag">{{ statusLabel(item.review_status) }}</span></td>
              <td class="chips">
                <span v-for="flag in item.risk_flags" :key="flag">{{ flag }}</span>
                <span v-if="!item.risk_flags?.length">-</span>
              </td>
              <td>{{ item.created_at?.slice(0, 16) || "-" }}</td>
              <td><button @click="openDetail(item.experience_id)">查看 / 审核</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="empty">{{ loading ? "加载中..." : "暂无审核项" }}</p>
    </section>

    <div v-if="selected" class="overlay" @click.self="closeDetail">
      <section class="drawer">
        <header class="drawer-head">
          <div>
            <h3>审核详情 #{{ selected.experience.id }}</h3>
            <p>{{ selected.experience.title || selected.experience.source_url || "无标题" }}</p>
          </div>
          <button class="icon" @click="closeDetail">×</button>
        </header>

        <div class="actions-bar">
          <button class="primary" :disabled="saving" @click="saveExperience">保存修改</button>
          <button class="approve" :disabled="saving" @click="approveItem">通过</button>
          <button class="danger" :disabled="saving" @click="rejectItem">拒绝</button>
          <button :disabled="saving" @click="requestRerun">退回重跑</button>
        </div>

        <section class="detail-grid">
          <label>公司 <input v-model="experienceForm.company" /></label>
          <label>岗位 <input v-model="experienceForm.position" /></label>
          <label>轮次 <input v-model="experienceForm.round_name" /></label>
          <label>审核备注 <input v-model="experienceForm.review_note" /></label>
          <label class="wide">摘要 <textarea v-model="experienceForm.summary" rows="4" /></label>
        </section>

        <section class="metrics">
          <div><span>状态</span><strong>{{ statusLabel(selected.experience.review_status) }}</strong></div>
          <div><span>可信度</span><strong>{{ scoreText(selected.experience.reliability_score) }}</strong></div>
          <div><span>平台</span><strong>{{ platformLabel(selected.experience.platform) }}</strong></div>
          <div><span>题目数</span><strong>{{ selected.questions.length }}</strong></div>
        </section>

        <section class="info-section">
          <h4>Agent 质量信息</h4>
          <div class="chips">
            <span v-for="flag in selected.experience.risk_flags" :key="flag">{{ flag }}</span>
            <span v-for="flag in selected.experience.quality_flags" :key="flag">{{ flag }}</span>
            <span v-for="reason in selected.experience.quality_gate_reasons" :key="reason">{{ reason }}</span>
            <span v-if="!selected.experience.risk_flags.length && !selected.experience.quality_flags.length && !selected.experience.quality_gate_reasons.length">无风险标签</span>
          </div>
        </section>

        <section class="info-section">
          <h4>原文预览</h4>
          <a v-if="selected.experience.source_url" :href="selected.experience.source_url" target="_blank" rel="noopener noreferrer">
            {{ selected.experience.source_url }}
          </a>
          <p class="snippet">{{ selected.experience.snippet || "无摘要" }}</p>
          <pre>{{ selected.experience.raw_text_preview || "暂无正文" }}</pre>
        </section>

        <section class="info-section">
          <h4>题目列表</h4>
          <details v-if="reportedTopics.length">
            <summary>原帖仅报道的考察主题（不作为确切面试题）</summary>
            <p v-for="(topic, i) in reportedTopics" :key="i">{{ topic.raw_excerpt }}</p>
          </details>
          <article v-for="question in selectedQuestions" :key="question.question_id" class="question-editor">
            <div class="question-head">
              <strong>#{{ question.question_id }}</strong>
              <button :disabled="saving" @click="saveQuestion(question)">保存题目</button>
            </div>
            <template v-if="question.source_anchor">
              <p class="muted">规范化题目仅供展示，不是原帖逐字引文。请对照原文审核，不能增加原文没有的考察内容。</p>
              <blockquote><strong>原帖片段</strong><p>{{ question.source_anchor.raw_excerpt }}</p></blockquote>
            </template>
            <label>{{ question.source_anchor ? '规范化题目（待核对）' : '题目' }} <textarea v-model="questionDrafts[question.question_id].question" rows="2" /></label>
            <label>标准答案 <textarea v-model="questionDrafts[question.question_id].standard_answer" rows="3" /></label>
            <label>原文答案 <textarea v-model="questionDrafts[question.question_id].original_answer" rows="3" /></label>
            <label>证据{{ question.source_anchor ? '（原文锚点只读）' : '' }} <textarea v-model="questionDrafts[question.question_id].evidence" :readonly="!!question.source_anchor" rows="2" /></label>
            <div class="question-grid">
              <label>
                答案来源
                <select v-model="questionDrafts[question.question_id].answer_source">
                  <option v-for="item in answerSourceOptions" :key="item" :value="item">{{ item }}</option>
                </select>
              </label>
              <label>题型 <input v-model="questionDrafts[question.question_id].question_type" /></label>
              <label>技术分类 <input v-model="questionDrafts[question.question_id].technical_categories_text" /></label>
              <label>难度 <input v-model="questionDrafts[question.question_id].difficulty" /></label>
              <label>
                状态
                <select v-model="questionDrafts[question.question_id].review_status">
                  <option v-for="item in questionStatusOptions" :key="item" :value="item">{{ item }}</option>
                </select>
              </label>
              <label class="inline"><input v-model="questionDrafts[question.question_id].should_index" type="checkbox" /> 可入库候选</label>
            </div>
          </article>
        </section>

        <section class="info-section">
          <h4>Agent Trace</h4>
          <div class="trace-summary">
            <span>run_id: {{ selected.agent.agent_run_id || "-" }}</span>
            <span>graph: {{ selected.agent.graph_version || "-" }}</span>
            <span>status: {{ selected.agent.status || "-" }}</span>
          </div>
          <table class="trace-table">
            <thead><tr><th>Step</th><th>状态</th><th>耗时</th><th>摘要</th><th>错误</th></tr></thead>
            <tbody>
              <tr v-for="step in selected.agent.step_runs" :key="step.id">
                <td>{{ step.step_name }}</td>
                <td>{{ step.status }}</td>
                <td>{{ step.latency_ms ?? "-" }} ms</td>
                <td><code>{{ JSON.stringify(step.output_summary || {}) }}</code></td>
                <td>{{ step.error_message || "-" }}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </section>
    </div>

    <div v-if="detailLoading" class="loading-mask">详情加载中...</div>
  </div>
</template>

<style scoped>
.review-page {
  max-width: 1220px;
  margin: 0 auto;
  padding: 12px 0 36px;
  color: #1f2933;
}

.page-head,
.panel-title,
.drawer-head,
.question-head,
.actions-bar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.page-head h2,
.panel-title h3,
.drawer-head h3 {
  margin: 0;
}

.page-head p,
.drawer-head p,
.muted,
.snippet {
  margin: 6px 0 0;
  color: #667085;
}

.filters,
.detail-grid,
.question-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr));
  gap: 12px;
}

.filters,
.list-panel,
.info-section {
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  background: #fff;
  padding: 16px;
  margin-top: 16px;
}

label {
  display: grid;
  gap: 6px;
  font-size: 14px;
}

input,
select,
textarea {
  border: 1px solid #cfd8e3;
  border-radius: 6px;
  padding: 8px;
  font: inherit;
}

textarea {
  resize: vertical;
}

.wide {
  grid-column: 1 / -1;
}

button {
  border: 1px solid #cfd8e3;
  background: #fff;
  border-radius: 6px;
  padding: 8px 10px;
  cursor: pointer;
}

button:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}

.primary {
  border-color: #1f6feb;
  background: #1f6feb;
  color: #fff;
}

.approve {
  border-color: #0f9f6e;
  color: #047857;
}

.danger {
  border-color: #f3b4b4;
  color: #b42318;
}

.table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
}

th,
td {
  border-bottom: 1px solid #e6edf5;
  padding: 10px 8px;
  text-align: left;
  vertical-align: top;
  font-size: 14px;
}

th {
  color: #52616f;
}

.tag,
.chips span {
  display: inline-flex;
  border-radius: 999px;
  background: #eef4ff;
  color: #1d4ed8;
  padding: 3px 8px;
  font-size: 12px;
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.overlay {
  position: fixed;
  inset: 0;
  z-index: 40;
  background: rgba(15, 23, 42, 0.38);
  display: flex;
  justify-content: flex-end;
}

.drawer {
  width: min(1040px, 96vw);
  height: 100vh;
  overflow: auto;
  background: #fff;
  padding: 20px;
}

.icon {
  width: 34px;
  height: 34px;
  padding: 0;
  font-size: 20px;
}

.actions-bar {
  justify-content: flex-start;
  margin-bottom: 16px;
}

.metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-top: 16px;
}

.metrics div {
  border: 1px solid #e1e8f0;
  border-radius: 8px;
  padding: 10px;
  background: #f8fafc;
}

.metrics span {
  display: block;
  color: #667085;
  font-size: 12px;
}

.metrics strong {
  display: block;
  margin-top: 4px;
  font-size: 18px;
}

pre {
  white-space: pre-wrap;
  line-height: 1.7;
  border: 1px solid #e1e8f0;
  border-radius: 8px;
  background: #f8fafc;
  padding: 12px;
  max-height: 280px;
  overflow: auto;
}

.question-editor {
  border: 1px solid #e1e8f0;
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 10px;
  margin-top: 10px;
}

.inline {
  display: inline-flex;
  align-items: center;
}

.trace-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.trace-summary span {
  background: #f1f5f9;
  border-radius: 6px;
  padding: 5px 8px;
}

.trace-table code {
  white-space: normal;
  word-break: break-word;
}

.empty,
.loading-mask {
  color: #667085;
}

.loading-mask {
  position: fixed;
  right: 20px;
  bottom: 20px;
  background: #fff;
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  padding: 10px 12px;
}

@media (max-width: 860px) {
  .filters,
  .detail-grid,
  .question-grid,
  .metrics {
    grid-template-columns: 1fr;
  }

  .drawer {
    width: 100vw;
  }
}
</style>
