<script setup lang="ts">
import { ref, onMounted } from "vue"
import {
  listExperienceKeywords,
  createExperienceKeyword,
  updateExperienceKeyword,
  deleteExperienceKeyword,
  type ExperienceKeywordPreset,
  listExperienceTasks,
  createExperienceTask,
  deleteExperienceTask,
  runExperienceTaskSearch,
  fetchExperienceTaskSources,
  listExperienceTaskSources,
  type ExperienceCollectionTask,
  type ExperienceSourceItem,
} from "../api/admin"

type SearchStats = {
  raw_result_count: number
  accepted_count: number
  filtered_count: number
  duplicate_count: number
  found_url_count: number
}

const activeTab = ref<"tasks" | "keywords">("tasks")

const keywords = ref<ExperienceKeywordPreset[]>([])
const kwTotal = ref(0)
const filterType = ref("")
const showForm = ref(false)
const editingId = ref<number | null>(null)
const kwForm = ref({ preset_type: "COMPANY", name: "", aliases_text: "", enabled: true })

const typeLabel: Record<string, string> = { COMPANY: "公司", JOB: "岗位", PLATFORM: "平台" }
const types = ["COMPANY", "JOB", "PLATFORM"]

const tasks = ref<ExperienceCollectionTask[]>([])
const taskTotal = ref(0)
const runningTaskId = ref<number | null>(null)
const fetchingTaskId = ref<number | null>(null)
const taskStats = ref<Record<number, SearchStats>>({})
const sourceItems = ref<ExperienceSourceItem[]>([])
const sourceTotal = ref(0)
const sourcesLoading = ref(false)
const sourceModalOpen = ref(false)
const previewItem = ref<ExperienceSourceItem | null>(null)
const selectedTask = ref<ExperienceCollectionTask | null>(null)
const createStatus = ref("")
const creating = ref(false)

const taskForm = ref({
  search_scope: "JOB" as "JOB" | "COMPANY",
  time_window_hours: 24,
  selected_job_keywords: [] as string[],
  selected_company_keywords: [] as string[],
  selected_platforms: [] as string[],
  max_results: 20,
  review_mode: "MANUAL",
  write_to_question_db: false,
  write_to_vector_index: false,
  update_public_summary: true,
})

const timePresets = [
  { label: "24 小时", value: 24 },
  { label: "3 天", value: 72 },
  { label: "7 天", value: 168 },
]

const allowedPlatformNames = ["牛客", "小红书", "抖音"]
const jobPresets = ref<ExperienceKeywordPreset[]>([])
const companyPresets = ref<ExperienceKeywordPreset[]>([])
const platformPresets = ref<ExperienceKeywordPreset[]>([])

async function loadKeywords() {
  const params: any = {}
  if (filterType.value) params.preset_type = filterType.value
  const res = await listExperienceKeywords(params)
  keywords.value = res.items || []
  kwTotal.value = res.total || 0
}

async function loadTasks() {
  const res = await listExperienceTasks()
  tasks.value = res.items || []
  taskTotal.value = res.total || 0
  if (selectedTask.value) {
    selectedTask.value = tasks.value.find(t => t.id === selectedTask.value?.id) || selectedTask.value
  }
}

async function loadPresets() {
  const [jobs, companies, platforms] = await Promise.all([
    listExperienceKeywords({ preset_type: "JOB", enabled: true }),
    listExperienceKeywords({ preset_type: "COMPANY", enabled: true }),
    listExperienceKeywords({ preset_type: "PLATFORM", enabled: true }),
  ])
  jobPresets.value = jobs.items || []
  companyPresets.value = companies.items || []
  const presetMap = new Map((platforms.items || []).map(p => [p.name, p]))
  platformPresets.value = allowedPlatformNames.map((name, index) => (
    presetMap.get(name) || {
      id: -(index + 1),
      preset_type: "PLATFORM",
      name,
      aliases_json: [],
      enabled: true,
    }
  ))
}

function toggleScope(scope: "JOB" | "COMPANY") {
  taskForm.value.search_scope = scope
  taskForm.value.selected_job_keywords = []
  taskForm.value.selected_company_keywords = []
}

