<script setup lang="ts">
import { computed, onMounted, ref } from "vue"
import {
  createExperienceKeyword,
  createExperienceTask,
  deleteExperienceKeyword,
  deleteExperienceTask,
  extractExperienceSource,
  fetchExperienceSource,
  fetchExperienceTaskSources,
  getExperienceSourcePreview,
  getExperienceTaskFetchStats,
  listExperienceKeywords,
  listExperienceTaskSources,
  listExperienceTasks,
  runExperienceTaskSearch,
  updateExperienceKeyword,
  type ExperienceCollectionTask,
  type ExperienceFetchStats,
  type ExperienceKeywordPreset,
  type ExperienceSourceItem,
  type ExperienceSourcePreview,
} from "../api/admin"

type TabKey = "tasks" | "keywords"
type SourceFilter = "ALL" | "FETCHED" | "FETCH_FAILED" | "DISCOVERED" | "SHORT"
type SearchStats = {
  raw_result_count: number
  accepted_count: number
  filtered_count: number
  duplicate_count: number
  found_url_count: number
}

const activeTab = ref<TabKey>("tasks")
const tasks = ref<ExperienceCollectionTask[]>([])
const taskTotal = ref(0)
const taskStats = ref<Record<number, SearchStats>>({})
const taskFetchStats = ref<Record<number, ExperienceFetchStats>>({})
const runningTaskId = ref<number | null>(null)
const fetchingTaskId = ref<number | null>(null)
const fetchingSourceId = ref<number | null>(null)
const extractingSourceId = ref<number | null>(null)
const creating = ref(false)
const createStatus = ref("")

const selectedTask = ref<ExperienceCollectionTask | null>(null)
const sourceModalOpen = ref(false)
const sourcesLoading = ref(false)
const sourceItems = ref<ExperienceSourceItem[]>([])
const sourceTotal = ref(0)
const sourceFilter = ref<SourceFilter>("ALL")
const previewItem = ref<ExperienceSourcePreview | null>(null)
const previewLoading = ref(false)

const keywords = ref<ExperienceKeywordPreset[]>([])
const kwTotal = ref(0)
const filterType = ref("")
const showKeywordForm = ref(false)
const editingKeywordId = ref<number | null>(null)
const kwForm = ref({ preset_type: "COMPANY", name: "", aliases_text: "", enabled: true })

const jobPresets = ref<ExperienceKeywordPreset[]>([])
const companyPresets = ref<ExperienceKeywordPreset[]>([])
const platformPresets = ref<ExperienceKeywordPreset[]>([])
const allowedPlatformNames = ["牛客", "小红书", "抖音"]

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

const typeLabel: Record<string, string> = {
  COMPANY: "公司",
  JOB: "岗位",
  PLATFORM: "平台",
}
const statusLabel: Record<string, string> = {
  PENDING: "待执行",
  SEARCHING: "搜索中",
  SEARCH_COMPLETED: "搜索完成",
  FETCHING: "抓取中",
  FETCH_COMPLETED: "抓取完成",
  EXTRACTING: "抽取中",
  WAITING_REVIEW: "待审核",
  NEEDS_MANUAL_CHECK: "需人工确认",
  REJECTED: "已拒绝",
  FAILED: "失败",
}
const fetchQualityLabel: Record<string, string> = {
  GOOD: "正文正常",
  SHORT: "正文较短",
  FAILED: "抓取失败",
  PENDING: "待抓取",
}
const sourceFilters: Array<{ key: SourceFilter; label: string }> = [
  { key: "ALL", label: "全部" },
  { key: "FETCHED", label: "已抓取" },
  { key: "FETCH_FAILED", label: "抓取失败" },
  { key: "DISCOVERED", label: "待抓取" },
  { key: "SHORT", label: "正文较短" },
]

const activeFetchStats = computed(() => {
  const task = selectedTask.value
  return task ? taskFetchStats.value[task.id] : null
})

