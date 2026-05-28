<script setup lang="ts">
import { ref, onMounted } from "vue"
import {
  listExperienceKeywords,
  createExperienceKeyword,
  updateExperienceKeyword,
  deleteExperienceKeyword,
  type ExperienceKeywordPreset,
} from "../api/admin"

const keywords = ref<ExperienceKeywordPreset[]>([])
const total = ref(0)
const filterType = ref("")
const showForm = ref(false)
const editingId = ref<number | null>(null)

const form = ref({
  preset_type: "COMPANY",
  name: "",
  aliases_text: "",
  enabled: true,
})

const typeLabel: Record<string, string> = { COMPANY: "公司", JOB: "岗位", PLATFORM: "平台" }
const types = ["COMPANY", "JOB", "PLATFORM"]

async function load() {
  const params: any = {}
  if (filterType.value) params.preset_type = filterType.value
  const res = await listExperienceKeywords(params)
  keywords.value = res.items || []
  total.value = res.total || 0
}

function openCreate() {
  editingId.value = null
  form.value = { preset_type: "COMPANY", name: "", aliases_text: "", enabled: true }
  showForm.value = true
}

function openEdit(kw: ExperienceKeywordPreset) {
  editingId.value = kw.id
  form.value = {
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

async function handleSave() {
  const aliases = form.value.aliases_text
    .split(/[,，、]/)
    .map(s => s.trim())
    .filter(s => s)
  const data = {
    preset_type: form.value.preset_type,
    name: form.value.name,
    aliases_json: aliases,
    enabled: form.value.enabled,
  }
  if (editingId.value) {
    await updateExperienceKeyword(editingId.value, data)
  } else {
    await createExperienceKeyword(data)
  }
  closeForm()
  await load()
}

async function handleToggle(kw: ExperienceKeywordPreset) {
  await updateExperienceKeyword(kw.id, { enabled: !kw.enabled })
  await load()
}

async function handleDelete(id: number) {
  if (!confirm("确认删除？")) return
  await deleteExperienceKeyword(id)
  await load()
}

onMounted(load)
</script>

<template>
  <div class="admin-experiences">
    <h2>面经更新管理</h2>
    <p class="desc">维护用于面经搜索的公司、岗位和平台关键词。后续采集任务会根据这些关键词组合搜索真实网页。</p>

    <!-- Filter tabs -->
    <div class="tabs">
      <button :class="{ active: !filterType }" @click="filterType = ''; load()">全部 ({{ total }})</button>
      <button v-for="t in types" :key="t" :class="{ active: filterType === t }" @click="filterType = t; load()">
        {{ typeLabel[t] || t }}
      </button>
    </div>

    <button class="btn-add" @click="openCreate">+ 新增关键词</button>

    <!-- Table -->
    <table class="kw-table" v-if="keywords.length">
      <thead>
        <tr>
          <th>类型</th>
          <th>名称</th>
          <th>别名</th>
          <th>启用</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="kw in keywords" :key="kw.id" :class="{ disabled: !kw.enabled }">
          <td>{{ typeLabel[kw.preset_type] || kw.preset_type }}</td>
          <td>{{ kw.name }}</td>
          <td>{{ (kw.aliases_json || []).join(", ") }}</td>
          <td>
            <span :class="kw.enabled ? 'enabled' : 'disabled-tag'">
              {{ kw.enabled ? "是" : "否" }}
            </span>
          </td>
          <td class="actions">
            <button @click="openEdit(kw)">编辑</button>
            <button @click="handleToggle(kw)">{{ kw.enabled ? "禁用" : "启用" }}</button>
            <button class="btn-del" @click="handleDelete(kw.id)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
    <p v-else class="empty">暂无关键词，点击"新增关键词"开始。</p>

    <!-- Form modal -->
    <div v-if="showForm" class="modal-overlay" @click.self="closeForm">
      <div class="modal">
        <h3>{{ editingId ? "编辑关键词" : "新增关键词" }}</h3>
        <label>
          类型
          <select v-model="form.preset_type" :disabled="!!editingId">
            <option v-for="t in types" :key="t" :value="t">{{ typeLabel[t] || t }}</option>
          </select>
        </label>
        <label>
          名称
          <input v-model="form.name" placeholder="例如: 快手" />
        </label>
        <label>
          别名（逗号分隔）
          <input v-model="form.aliases_text" placeholder="例如: 快手电商, Kuaishou" />
        </label>
        <label class="checkbox-label">
          <input type="checkbox" v-model="form.enabled" /> 启用
        </label>
        <div class="modal-actions">
          <button @click="handleSave">保存</button>
          <button class="btn-cancel" @click="closeForm">取消</button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.admin-experiences { max-width: 1000px; margin: 0 auto; padding: 24px; }
.desc { color: #666; margin-bottom: 16px; }
.tabs { display: flex; gap: 8px; margin-bottom: 16px; }
.tabs button { padding: 6px 16px; border: 1px solid #ddd; border-radius: 6px; background: #fff; cursor: pointer; }
.tabs button.active { background: #409eff; color: #fff; border-color: #409eff; }
.btn-add { padding: 8px 20px; background: #409eff; color: #fff; border: none; border-radius: 6px; cursor: pointer; margin-bottom: 16px; }
.kw-table { width: 100%; border-collapse: collapse; }
.kw-table th, .kw-table td { padding: 10px 12px; border-bottom: 1px solid #eee; text-align: left; }
.kw-table tr.disabled { opacity: 0.5; }
.enabled { color: #67c23a; font-weight: 600; }
.disabled-tag { color: #f56c6c; }
.actions { display: flex; gap: 6px; }
.actions button { padding: 4px 10px; border: 1px solid #ddd; border-radius: 4px; background: #fff; cursor: pointer; font-size: 13px; }
.btn-del { color: #f56c6c; border-color: #f56c6c !important; }
.empty { color: #999; text-align: center; padding: 40px; }

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