function canRunSearch(status: string) {
  return ["PENDING", "FAILED", "SEARCH_COMPLETED"].includes(status)
}

function canFetchSources(task: ExperienceCollectionTask) {
  return task.found_url_count > 0 && task.status !== "FETCHING"
}

function scopeLabel(scope: string) {
  return scope === "COMPANY" ? "按公司" : "按岗位"
}

function taskKeywords(task: ExperienceCollectionTask) {
  const values = task.search_scope === "COMPANY" ? task.company_keywords_json : task.job_keywords_json
  return values?.join(", ") || "-"
}

function taskPlatforms(task: ExperienceCollectionTask) {
  const values = (task.platforms_json || []).filter(p => p !== "全网")
  return values.length ? values.join(", ") : "通用搜索"
}

function sourcePlatform(platform?: string | null) {
  return platform && platform !== "全网" ? platform : "通用搜索"
}

function formatSearchResultMessage(result: SearchStats) {
  return `搜索完成，发现 ${result.found_url_count || 0} 个 URL（raw=${result.raw_result_count || 0}, accepted=${result.accepted_count || 0}, filtered=${result.filtered_count || 0}, duplicate=${result.duplicate_count || 0}）`
}

function rememberStats(taskId: number, result: SearchStats) {
  taskStats.value = { ...taskStats.value, [taskId]: result }
}

function statValue(key: keyof SearchStats) {
  const task = selectedTask.value
  if (!task) return "-"
  if (key === "found_url_count") return String(task.found_url_count || 0)
  const stats = taskStats.value[task.id]
  return stats ? String(stats[key] || 0) : "-"
}

async function handleRunSearch(task: ExperienceCollectionTask) {
  runningTaskId.value = task.id
  try {
    const result = await runExperienceTaskSearch(task.id)
    rememberStats(task.id, result)
    await loadTasks()
    if (sourceModalOpen.value && selectedTask.value?.id === task.id) {
      await loadSources(task.id)
    }
    alert(formatSearchResultMessage(result))
  } catch (e: any) {
    alert(e?.message || "搜索执行失败")
  } finally {
    runningTaskId.value = null
  }
}

async function handleFetchSources(task: ExperienceCollectionTask) {
  fetchingTaskId.value = task.id
  try {
    const result = await fetchExperienceTaskSources(task.id, { retry_failed: false, limit: 20 })
    await loadTasks()
    if (sourceModalOpen.value && selectedTask.value?.id === task.id) {
      selectedTask.value = tasks.value.find(t => t.id === task.id) || selectedTask.value
      await loadSources(task.id)
    }
    alert(`正文抓取完成：成功 ${result.fetched_count}，失败 ${result.failed_count}`)
  } catch (e: any) {
    alert(e?.message || "正文抓取失败")
  } finally {
    fetchingTaskId.value = null
  }
}

async function handleCreateTask() {
  const f = taskForm.value
  if (f.search_scope === "JOB" && f.selected_job_keywords.length === 0) {
    alert("请至少选择一个岗位关键词")
    return
  }
  if (f.search_scope === "COMPANY" && f.selected_company_keywords.length === 0) {
    alert("请至少选择一个公司关键词")
    return
  }
  creating.value = true
  createStatus.value = ""
  try {
    const task = await createExperienceTask({
      search_scope: f.search_scope,
      time_window_hours: f.time_window_hours,
      job_keywords_json: f.search_scope === "JOB" ? f.selected_job_keywords : [],
      company_keywords_json: f.search_scope === "COMPANY" ? f.selected_company_keywords : [],
      platforms_json: f.selected_platforms,
      max_results: f.max_results,
      review_mode: f.review_mode,
      write_to_question_db: f.write_to_question_db,
      write_to_vector_index: f.write_to_vector_index,
      update_public_summary: f.update_public_summary,
    })
    createStatus.value = "任务已创建，正在自动搜索..."
    await loadTasks()
    runningTaskId.value = task.id
    try {
      const result = await runExperienceTaskSearch(task.id)
      rememberStats(task.id, result)
      await loadTasks()
      selectedTask.value = tasks.value.find(t => t.id === task.id) || task
      await loadSources(task.id)
      sourceModalOpen.value = true
      createStatus.value = formatSearchResultMessage(result)
    } catch (searchError: any) {
      await loadTasks()
      createStatus.value = searchError?.message || "自动搜索失败，任务已保留在采集历史中"
    } finally {
      runningTaskId.value = null
    }
  } catch (e: any) {
    alert(e?.message || "创建失败")
  } finally {
    creating.value = false
  }
}

