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
  runExperienceTaskSearch,
  listExperienceTaskSources,
  type ExperienceCollectionTask,
  type ExperienceSourceItem,
} from "../api/admin"

const activeTab = ref<"tasks" | "keywords">("tasks")

// ── Keywords state ──
const keywords = ref<ExperienceKeywordPreset[]>([])
const kwTotal = ref(0)
const filterType = ref("")
const showForm = ref(false)
const editingId = ref<number | null>(null)
const kwForm = ref({ preset_type: "COMPANY", name: "", aliases_text: "", enabled: true })

const typeLabel: Record<string, string> = { COMPANY: "公司", JOB: "岗位", PLATFORM: "平台" }
const types = ["COMPANY", "JOB", "PLATFORM"]

async function loadKeywords() {
  const params: any = {}
  if (filterType.value) params.preset_type = filterType.value
  const res = await listExperienceKeywords(params)
  keywords.value = res.items || []
  kwTotal.value = res.total || 0
}

function openCreate() { editingId.value = null; kwForm.value = { preset_type: "COMPANY", name: "", aliases_text: "", enabled: true }; showForm.value = true }
function openEdit(kw: ExperienceKeywordPreset) {
  editingId.value = kw.id
  kwForm.value = { preset_type: kw.preset_type, name: kw.name, aliases_text: (kw.aliases_json || []).join(", "), enabled: kw.enabled }
  showForm.value = true
}
function closeForm() { showForm.value = false; editingId.value = null }

async function handleSave() {
  const aliases = kwForm.value.aliases_text.split(/[,，、]/).map(s => s.trim()).filter(s => s)
  const data = { preset_type: kwForm.value.preset_type, name: kwForm.value.name, aliases_json: aliases, enabled: kwForm.value.enabled }
  if (editingId.value) await updateExperienceKeyword(editingId.value, data)
  else await createExperienceKeyword(data)
  closeForm(); await loadKeywords()
}
async function handleToggle(kw: ExperienceKeywordPreset) { await updateExperienceKeyword(kw.id, { enabled: !kw.enabled }); await loadKeywords() }
async function handleDelete(id: number) { if (!confirm("确认删除？")) return; await deleteExperienceKeyword(id); await loadKeywords() }

// ── Tasks state ──
const tasks = ref<ExperienceCollectionTask[]>([])
const taskTotal = ref(0)
const runningTaskId = ref<number | null>(null)
const expandedTaskId = ref<number | null>(null)
const sourceItems = ref<ExperienceSourceItem[]>([])
const sourceTotal = ref(0)
const sourcesLoading = ref(false)
const showTaskForm = ref(false)
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
const creating = ref(false)
const timePresets = [
  { label: "24 小时", value: 24 },
  { label: "3 天", value: 72 },
  { label: "7 天", value: 168 },
]

// Load enabled keyword presets for multi-select
const jobPresets = ref<ExperienceKeywordPreset[]>([])
const companyPresets = ref<ExperienceKeywordPreset[]>([])
const platformPresets = ref<ExperienceKeywordPreset[]>([])

async function loadPresets() {
  const [jobs, companies, platforms] = await Promise.all([
    listExperienceKeywords({ preset_type: "JOB", enabled: true }),
    listExperienceKeywords({ preset_type: "COMPANY", enabled: true }),
    listExperienceKeywords({ preset_type: "PLATFORM", enabled: true }),
  ])
  jobPresets.value = jobs.items || []
  companyPresets.value = companies.items || []
  platformPresets.value = platforms.items || []
  // Default select all platforms
  if (taskForm.value.selected_platforms.length === 0) {
    taskForm.value.selected_platforms = platformPresets.value.map(p => p.name)
  }
}

function toggleScope(scope: "JOB" | "COMPANY") {
  taskForm.value.search_scope = scope
  taskForm.value.selected_job_keywords = []
  taskForm.value.selected_company_keywords = []
}

async function loadTasks() {
  const res = await listExperienceTasks()
  tasks.value = res.items || []
  taskTotal.value = res.total || 0
}

function canRunSearch(status: string) {
  return ["PENDING", "FAILED", "SEARCH_COMPLETED"].includes(status)
}