const filteredSourceItems = computed(() => {
  if (sourceFilter.value === "ALL") return sourceItems.value
  if (sourceFilter.value === "SHORT") {
    return sourceItems.value.filter((item) => item.fetch_quality === "SHORT")
  }
  return sourceItems.value.filter((item) => item.fetch_status === sourceFilter.value)
})

async function loadTasks() {
  const res = await listExperienceTasks()
  tasks.value = res.items || []
  taskTotal.value = res.total || 0
  if (selectedTask.value) {
    selectedTask.value = tasks.value.find((task) => task.id === selectedTask.value?.id) || selectedTask.value
  }
  await loadTaskFetchStats()
}

async function loadTaskFetchStats() {
  const next = { ...taskFetchStats.value }
  const candidates = tasks.value.filter((task) => task.found_url_count > 0).slice(0, 50)
  const results = await Promise.allSettled(
    candidates.map(async (task) => [task.id, await getExperienceTaskFetchStats(task.id)] as const)
  )
  for (const result of results) {
    if (result.status === "fulfilled") {
      const [taskId, stats] = result.value
      next[taskId] = stats
    }
  }
  taskFetchStats.value = next
}

async function loadKeywords() {
  const params: { preset_type?: string } = {}
  if (filterType.value) params.preset_type = filterType.value
  const res = await listExperienceKeywords(params)
  keywords.value = res.items || []
  kwTotal.value = res.total || 0
}