async function loadSources(taskId: number) {
  sourcesLoading.value = true
  try {
    const res = await listExperienceTaskSources(taskId, { offset: 0, limit: 50 })
    sourceItems.value = res.items || []
    sourceTotal.value = res.total || 0
  } finally {
    sourcesLoading.value = false
  }
}

async function openSources(task: ExperienceCollectionTask) {
  selectedTask.value = task
  sourceModalOpen.value = true
  await loadSources(task.id)
}

function closeSources() {
  sourceModalOpen.value = false
  selectedTask.value = null
  previewItem.value = null
  sourceItems.value = []
  sourceTotal.value = 0
}

function openPreview(item: ExperienceSourceItem) {
  previewItem.value = item
}

function closePreview() {
  previewItem.value = null
}

async function handleDeleteTask(task: ExperienceCollectionTask) {
  if (!confirm(`确认删除采集任务 #${task.id}？相关来源 URL 也会一并删除。`)) return
  await deleteExperienceTask(task.id)
  if (selectedTask.value?.id === task.id) closeSources()
  await loadTasks()
}

function openCreate() {
  editingId.value = null
  kwForm.value = { preset_type: "COMPANY", name: "", aliases_text: "", enabled: true }
  showForm.value = true
}

function openEdit(kw: ExperienceKeywordPreset) {
  editingId.value = kw.id
  kwForm.value = {
    preset_type: kw.preset_type,
    name: kw.name,
    aliases_text: (kw.aliases_json || []).join(", "),
    enabled: kw.enabled,
  }
  showForm.value = true
}

function closeForm() {
  showForm.value = false
  editingId.value = null
}

async function handleSaveKeyword() {
  const aliases = kwForm.value.aliases_text.split(/[,，、]/).map(s => s.trim()).filter(Boolean)
  const data = {
    preset_type: kwForm.value.preset_type,
    name: kwForm.value.name,
    aliases_json: aliases,
    enabled: kwForm.value.enabled,
  }
  if (editingId.value) await updateExperienceKeyword(editingId.value, data)
  else await createExperienceKeyword(data)
  closeForm()
  await loadKeywords()
}

async function handleToggleKeyword(kw: ExperienceKeywordPreset) {
  await updateExperienceKeyword(kw.id, { enabled: !kw.enabled })
  await loadKeywords()
}

async function handleDeleteKeyword(id: number) {
  if (!confirm("确认删除？")) return
  await deleteExperienceKeyword(id)
  await loadKeywords()
}

const statusLabel: Record<string, string> = {
  PENDING: "待执行",
  SEARCHING: "搜索中",
  SEARCH_COMPLETED: "搜索完成",
  FETCHING: "抓取中",
  FETCH_COMPLETED: "抓取完成",
  EXTRACTING: "抽取中",
  ROUTING: "分类中",
  SCORING: "评分中",
  DEDUPING: "去重中",
  WAITING_REVIEW: "待审核",
  APPROVED: "已通过",
  INDEXING: "索引入库",
  COMPLETED: "已完成",
  FAILED: "失败",
}

onMounted(() => {
  loadTasks()
  loadKeywords()
  loadPresets()
})
</script>

