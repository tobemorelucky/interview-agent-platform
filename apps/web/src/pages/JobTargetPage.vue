<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ApiError } from '../api/client'
import {
  archiveTarget, confirmTarget, createTarget, deleteTarget, getTarget, listTargets, matchTarget,
  parseTarget, restoreTarget, skillRecommendations, updateRequirements,
} from '../api/job-target'
import type {
  JobTarget, JobTargetMatch, JobTargetRequirement, RequirementBasis, SkillEvidenceHit,
  SkillRecommendationCandidate, TargetSkillRecommendation,
} from '../types/job-target'
import {
  basisLabels, evidenceLabels, evidenceStatusLabels, importanceLabels,
  recommendationEmptyLabels, recommendationStatusLabels, statusLabels,
} from '../types/job-target'

const router = useRouter()
const targets = ref<JobTarget[]>([])
const current = ref<JobTarget | null>(null)
const match = ref<JobTargetMatch | null>(null)
const skillRec = ref<TargetSkillRecommendation | null>(null)
const skillRecError = ref('')
const busy = ref(false)
const error = ref('')
const notice = ref('')
// A late recommendation response must never replace a newer JD view.
let skillRecSequence = 0

const rawText = ref('')
const companyName = ref('')
const jobTitle = ref('')
const sourceReference = ref('')
const draft = ref<{ statement: string; importance: JobTargetRequirement['importance']; basis?: RequirementBasis }[]>([])

const message = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请稍后重试'
const canCreate = computed(() => rawText.value.trim().length >= 80 && !busy.value)
const confirmed = computed(() => current.value?.parse_status === 'CONFIRMED')

const ARCHIVE_NOTICE =
  '归档后将无法用于新的面试或训练，但历史记录及相关数据不会因此被彻底删除。'

