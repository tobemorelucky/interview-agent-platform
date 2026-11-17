<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import client from '../api/client';
type Job = { company_id: number; company: string; job_id: number; title: string; source_url?: string; source_type?: string; updated_at: string; published_at?: string };
type Experience = { id: number; company?: string; title?: string; summary?: string; source_url?: string; updated_at: string };
type Detail = Job & {
  description: string; requirements: { id: number; text: string; type?: string; importance?: string }[];
  responsibilities: string[]; qualifications: string[]; has_sections: boolean;
};
const jobs = ref<Job[]>([]), experiences = ref<Experience[]>([]), detail = ref<Detail | null>(null);
const companies = ref<{ company_id: number; name: string; job_count: number }[]>([]);
const loading = ref(false), error = ref(''), selecting = ref(false);
const total = ref(0), page = ref(1), pageSize = 20;
const q = ref(''), direction = ref(''), companyId = ref<number | ''>('');
const showExperiences = ref(false);
const DIRECTIONS = [
  { key: '', label: '全部方向' },
  { key: 'ai_agent', label: 'AI / Agent' },
  { key: 'java', label: 'Java 后端' },
  { key: 'python', label: 'Python' },
  { key: 'frontend', label: '前端' },
  { key: 'test', label: '测试开发' },
  { key: 'data', label: '数据' },
  { key: 'embedded', label: '嵌入式 / 系统' },
];
const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)));
const hasFilter = computed(() => Boolean(q.value || direction.value || companyId.value !== ''));

let timer: ReturnType<typeof setTimeout> | undefined;
watch([q, direction, companyId], () => {
  clearTimeout(timer);
  timer = setTimeout(() => { page.value = 1; loadJobs(); }, 250);
});