<template>
  <div class="admin-experiences">
    <header class="page-heading">
      <div>
        <h2>面经采集</h2>
        <p class="desc">创建搜索任务、自动发现候选 URL，并在进入正文抓取前完成来源检查。</p>
      </div>
    </header>

    <div class="main-tabs">
      <button :class="{ active: activeTab === 'tasks' }" @click="activeTab = 'tasks'">采集历史</button>
      <button :class="{ active: activeTab === 'keywords' }" @click="activeTab = 'keywords'">关键词预设</button>
    </div>

    <div v-if="activeTab === 'tasks'" class="tasks-view">
      <section class="section-block">
        <div class="section-title">
          <h3>创建采集任务</h3>
          <span v-if="createStatus">{{ createStatus }}</span>
        </div>

        <div class="task-form">
          <div class="form-grid">
            <label>
              搜索维度
              <select v-model="taskForm.search_scope" @change="toggleScope(taskForm.search_scope)">
                <option value="JOB">按岗位搜索</option>
                <option value="COMPANY">按公司搜索</option>
              </select>
            </label>
            <label>
              时间范围
              <select v-model.number="taskForm.time_window_hours">
                <option v-for="p in timePresets" :key="p.value" :value="p.value">{{ p.label }}</option>
              </select>
            </label>
            <label>
              最大结果数
              <input v-model.number="taskForm.max_results" type="number" min="1" max="100" />
            </label>
            <label>
              审核模式
              <select v-model="taskForm.review_mode">
                <option value="MANUAL">人工审核</option>
                <option value="AUTO_PUBLISH">自动发布</option>
              </select>
            </label>
          </div>

          <div v-if="taskForm.search_scope === 'JOB'" class="select-section">
            <div class="field-title">关键词多选</div>
            <div class="checkbox-group">
              <label v-for="j in jobPresets" :key="j.id" class="cb-label">
                <input type="checkbox" :value="j.name" v-model="taskForm.selected_job_keywords" />
                {{ j.name }}
              </label>
            </div>
          </div>

          <div v-if="taskForm.search_scope === 'COMPANY'" class="select-section">
            <div class="field-title">关键词多选</div>
            <div class="checkbox-group">
              <label v-for="c in companyPresets" :key="c.id" class="cb-label">
                <input type="checkbox" :value="c.name" v-model="taskForm.selected_company_keywords" />
                {{ c.name }}
              </label>
            </div>
          </div>

          <div class="select-section">
            <div class="field-title">平台多选</div>
            <p class="field-hint">不选择平台时，将进行不限站点的通用网页搜索；选择具体平台时，只保留对应平台官网链接，结果更精准但数量可能更少。</p>
            <div class="checkbox-group">
              <label v-for="p in platformPresets" :key="p.id" class="cb-label">
                <input type="checkbox" :value="p.name" v-model="taskForm.selected_platforms" />
                {{ p.name }}
              </label>
            </div>
          </div>

          <div class="write-options">
            <label><input type="checkbox" v-model="taskForm.write_to_question_db" /> 写入题库</label>
            <label><input type="checkbox" v-model="taskForm.write_to_vector_index" /> 写入向量库</label>
            <label><input type="checkbox" v-model="taskForm.update_public_summary" /> 更新公开总结</label>
          </div>

          <button class="btn-primary" @click="handleCreateTask" :disabled="creating">
            {{ creating ? "创建并搜索中..." : "创建更新任务" }}
          </button>
        </div>
      </section>

      <section class="section-block">
        <div class="section-title">
          <h3>历史任务列表</h3>
          <span>共 {{ taskTotal }} 条</span>
        </div>

        <div class="history-table-wrap" v-if="tasks.length">
          <table class="history-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>搜索维度</th>
                <th>关键词</th>
                <th>平台</th>
                <th>状态</th>
                <th>found / fetched / failed</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in tasks" :key="t.id">
                <td>#{{ t.id }}</td>
                <td>{{ scopeLabel(t.search_scope) }}</td>
                <td class="truncate-cell">{{ taskKeywords(t) }}</td>
                <td class="truncate-cell">{{ taskPlatforms(t) }}</td>
                <td>
                  <span class="status-tag">{{ statusLabel[t.status] || t.status }}</span>
                  <div v-if="t.error_message" class="row-error">{{ t.error_message }}</div>
                </td>
                <td>{{ t.found_url_count }} / {{ t.fetched_count }} / {{ t.failed_count }}</td>
                <td>{{ t.created_at?.slice(0, 16) || "" }}</td>
                <td class="actions">
                  <button @click="openSources(t)">查看来源</button>
                  <button v-if="canRunSearch(t.status)" @click="handleRunSearch(t)" :disabled="runningTaskId === t.id">
                    {{ runningTaskId === t.id ? "搜索中..." : "重新搜索" }}
                  </button>
                  <button v-if="canFetchSources(t)" @click="handleFetchSources(t)" :disabled="fetchingTaskId === t.id">
                    {{ fetchingTaskId === t.id ? "抓取中..." : "抓取正文" }}
                  </button>
                  <button class="btn-del" @click="handleDeleteTask(t)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty">暂无任务，先创建一个采集任务。</p>
      </section>
    </div>

    <div v-if="activeTab === 'keywords'">
      <section class="section-block">
        <div class="section-title">
          <h3>关键词预设</h3>
          <button class="btn-primary small" @click="openCreate">新增关键词</button>
        </div>
        <div class="tabs">
          <button :class="{ active: !filterType }" @click="filterType = ''; loadKeywords()">全部 ({{ kwTotal }})</button>
          <button v-for="t in types" :key="t" :class="{ active: filterType === t }" @click="filterType = t; loadKeywords()">{{ typeLabel[t] || t }}</button>
        </div>

        <div class="history-table-wrap" v-if="keywords.length">
          <table class="keyword-table">
            <thead><tr><th>类型</th><th>名称</th><th>别名</th><th>启用</th><th>操作</th></tr></thead>
            <tbody>
              <tr v-for="kw in keywords" :key="kw.id" :class="{ disabled: !kw.enabled }">
                <td>{{ typeLabel[kw.preset_type] || kw.preset_type }}</td>
                <td>{{ kw.name }}</td>
                <td class="truncate-cell">{{ (kw.aliases_json || []).join(", ") }}</td>
                <td><span :class="kw.enabled ? 'enabled' : 'disabled-tag'">{{ kw.enabled ? "是" : "否" }}</span></td>
                <td class="actions">
                  <button @click="openEdit(kw)">编辑</button>
                  <button @click="handleToggleKeyword(kw)">{{ kw.enabled ? "禁用" : "启用" }}</button>
                  <button class="btn-del" @click="handleDeleteKeyword(kw.id)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty">暂无关键词。</p>
      </section>
    </div>

    <div v-if="sourceModalOpen" class="modal-overlay drawer-overlay" @click.self="closeSources">
      <div class="source-modal">
        <div class="modal-head">
          <div>
            <h3>来源 URL</h3>
            <p v-if="selectedTask">任务 #{{ selectedTask.id }} · {{ scopeLabel(selectedTask.search_scope) }} · {{ taskKeywords(selectedTask) }}</p>
          </div>
          <button class="icon-btn" @click="closeSources">×</button>
        </div>

        <div class="stat-strip">
          <div><span>found</span><strong>{{ statValue("found_url_count") }}</strong></div>
          <div><span>raw</span><strong>{{ statValue("raw_result_count") }}</strong></div>
          <div><span>accepted</span><strong>{{ statValue("accepted_count") }}</strong></div>
          <div><span>filtered</span><strong>{{ statValue("filtered_count") }}</strong></div>
        </div>

        <div class="source-list" v-if="sourceItems.length">
          <article v-for="item in sourceItems" :key="item.id" class="source-item">
            <div class="source-main">
              <a class="source-title" :href="item.source_url" target="_blank" rel="noopener noreferrer">{{ item.title || item.source_url }}</a>
              <a class="source-url" :href="item.source_url" target="_blank" rel="noopener noreferrer">{{ item.source_url }}</a>
              <p class="source-snippet">{{ item.snippet || "无摘要" }}</p>
            </div>
            <div class="source-tags">
              <span>{{ sourcePlatform(item.platform) }}</span>
              <span>{{ item.query_text || "无 query" }}</span>
              <span>{{ item.engine || "未知引擎" }}</span>
              <span>{{ item.matched_reason || "未记录原因" }}</span>
              <span>{{ item.fetch_status }}</span>
              <span>正文 {{ item.raw_text_char_count || 0 }} 字</span>
              <span>{{ item.fetched_at?.slice(0, 16) || item.created_at?.slice(0, 16) || "" }}</span>
            </div>
            <div v-if="item.error_message" class="source-error">{{ item.error_message }}</div>
            <button class="preview-btn" @click="openPreview(item)">查看正文预览</button>
          </article>
        </div>
        <p v-else class="empty compact">{{ sourcesLoading ? "加载中..." : "暂无来源 URL" }}</p>
      </div>
    </div>

    <div v-if="previewItem" class="modal-overlay preview-overlay" @click.self="closePreview">
      <div class="preview-modal">
        <div class="modal-head">
          <div>
            <h3>正文预览</h3>
            <p>{{ previewItem.title || previewItem.source_url }}</p>
          </div>
          <button class="icon-btn" @click="closePreview">×</button>
        </div>
        <pre class="raw-preview">{{ previewItem.raw_text_preview || "暂无正文" }}</pre>
      </div>
    </div>

    <div v-if="showForm" class="modal-overlay" @click.self="closeForm">
      <div class="keyword-modal">
        <h3>{{ editingId ? "编辑关键词" : "新增关键词" }}</h3>
        <label>类型 <select v-model="kwForm.preset_type" :disabled="!!editingId"><option v-for="t in types" :key="t" :value="t">{{ typeLabel[t] || t }}</option></select></label>
        <label>名称 <input v-model="kwForm.name" placeholder="例如: 快手" /></label>
        <label>别名（逗号分隔）<input v-model="kwForm.aliases_text" placeholder="例如: 快手电商, Kuaishou" /></label>
        <label class="checkbox-label"><input type="checkbox" v-model="kwForm.enabled" /> 启用</label>
        <div class="modal-actions"><button @click="handleSaveKeyword">保存</button><button class="btn-cancel" @click="closeForm">取消</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-experiences {
  max-width: 1180px;
  margin: 0 auto;
  padding: 8px 0 32px;
  color: #20242a;
}

