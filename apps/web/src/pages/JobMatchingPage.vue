<script setup lang="ts">
import { ref, watch, computed } from 'vue'
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
const csv = (s: string) => [...new Set(s.split(/[,，]/).map(v => v.trim()).filter(Boolean))]
const cards = computed(() => run.value?.candidates.map(candidate => ({ candidate,
  presentation: presentMatch(candidate, run.value!.snapshot.profile.facts || [], run.value!.snapshot.profile.evidence) })) || [])
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
    <header><h1>岗位推荐</h1><p>根据简历中的真实依据了解匹配亮点和待确认项，选择岗位开始模拟面试。</p></header>
    <form class="filters" @submit.prevent="match">
      <label>工作地区（逗号分隔）<input v-model="locations" placeholder="北京，上海"></label>
      <label>校招类型<input v-model="campus" placeholder="可选，例如：应届生"></label>
      <label>岗位方向<input v-model="family" placeholder="例如：后端开发"></label>
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
      <p>{{ run.created_at }} · {{ modelModeLabel(run.provider, run.model) }}</p><details class="ux-technical"><summary>技术详情</summary>画像版本 ID {{ run.profile_version_id }} · 排序规则 {{ run.policy_version }} · 提示版本 {{ run.prompt_version }}<p>{{ run.input_fingerprint }}</p><p>数据范围：只使用已发布岗位库，不会联网补库。</p></details>
      <p v-if="run.coverage.generation_mode === 'REUSED_RESULT'">资料和偏好未变化，已为你快速恢复上次推荐。</p>
      <p v-if="run.producer === 'REPLAY_FIXTURE' || run.snapshot.data_mode === 'DEMO_REPLAY'" class="banner">DEMO / REPLAY · Replay 回放模式 · fixture — 非真实招聘网络数据或真实模型验收</p>
      <p v-if="run.coverage.status !== 'SUFFICIENT'" class="banner">当前岗位库覆盖不足（{{ run.coverage.candidate_count }} 个），推荐不代表全部招聘机会。</p>
      <p v-if="run.coverage.window_may_be_truncated">本次只检查有界召回窗口，结果并非整个岗位市场排名。</p>
      <p v-if="run.error" role="alert" class="error">生成失败：{{ userError(run.error) }}。固定输入仍可查看。</p>
      <button v-if="run.status === 'READY' || run.status === 'FAILED' || run.status === 'RUNNING'" :disabled="busy" @click="retry">安全重试 / 读取生成结果</button>
      <details><summary>本次固定的显式筛选与已确认偏好</summary><pre>{{ run.snapshot.request.filters }}</pre><p v-for="m in run.snapshot.approved_preferences.items" :key="m.id">已确认偏好：{{ m.content }}（只作偏好，不作资格证明）</p><p>省略偏好 {{ run.snapshot.approved_preferences.omitted_count }} 条</p></details>
      <article v-for="card in cards" :key="card.candidate.id" class="card">
        <h2>{{ publicName(card.candidate.company_name) }} · {{ publicName(card.candidate.title) }}</h2>
        <p class="fit-band">{{ fitLabels[card.candidate.recommendation?.fit_band || card.candidate.ranking_components.fit_band_ceiling] }}</p>
        <h3>匹配亮点 / 为什么推荐</h3>
        <ul v-if="card.presentation.highlights.length"><li v-for="highlight in card.presentation.highlights" :key="highlight">{{ highlight }}</li></ul>
        <p v-else>当前材料暂无充分的匹配依据，不代表你不具备相关能力。</p>
        <h3>待补充 / 风险</h3>
        <ul v-if="card.presentation.risks.length"><li v-for="risk in card.presentation.risks" :key="risk">{{ risk }}</li></ul>
        <p v-else>当前匹配结果没有额外提示。</p>
        <small>当前材料未覆盖 ≠ 不具备。没证据 ≠ 不会。</small>
        <h3>硬资格 · {{ eligibilityLabels[card.candidate.eligibility_status] }}</h3>
        <ul v-if="card.presentation.eligibility.length"><li v-for="r in card.presentation.eligibility" :key="r.field">{{ displayLabel(r.field, '资格条件') }}<template v-if="r.required">（岗位要求：{{ r.required }}）</template> · {{ displayLabel(r.status) }}</li></ul>
        <p v-if="!card.presentation.eligibility.length">暂无需要额外核实的关键条件。</p>
        <button v-if="card.candidate.recommendation" @click="interview(card.candidate)">开始模拟面试</button>
        <router-link v-if="card.candidate.recommendation" :to="{path:'/training',query:interviewTarget(card.candidate)}">选择个性化训练</router-link>
        <p v-if="card.candidate.interviewer_skill_recommendation" class="muted">面试 Skill：{{ card.candidate.interviewer_skill_recommendation.auto_select ? card.candidate.interviewer_skill_recommendation.ranking.find(s => s.skill_id === card.candidate.interviewer_skill_recommendation?.recommended_skill_id)?.label : '需手动选择' }} · {{ card.candidate.interviewer_skill_recommendation.reason }}</p>
        <details class="match-details">
          <summary>查看详细依据</summary>
          <p>画像版本 {{ run.profile_version_id }} · 岗位描述第 {{ card.candidate.jd_version_number }} 版</p>
          <p>召回顺序 {{ card.candidate.pre_rank ?? '未排序' }} · Agent 顺序 {{ card.candidate.agent_rank ?? '未排序' }} · 最终顺序 {{ card.candidate.final_rank ?? '未排序' }}</p>
          <p v-for="r in card.presentation.eligibility" :key="r.field">{{ displayLabel(r.field, '资格条件') }}：{{ displayText(r.reason) }}</p>
          <div v-for="row in card.presentation.requirements.filter(r => r.status !== 'NOT_APPLICABLE')" :key="row.job_requirement_id" class="matrix">
            <p>{{ displayLabel(row.status) }} · {{ row.statement }}</p>
            <p v-if="row.status === 'NO_EVIDENCE'">当前材料未证明此项要求。</p>
            <p v-for="explanation in row.explanations" :key="explanation">{{ displayText(explanation) }}</p>
            <p v-if="row.supports.length">关联材料事实：{{ row.supports.length }} 条</p>
            <blockquote v-for="e in row.evidence" :key="e.key">{{ e.excerpt }}<footer>{{ displayLabel(e.kind) }} · {{ displayLabel(e.section) }}<template v-if="e.resume_id"> · 简历文本位置 {{ e.start_offset }}–{{ e.end_offset }}</template></footer></blockquote>
            <details v-if="row.moreEvidence.length"><summary>更多依据（{{ row.moreEvidence.length }} 条）</summary><blockquote v-for="e in row.moreEvidence" :key="e.key">{{ e.excerpt }}<footer>{{ displayLabel(e.section) }} · {{ e.start_offset }}–{{ e.end_offset }}</footer></blockquote></details>
          </div>
          <h3 v-if="card.presentation.preparation.length">建议补充材料</h3>
          <p v-for="item in card.presentation.preparation" :key="item">{{ item }}</p>
          <details class="ux-technical"><summary>技术详情</summary>岗位描述指纹：{{ card.candidate.jd_fingerprint }}</details>
        </details>
        <small>将使用当前岗位、画像和岗位描述开始新面试；已关闭岗位不能启动。</small>
      </article>
      <details v-if="run.excluded_candidates.length"><summary>明确不满足 / 已排除（不参与推荐排序）</summary><p v-for="c in run.excluded_candidates" :key="c.id">{{ publicName(c.company_name) }} · {{ publicName(c.title) }} — {{ c.eligibility_reasons.filter(r => r.status === 'NOT_SATISFIED').map(r => `${displayLabel(r.field, "资格条件")}: ${r.reason}`).join('；') }}</p></details>
    </section>
  </main>
</template>

<style scoped>
.matching-page{max-width:1000px;margin:auto;padding:24px;color:#243047}.filters{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin:24px 0}label{display:grid;gap:6px}input{padding:10px;border:1px solid #cbd5e1;border-radius:6px}button{background:#315be5;color:white;border:0;border-radius:6px;padding:10px;cursor:pointer}button:disabled{opacity:.55;cursor:wait}.history{display:flex;gap:14px;flex-wrap:wrap;margin:20px 0}.card{border:1px solid #d8e0ed;border-radius:12px;padding:22px;margin:20px 0;background:white}.banner{padding:12px;background:#fff2ce}.error{color:#b42318}.matrix{border-top:1px solid #edf1f7;padding:8px 0}details{padding:12px 0}summary{cursor:pointer}blockquote{background:#f3f6fb;padding:12px;white-space:pre-wrap;overflow-wrap:anywhere}small{display:block;margin-top:10px}pre{white-space:pre-wrap}code{overflow-wrap:anywhere}
</style>
