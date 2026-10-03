<script setup lang="ts">
import { ref, watch } from 'vue'
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
const csv = (s: string) => [...new Set(s.split(/[,，]/).map(v => v.trim()).filter(Boolean))]
const source = (key: string) => run.value?.snapshot.profile.evidence.find(e => e.key === key)
const message = (e: unknown) => e instanceof ApiError ? e.message : '请求失败，请刷新历史记录后安全重试'
let requestKey: string | null = null
let requestPayload = ''
let loadVersion = 0
async function load() {
  const token = ++loadVersion, id = route.params.id
  loading.value = true
  error.value = ''
  try {
    const entries = await matchHistory()
    const detail = id ? await getMatch(Number(id)) : null
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
    await router.replace(`/jobs/matches/${created.id}`)
    const generated = await generateMatch(created.id)
    if (Number(route.params.id) === created.id) { ++loadVersion; run.value = generated }
    requestKey = null // a new user-requested match is a new frozen Run; transport retry reuses key
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
  }
  catch (e) { error.value = message(e) }
  finally { busy.value = false }
}
function interview(candidate: MatchCandidate) {
  router.push({ path: '/interview', query: interviewTarget(candidate) })
}
</script>

<template>
  <main class="matching-page">
    <header><h1>岗位推荐</h1><p>只比较已发布岗位库与我的画像。已有目标岗位可直接进入模拟面试；这里不会联网补库。</p></header>
    <form class="filters" @submit.prevent="match">
      <label>工作地区（逗号分隔）<input v-model="locations" placeholder="北京，上海"></label>
      <label>校招类型<input v-model="campus" placeholder="与岗位库结构化值一致"></label>
      <label>岗位族<input v-model="family" placeholder="例如：后端开发"></label>
      <label>技术标签<input v-model="tags" placeholder="Python，Redis"></label>
      <label>明确排除标签<input v-model="excluded" placeholder="可选"></label>
      <button :disabled="busy">{{ busy ? '正在匹配…' : '使用当前画像匹配' }}</button>
    </form>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="loading" role="status">正在读取岗位推荐…</p>
    <nav class="history" aria-label="历史匹配"><span>历史推荐</span><router-link v-for="h in history" :key="h.id" :to="`/jobs/matches/${h.id}`">{{ h.created_at }} · {{ displayLabel(h.status) }}</router-link><button v-if="moreHistory" @click="older">更早的记录</button></nav>
    <p v-if="!run && !history.length && !busy && !loading && !error" class="ux-empty">还没有岗位推荐记录。当前公开岗位数据不足时，暂时无法生成可靠推荐。</p>
    <section v-if="run">
      <h2>本次推荐 · {{ displayLabel(run.status) }}</h2>
      <p>{{ run.created_at }} · {{ modelModeLabel(run.provider, run.model) }}</p><details class="ux-technical"><summary>技术详情</summary>画像版本 ID {{ run.profile_version_id }} · 排序规则 {{ run.policy_version }} · 提示版本 {{ run.prompt_version }}<p>{{ run.input_fingerprint }}</p></details>
      <p v-if="run.producer === 'REPLAY_FIXTURE' || run.snapshot.data_mode === 'DEMO_REPLAY'" class="banner">DEMO / REPLAY · Replay 回放模式 · fixture — 非真实招聘网络数据或真实模型验收</p>
      <p v-if="run.coverage.status !== 'SUFFICIENT'" class="banner">当前岗位库覆盖不足（{{ run.coverage.candidate_count }} 个候选）。不会联网补库。</p>
      <p v-if="run.coverage.window_may_be_truncated">本次只检查有界召回窗口，结果并非整个岗位市场排名。</p>
      <p v-if="run.error" role="alert" class="error">生成失败：{{ userError(run.error) }}。固定输入仍可查看。</p>
      <button v-if="run.status === 'READY' || run.status === 'FAILED' || run.status === 'RUNNING'" :disabled="busy" @click="retry">安全重试 / 读取生成结果</button>
      <details><summary>本次固定的显式筛选与已确认偏好</summary><pre>{{ run.snapshot.request.filters }}</pre><p v-for="m in run.snapshot.approved_preferences.items" :key="m.id">已确认偏好：{{ m.content }}（只作偏好，不作资格证明）</p><p>省略偏好 {{ run.snapshot.approved_preferences.omitted_count }} 条</p></details>
      <article v-for="c in run.candidates" :key="c.id" class="card">
        <h2>{{ publicName(c.company_name) }} · {{ publicName(c.title) }}</h2>
        <p>{{ eligibilityLabels[c.eligibility_status] }} · {{ fitLabels[c.recommendation?.fit_band || c.ranking_components.fit_band_ceiling] }}</p>
        <p>岗位描述第 {{ c.jd_version_number }} 版 · 推荐顺序 {{ c.final_rank ?? '未排序' }}</p>
        <h3>已有证据 / 岗位要求</h3>
        <div v-for="row in c.evidence_matrix" :key="row.id" class="matrix">
          <p>{{ displayLabel(row.status) }} · {{ row.statement }}</p><p v-if="row.status === 'NO_EVIDENCE'">{{ displayText(row.message) }}</p>
          <details v-for="s in row.supports" :key="s.candidate_fact_id"><summary>{{ displayText(s.explanation) }}</summary><p>{{ displayLabel(s.category) }}</p><blockquote v-for="key in s.evidence_ids" :key="key">{{ displayLabel(source(key)?.kind) }}<br>{{ source(key)?.excerpt }}<footer>依据 · {{ displayLabel(source(key)?.section) }}<template v-if="source(key)?.resume_id"> · 简历文本位置 {{ source(key)?.start_offset }}–{{ source(key)?.end_offset }}</template></footer></blockquote></details>
        </div>
        <template v-if="c.recommendation">
          <h3>为什么推荐</h3><p v-for="s in c.recommendation.analysis.alignment_reasons" :key="`${s.candidate_fact_id}-${s.explanation}`">{{ displayText(s.explanation) }}</p>
          <h3>当前材料未证明 / 建议准备</h3><p v-for="g in c.recommendation.analysis.current_evidence_gaps" :key="g.id">? {{ g.statement }} · {{ displayLabel(g.status) }}。没证据 ≠ 不会。</p>
          <p v-for="g in c.recommendation.analysis.preparation_focus" :key="g.id">建议核实或补充材料：{{ g.statement }}</p>
        </template>
        <details><summary>硬资格与待确认条件</summary><p v-for="r in c.eligibility_reasons" :key="r.field">{{ displayLabel(r.field, "资格条件") }}：{{ displayLabel(r.status) }} — {{ r.reason }}</p></details>
        <button v-if="c.recommendation" @click="interview(c)">开始模拟面试</button><small>将使用当前岗位、画像和岗位描述开始新面试；已关闭岗位不能启动。</small>
      </article>
      <details v-if="run.excluded_candidates.length"><summary>明确不满足 / 已排除（不参与推荐排序）</summary><p v-for="c in run.excluded_candidates" :key="c.id">{{ publicName(c.company_name) }} · {{ publicName(c.title) }} — {{ c.eligibility_reasons.filter(r => r.status === 'NOT_SATISFIED').map(r => `${displayLabel(r.field, "资格条件")}: ${r.reason}`).join('；') }}</p></details>
    </section>
  </main>
</template>

<style scoped>
.matching-page{max-width:1000px;margin:auto;padding:24px;color:#243047}.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:24px 0}label{display:grid;gap:6px}input{padding:10px;border:1px solid #cbd5e1;border-radius:6px}button{background:#315be5;color:white;border:0;border-radius:6px;padding:10px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.history{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}.card{border:1px solid #d8e0ed;border-radius:12px;padding:22px;margin:20px 0;background:white}.banner{padding:12px;background:#fff2ce}.error{color:#b42318}.matrix{border-top:1px solid #edf1f7;padding:8px 0}details{padding:12px 0}summary{cursor:pointer}blockquote{background:#f3f6fb;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}small{display:block;margin-top:10px}pre{white-space:pre-wrap}code{overflow-wrap:anywhere}
</style>
