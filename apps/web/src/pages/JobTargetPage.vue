<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import {
  confirmTarget, createTarget, deleteTarget, getTarget, listTargets, matchTarget, parseTarget, updateRequirements,
} from '../api/job-target'
import type { JobTarget, JobTargetMatch, JobTargetRequirement } from '../types/job-target'
import { evidenceLabels, importanceLabels, statusLabels } from '../types/job-target'

const router = useRouter()
const targets = ref<JobTarget[]>([])
const current = ref<JobTarget | null>(null)
const match = ref<JobTargetMatch | null>(null)
const busy = ref(false)
const error = ref('')
const notice = ref('')

const rawText = ref('')
const companyName = ref('')
const jobTitle = ref('')
const sourceReference = ref('')
const draft = ref<{ statement: string; importance: JobTargetRequirement['importance'] }[]>([])

const message = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请稍后重试'
const canCreate = computed(() => rawText.value.trim().length >= 80 && !busy.value)
const confirmed = computed(() => current.value?.parse_status === 'CONFIRMED')

async function load() {
  busy.value = true; error.value = ''
  try { targets.value = await listTargets() } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function open(id: number) {
  busy.value = true; error.value = ''; notice.value = ''; match.value = null
  try {
    const target = await getTarget(id)
    current.value = target
    draft.value = (target.requirements || []).map(row => ({ statement: row.statement, importance: row.importance }))
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function create() {
  if (!canCreate.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const created = await createTarget({
      raw_text: rawText.value.trim(),
      company_name: companyName.value.trim() || undefined,
      job_title: jobTitle.value.trim() || undefined,
      source_reference: sourceReference.value.trim() || undefined,
    })
    rawText.value = ''; companyName.value = ''; jobTitle.value = ''; sourceReference.value = ''
    await load()
    await open(created.id)
    notice.value = created.reused
      ? `这段 JD 与已有目标内容相同，已打开原来的目标（第 ${created.version} 版），没有新建重复岗位。`
      : '已保存。下一步解析岗位要求。'
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function parse() {
  if (!current.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    const parsed = await parseTarget(current.value.id)
    current.value = parsed
    draft.value = (parsed.requirements || []).map(row => ({ statement: row.statement, importance: row.importance }))
    notice.value = parsed.parse_mode === 'RULE'
      ? '当前为规则解析（未使用模型），结果只按原文分行归类，请逐条核对。'
      : parsed.parse_status === 'FAILED' ? (parsed.parse_error || '未识别到要求，请手动添加。') : '解析完成，请核对后确认。'
    await load()
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function save() {
  if (!current.value) return
  const rows = draft.value.filter(row => row.statement.trim()).map(row => ({
    statement: row.statement.trim(), importance: row.importance,
  }))
  if (!rows.length) { error.value = '至少保留一条岗位要求'; return }
  busy.value = true; error.value = ''; notice.value = ''
  try {
    current.value = await updateRequirements(current.value.id, rows)
    draft.value = (current.value.requirements || []).map(row => ({ statement: row.statement, importance: row.importance }))
    notice.value = `要求已保存在第 ${current.value.version} 版草稿中；再次确认会生成第 ${current.value.version + 1} 版，已开始的面试仍使用原冻结版本。`
    await load()
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function confirm() {
  if (!current.value) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    current.value = await confirmTarget(current.value.id)
    notice.value = `已确认第 ${current.value.version} 版；可用于创建面试，后续修改不会影响已开始的面试。`
    await load()
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function remove() {
  if (!current.value) return
  if (!window.confirm('删除后不可恢复；已被面试引用的目标不能删除。继续？')) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await deleteTarget(current.value.id)
    current.value = null; draft.value = []; match.value = null
    notice.value = '已删除。'
    await load()
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function inspect() {
  if (!current.value) return
  busy.value = true; error.value = ''
  try { match.value = await matchTarget(current.value.id) } catch (e) { error.value = message(e) } finally { busy.value = false }
}

function addRow() { draft.value.push({ statement: '', importance: 'UNKNOWN' }) }
function dropRow(index: number) { draft.value.splice(index, 1) }
function useInInterview() { if (current.value) router.push({ path: '/interview', query: { job_target_id: String(current.value.id) } }) }
function useInTraining() { if (current.value) router.push({ path: '/training', query: { job_target_id: String(current.value.id) } }) }

load()
</script>

<template>
  <div class="page">
    <h2>我的目标岗位</h2>
    <p role="status">这里保存你从招聘网站或公司官网复制的 JD。内容只对你可见，不会发布为企业官方岗位或公共面经；面试只使用你确认过的版本。</p>
    <p v-if="error" class="ux-empty" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>

    <section class="panel">
      <h3>粘贴新的岗位描述</h3>
      <label>JD 原文（至少 80 字，最多 20000 字）
        <textarea v-model="rawText" rows="10" maxlength="20000" style="width:100%" aria-label="JD 原文"
          placeholder="把岗位职责与任职要求完整粘贴到这里。系统只会提取原文里写到的内容。"></textarea>
      </label>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <label>公司（可留空）<input v-model="companyName" maxlength="255" aria-label="公司名称"></label>
        <label>岗位名称（可留空）<input v-model="jobTitle" maxlength="255" aria-label="岗位名称"></label>
        <label>来源链接（可选）<input v-model="sourceReference" maxlength="500" aria-label="来源链接"></label>
      </div>
      <button :disabled="!canCreate" @click="create">{{ busy ? '处理中…' : '保存为我的目标岗位' }}</button>
      <small>来源标记固定为「用户提供（未经企业核实）」，不会记为 Research 已核实或企业官方发布。</small>
    </section>

    <section class="panel">
      <h3>已有目标岗位（{{ targets.length }}）</h3>
      <p v-if="!targets.length">还没有目标岗位。粘贴一份 JD 开始。</p>
      <ul>
        <li v-for="item in targets" :key="item.id">
          <button @click="open(item.id)">{{ item.company_name }} · {{ item.job_title }}</button>
          <span> {{ item.source_label }} · {{ statusLabels[item.parse_status] }} · {{ item.requirement_count }} 条要求 · 第 {{ item.version }} 版</span>
        </li>
      </ul>
    </section>

    <section v-if="current" class="panel">
      <h3>{{ current.company_name }} · {{ current.job_title }}</h3>
      <p>{{ current.source_label }} · {{ statusLabels[current.parse_status] }}
        <template v-if="current.parse_mode"> · {{ current.parse_mode === 'RULE' ? '规则解析' : '模型解析' }}</template>
        <template v-if="current.parse_error"> · {{ current.parse_error }}</template>
      </p>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <button :disabled="busy || confirmed" @click="parse">解析岗位要求</button>
        <button :disabled="busy" @click="save">保存要求修改</button>
        <button :disabled="busy || confirmed || !draft.length" @click="confirm">确认用于面试</button>
        <button :disabled="busy" @click="inspect">查看材料证据</button>
        <button :disabled="busy" @click="useInInterview">用这个岗位开始面试</button>
        <button :disabled="busy || !confirmed" @click="useInTraining">用这个岗位开始训练</button>
        <button :disabled="busy" @click="remove">删除</button>
      </div>

      <h4>岗位要求（可修改、删除、补充）</h4>
      <table v-if="draft.length" style="width:100%">
        <thead><tr><th>要求</th><th>性质</th><th></th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in draft" :key="index">
            <td><input v-model="row.statement" maxlength="300" style="width:100%" :aria-label="`要求 ${index + 1}`"></td>
            <td>
              <select v-model="row.importance" :aria-label="`要求 ${index + 1} 性质`">
                <option value="REQUIRED">{{ importanceLabels.REQUIRED }}</option>
                <option value="PREFERRED">{{ importanceLabels.PREFERRED }}</option>
                <option value="UNKNOWN">{{ importanceLabels.UNKNOWN }}</option>
              </select>
            </td>
            <td><button @click="dropRow(index)">删除</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else>尚未解析出要求；可以手动添加。</p>
      <button @click="addRow">+ 添加一条要求</button>

      <details>
        <summary>查看 JD 原文</summary>
        <pre style="white-space:pre-wrap">{{ current.raw_text }}</pre>
      </details>

      <div v-if="match">
        <h4>材料证据（不是匹配分数）</h4>
        <p>{{ match.disclaimer }}</p>
        <p>按「要求」的最终证据等级：有材料支持 {{ match.counts.SUPPORTED || 0 }} · 部分支持 {{ match.counts.PARTIAL || 0 }} · 无证据 {{ match.counts.NO_EVIDENCE || 0 }}</p>
        <p v-if="match.support_counts">
          按引用到的「材料事实」：SUPPORTED {{ match.support_counts.SUPPORTED || 0 }} 条 · PARTIAL {{ match.support_counts.PARTIAL || 0 }} 条。
          一条要求只要有一条强证据就整体记为「有材料支持」，所以两处数字不必相等。
        </p>
        <ul>
          <li v-for="row in match.rows" :key="row.id">
            <strong>{{ row.statement }}</strong> — {{ evidenceLabels[row.status] }}
            <div v-for="(support, i) in row.supports.slice(0, 2)" :key="i"><small>{{ support.explanation }}</small></div>
          </li>
        </ul>
      </div>
    </section>
  </div>
</template>