async function load() {
  busy.value = true; error.value = ''
  // The management view includes archived rows (badge + restore); new business
  // gates live on the server, so hiding them here would only be cosmetic.
  try { targets.value = await listTargets(true) } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function handleArchive(id: number) {
  if (!window.confirm(ARCHIVE_NOTICE + ' 确定归档这个目标岗位吗？')) return
  try {
    await archiveTarget(id)
    if (current.value?.id === id) current.value = null
    await load()
  } catch (e) { error.value = message(e) }
}

async function handleRestore(id: number) {
  try {
    await restoreTarget(id)
    await load()
    // Re-open so the archived panel and the read-only state disappear at once.
    await open(id)
  } catch (e) { error.value = message(e) }
}

async function loadSkillRecommendation(id: number) {
  const sequence = ++skillRecSequence
  skillRecError.value = ''
  try {
    const rec = await skillRecommendations(id)
    if (sequence === skillRecSequence) skillRec.value = rec
  } catch (e) {
    // The manual Skill dropdown stays usable if the advisory read fails.
    if (sequence === skillRecSequence) { skillRec.value = null; skillRecError.value = message(e) }
  }
}

async function open(id: number) {
  busy.value = true; error.value = ''; notice.value = ''; match.value = null
  skillRec.value = null; skillRecError.value = ''; skillRecSequence++
  try {
    const target = await getTarget(id)
    current.value = target
    draft.value = (target.requirements || []).map(row => ({ statement: row.statement, importance: row.importance, basis: row.basis }))
    if (target.parse_status === 'CONFIRMED' && !target.archived_at) void loadSkillRecommendation(id)
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
    draft.value = (parsed.requirements || []).map(row => ({ statement: row.statement, importance: row.importance, basis: row.basis }))
    // Re-parsing returns to a draft; an older recommendation must not linger.
    skillRec.value = null; skillRecError.value = ''; skillRecSequence++
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
    draft.value = (current.value.requirements || []).map(row => ({ statement: row.statement, importance: row.importance, basis: row.basis }))
    // Editing requirements invalidates the previous recommendation input key.
    skillRec.value = null; skillRecError.value = ''; skillRecSequence++
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
    await loadSkillRecommendation(current.value.id)
  } catch (e) { error.value = message(e) } finally { busy.value = false }
}

async function remove() {
  if (!current.value) return
  if (!window.confirm('删除后不可恢复；已被面试引用的目标不能删除。继续？')) return
  busy.value = true; error.value = ''; notice.value = ''
  try {
    await deleteTarget(current.value.id)
    current.value = null; draft.value = []; match.value = null
    skillRec.value = null; skillRecError.value = ''; skillRecSequence++
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
// Same destination as the detail button, reachable without opening the target.
function startTraining(id: number) { router.push({ path: '/training', query: { job_target_id: String(id) } }) }

function uniqueEvidence(rows: SkillEvidenceHit[]) {
  const seen = new Set<string>()
  return rows.filter(row => {
    // One confirmed requirement / duty is shown once, even if several rule
    // terms matched it; the quoted text, not the matched term, is the evidence.
    const key = row.verbatim_jd_evidence
      ? `verbatim|${row.verbatim_jd_evidence}`
      : `${row.field}|${row.requirement_id}|${row.requirement_statement || row.excerpt}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  }).slice(0, 6)
}

/** One click only *preselects* a direction; neither page creates a session on arrival. */
function useRecommendedSkill(candidate: SkillRecommendationCandidate, destination: 'interview' | 'training') {
  if (!current.value || !skillRec.value) return
  const isPrimary = skillRec.value.auto_select && candidate.skill_id === skillRec.value.recommended_skill_id
  router.push({
    path: destination === 'interview' ? '/interview' : '/training',
    query: {
      job_target_id: String(current.value.id),
      recommended_skill_id: candidate.skill_id,
      recommended_skill_version: candidate.version,
      recommendation_key: skillRec.value.input_fingerprint,
      recommendation_target_version: String(current.value.version),
      // Only the rule's clear winner is marked auto; any other direction is an
      // explicit user choice and must never be silently re-ranked later.
      recommendation_source: isPrimary ? 'AUTO_RECOMMENDED' : 'USER_SELECTED',
    },
  })
}

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
          <!-- A confirmed target can go straight to the training centre; an
               unconfirmed one says why instead of offering a button that the
               server would refuse. Own line, so the reason does not run into
               the version text. -->
          <div v-if="item.archived_at">
            <span class="badge-archived" title="归档后不能用于新的面试或训练；历史记录不会被删除">已归档</span>
            <button :disabled="busy" @click="handleRestore(item.id)">恢复</button>
          </div>
          <div v-else-if="item.parse_status === 'CONFIRMED'">
            <button :disabled="busy" :aria-label="`用 ${item.company_name} ${item.job_title} 开始训练`"
              @click="startTraining(item.id)">开始训练</button>
            <button :disabled="busy" @click="handleArchive(item.id)">归档</button>
          </div>
          <div v-else>
            <small>确认 JD 后可开始训练（当前为{{ statusLabels[item.parse_status] }}）</small>
            <button :disabled="busy" @click="handleArchive(item.id)">归档</button>
          </div>
        </li>
      </ul>
    </section>

    <section v-if="current" class="panel">
      <h3>{{ current.company_name }} · {{ current.job_title }}</h3>
      <p>{{ current.source_label }} · {{ statusLabels[current.parse_status] }}
        <template v-if="current.parse_mode"> · {{ current.parse_mode === 'RULE' ? '规则解析' : '模型解析' }}</template>
        <template v-if="current.parse_error"> · {{ current.parse_error }}</template>
      </p>
      <div v-if="current.archived_at" class="archived-panel" role="status">
        <span class="badge-archived">已归档</span>
        <p>归档后将无法用于新的面试或训练，但历史记录及相关数据不会因此被彻底删除。
          你仍可以查看原文与要求；历史会话继续使用它们当时冻结的版本。</p>
        <button :disabled="busy" @click="handleRestore(current.id)">恢复使用</button>
      </div>
      <div v-else style="display:flex;gap:8px;flex-wrap:wrap">
        <button :disabled="busy || confirmed" @click="parse">解析岗位要求</button>
        <button :disabled="busy" @click="save">保存要求修改</button>
        <button :disabled="busy || confirmed || !draft.length" @click="confirm">确认用于面试</button>
        <button :disabled="busy" @click="inspect">查看材料证据</button>
        <button :disabled="busy" @click="useInInterview">用这个岗位开始面试</button>
        <button :disabled="busy || !confirmed" @click="useInTraining">用这个岗位开始训练</button>
        <button :disabled="busy" @click="remove">删除</button>
        <button :disabled="busy" class="btn-archive" @click="handleArchive(current.id)">归档</button>
      </div>

      <section v-if="confirmed && !current.archived_at" class="skill-rec" aria-label="私有 JD 的推荐专业方向">
        <h4>推荐的专业方向（规则匹配 · 0 次模型调用）</h4>
        <p v-if="skillRecError" role="alert">推荐读取失败：{{ skillRecError }}。你仍可在面试或训练页手动选择专业 Skill。</p>
        <template v-if="skillRec">
          <p>{{ recommendationStatusLabels[skillRec.status] || skillRec.status }} · {{ skillRec.message }}</p>
          <p v-if="skillRec.ambiguity_reason === 'TITLE_CONFLICTS_WITH_REQUIREMENTS'" role="status">
            <small>岗位名称与要求指向不同方向：系统不替你决定，请核对下面的原文证据后手动选择。</small>
          </p>
          <p v-else-if="skillRec.ambiguity_reason === 'TITLE_ONLY_LEADER'" role="status">
            <small>只有岗位名称对得上：岗位职责与要求里缺少该方向的依据，不按标题自动选择。</small>
          </p>
          <p><small>{{ skillRec.ranking_semantics }}</small></p>
          <p><small>{{ skillRec.confidence_note }}</small></p>
          <p v-if="!skillRec.candidates.filter(c => c.eligible).length">
            {{ recommendationEmptyLabels[skillRec.empty_reason || ''] || '当前没有可用的推荐方向，请手动选择专业 Skill。' }}
          </p>
          <ul class="skill-list">
            <li v-for="c in skillRec.candidates.filter(c => c.eligible)" :key="c.skill_id">
              <div><strong>{{ c.label }}</strong>
                <span> {{ c.skill_id }} · v{{ c.version }} · 第 {{ c.rank }} 位 · 命中权重 {{ c.ranking_points }}（要求与职责 {{ c.content_points }} + 岗位名称 {{ c.title_points }}）</span>
                <span v-if="c.skill_id === skillRec.recommended_skill_id && skillRec.auto_select"> 系统首推</span>
                <span v-if="c.uncertain"> 证据不确定</span>
              </div>
              <p>{{ c.reason_note }}</p>
              <p><small>规则判定（算法按关键词加分，不是 JD 原文）：{{ c.matched_reason }}</small></p>
              <ul v-if="c.evidence.length" class="evidence-list">
                <li v-for="(e, i) in uniqueEvidence(c.evidence)" :key="i">
                  <small>{{ evidenceStatusLabels[e.evidence_status] || e.evidence_status }}：{{ e.verbatim_jd_evidence || e.requirement_statement || e.excerpt }}</small>
                </li>
              </ul>
              <div style="display:flex;gap:8px;flex-wrap:wrap">
                <button :disabled="busy" :aria-label="`用 ${c.label} 开始面试`" @click="useRecommendedSkill(c, 'interview')">用这个方向开始面试</button>
                <button :disabled="busy" :aria-label="`用 ${c.label} 开始训练`" @click="useRecommendedSkill(c, 'training')">用这个方向开始训练</button>
              </div>
            </li>
          </ul>
          <p><small>{{ skillRec.disclaimer }}</small></p>
          <p><small>手动更换：跳转后在「专业方向」下拉自行选择；你手动选中的方向不会被异步推荐结果覆盖。当前为 JD 第 {{ skillRec.target_version }} 版，推荐输入指纹 {{ skillRec.input_fingerprint.slice(0, 12) }}…；JD 更新后请重新查看推荐。</small></p>
        </template>
        <p v-else role="status">正在读取推荐方向…</p>
      </section>
      <section v-else class="skill-rec">
        <h4>推荐的专业方向</h4>
        <p>确认 JD 之后才提供正式推荐；未确认版本不提供推荐，也不会编造相关性。当前状态：{{ statusLabels[current.parse_status] }}。</p>
      </section>

      <h4 v-if="!current.archived_at">岗位要求（可修改、删除、补充）</h4>
      <h4 v-else>岗位要求（只读 · 已归档）</h4>
      <p v-if="!current.archived_at"><small>「性质」由服务端按 JD 原文判定：只有原文写了必须或最低门槛才是硬性要求；
        列在任职要求里但没写必须的仍记为「原文未标明」，并在下一行说明依据。</small></p>
      <table v-if="draft.length && !current.archived_at" style="width:100%">
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
              <div v-if="row.basis" class="basis-note"><small>{{ basisLabels[row.basis] || row.basis }}</small></div>
            </td>
            <td><button @click="dropRow(index)">删除</button></td>
          </tr>
        </tbody>
      </table>
      <p v-else-if="!current.archived_at">尚未解析出要求；可以手动添加。</p>
      <ul v-if="current.archived_at">
        <li v-for="row in draft" :key="row.statement">
          {{ row.statement }} — {{ importanceLabels[row.importance] }}<span
            v-if="row.basis">（{{ basisLabels[row.basis] || row.basis }}）</span>
        </li>
      </ul>
      <button v-if="!current.archived_at" @click="addRow">+ 添加一条要求</button>

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

<style scoped>
.skill-rec{border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:18px 0;background:#fafcff}
.skill-list,.evidence-list{list-style:none;padding:0}
.skill-list>li{padding:12px 0;border-bottom:1px solid #eef2f6}
.skill-list>li:last-child{border-bottom:0}
.skill-list strong{font-size:16px}
.skill-list span{color:#667085;font-size:13px}
.evidence-list li{padding:2px 0 2px 12px;border-left:2px solid #d6e4e1}
.skill-rec small{color:#667085}
.basis-note{margin-top:2px}
.basis-note small{color:#667085}
</style>
