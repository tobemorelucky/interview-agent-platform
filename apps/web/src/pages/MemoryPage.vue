<script setup lang="ts">
import { onMounted, ref } from "vue"
import {
  createMemoryItem,
  deleteMemoryItem,
  listMemoryItems,
  listSkillProfiles,
  type UserMemoryItem,
  type UserSkillProfile,
} from "../api/memory"

const memories = ref<UserMemoryItem[]>([])
const skills = ref<UserSkillProfile[]>([])
const total = ref(0)
const loading = ref(false)
const saving = ref(false)
const deletingId = ref<number | null>(null)
const filterType = ref("")
const keyword = ref("")

const form = ref({
  key: "interview_preference",
  content: "",
  importance: 0.6,
})

const memoryTypes = [
  { value: "", label: "全部" },
  { value: "SEMANTIC", label: "长期事实" },
  { value: "SKILL", label: "能力画像" },
  { value: "EPISODIC", label: "历史事件" },
  { value: "PROCEDURAL", label: "流程记忆" },
  { value: "PREFERENCE", label: "偏好" },
  { value: "SAFETY", label: "安全/同意" },
]

const typeLabel: Record<string, string> = {
  SEMANTIC: "长期事实",
  SKILL: "能力画像",
  EPISODIC: "历史事件",
  PROCEDURAL: "流程记忆",
  PREFERENCE: "偏好",
  SAFETY: "安全/同意",
}

async function loadMemories() {
  loading.value = true
  try {
    const params: Record<string, string | number> = { limit: 50 }
    if (filterType.value) params.memory_type = filterType.value
    if (keyword.value.trim()) params.keyword = keyword.value.trim()
    const res = await listMemoryItems(params)
    memories.value = res.items || []
    total.value = res.total || 0
  } finally {
    loading.value = false
  }
}

async function loadSkills() {
  const res = await listSkillProfiles()
  skills.value = res.items || []
}

async function handleCreatePreference() {
  if (!form.value.content.trim()) {
    alert("请输入偏好内容")
    return
  }
  saving.value = true
  try {
    await createMemoryItem({
      memory_type: "PREFERENCE",
      scope: "INTERVIEW",
      key: form.value.key || "interview_preference",
      content: form.value.content,
      importance: form.value.importance,
      confidence: 0.9,
      visibility: "PRIVATE",
    })
    form.value.content = ""
    await loadMemories()
  } catch (e: any) {
    alert(e?.message || "创建记忆失败")
  } finally {
    saving.value = false
  }
}

async function handleDelete(item: UserMemoryItem) {
  if (!confirm("确认删除这条记忆？")) return
  deletingId.value = item.id
  try {
    await deleteMemoryItem(item.id)
    await loadMemories()
  } catch (e: any) {
    alert(e?.message || "删除失败")
  } finally {
    deletingId.value = null
  }
}

function scorePercent(value: number) {
  return `${Math.round((value || 0) * 100)}%`
}

onMounted(() => {
  loadMemories()
  loadSkills()
})
</script>

<template>
  <div class="memory-page">
    <header class="page-head">
      <div>
        <h2>我的记忆</h2>
        <p>查看和管理系统为你保留的长期事实、偏好和能力画像。</p>
      </div>
    </header>

    <section class="panel create-panel">
      <div>
        <h3>新增偏好记忆</h3>
        <p>第一版只支持手动新增偏好，自动抽取会在后续阶段接入。</p>
      </div>
      <div class="create-form">
        <input v-model="form.key" placeholder="key，例如 interview_preference" />
        <textarea
          v-model="form.content"
          rows="3"
          placeholder="例如：希望面试官严格追问，并在我卡住后再给提示"
        />
        <div class="form-row">
          <label>
            重要性
            <input v-model.number="form.importance" type="range" min="0" max="1" step="0.1" />
            <span>{{ scorePercent(form.importance) }}</span>
          </label>
          <button class="primary-btn" :disabled="saving" @click="handleCreatePreference">
            {{ saving ? "保存中..." : "保存偏好" }}
          </button>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="section-title">
        <div>
          <h3>记忆列表</h3>
          <span>共 {{ total }} 条</span>
        </div>
        <div class="filters">
          <select v-model="filterType" @change="loadMemories">
            <option v-for="item in memoryTypes" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
          <input
            v-model="keyword"
            placeholder="搜索 key / 内容 / 摘要"
            @keyup.enter="loadMemories"
          />
          <button @click="loadMemories">搜索</button>
        </div>
      </div>

      <div v-if="loading" class="empty">加载中...</div>
      <div v-else-if="memories.length" class="memory-list">
        <article v-for="item in memories" :key="item.id" class="memory-item">
          <div class="memory-main">
            <div class="memory-meta">
              <span>{{ typeLabel[item.memory_type] || item.memory_type }}</span>
              <span>{{ item.scope }}</span>
              <span>{{ item.key || "无 key" }}</span>
              <span>confidence {{ scorePercent(item.confidence) }}</span>
              <span>importance {{ scorePercent(item.importance) }}</span>
            </div>
            <p class="memory-content">{{ item.content }}</p>
            <p v-if="item.summary" class="memory-summary">{{ item.summary }}</p>
          </div>
          <button class="danger-btn" :disabled="deletingId === item.id" @click="handleDelete(item)">
            {{ deletingId === item.id ? "删除中..." : "删除" }}
          </button>
        </article>
      </div>
      <div v-else class="empty">暂无记忆。</div>
    </section>

    <section class="panel">
      <div class="section-title">
        <div>
          <h3>技能画像</h3>
          <span>当前 {{ skills.length }} 项</span>
        </div>
      </div>
      <div v-if="skills.length" class="skill-grid">
        <article v-for="skill in skills" :key="skill.id" class="skill-item">
          <div class="skill-head">
            <strong>{{ skill.skill_name }}</strong>
            <span>{{ skill.skill_category || "未分类" }}</span>
          </div>
          <div class="score-bar">
            <div :style="{ width: scorePercent(skill.level_score) }"></div>
          </div>
          <p>掌握度 {{ scorePercent(skill.level_score) }} · 证据 {{ skill.evidence_count }}</p>
          <p v-if="skill.strength_summary">优势：{{ skill.strength_summary }}</p>
          <p v-if="skill.weakness_summary">薄弱：{{ skill.weakness_summary }}</p>
        </article>
      </div>
      <div v-else class="empty">暂无技能画像。后续面试 Agent 接入后会逐步生成。</div>
    </section>
  </div>