.page-heading {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-bottom: 18px;
}

.page-heading h2 {
  margin: 0;
  font-size: 24px;
  letter-spacing: 0;
}

.desc {
  color: #667085;
  margin: 8px 0 0;
  line-height: 1.6;
}

.main-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  margin-bottom: 18px;
  background: #eef2f6;
  border-radius: 8px;
}

.main-tabs button {
  min-width: 112px;
  padding: 8px 16px;
  border: none;
  background: transparent;
  cursor: pointer;
  font-size: 14px;
  color: #475467;
  border-radius: 6px;
}

.main-tabs button.active {
  color: #0f172a;
  background: #fff;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.08);
}

.tasks-view {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.section-block {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 18px;
  background: #fff;
  box-shadow: 0 1px 2px rgba(16, 24, 40, 0.04);
}

.section-title {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.section-title h3 {
  margin: 0;
  font-size: 16px;
}

.section-title span {
  font-size: 13px;
  color: #667085;
}

.task-form {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 14px;
}

.form-grid label,
.keyword-modal label {
  font-size: 13px;
  color: #344054;
}

.form-grid input,
.form-grid select,
.keyword-modal input,
.keyword-modal select {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  margin-top: 6px;
  background: #fff;
}

.select-section {
  padding-top: 2px;
}

.field-title {
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
  color: #344054;
}

.field-hint {
  margin: -2px 0 10px;
  color: #667085;
  font-size: 12px;
  line-height: 1.6;
}

.checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.cb-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  padding: 6px 10px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  cursor: pointer;
  background: #fff;
}

.cb-label:has(input:checked) {
  border-color: #2e7d6f;
  background: #eef8f5;
}

.write-options {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  padding-top: 2px;
  font-size: 13px;
  color: #344054;
}

.btn-primary {
  align-self: flex-start;
  padding: 8px 18px;
  background: #2e7d6f;
  color: #fff;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.btn-primary.small {
  padding: 7px 14px;
}

.btn-primary:disabled,
.actions button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.history-table-wrap {
  overflow-x: auto;
  border: 1px solid #eef2f6;
  border-radius: 8px;
}

.history-table,
.keyword-table {
  width: 100%;
  border-collapse: collapse;
}

.history-table th,
.history-table td,
.keyword-table th,
.keyword-table td {
  padding: 11px 12px;
  border-bottom: 1px solid #eef2f6;
  text-align: left;
  font-size: 13px;
  vertical-align: top;
}

.history-table th,
.keyword-table th {
  color: #667085;
  font-weight: 600;
  background: #f8fafc;
  white-space: nowrap;
}

.history-table tr:last-child td,
.keyword-table tr:last-child td {
  border-bottom: none;
}

.truncate-cell {
  max-width: 200px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.row-error {
  max-width: 260px;
  color: #b42318;
  font-size: 12px;
  margin-top: 4px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
  background: #eef8f5;
  color: #2e7d6f;
  white-space: nowrap;
}

.actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 190px;
}

.actions button {
  padding: 5px 10px;
  border: 1px solid #d0d5dd;
  border-radius: 4px;
  background: #fff;
  cursor: pointer;
  font-size: 13px;
}

.btn-del {
  color: #b42318;
  border-color: #fecdca !important;
}

.empty {
  color: #98a2b3;
  text-align: center;
  padding: 32px;
}

.compact {
  padding: 18px;
}

.tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}