async function loadJobs() {
  loading.value = true; error.value = '';
  try {
    const data = await client.get<never, { items: Job[]; total: number }>('/public-career/job-search', {
      params: { limit: pageSize, offset: (page.value - 1) * pageSize, q: q.value || undefined,
        direction: direction.value || undefined, company_id: companyId.value || undefined },
    });
    jobs.value = data.items; total.value = data.total;
  } catch (e) { error.value = e instanceof Error ? e.message : '公共资料暂时不可用，请稍后重试。'; }
  finally { loading.value = false; }
}
async function loadMeta() {
  try {
    const [companyList, expList] = await Promise.all([
      client.get<never, { company_id: number; name: string; job_count: number }[]>('/public-career/companies'),
      client.get<never, Experience[]>('/public-career/experiences', { params: { offset: 0 } }),
    ]);
    companies.value = companyList; experiences.value = expList;
  } catch { /* filters degrade gracefully; job list shows its own error */ }
}
async function show(job: Job) {
  selecting.value = true; error.value = '';
  try { detail.value = await client.get<never, Detail>(`/public-career/jobs/${job.job_id}`); }
  catch (e) { error.value = e instanceof Error ? e.message : '岗位已撤回或不可用。'; }
  finally { selecting.value = false; }
}
function reset() { q.value = ''; direction.value = ''; companyId.value = ''; }
function go(target: number) { page.value = Math.min(Math.max(1, target), pages.value); loadJobs(); }
function source(url?: string) { return url && /^https?:\/\//i.test(url) ? url : undefined; }
function date(value: string) { return new Date(value).toLocaleDateString('zh-CN'); }
function sourceLabel(type?: string) {
  if (type === 'CURATED_PARAPHRASE') return '招聘信息整理版';
  if (type === 'official') return '官方来源';
  if (type === 'community') return '社区来源（已审核）';
  return '来源身份未标注';
}
onMounted(() => { loadJobs(); loadMeta(); });
</script>

<template>
  <section class="career">
    <header class="page-head">
      <div><h1>岗位库</h1><p>已发布的真实招聘岗位，可按公司、岗位名和技术关键字搜索；点击卡片查看职责与要求。</p></div>
      <div class="count" v-if="!loading">共 {{ total }} 个岗位</div>
    </header>

    <div class="toolbar">
      <input v-model="q" type="search" class="search" placeholder="搜索公司、岗位或技术关键字，如 Agent、Java、RAG" aria-label="搜索岗位" />
      <select v-model="companyId" aria-label="按公司筛选">
        <option value="">全部公司</option>
        <option v-for="c in companies" :key="c.company_id" :value="c.company_id">{{ c.name }}（{{ c.job_count }}）</option>
      </select>
      <button v-if="hasFilter" class="ghost" @click="reset">清空筛选</button>
    </div>
    <div class="chips" role="group" aria-label="岗位方向">
      <button v-for="d in DIRECTIONS" :key="d.key" :class="['chip', { active: direction === d.key }]" @click="direction = d.key">{{ d.label }}</button>
    </div>

    <p v-if="error" role="alert" class="error">{{ error }} <button class="ghost" @click="loadJobs">重试</button></p>

    <div v-if="loading" class="skeleton" role="status">正在加载岗位…</div>
    <p v-else-if="!jobs.length" class="empty">没有符合条件的岗位。换个关键字，或清空筛选查看全部 {{ total === 0 ? '' : '' }}岗位。</p>

    <div v-else class="grid">
      <button v-for="job in jobs" :key="job.job_id" class="job-card" @click="show(job)">
        <span class="company">{{ job.company }}</span>
        <strong>{{ job.title }}</strong>
        <span class="meta">{{ sourceLabel(job.source_type) }} · 更新于 {{ date(job.updated_at) }}</span>
        <span class="more">查看职责与要求 →</span>
      </button>
    </div>

    <nav v-if="pages > 1" class="pager" aria-label="分页">
      <button class="ghost" :disabled="page <= 1" @click="go(page - 1)">上一页</button>
      <span>第 {{ page }} / {{ pages }} 页</span>
      <button class="ghost" :disabled="page >= pages" @click="go(page + 1)">下一页</button>
    </nav>

    <section class="experiences">
      <button class="ghost toggle" @click="showExperiences = !showExperiences">
        {{ showExperiences ? '收起' : '查看' }}已审核面经（{{ experiences.length }}）
      </button>
      <template v-if="showExperiences">
        <p v-if="!experiences.length" class="meta">当前暂无已发布面经。</p>
        <article v-for="experience in experiences" :key="experience.id" class="exp-card">
          <h3>{{ experience.company || '公司未确认' }} · {{ experience.title || '岗位未确认' }}</h3>
          <p>{{ experience.summary || '暂无公开摘要' }}</p>
          <p class="meta">更新于 {{ date(experience.updated_at) }}
            <a v-if="source(experience.source_url)" :href="source(experience.source_url)" target="_blank" rel="noopener noreferrer">原始来源</a>
          </p>
        </article>
      </template>
    </section>
    <p class="meta footnote">此页不触发采集或实时搜索；资料更新由管理员核实和审核。面经为公开用户自述的整理改写版，未经事件独立核实。</p>

    <div v-if="detail" class="overlay" role="dialog" aria-modal="true" @click.self="detail = null">
      <div class="drawer">
        <header>
          <div><span class="company">{{ detail.company }}</span><h2>{{ detail.title }}</h2></div>
          <button class="ghost" aria-label="关闭" @click="detail = null">关闭</button>
        </header>
        <p class="meta">
          {{ sourceLabel(detail.source_type) }} · 更新于 {{ date(detail.updated_at) }}
          <a v-if="source(detail.source_url)" :href="source(detail.source_url)" target="_blank" rel="noopener noreferrer">查看原始来源</a>
        </p>
        <template v-if="detail.has_sections">
          <h3>岗位职责</h3>
          <ul><li v-for="(item, i) in detail.responsibilities" :key="`r${i}`">{{ item }}</li></ul>
          <h3>任职要求</h3>
          <ul><li v-for="(item, i) in detail.qualifications" :key="`q${i}`">{{ item }}</li></ul>
        </template>
        <template v-else>
          <h3>岗位要求</h3>
          <ul><li v-for="r in detail.requirements" :key="r.id">{{ r.text }}</li></ul>
          <details><summary>JD 原文节选</summary><p class="body">{{ detail.description }}</p></details>
        </template>
        <div class="actions">
          <router-link class="primary" :to="{ path: '/interview', query: { company_id: detail.company_id, job_id: detail.job_id } }">用这个岗位模拟面试</router-link>
          <router-link :to="{ path: '/jobs/matches', query: { company_id: detail.company_id, job_id: detail.job_id } }">查看与我的匹配</router-link>
        </div>
        <p class="meta">来源与完整内容以原始链接为准；本页为招聘信息整理版，非企业逐字原文。</p>
      </div>
    </div>
    <p v-if="selecting" role="status" class="meta">正在读取岗位详情…</p>
  </section>
</template>

<style scoped>
.career { max-width: 1080px; margin: auto; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
.page-head p { color: var(--muted); margin: 6px 0 0; }
.count { color: var(--muted); font-size: 14px; }
.toolbar { display: flex; gap: 10px; flex-wrap: wrap; margin: 18px 0 10px; }
.search { flex: 1 1 320px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; }
select { padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: white; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 18px; }
.chip { border: 1px solid var(--line); background: white; border-radius: 999px; padding: 6px 14px; cursor: pointer; color: var(--ink); }
.chip.active { background: var(--brand); border-color: var(--brand); color: white; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 14px; }
.job-card { display: flex; flex-direction: column; gap: 6px; text-align: left; background: white;
  border: 1px solid var(--line); border-radius: 12px; padding: 16px; cursor: pointer; }
.job-card:hover { border-color: var(--brand); box-shadow: 0 4px 14px rgba(49, 91, 229, .08); }
.job-card strong { font-size: 16px; }
.company { color: var(--muted); font-size: 13px; }
.meta { color: var(--muted); font-size: 13px; }
.more { color: var(--brand); font-size: 13px; margin-top: 4px; }
.pager { display: flex; gap: 14px; align-items: center; justify-content: center; margin: 22px 0; }
.ghost { background: white; border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px; cursor: pointer; color: var(--ink); }
.ghost:disabled { opacity: .45; cursor: default; }
.empty { padding: 32px; text-align: center; color: var(--muted); }
.skeleton { padding: 32px; text-align: center; color: var(--muted); }
.experiences { margin-top: 28px; border-top: 1px solid var(--line); padding-top: 16px; }
.toggle { margin-bottom: 10px; }
.exp-card { background: white; border: 1px solid var(--line); border-radius: 10px; padding: 16px; margin: 12px 0; }
.footnote { margin-top: 16px; }
.overlay { position: fixed; inset: 0; background: rgba(16, 24, 40, .45); display: flex; justify-content: flex-end; z-index: 40; }
.drawer { background: white; width: min(560px, 100%); height: 100%; overflow-y: auto; padding: 22px; }
.drawer header { display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; }
.drawer h2 { margin: 4px 0 8px; font-size: 20px; }
.drawer h3 { margin: 18px 0 8px; font-size: 15px; }
.drawer li { margin: 6px 0; line-height: 1.7; }
.body { white-space: pre-wrap; background: var(--soft); padding: 12px; border-radius: 8px; }
.actions { display: flex; gap: 12px; flex-wrap: wrap; margin: 20px 0 12px; }
.primary { background: var(--brand); color: white; padding: 9px 16px; border-radius: 8px; text-decoration: none; }
@media (max-width: 640px) {
  .grid { grid-template-columns: 1fr; }
  .search { flex-basis: 100%; }
}
</style>
