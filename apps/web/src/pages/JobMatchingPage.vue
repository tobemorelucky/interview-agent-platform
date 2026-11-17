<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { presentMatch } from '../types/match-presentation'
import { displayLabel, displayText, publicName, modelModeLabel, userError } from '../i18n/zh-CN'
import { useRoute, useRouter } from 'vue-router'
import { createMatch, generateMatch, getMatch, matchHistory } from '../api/job-matching'
import { ApiError } from '../api/client'
import { fitLabels, eligibilityLabels, interviewTarget } from '../types/job-matching'
import type { MatchRun, MatchHistory, MatchCandidate } from '../types/job-matching'

const route = useRoute(), router = useRouter()
const run = ref<MatchRun | null>(null), history = ref<MatchHistory[]>([])
const busy = ref(false), error = ref('')
const loading = ref(true)
const moreHistory = ref(false)
const locations = ref(''), campus = ref(''), family = ref(''), tags = ref(''), excluded = ref('')
const direction = ref('')
const showAll = ref(false)
const showMoreFilters = ref(false)
const csv = (s: string) => [...new Set(s.split(/[,，]/).map(v => v.trim()).filter(Boolean))]
const DIRECTIONS = [
  { key: '', label: '全部方向', match: /.*/ },
  { key: 'ai', label: 'AI / Agent', match: /AI|Agent|大模型|LLM|RAG|算法|机器学习|深度学习/i },
  { key: 'java', label: 'Java 后端', match: /Java|后端|服务端|Spring/i },
  { key: 'python', label: 'Python', match: /Python/i },
  { key: 'frontend', label: '前端', match: /前端|Vue|React|Web/i },
  { key: 'test', label: '测试', match: /测试|QA/i },
  { key: 'data', label: '数据', match: /数据|数仓|大数据/i },
]
const cards = computed(() => run.value?.candidates.map(candidate => ({
  candidate, presentation: presentMatch(candidate, run.value!.snapshot.profile.facts || [], run.value!.snapshot.profile.evidence),
})) || [])
const activeDirection = computed(() => DIRECTIONS.find(d => d.key === direction.value) || DIRECTIONS[0])
const visibleCards = computed(() => {
  const list = cards.value.filter(card => activeDirection.value.match.test(`${card.candidate.title} ${card.candidate.evidence_matrix.map(r => r.statement).join(' ')}`))
  return showAll.value ? list : list.slice(0, 6)
})
const hiddenCount = computed(() => cards.value.filter(card => activeDirection.value.match.test(`${card.candidate.title} ${card.candidate.evidence_matrix.map(r => r.statement).join(' ')}`)).length - visibleCards.value.length)
const unreliable = computed(() => (run.value?.coverage.ranking_confidence || 'EVIDENCE') === 'NONE')
const message = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请刷新历史记录后安全重试'
let requestKey: string | null = null
let requestPayload = ''
let loadVersion = 0
async function load() {
  const token = ++loadVersion, id = route.params.id
  loading.value = true
  error.value = ''
  try {
    const [entries, detail] = await Promise.all([matchHistory(), id ? getMatch(Number(id)) : Promise.resolve(null)])
    if (token !== loadVersion) return
    history.value = entries
    moreHistory.value = history.value.length === 20
    run.value = detail
  } catch (e) { if (token === loadVersion) error.value = message(e) }
  finally { if (token === loadVersion) loading.value = false }
}
watch(() => route.params.id, load, { immediate: true })
async function older() {
  try { const items = await matchHistory(history.value.length); history.value.push(...items); moreHistory.value = items.length === 20 }
  catch (e) { error.value = message(e) }
}
async function match() {
  busy.value = true; error.value = ''
  try {
    const filters = { locations: csv(locations.value), campus_types: csv(campus.value), job_families: csv(family.value),
      tags: csv(tags.value), exclude_tags: csv(excluded.value), exclude_company_ids: [], exclude_job_ids: [] }
    const payload = JSON.stringify(filters)
    if (!requestKey || payload !== requestPayload) { requestKey = crypto.randomUUID(); requestPayload = payload }
    const created = await createMatch(filters, requestKey)
    run.value = created
    showAll.value = false
    await router.replace(`/jobs/matches/${created.id}`)
    const generated = await generateMatch(created.id)
    if (Number(route.params.id) === created.id) { ++loadVersion; run.value = generated }
    requestKey = null
    history.value = await matchHistory()
  } catch (e) { error.value = message(e) }
  finally { busy.value = false }
}
async function retry() {
  if (!run.value) return
  busy.value = true; error.value = ''
  const id = run.value.id
  try {
    const generated = await generateMatch(id)
    if (Number(route.params.id) === id) { ++loadVersion; run.value = generated }
    history.value = await matchHistory()
  } catch (e) { error.value = message(e) }
  finally { busy.value = false }
}
function interview(candidate: MatchCandidate) {
  router.push({ path: '/interview', query: interviewTarget(candidate) })
}
function requirementsCovered(card: { candidate: MatchCandidate }) {
  const components = card.candidate.ranking_components
  const counts = components.required_counts
  if (!counts) return null
  return `${counts.SUPPORTED} 项已支持${counts.PARTIAL ? ` · ${counts.PARTIAL} 项部分覆盖` : ''}${counts.NO_EVIDENCE ? ` · ${counts.NO_EVIDENCE} 项待确认` : ''}`
}
function gapsOf(card: { presentation: { requirements: { status: string; statement: string }[] } }) {
  return card.presentation.requirements.filter(r => r.status === 'NO_EVIDENCE').slice(0, 3)
}
function suggestions(card: { candidate: MatchCandidate }) {
  const gaps = card.candidate.evidence_matrix.filter(r => r.status === 'NO_EVIDENCE').slice(0, 2)
  return gaps.map(row => `如果做过「${row.statement.replace(/^熟悉|^掌握|^了解|^有|^具备/, '').slice(0, 40)}」相关的事，把那段项目或实习补进简历，这里就会显示依据。`)
}
</script>