</template>

<style scoped>
.memory-page {
  max-width: 1120px;
  margin: 0 auto;
  color: #20242a;
}

.page-head {
  margin-bottom: 18px;
}

.page-head h2 {
  margin: 0;
  font-size: 24px;
}

.page-head p,
.create-panel p,
.section-title span,
.memory-summary,
.skill-item p {
  color: #667085;
}

.panel {
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 18px;
  margin-bottom: 18px;
}

.create-panel {
  display: grid;
  grid-template-columns: minmax(180px, 260px) 1fr;
  gap: 20px;
}

.panel h3 {
  margin: 0 0 6px;
  font-size: 16px;
}

.create-form {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

input,
textarea,
select {
  width: 100%;
  box-sizing: border-box;
  padding: 8px 10px;
  border: 1px solid #d0d5dd;
  border-radius: 6px;
  font: inherit;
}

.form-row,
.section-title,
.filters,
.memory-item,
.skill-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.form-row,
.section-title {
  justify-content: space-between;
}

.form-row label {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #475467;
  font-size: 13px;
}

.primary-btn,
.filters button {
  padding: 8px 16px;
  border: none;
  border-radius: 6px;
  background: #2e7d6f;
  color: #fff;
  cursor: pointer;
}

.danger-btn {
  padding: 6px 12px;
  border: 1px solid #fecdca;
  border-radius: 4px;
  background: #fff;
  color: #b42318;
  cursor: pointer;
}

button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.filters {
  min-width: 420px;
}

.filters select {
  max-width: 150px;
}

.memory-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.memory-item {
  justify-content: space-between;
  align-items: flex-start;
  padding: 12px 0;
  border-top: 1px solid #eef2f6;
}

.memory-item:first-child {
  border-top: none;
}

.memory-main {
  min-width: 0;
}

.memory-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}

.memory-meta span {
  padding: 3px 8px;
  background: #f2f4f7;
  border-radius: 999px;
  color: #475467;
  font-size: 12px;
}

.memory-content {
  margin: 0;
  line-height: 1.6;
}

.memory-summary {
  margin: 6px 0 0;
  line-height: 1.5;
}

.skill-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 12px;
}

.skill-item {
  border: 1px solid #eef2f6;
  border-radius: 8px;
  padding: 12px;
}

.skill-head {
  justify-content: space-between;
}

.skill-head span {
  color: #667085;
  font-size: 12px;
}

.score-bar {
  height: 8px;
  margin: 10px 0;
  overflow: hidden;
  background: #eef2f6;
  border-radius: 999px;
}

.score-bar div {
  height: 100%;
  background: #2e7d6f;
}

.empty {
  padding: 28px;
  text-align: center;
  color: #98a2b3;
}

@media (max-width: 820px) {
  .create-panel {
    grid-template-columns: 1fr;
  }

  .section-title,
  .filters,
  .memory-item,
  .form-row {
    align-items: stretch;
    flex-direction: column;
  }

  .filters {
    min-width: 0;
    width: 100%;
  }

  .filters select {
    max-width: none;
  }
}
</style>