async function handleRunSearch(task: ExperienceCollectionTask) {
  runningTaskId.value = task.id
  try {
    const result = await runExperienceTaskSearch(task.id)
    await loadTasks()
    if (expandedTaskId.value === task.id) {
      await loadSources(task.id)
    }
    alert(`搜索完成，当前发现 ${result.found_url_count || 0} 个 URL`)
  } catch (e: any) {
    alert(e?.message || "搜索执行失败")
  } finally {
    runningTaskId.value = null
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

async function toggleSources(taskId: number) {
  if (expandedTaskId.value === taskId) {
    expandedTaskId.value = null
    sourceItems.value = []
    sourceTotal.value = 0
    return
  }
  expandedTaskId.value = taskId
  await loadSources(taskId)
}

async function handleCreateTask() {
  const f = taskForm.value
  if (f.search_scope === "JOB" && f.selected_job_keywords.length === 0) {
    alert("请至少选择一个岗位关键词"); return
  }
  if (f.search_scope === "COMPANY" && f.selected_company_keywords.length === 0) {
    alert("请至少选择一个公司关键词"); return
  }
  if (f.selected_platforms.length === 0) {
    alert("请至少选择一个平台"); return
  }
  creating.value = true
  try {
    await createExperienceTask({
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
    showTaskForm.value = false
    await loadTasks()
    alert("任务已创建，等待后续搜索执行")
  } catch (e: any) { alert(e?.message || "创建失败") }
  finally { creating.value = false }
}

const statusLabel: Record<string, string> = {
  PENDING: "待执行", SEARCHING: "搜索中", SEARCH_COMPLETED: "搜索完成", FETCHING: "抓取中", EXTRACTING: "抽取中",
  ROUTING: "分类中", SCORING: "评分中", DEDUPING: "去重中", WAITING_REVIEW: "待审核",
  APPROVED: "已通过", INDEXING: "索引入库", COMPLETED: "已完成", FAILED: "失败",
}

onMounted(() => { loadTasks(); loadKeywords(); loadPresets() })
</script>

<template>
  <div class="admin-experiences">
    <h2>面经更新管理</h2>
    <p class="desc">创建面经采集任务并管理搜索关键词。</p>

    <!-- Main tabs -->
    <div class="main-tabs">
      <button :class="{ active: activeTab === 'tasks' }" @click="activeTab = 'tasks'">更新任务</button>
      <button :class="{ active: activeTab === 'keywords' }" @click="activeTab = 'keywords'">关键词预设</button>
    </div>

    <!-- ====== Tasks Tab ====== -->
    <div v-if="activeTab === 'tasks'">
      <button class="btn-add" @click="showTaskForm = !showTaskForm">{{ showTaskForm ? '取消' : '+ 创建采集任务' }}</button>

      <!-- Create form -->
      <div v-if="showTaskForm" class="task-form">
        <div class="form-row">
          <label>搜索维度
            <select v-model="taskForm.search_scope" @change="toggleScope(taskForm.search_scope)">
              <option value="JOB">按岗位搜索</option>
              <option value="COMPANY">按公司搜索</option>
            </select>
          </label>
          <label>时间范围
            <select v-model.number="taskForm.time_window_hours">
              <option v-for="p in timePresets" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </label>
          <label>最大结果数 <input v-model.number="taskForm.max_results" type="number" min="1" max="100" /></label>
          <label>审核模式
            <select v-model="taskForm.review_mode">
              <option value="MANUAL">人工审核</option>
              <option value="AUTO_PUBLISH">自动发布</option>
            </select>
          </label>
        </div>
        <!-- Job keywords (only when JOB scope) -->
        <div v-if="taskForm.search_scope === 'JOB'" class="kw-select">
          <label>岗位关键词（多选）</label>
          <div class="checkbox-group">
            <label v-for="j in jobPresets" :key="j.id" class="cb-label">
              <input type="checkbox" :value="j.name" v-model="taskForm.selected_job_keywords" /> {{ j.name }}
            </label>
          </div>
        </div>
        <!-- Company keywords (only when COMPANY scope) -->
        <div v-if="taskForm.search_scope === 'COMPANY'" class="kw-select">
          <label>公司关键词（多选）</label>
          <div class="checkbox-group">
            <label v-for="c in companyPresets" :key="c.id" class="cb-label">
              <input type="checkbox" :value="c.name" v-model="taskForm.selected_company_keywords" /> {{ c.name }}
            </label>
          </div>
        </div>
        <!-- Platforms (always shown) -->
        <div class="kw-select">
          <label>平台（多选）</label>
          <div class="checkbox-group">
            <label v-for="p in platformPresets" :key="p.id" class="cb-label">
              <input type="checkbox" :value="p.name" v-model="taskForm.selected_platforms" /> {{ p.name }}
            </label>
          </div>
        </div>
        <div class="form-row checks">
          <label><input type="checkbox" v-model="taskForm.write_to_question_db" /> 写入题库</label>
          <label><input type="checkbox" v-model="taskForm.write_to_vector_index" /> 写入向量库</label>
          <label><input type="checkbox" v-model="taskForm.update_public_summary" /> 更新公开总结</label>
        </div>
        <button class="btn-add" @click="handleCreateTask" :disabled="creating">
          {{ creating ? '创建中...' : '创建更新任务' }}
        </button>
      </div>

      <!-- Task list -->
      <table class="kw-table" v-if="tasks.length">
        <thead>
          <tr>
            <th>ID</th>
            <th>维度</th>
            <th>时间</th>
            <th>关键词/平台</th>
            <th>状态</th>
            <th>进度</th>
            <th>发现/抓取/抽取/问题/通过/失败</th>
            <th>创建时间</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <template v-for="t in tasks" :key="t.id">
            <tr>
              <td>{{ t.id }}</td>
              <td>{{ t.search_scope === "COMPANY" ? "按公司" : "按岗位" }}</td>
              <td>{{ t.time_window_hours }}h</td>
              <td class="kw-cell">
                <span v-if="t.job_keywords_json?.length">岗位: {{ t.job_keywords_json.join(", ") }}<br/></span>
                <span v-if="t.company_keywords_json?.length">公司: {{ t.company_keywords_json.join(", ") }}<br/></span>
                <span v-if="t.platforms_json?.length">平台: {{ t.platforms_json.join(", ") }}</span>
              </td>
              <td><span class="status-tag">{{ statusLabel[t.status] || t.status }}</span></td>
              <td>{{ t.progress }}%</td>
              <td class="counts">{{ t.found_url_count }} / {{ t.fetched_count }} / {{ t.extracted_count }} / {{ t.question_count }} / {{ t.approved_count }} / {{ t.failed_count }}</td>
              <td>{{ t.created_at?.slice(0, 16) || "" }}</td>
              <td class="actions">
                <button v-if="canRunSearch(t.status)" @click="handleRunSearch(t)" :disabled="runningTaskId === t.id">
                  {{ runningTaskId === t.id ? "搜索中..." : "执行搜索" }}
                </button>
                <button @click="toggleSources(t.id)">
                  {{ expandedTaskId === t.id ? "收起来源" : "查看来源" }}
                </button>
              </td>
            </tr>
            <tr v-if="expandedTaskId === t.id" class="source-row">
              <td colspan="9">
                <div class="sources-panel">
                  <div class="sources-head">
                    <strong>发现来源</strong>
                    <span>{{ sourcesLoading ? "加载中..." : `共 ${sourceTotal} 条` }}</span>
                  </div>
                  <table v-if="sourceItems.length" class="source-table">
                    <thead>
                      <tr><th>标题</th><th>URL</th><th>平台</th><th>状态</th><th>错误信息</th><th>创建时间</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="item in sourceItems" :key="item.id">
                        <td>{{ item.title || "-" }}</td>
                        <td class="url-cell"><a :href="item.source_url" target="_blank" rel="noopener noreferrer">{{ item.source_url }}</a></td>
                        <td>{{ item.platform || "-" }}</td>
                        <td>{{ item.fetch_status }}</td>
                        <td>{{ item.error_message || "-" }}</td>
                        <td>{{ item.created_at?.slice(0, 16) || "" }}</td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-else class="empty compact">{{ sourcesLoading ? "加载中..." : "暂无来源 URL" }}</p>
                </div>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <p v-else class="empty">暂无任务，点击"创建采集任务"开始。</p>
    </div>

    <!-- ====== Keywords Tab ====== -->
    <div v-if="activeTab === 'keywords'">
      <div class="tabs">
        <button :class="{ active: !filterType }" @click="filterType = ''; loadKeywords()">全部 ({{ kwTotal }})</button>
        <button v-for="t in types" :key="t" :class="{ active: filterType === t }" @click="filterType = t; loadKeywords()">{{ typeLabel[t] || t }}</button>
      </div>
      <button class="btn-add" @click="openCreate">+ 新增关键词</button>

      <table class="kw-table" v-if="keywords.length">
        <thead><tr><th>类型</th><th>名称</th><th>别名</th><th>启用</th><th>操作</th></tr></thead>
        <tbody>
          <tr v-for="kw in keywords" :key="kw.id" :class="{ disabled: !kw.enabled }">
            <td>{{ typeLabel[kw.preset_type] || kw.preset_type }}</td>
            <td>{{ kw.name }}</td>
            <td>{{ (kw.aliases_json || []).join(", ") }}</td>
            <td><span :class="kw.enabled ? 'enabled' : 'disabled-tag'">{{ kw.enabled ? "是" : "否" }}</span></td>
            <td class="actions">
              <button @click="openEdit(kw)">编辑</button>
              <button @click="handleToggle(kw)">{{ kw.enabled ? "禁用" : "启用" }}</button>
              <button class="btn-del" @click="handleDelete(kw.id)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty">暂无关键词。</p>
    </div>

    <!-- KW Form modal -->
    <div v-if="showForm" class="modal-overlay" @click.self="closeForm">
      <div class="modal">
        <h3>{{ editingId ? "编辑关键词" : "新增关键词" }}</h3>
        <label>类型 <select v-model="kwForm.preset_type" :disabled="!!editingId"><option v-for="t in types" :key="t" :value="t">{{ typeLabel[t] || t }}</option></select></label>
        <label>名称 <input v-model="kwForm.name" placeholder="例如: 快手" /></label>
        <label>别名（逗号分隔）<input v-model="kwForm.aliases_text" placeholder="例如: 快手电商, Kuaishou" /></label>
        <label class="checkbox-label"><input type="checkbox" v-model="kwForm.enabled" /> 启用</label>
        <div class="modal-actions"><button @click="handleSave">保存</button><button class="btn-cancel" @click="closeForm">取消</button></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-experiences { max-width: 1000px; margin: 0 auto; padding: 24px; }
.desc { color: #666; margin-bottom: 16px; }
.main-tabs { display: flex; gap: 8px; margin-bottom: 20px; border-bottom: 2px solid #eee; padding-bottom: 8px; }
.main-tabs button { padding: 8px 20px; border: none; background: none; cursor: pointer; font-size: 15px; font-weight: 500; color: #666; border-bottom: 2px solid transparent; margin-bottom: -10px; }
.main-tabs button.active { color: #409eff; border-bottom-color: #409eff; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tabs button { padding: 6px 16px; border: 1px solid #ddd; border-radius: 6px; background: #fff; cursor: pointer; }
.tabs button.active { background: #409eff; color: #fff; border-color: #409eff; }
.btn-add { padding: 8px 20px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; margin-bottom: 16px; }
.btn-add:disabled { opacity: 0.6; }
.kw-table { width: 100%; border-collapse: collapse; margin-top: 12px; }
.kw-table th, .kw-table td { padding: 10px 12px; border-bottom: 1px solid #eee; text-align: left; font-size: 13px; }
.kw-table tr.disabled { opacity: 0.5; }
.enabled { color: #67c23a; font-weight: 600; }
.disabled-tag { color: #f56c6c; }
.actions { display: flex; gap: 6px; }
.actions button { padding: 4px 10px; border: 1px solid #ddd; border-radius: 4px; background: #fff; cursor: pointer; font-size: 13px; }
.btn-del { color: #f56c6c; border-color: #f56c6c !important; }
.empty { color: #999; text-align: center; padding: 40px; }
.status-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; background: #ecf5ff; color: #409eff; }
.kw-cell { font-size: 12px; line-height: 1.5; max-width: 200px; }
.counts { font-size: 11px; color: #666; }
.source-row > td { background: #fbfcff; padding: 0 12px 12px; }
.sources-panel { border: 1px solid #e5e7eb; border-radius: 6px; padding: 12px; }
.sources-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; color: #555; }
.source-table { width: 100%; border-collapse: collapse; }
.source-table th, .source-table td { padding: 8px; border-bottom: 1px solid #eee; text-align: left; font-size: 12px; vertical-align: top; }
.url-cell { max-width: 320px; word-break: break-all; }
.url-cell a { color: #409eff; }
.compact { padding: 12px; }

.task-form { background: #f8f9fc; border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px; margin-bottom: 16px; }
.form-row { display: flex; gap: 16px; margin-bottom: 12px; flex-wrap: wrap; }
.form-row label { font-size: 13px; flex: 1; min-width: 150px; }
.form-row input, .form-row select { width: 100%; padding: 6px 8px; border: 1px solid #ddd; border-radius: 4px; margin-top: 4px; }
.checks { gap: 24px; }
.checks label { display: flex; align-items: center; gap: 6px; flex: none; }
.checks input { width: auto; }

.modal-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.4); display: flex; align-items: center; justify-content: center; z-index: 100; }
.modal { background: #fff; border-radius: 12px; padding: 24px; width: 420px; max-width: 90vw; }
.modal h3 { margin-bottom: 16px; }
.modal label { display: block; margin-bottom: 12px; font-size: 14px; }
.modal input, .modal select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 6px; margin-top: 4px; }
.checkbox-label { display: flex; align-items: center; gap: 8px; }
.checkbox-label input { width: auto; }
.modal-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 16px; }
.modal-actions button { padding: 8px 20px; border: none; border-radius: 6px; cursor: pointer; }
.modal-actions button:first-child { background: #409eff; color: #fff; }
.btn-cancel { background: #f0f0f0 !important; color: #333 !important; }
.kw-select { margin-bottom: 12px; }
.kw-select > label { font-size: 13px; font-weight: 600; display: block; margin-bottom: 6px; }
.checkbox-group { display: flex; flex-wrap: wrap; gap: 6px; }
.cb-label { display: flex; align-items: center; gap: 4px; font-size: 13px; padding: 4px 10px; border: 1px solid #e5e7eb; border-radius: 6px; cursor: pointer; }
.cb-label input { width: auto; }
</style>