<template>
  <main class="matching-page">
    <header class="page-head">
      <div><h1>岗位推荐</h1><p>用简历里的真实依据对照岗位要求，先说清"为什么推荐"，再说"还需要确认什么"。</p></div>
      <form class="run-action" @submit.prevent="match">
        <button class="primary" :disabled="busy">{{ busy ? '正在匹配…' : '用当前画像重新匹配' }}</button>
      </form>
    </header>

    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="loading" role="status" class="meta">正在读取岗位推荐…</p>

    <section v-if="run" class="result">
      <div class="result-head">
        <div>
          <h2>本次推荐 <span class="badge">{{ displayLabel(run.result_state) }}</span></h2>
          <p class="meta">{{ run.created_at }} · {{ modelModeLabel(run.provider, run.model) }}</p>
        </div>
        <button class="ghost" :disabled="busy" @click="showMoreFilters = !showMoreFilters">
          {{ showMoreFilters ? '收起筛选' : '筛选条件' }}
        </button>
      </div>

      <div v-if="showMoreFilters" class="filters">
        <label>工作地区<input v-model="locations" placeholder="北京，上海"></label>
        <label>校招类型<input v-model="campus" placeholder="例如：应届生"></label>
        <label>岗位方向<input v-model="family" placeholder="例如：后端开发"></label>
        <label>技术标签<input v-model="tags" placeholder="Python，Redis"></label>
        <label>排除标签<input v-model="excluded" placeholder="可选"></label>
        <button class="ghost" :disabled="busy" @click="match">应用筛选并重新匹配</button>
      </div>

      <div class="chips" role="group" aria-label="按方向查看">
        <button v-for="d in DIRECTIONS" :key="d.key" :class="['chip', { active: direction === d.key }]" @click="direction = d.key; showAll = false">{{ d.label }}</button>
      </div>

      <div v-if="unreliable" class="banner warn">
        暂不能可靠排序：简历里没有能和岗位要求对照的技术证据。这里的顺序只代表岗位库顺序，请先补全技术栈或项目描述后再匹配。
      </div>
      <p v-else class="basis">
        排序依据：先看岗位硬性条件，再看必需技术要求里"有项目/实习依据"的条数，然后是加分项；同一档内才按关键词重合度排列。不计算"匹配百分比"。
      </p>
      <p v-if="run.coverage.status !== 'SUFFICIENT'" class="banner">当前岗位库只覆盖 {{ run.coverage.candidate_count }} 个岗位，推荐不代表全部招聘机会。</p>
      <p v-if="run.producer === 'REPLAY_FIXTURE' || run.snapshot.data_mode === 'DEMO_REPLAY'" class="banner">DEMO / REPLAY · 回放模式，非真实模型验收结果。</p>
      <p v-if="run.result_state === 'AI_FAILED_RULE_READY'" role="alert" class="banner warn">
        AI 解读这一步没有完成：{{ userError(run.error || '') }}
        下面的顺序来自规则排序（硬性条件 → 必需技术要求里有无真实依据 → 加分项），和完整解读用的是同一份材料。
      </p>
      <button v-if="run.result_state !== 'AI_COMPLETED'" class="ghost" :disabled="busy" @click="retry">
        {{ run.result_state === 'AI_RUNNING' ? '读取生成结果' : '补充 AI 解读（会调用一次模型）' }}
      </button>

      <p v-if="!cards.length" class="empty">这次没有可推荐的岗位。</p>
      <div v-else class="cards">
        <article v-for="card in visibleCards" :key="card.candidate.id" class="card">
          <header>
            <div>
              <span class="company">{{ publicName(card.candidate.company_name) }}</span>
              <h3>{{ publicName(card.candidate.title) }}</h3>
            </div>
            <span class="band">{{ fitLabels[card.candidate.recommendation?.fit_band || card.candidate.ranking_components.fit_band_ceiling] }}</span>
          </header>
          <p class="counts">{{ requirementsCovered(card) }}</p>

          <h4>为什么推荐</h4>
          <ul v-if="card.presentation.highlights.length">
            <li v-for="highlight in card.presentation.highlights.slice(0, 3)" :key="highlight">{{ highlight }}</li>
          </ul>
          <p v-else class="meta">这份简历里还没有与该岗位技术要求直接对应的内容。</p>

          <h4>还需要确认</h4>
          <ul v-if="gapsOf(card).length">
            <li v-for="gap in gapsOf(card)" :key="gap.statement">{{ gap.statement }}</li>
          </ul>
          <p v-else class="meta">必需技术要求都有对应依据，仍建议面试时确认深度。</p>

          <div v-if="!card.presentation.highlights.length" class="suggest">
            <p v-for="s in suggestions(card)" :key="s">{{ s }}</p>
          </div>

          <p class="meta">硬性条件 · {{ eligibilityLabels[card.candidate.eligibility_status] }}</p>
          <div class="actions">
            <button class="primary" @click="interview(card.candidate)">开始模拟面试</button>
            <router-link class="ghost" :to="{ path: '/training', query: interviewTarget(card.candidate) }">专项训练</router-link>
          </div>
          <p v-if="card.candidate.interviewer_skill_recommendation" class="meta">
            面试方向：{{ card.candidate.interviewer_skill_recommendation.auto_select
              ? card.candidate.interviewer_skill_recommendation.ranking.find(s => s.skill_id === card.candidate.interviewer_skill_recommendation?.recommended_skill_id)?.label
              : '需要手动选择' }}
          </p>

          <details class="advanced">
            <summary>详细依据与高级信息</summary>
            <p class="meta">画像版本 {{ run.profile_version_id }} · 岗位描述第 {{ card.candidate.jd_version_number }} 版
              · 召回顺序 {{ card.candidate.pre_rank ?? '未排序' }} · 最终顺序 {{ card.candidate.final_rank ?? '未排序' }}</p>
            <div v-for="row in card.presentation.requirements.filter(r => r.status !== 'NOT_APPLICABLE')" :key="row.job_requirement_id" class="matrix">
              <p><strong>{{ displayLabel(row.status) }}</strong> · {{ row.statement }}</p>
              <p v-for="explanation in row.explanations" :key="explanation" class="meta">{{ displayText(explanation) }}</p>
              <blockquote v-for="e in row.evidence" :key="e.key">
                {{ e.excerpt }}
                <footer>{{ displayLabel(e.kind) }} · {{ displayLabel(e.section) }}<template v-if="e.resume_id"> · 文本位置 {{ e.start_offset }}–{{ e.end_offset }}</template></footer>
              </blockquote>
            </div>
            <h4 v-if="card.presentation.preparation.length">建议补充的材料</h4>
            <p v-for="item in card.presentation.preparation" :key="item" class="meta">{{ item }}</p>
            <p class="meta">岗位描述指纹：{{ card.candidate.jd_fingerprint }}</p>
          </details>
        </article>
      </div>
      <button v-if="hiddenCount > 0" class="ghost more" @click="showAll = true">再看 {{ hiddenCount }} 个岗位</button>

      <details class="advanced history-box">
        <summary>历史推荐（{{ history.length }}）与本次固定筛选</summary>
        <p v-for="h in history" :key="h.id"><router-link :to="`/jobs/matches/${h.id}`">{{ h.created_at }} · {{ displayLabel(h.result_state) }}</router-link></p>
        <button v-if="moreHistory" class="ghost" @click="older">更早的记录</button>
        <pre>{{ run.snapshot.request.filters }}</pre>
      </details>
      <details v-if="run.excluded_candidates.length" class="advanced">
        <summary>明确不满足 / 已排除（{{ run.excluded_candidates.length }}）</summary>
        <p v-for="c in run.excluded_candidates" :key="c.id" class="meta">{{ publicName(c.company_name) }} · {{ publicName(c.title) }} —
          {{ c.eligibility_reasons.filter(r => r.status === 'NOT_SATISFIED').map(r => `${displayLabel(r.field, '资格条件')}: ${r.reason}`).join('；') }}</p>
      </details>
    </section>

    <p v-else-if="!loading && !busy" class="empty">
      还没有岗位推荐。点击上方"用当前画像重新匹配"生成；如果没有上传简历，请先去画像页生成画像。
    </p>
  </main>
