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
  type ExperienceCollectionTask,
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
const showTaskForm = ref(false)
const taskForm = ref({
  time_window_hours: 24,
  job_keywords_text: "",
  company_keywords_text: "",
  platforms_text: "",
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
  { label: "自定义", value: 0 },
]

async function loadTasks() {
  const res = await listExperienceTasks()
  tasks.value = res.items || []
  taskTotal.value = res.total || 0
}

async function handleCreateTask() {
  if (!taskForm.value.job_keywords_text && !taskForm.value.company_keywords_text && !taskForm.value.platforms_text) {
    alert("请至少填写一个关键词字段")
    return
  }
  creating.value = true
  try {
    await createExperienceTask({
      time_window_hours: taskForm.value.time_window_hours,
      job_keywords_json: taskForm.value.job_keywords_text.split(/[,，、]/).map(s => s.trim()).filter(s => s),
      company_keywords_json: taskForm.value.company_keywords_text.split(/[,，、]/).map(s => s.trim()).filter(s => s),
      platforms_json: taskForm.value.platforms_text.split(/[,，、]/).map(s => s.trim()).filter(s => s),
      max_results: taskForm.value.max_results,
      review_mode: taskForm.value.review_mode,
      write_to_question_db: taskForm.value.write_to_question_db,
      write_to_vector_index: taskForm.value.write_to_vector_index,
      update_public_summary: taskForm.value.update_public_summary,
    })
    showTaskForm.value = false
    await loadTasks()
    alert("任务已创建，等待后续搜索执行")
  } catch (e: any) { alert(e?.message || "创建失败") }
  finally { creating.value = false }
}

const statusLabel: Record<string, string> = {
  PENDING: "待执行", SEARCHING: "搜索中", FETCHING: "抓取中", EXTRACTING: "抽取中",
  ROUTING: "分类中", SCORING: "评分中", DEDUPING: "去重中", WAITING_REVIEW: "待审核",
  APPROVED: "已通过", INDEXING: "索引入库", COMPLETED: "已完成", FAILED: "失败",
}

onMounted(() => { loadTasks(); loadKeywords() })
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
          <label>时间范围
            <select v-model.number="taskForm.time_window_hours">
              <option v-for="p in timePresets" :key="p.value" :value="p.value">{{ p.label }}</option>
            </select>
          </label>
          <input v-if="taskForm.time_window_hours === 0" v-model.number="taskForm.time_window_hours" placeholder="自定义小时数" type="number" min="1" />
          <label>最大结果数 <input v-model.number="taskForm.max_results" type="number" min="1" max="100" /></label>
          <label>审核模式
            <select v-model="taskForm.review_mode">
              <option value="MANUAL">人工审核</option>
              <option value="AUTO_PUBLISH">自动发布</option>
            </select>
          </label>
        </div>
        <div class="form-row">
          <label>岗位关键词 <input v-model="taskForm.job_keywords_text" placeholder="Java, 后端（逗号分隔）" /></label>
          <label>公司关键词 <input v-model="taskForm.company_keywords_text" placeholder="腾讯, 字节（逗号分隔）" /></label>
          <label>平台 <input v-model="taskForm.platforms_text" placeholder="牛客, 全网（逗号分隔）" /></label>
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
            <th>时间</th>
            <th>岗位/公司/平台</th>
            <th>状态</th>
            <th>进度</th>
            <th>发现/抓取/抽取/问题/通过/失败</th>
            <th>创建时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="t in tasks" :key="t.id">
            <td>{{ t.id }}</td>
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
          </tr>
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
</style>