async function loadPresets() {
  const [jobs, companies, platforms] = await Promise.all([
    listExperienceKeywords({ preset_type: "JOB", enabled: true }),
    listExperienceKeywords({ preset_type: "COMPANY", enabled: true }),
    listExperienceKeywords({ preset_type: "PLATFORM", enabled: true }),
  ])
  jobPresets.value = jobs.items || []
  companyPresets.value = companies.items || []
  const presetMap = new Map((platforms.items || []).map((preset) => [preset.name, preset]))
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

function scopeLabel(scope: string) {
  return scope === "COMPANY" ? "按公司" : "按岗位"
}

function taskKeywords(task: ExperienceCollectionTask) {
  const values = task.search_scope === "COMPANY" ? task.company_keywords_json : task.job_keywords_json
  return values?.join(", ") || "-"
}

function taskPlatforms(task: ExperienceCollectionTask) {
  const values = task.platforms_json || []
  return values.length ? values.join(", ") : "通用搜索"
}

function sourcePlatform(platform?: string | null) {
  return platform || "通用搜索"
}

function canRunSearch(status: string) {
  return ["PENDING", "FAILED", "SEARCH_COMPLETED"].includes(status)
}

function canFetchSources(task: ExperienceCollectionTask) {
  return task.found_url_count > 0 && task.status !== "FETCHING"
}

function canRetryFailed(task: ExperienceCollectionTask) {
  return (task.failed_count || 0) > 0 && task.status !== "FETCHING"
}

function rememberStats(taskId: number, result: SearchStats) {
  taskStats.value = { ...taskStats.value, [taskId]: result }
}

function searchStat(taskId: number, key: keyof SearchStats) {
  return taskStats.value[taskId]?.[key] ?? "-"
}

function formatSearchMessage(result: SearchStats) {
  return `搜索完成，发现 ${result.found_url_count || 0} 个 URL（raw=${result.raw_result_count || 0}, accepted=${result.accepted_count || 0}, filtered=${result.filtered_count || 0}, duplicate=${result.duplicate_count || 0}）`
}

async function handleCreateTask() {
  const form = taskForm.value
  if (form.search_scope === "JOB" && form.selected_job_keywords.length === 0) {
    alert("请至少选择一个岗位关键词")
    return
  }
  if (form.search_scope === "COMPANY" && form.selected_company_keywords.length === 0) {
    alert("请至少选择一个公司关键词")
    return
  }
  creating.value = true
  createStatus.value = "正在创建任务..."
  try {
    const task = await createExperienceTask({
      search_scope: form.search_scope,
      time_window_hours: form.time_window_hours,
      job_keywords_json: form.search_scope === "JOB" ? form.selected_job_keywords : [],
      company_keywords_json: form.search_scope === "COMPANY" ? form.selected_company_keywords : [],
      platforms_json: form.selected_platforms,
      max_results: form.max_results,
      review_mode: form.review_mode,
      write_to_question_db: form.write_to_question_db,
      write_to_vector_index: form.write_to_vector_index,
      update_public_summary: form.update_public_summary,
    })
    createStatus.value = "任务已创建，正在自动搜索..."
    await loadTasks()
    runningTaskId.value = task.id
    try {
      const result = await runExperienceTaskSearch(task.id)
      rememberStats(task.id, result)
      await loadTasks()
      selectedTask.value = tasks.value.find((item) => item.id === task.id) || task
      await loadSources(task.id)
      await loadFetchStats(task.id)
      sourceModalOpen.value = true
      createStatus.value = formatSearchMessage(result)
    } catch (error: any) {
      await loadTasks()
      createStatus.value = error?.message || "自动搜索失败，任务已保留在采集历史中"
    } finally {
      runningTaskId.value = null
    }
  } catch (error: any) {
    createStatus.value = ""
    alert(error?.message || "创建失败")
  } finally {
    creating.value = false
  }
}

async function handleRunSearch(task: ExperienceCollectionTask) {
  runningTaskId.value = task.id
  try {
    const result = await runExperienceTaskSearch(task.id)
    rememberStats(task.id, result)
    await loadTasks()
    if (sourceModalOpen.value && selectedTask.value?.id === task.id) {
      await loadSources(task.id)
      await loadFetchStats(task.id)
    }
    alert(formatSearchMessage(result))
  } catch (error: any) {
    alert(error?.message || "搜索执行失败")
  } finally {
    runningTaskId.value = null
  }
}

async function handleFetchSources(task: ExperienceCollectionTask, retryFailed = false) {
  fetchingTaskId.value = task.id
  try {
    const result = await fetchExperienceTaskSources(task.id, { retry_failed: retryFailed, limit: 20 })
    await loadTasks()
    if (sourceModalOpen.value && selectedTask.value?.id === task.id) {
      selectedTask.value = tasks.value.find((item) => item.id === task.id) || selectedTask.value
      await loadSources(task.id)
      await loadFetchStats(task.id)
    }
    alert(`${retryFailed ? "失败项重试" : "正文抓取"}完成：成功 ${result.fetched_count}，失败 ${result.failed_count}`)
  } catch (error: any) {
    alert(error?.message || (retryFailed ? "失败项重试失败" : "正文抓取失败"))
  } finally {
    fetchingTaskId.value = null
  }
}

async function loadSources(taskId: number) {
  sourcesLoading.value = true
  try {
    const res = await listExperienceTaskSources(taskId, { offset: 0, limit: 100 })
    sourceItems.value = res.items || []
    sourceTotal.value = res.total || 0
  } finally {
    sourcesLoading.value = false
  }
}

async function loadFetchStats(taskId: number) {
  const stats = await getExperienceTaskFetchStats(taskId)
  taskFetchStats.value = { ...taskFetchStats.value, [taskId]: stats }
}

async function openSources(task: ExperienceCollectionTask) {
  selectedTask.value = task
  sourceFilter.value = "ALL"
  previewItem.value = null
  sourceModalOpen.value = true
  await Promise.all([loadSources(task.id), loadFetchStats(task.id)])
}

function closeSources() {
  sourceModalOpen.value = false
  selectedTask.value = null
  previewItem.value = null
  sourceItems.value = []
  sourceTotal.value = 0
}

async function openPreview(item: ExperienceSourceItem) {
  previewLoading.value = true
  try {
    previewItem.value = await getExperienceSourcePreview(item.id)
  } catch (error: any) {
    alert(error?.message || "正文预览加载失败")
  } finally {
    previewLoading.value = false
  }
}

async function handleFetchSource(item: ExperienceSourceItem) {
  fetchingSourceId.value = item.id
  try {
    const result = await fetchExperienceSource(item.id, { force: true })
    if (selectedTask.value) {
      await Promise.all([loadSources(selectedTask.value.id), loadFetchStats(selectedTask.value.id)])
      await loadTasks()
    }
    alert(result.fetch_status === "FETCHED" ? "重新抓取成功" : `重新抓取失败：${result.error_message || "未知错误"}`)
  } catch (error: any) {
    alert(error?.message || "重新抓取失败")
  } finally {
    fetchingSourceId.value = null
  }
}

async function handleExtractSource(item: ExperienceSourceItem) {
  extractingSourceId.value = item.id
  try {
    const result = await extractExperienceSource(item.id, { force: false })
    if (selectedTask.value) {
      await Promise.all([loadSources(selectedTask.value.id), loadFetchStats(selectedTask.value.id)])
      await loadTasks()
    }
    alert(
      `抽取完成：${result.is_interview_experience ? "是面经" : "非面经"}；` +
      `问题数 ${result.question_count || 0}，可索引 ${result.indexable_question_count || 0}；` +
      `可靠性 ${result.reliability_score ?? "-"}；` +
      `审核状态 ${result.review_status || result.status || "-"}；` +
      `质量门 ${result.quality_gate_reasons?.join(", ") || "-"}`
    )
  } catch (error: any) {
    alert(error?.message || "抽取面经失败")
  } finally {
    extractingSourceId.value = null
  }
}

async function handleDeleteTask(task: ExperienceCollectionTask) {
  if (!confirm(`确认删除采集任务 #${task.id}？相关来源 URL 也会一起删除。`)) return
  await deleteExperienceTask(task.id)
  if (selectedTask.value?.id === task.id) closeSources()
  await loadTasks()
}

function openCreateKeyword() {
  editingKeywordId.value = null
  kwForm.value = { preset_type: "COMPANY", name: "", aliases_text: "", enabled: true }
  showKeywordForm.value = true
}

function openEditKeyword(keyword: ExperienceKeywordPreset) {
  editingKeywordId.value = keyword.id
  kwForm.value = {
    preset_type: keyword.preset_type,
    name: keyword.name,
    aliases_text: (keyword.aliases_json || []).join(", "),
    enabled: keyword.enabled,
  }
  showKeywordForm.value = true
}

function closeKeywordForm() {
  showKeywordForm.value = false
  editingKeywordId.value = null
}

async function handleSaveKeyword() {
  const aliases = kwForm.value.aliases_text
    .split(/[,，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
  const data = {
    preset_type: kwForm.value.preset_type,
    name: kwForm.value.name,
    aliases_json: aliases,
    enabled: kwForm.value.enabled,
  }
  if (editingKeywordId.value) {
    await updateExperienceKeyword(editingKeywordId.value, data)
  } else {
    await createExperienceKeyword(data)
  }
  closeKeywordForm()
  await Promise.all([loadKeywords(), loadPresets()])
}

async function handleToggleKeyword(keyword: ExperienceKeywordPreset) {
  await updateExperienceKeyword(keyword.id, { enabled: !keyword.enabled })
  await Promise.all([loadKeywords(), loadPresets()])
}

async function handleDeleteKeyword(id: number) {
  if (!confirm("确认删除该关键词？")) return
  await deleteExperienceKeyword(id)
  await Promise.all([loadKeywords(), loadPresets()])
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
        <p>创建采集任务、查看来源、抓取正文，并对正文运行面经抽取质量链路。</p>
      </div>
    </header>

    <div class="tabs">
      <button :class="{ active: activeTab === 'tasks' }" @click="activeTab = 'tasks'">采集历史</button>
      <button :class="{ active: activeTab === 'keywords' }" @click="activeTab = 'keywords'">关键词预设</button>
    </div>

    <main v-if="activeTab === 'tasks'" class="task-layout">
      <section class="panel">
        <div class="panel-title">
          <h3>创建采集任务</h3>
          <span v-if="createStatus">{{ createStatus }}</span>
        </div>

        <div class="task-form">
          <div class="form-grid">
            <label>
              搜索维度
              <select v-model="taskForm.search_scope" @change="toggleScope(taskForm.search_scope)">
                <option value="JOB">按岗位</option>
                <option value="COMPANY">按公司</option>
              </select>
            </label>
            <label>
              时间范围
              <select v-model.number="taskForm.time_window_hours">
                <option v-for="preset in timePresets" :key="preset.value" :value="preset.value">
                  {{ preset.label }}
                </option>
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

          <div class="field-group" v-if="taskForm.search_scope === 'JOB'">
            <div class="field-title">岗位关键词</div>
            <div class="check-grid">
              <label v-for="item in jobPresets" :key="item.id">
                <input v-model="taskForm.selected_job_keywords" type="checkbox" :value="item.name" />
                {{ item.name }}
              </label>
            </div>
          </div>

          <div class="field-group" v-if="taskForm.search_scope === 'COMPANY'">
            <div class="field-title">公司关键词</div>
            <div class="check-grid">
              <label v-for="item in companyPresets" :key="item.id">
                <input v-model="taskForm.selected_company_keywords" type="checkbox" :value="item.name" />
                {{ item.name }}
              </label>
            </div>
          </div>

          <div class="field-group">
            <div class="field-title">平台</div>
            <p class="hint">不选择平台时进行不限站点的通用网页搜索；选择平台时只保留对应官方链接。</p>
            <div class="check-grid">
              <label v-for="item in platformPresets" :key="item.id">
                <input v-model="taskForm.selected_platforms" type="checkbox" :value="item.name" />
                {{ item.name }}
              </label>
            </div>
          </div>

          <div class="write-options">
            <label><input v-model="taskForm.write_to_question_db" type="checkbox" /> 写入题库</label>
            <label><input v-model="taskForm.write_to_vector_index" type="checkbox" /> 写入向量库</label>
            <label><input v-model="taskForm.update_public_summary" type="checkbox" /> 更新公开摘要</label>
          </div>

          <button class="primary" :disabled="creating" @click="handleCreateTask">
            {{ creating ? "创建并搜索中..." : "创建采集任务" }}
          </button>
        </div>
      </section>

      <section class="panel">
        <div class="panel-title">
          <h3>历史任务列表</h3>
          <span>共 {{ taskTotal }} 条</span>
        </div>

        <div v-if="tasks.length" class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>搜索维度</th>
                <th>关键词</th>
                <th>平台</th>
                <th>状态</th>
                <th>URL / raw / accepted / filtered</th>
                <th>抓取摘要</th>
                <th>创建时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="task in tasks" :key="task.id">
                <td>#{{ task.id }}</td>
                <td>{{ scopeLabel(task.search_scope) }}</td>
                <td class="clip">{{ taskKeywords(task) }}</td>
                <td class="clip">{{ taskPlatforms(task) }}</td>
                <td>
                  <span class="tag">{{ statusLabel[task.status] || task.status }}</span>
                  <div v-if="task.error_message" class="error-line">{{ task.error_message }}</div>
                </td>
                <td>
                  {{ task.found_url_count }} /
                  {{ searchStat(task.id, "raw_result_count") }} /
                  {{ searchStat(task.id, "accepted_count") }} /
                  {{ searchStat(task.id, "filtered_count") }}
                </td>
                <td>
                  {{ task.fetched_count }} 成功 / {{ task.failed_count }} 失败 /
                  {{ taskFetchStats[task.id]?.avg_raw_text_chars ?? "-" }} 字
                </td>
                <td>{{ task.created_at?.slice(0, 16) || "-" }}</td>
                <td class="actions">
                  <button @click="openSources(task)">查看来源</button>
                  <button v-if="canRunSearch(task.status)" :disabled="runningTaskId === task.id" @click="handleRunSearch(task)">
                    {{ runningTaskId === task.id ? "搜索中..." : "重新搜索" }}
                  </button>
                  <button v-if="canFetchSources(task)" :disabled="fetchingTaskId === task.id" @click="handleFetchSources(task)">
                    {{ fetchingTaskId === task.id ? "抓取中..." : "抓取正文" }}
                  </button>
                  <button v-if="canRetryFailed(task)" :disabled="fetchingTaskId === task.id" @click="handleFetchSources(task, true)">
                    重试失败项
                  </button>
                  <button class="danger" @click="handleDeleteTask(task)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty">暂无采集任务。</p>
      </section>
    </main>

    <main v-else class="keyword-layout">
      <section class="panel">
        <div class="panel-title">
          <h3>关键词预设</h3>
          <button class="primary small" @click="openCreateKeyword">新增关键词</button>
        </div>
        <div class="filter-tabs">
          <button :class="{ active: !filterType }" @click="filterType = ''; loadKeywords()">全部</button>
          <button v-for="type in ['COMPANY', 'JOB', 'PLATFORM']" :key="type" :class="{ active: filterType === type }" @click="filterType = type; loadKeywords()">
            {{ typeLabel[type] }}
          </button>
        </div>
        <div v-if="keywords.length" class="table-wrap">
          <table>
            <thead>
              <tr><th>类型</th><th>名称</th><th>别名</th><th>启用</th><th>操作</th></tr>
            </thead>
            <tbody>
              <tr v-for="item in keywords" :key="item.id">
                <td>{{ typeLabel[item.preset_type] || item.preset_type }}</td>
                <td>{{ item.name }}</td>
                <td class="clip">{{ (item.aliases_json || []).join(", ") || "-" }}</td>
                <td>{{ item.enabled ? "是" : "否" }}</td>
                <td class="actions">
                  <button @click="openEditKeyword(item)">编辑</button>
                  <button @click="handleToggleKeyword(item)">{{ item.enabled ? "禁用" : "启用" }}</button>
                  <button class="danger" @click="handleDeleteKeyword(item.id)">删除</button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-else class="empty">暂无关键词。</p>
      </section>
    </main>

    <div v-if="sourceModalOpen" class="overlay" @click.self="closeSources">
      <section class="drawer">
        <header class="drawer-head">
          <div>
            <h3>来源 URL</h3>
            <p v-if="selectedTask">任务 #{{ selectedTask.id }} · {{ scopeLabel(selectedTask.search_scope) }} · {{ taskKeywords(selectedTask) }}</p>
          </div>
          <button class="icon" @click="closeSources">×</button>
        </header>

        <div class="stats">
          <div><span>总数</span><strong>{{ activeFetchStats?.total ?? sourceTotal }}</strong></div>
          <div><span>成功</span><strong>{{ activeFetchStats?.fetched_count ?? 0 }}</strong></div>
          <div><span>失败</span><strong>{{ activeFetchStats?.failed_count ?? 0 }}</strong></div>
          <div><span>待抓取</span><strong>{{ activeFetchStats?.pending_count ?? 0 }}</strong></div>
          <div><span>平均字数</span><strong>{{ activeFetchStats?.avg_raw_text_chars ?? "-" }}</strong></div>
        </div>

        <div class="source-toolbar">
          <div class="filter-tabs">
            <button v-for="filter in sourceFilters" :key="filter.key" :class="{ active: sourceFilter === filter.key }" @click="sourceFilter = filter.key">
              {{ filter.label }}
            </button>
          </div>
          <div v-if="activeFetchStats?.failure_reasons?.length" class="failure-list">
            <span v-for="reason in activeFetchStats.failure_reasons" :key="reason.reason">{{ reason.reason }}：{{ reason.count }}</span>
          </div>
        </div>

        <div v-if="filteredSourceItems.length" class="source-list">
          <article v-for="item in filteredSourceItems" :key="item.id" class="source-item">
            <div class="source-content">
              <a class="source-title" :href="item.source_url" target="_blank" rel="noopener noreferrer">{{ item.title || item.source_url }}</a>
              <a class="source-url" :href="item.source_url" target="_blank" rel="noopener noreferrer">{{ item.source_url }}</a>
              <p>{{ item.snippet || "无摘要" }}</p>
              <div v-if="item.error_message" class="error-line">{{ item.error_message }}</div>
            </div>
            <div class="source-tags">
              <span>{{ sourcePlatform(item.platform) }}</span>
              <span>{{ item.query_text || "无 query" }}</span>
              <span>{{ item.engine || "未知引擎" }}</span>
              <span>{{ item.matched_reason || "无匹配原因" }}</span>
              <span>{{ item.fetch_status_label || item.fetch_status }}</span>
              <span v-if="item.extract_status">抽取 {{ item.extract_status }}</span>
              <span :class="['quality', (item.fetch_quality || '').toLowerCase()]">
                {{ fetchQualityLabel[item.fetch_quality || ""] || item.fetch_quality || "-" }}
              </span>
              <span>{{ item.raw_text_char_count || 0 }} 字</span>
            </div>
            <div class="source-actions">
              <a :href="item.source_url" target="_blank" rel="noopener noreferrer">打开原网页</a>
              <button :disabled="previewLoading" @click="openPreview(item)">查看正文预览</button>
              <button :disabled="fetchingSourceId === item.id" @click="handleFetchSource(item)">
                {{ fetchingSourceId === item.id ? "抓取中..." : "重新抓取" }}
              </button>
              <button v-if="item.fetch_status === 'FETCHED'" :disabled="extractingSourceId === item.id" @click="handleExtractSource(item)">
                {{ extractingSourceId === item.id ? "抽取中..." : "抽取面经" }}
              </button>
            </div>
          </article>
        </div>
        <p v-else class="empty">{{ sourcesLoading ? "加载中..." : "暂无匹配来源 URL" }}</p>
      </section>
    </div>

    <div v-if="previewItem" class="overlay" @click.self="previewItem = null">
      <section class="preview">
        <header class="drawer-head">
          <div>
            <h3>正文预览</h3>
            <p>{{ previewItem.title || previewItem.source_url }}</p>
            <p>{{ previewItem.fetch_status_label || previewItem.fetch_status }} · {{ previewItem.raw_text_char_count }} 字</p>
          </div>
          <button class="icon" @click="previewItem = null">×</button>
        </header>
        <pre>{{ previewItem.raw_text_preview || "暂无正文" }}</pre>
      </section>
    </div>

    <div v-if="showKeywordForm" class="overlay" @click.self="closeKeywordForm">
      <section class="keyword-modal">
        <h3>{{ editingKeywordId ? "编辑关键词" : "新增关键词" }}</h3>
        <label>
          类型
          <select v-model="kwForm.preset_type" :disabled="!!editingKeywordId">
            <option value="COMPANY">公司</option>
            <option value="JOB">岗位</option>
            <option value="PLATFORM">平台</option>
          </select>
        </label>
        <label>名称 <input v-model="kwForm.name" /></label>
        <label>别名 <input v-model="kwForm.aliases_text" placeholder="用逗号分隔" /></label>
        <label class="inline"><input v-model="kwForm.enabled" type="checkbox" /> 启用</label>
        <div class="modal-actions">
          <button class="primary" @click="handleSaveKeyword">保存</button>
          <button @click="closeKeywordForm">取消</button>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.admin-experiences {
  max-width: 1200px;
  margin: 0 auto;
  padding: 12px 0 36px;
  color: #1f2933;
}

.page-heading {
  margin-bottom: 16px;
}

.page-heading h2,
.panel-title h3,
.drawer-head h3 {
  margin: 0;
}

.page-heading p,
.drawer-head p,
.hint {
  margin: 6px 0 0;
  color: #667085;
}

.tabs,
.filter-tabs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.tabs {
  margin-bottom: 16px;
}

button,
.source-actions a {
  border: 1px solid #cfd8e3;
  background: #fff;
  border-radius: 6px;
  padding: 7px 10px;
  color: #263445;
  cursor: pointer;
  text-decoration: none;
}

button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

button.active,
.primary {
  border-color: #1f6feb;
  background: #1f6feb;
  color: #fff;
}

.small {
  padding: 6px 9px;
}

.danger {
  border-color: #f3b4b4;
  color: #b42318;
}

.task-layout,
.keyword-layout {
  display: grid;
  gap: 16px;
}

.panel {
  border: 1px solid #d9e2ec;
  border-radius: 8px;
  background: #fff;
  padding: 18px;
}

.panel-title,
.drawer-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 14px;
}

.panel-title span {
  color: #667085;
}

.task-form {
  display: grid;
  gap: 16px;
}

.form-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(150px, 1fr));
  gap: 12px;
}

label {
  display: grid;
  gap: 6px;
  font-size: 14px;
}

select,
input {
  min-height: 34px;
  border: 1px solid #cfd8e3;
  border-radius: 6px;
  padding: 0 8px;
  background: #fff;
}

.field-title {
  margin-bottom: 8px;
  font-weight: 700;
}

.check-grid,
.write-options {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 16px;
}

.check-grid label,
.write-options label,
.inline {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.table-wrap {
  overflow-x: auto;
}

table {
  width: 100%;
  border-collapse: collapse;
  min-width: 920px;
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
  font-weight: 700;
}

.clip {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tag,
.quality {
  display: inline-flex;
  border-radius: 999px;
  padding: 3px 8px;
  background: #eef4ff;
  color: #1d4ed8;
  font-size: 12px;
}

.quality.short {
  background: #fff7cc;
  color: #8a5a00;
}

.quality.failed {
  background: #ffe7e7;
  color: #b42318;
}

.error-line {
  margin-top: 6px;
  color: #b42318;
  font-size: 12px;
}

.actions,
.source-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.empty {
  color: #667085;
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
  width: min(980px, 94vw);
  height: 100vh;
  overflow: auto;
  background: #fff;
  padding: 20px;
}

.preview,
.keyword-modal {
  width: min(760px, 92vw);
  max-height: 86vh;
  overflow: auto;
  margin: auto;
  background: #fff;
  border-radius: 8px;
  padding: 20px;
}

.icon {
  font-size: 20px;
  line-height: 1;
  width: 34px;
  height: 34px;
  padding: 0;
}

.stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  gap: 10px;
  margin-bottom: 14px;
}

.stats div {
  border: 1px solid #e1e8f0;
  border-radius: 8px;
  padding: 10px;
  background: #f8fafc;
}

.stats span {
  display: block;
  color: #667085;
  font-size: 12px;
}

.stats strong {
  display: block;
  margin-top: 4px;
  font-size: 20px;
}

.source-toolbar {
  display: grid;
  gap: 10px;
  margin-bottom: 14px;
}

.failure-list,
.source-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.failure-list span,
.source-tags span {
  border-radius: 999px;
  background: #f1f5f9;
  padding: 3px 8px;
  color: #475569;
  font-size: 12px;
}

.source-list {
  display: grid;
  gap: 12px;
}

.source-item {
  border: 1px solid #e1e8f0;
  border-radius: 8px;
  padding: 12px;
  display: grid;
  gap: 10px;
}

.source-title {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  color: #0f172a;
  font-weight: 700;
  text-decoration: none;
}

.source-url {
  display: block;
  margin-top: 4px;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: #1f6feb;
  text-decoration: none;
}

.source-content p {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  margin: 8px 0 0;
  color: #52616f;
}

pre {
  white-space: pre-wrap;
  line-height: 1.7;
  border: 1px solid #e1e8f0;
  border-radius: 8px;
  background: #f8fafc;
  padding: 14px;
}

.keyword-modal {
  display: grid;
  gap: 12px;
}

.modal-actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
}

@media (max-width: 860px) {
  .form-grid,
  .stats {
    grid-template-columns: 1fr;
  }

  .drawer {
    width: 100vw;
  }
}
</style>