</template>

<style scoped>
.matching-page { max-width: 1080px; margin: auto; }
.page-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; flex-wrap: wrap; }
.page-head p { color: var(--muted); margin: 6px 0 0; }
.result { margin-top: 18px; }
.result-head { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; flex-wrap: wrap; }
.result-head h2 { font-size: 20px; margin: 0; }
.badge { font-size: 12px; color: var(--muted); font-weight: normal; margin-left: 6px; }
.filters { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 16px 0; padding: 16px; background: white; border: 1px solid var(--line); border-radius: 12px; }
.filters label { display: grid; gap: 6px; font-size: 13px; color: var(--muted); }
input { padding: 9px 11px; border: 1px solid var(--line); border-radius: 8px; font: inherit; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0 10px; }
.chip { border: 1px solid var(--line); background: white; border-radius: 999px; padding: 6px 14px; cursor: pointer; color: var(--ink); }
.chip.active { background: var(--brand); border-color: var(--brand); color: white; }
.basis { color: var(--muted); font-size: 13px; margin: 8px 0 16px; }
.cards { display: grid; gap: 14px; }
.card { background: white; border: 1px solid var(--line); border-radius: 12px; padding: 18px; }
.card header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.card h3 { margin: 4px 0 0; font-size: 17px; }
.card h4 { margin: 14px 0 6px; font-size: 13px; color: var(--muted); }
.card ul { margin: 0; padding-left: 18px; line-height: 1.75; }
.company { color: var(--muted); font-size: 13px; }
.band { background: var(--soft); color: var(--brand); border-radius: 999px; padding: 4px 12px; font-size: 12px; white-space: nowrap; }
.counts { color: var(--muted); font-size: 13px; margin: 8px 0 0; }
.suggest { background: var(--soft); border-radius: 8px; padding: 10px 12px; margin-top: 8px; }
.suggest p { margin: 4px 0; font-size: 13px; line-height: 1.7; }
.actions { display: flex; gap: 10px; flex-wrap: wrap; margin: 14px 0 8px; }
.primary { background: var(--brand); color: white; border: 0; border-radius: 8px; padding: 9px 16px; cursor: pointer; text-decoration: none; }
.ghost { background: white; border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px; cursor: pointer; color: var(--ink); text-decoration: none; display: inline-block; }
.ghost:disabled { opacity: .5; cursor: default; }
.meta { color: var(--muted); font-size: 13px; }
.error { color: #b42318; }
.banner { padding: 12px; background: #fff2ce; border-radius: 8px; margin: 10px 0; }
.banner.warn { background: #fdeceb; color: #8a1c12; }
.empty { text-align: center; color: var(--muted); padding: 40px 0; }
.more { margin: 16px auto 0; display: block; }
.advanced { margin-top: 14px; }
.advanced summary { cursor: pointer; color: var(--muted); font-size: 13px; }
.history-box { border-top: 1px solid var(--line); padding-top: 12px; }
.matrix { border-top: 1px solid var(--soft); padding: 8px 0; }
blockquote { background: var(--soft); border-radius: 8px; padding: 10px; margin: 8px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
blockquote footer { color: var(--muted); font-size: 12px; margin-top: 6px; }
pre { white-space: pre-wrap; background: var(--soft); padding: 10px; border-radius: 8px; font-size: 12px; }
@media (max-width: 640px) {
  .card header { flex-direction: column; }
}
</style>