.tabs button {
  padding: 6px 14px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
}

.tabs button.active {
  background: #2e7d6f;
  color: #fff;
  border-color: #2e7d6f;
}

.enabled {
  color: #12b76a;
  font-weight: 600;
}

.disabled-tag {
  color: #b42318;
}

.disabled {
  opacity: 0.55;
}

.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: 24px;
}

.drawer-overlay {
  align-items: stretch;
  justify-content: flex-end;
  padding: 0;
}

.source-modal {
  width: min(820px, 100vw);
  height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  background: #fff;
  box-shadow: -10px 0 28px rgba(15, 23, 42, 0.16);
}

.keyword-modal {
  background: #fff;
  border-radius: 8px;
  padding: 22px;
  width: 430px;
  max-width: 92vw;
}

.modal-head {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-head h3 {
  margin: 0;
  font-size: 18px;
}

.modal-head p {
  margin: 5px 0 0;
  color: #667085;
  font-size: 13px;
}

.icon-btn {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 1px solid #d0d5dd;
  background: #fff;
  cursor: pointer;
  font-size: 20px;
  line-height: 1;
  flex-shrink: 0;
}

.stat-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  padding: 14px 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #f8fafc;
}

.stat-strip div {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 10px;
  background: #fff;
}

.stat-strip span {
  display: block;
  font-size: 12px;
  color: #667085;
}

.stat-strip strong {
  display: block;
  margin-top: 4px;
  font-size: 20px;
}

.source-list {
  overflow: auto;
  padding: 14px 20px 18px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.source-item {
  border: 1px solid #e5e7eb;
  border-radius: 6px;
  padding: 12px;
}

.source-title {
  color: #1f2937;
  font-weight: 600;
  text-decoration: none;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.source-url {
  display: block;
  max-width: 100%;
  margin-top: 5px;
  color: #1f6feb;
  font-size: 12px;
  text-decoration: none;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-snippet {
  margin: 8px 0 0;
  color: #667085;
  font-size: 13px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.source-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 10px;
}

.source-tags span {
  font-size: 12px;
  color: #475467;
  background: #f2f4f7;
  border-radius: 999px;
  padding: 3px 8px;
  max-width: 280px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-error {
  margin-top: 8px;
  color: #b42318;
  font-size: 12px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.preview-btn {
  margin-top: 10px;
  padding: 5px 10px;
  border: 1px solid #d0d5dd;
  border-radius: 4px;
  background: #fff;
  color: #344054;
  cursor: pointer;
  font-size: 13px;
}

.preview-overlay {
  z-index: 120;
}

.preview-modal {
  width: min(820px, 92vw);
  max-height: 84vh;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
}

.raw-preview {
  margin: 0;
  padding: 16px 20px 20px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  color: #344054;
  font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace;
  font-size: 13px;
  line-height: 1.7;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
}

.modal-actions {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
  margin-top: 16px;
}

.modal-actions button {
  padding: 8px 18px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
}

.modal-actions button:first-child {
  background: #2e7d6f;
  color: #fff;
}

.btn-cancel {
  background: #f2f4f7 !important;
  color: #344054 !important;
}

@media (max-width: 760px) {
  .page-heading,
  .section-title {
    align-items: flex-start;
    flex-direction: column;
  }

  .form-grid,
  .stat-strip {
    grid-template-columns: 1fr;
  }

  .main-tabs {
    display: grid;
    grid-template-columns: 1fr 1fr;
    width: 100%;
  }
}
</style>
