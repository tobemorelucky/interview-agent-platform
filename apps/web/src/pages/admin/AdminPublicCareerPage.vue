<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import client from '../../api/client';
type Result = { title: string; url: string; snippet?: string; body_available: boolean };
type Task = { id: number; status: string; target_type: string; error?: string; provider_mode?: string; body_preview?: string;
  manifest?: { source_url: string; source_type: string };
  research_result?: { jd_version_id?: number; experience_id?: number };
  calls?: { tool: string; status: string; result: { data?: { results?: Result[] } } }[] };
type JD = { id: number; company: string; title: string; publication_status: string; metadata?: { quality_assessment?: { suitable: boolean; risk_flags: string[] } }; requirements: { id: number; text: string }[]; evidence: { excerpt: string }[] };
const company = ref(''), title = ref(''), keywords = ref(''), sites = ref(''), after = ref(''), before = ref('');
const count = ref(5), needContent = ref(true), documentType = ref('job_jd'), sourceType = ref('official');
const config = ref<{credential_configured: boolean; daily_search_limit: number; daily_fetch_limit: number} | null>(null);
const tasks = ref<Task[]>([]), discovery = ref<Task | null>(null), selected = ref<Task | null>(null), jd = ref<JD | null>(null);
const busy = ref(''), error = ref(''), notice = ref(''), reason = ref('');
let requestKey = '';
const results = computed(() => discovery.value?.calls?.find(c => c.tool === 'public.search' && c.status === 'SUCCEEDED')?.result.data?.results || []);
async function history() { tasks.value = await client.get<never, Task[]>('/admin/public-career/tasks'); }
async function action(label: string, run: () => Promise<void>) {
  if (busy.value) return; busy.value = label; error.value = ''; notice.value = '';
  try { await run(); await history(); } catch (e) { error.value = e instanceof Error ? e.message : '处理失败，请刷新状态后重试。'; }
  finally { busy.value = ''; }
}
async function load(id: number) {
  const task = await client.get<never, Task>(`/admin/public-career/tasks/${id}`);
  if (task.target_type === 'WEB_DISCOVERY') discovery.value = task;
  else { selected.value = task; jd.value = null; if (task.research_result?.jd_version_id) jd.value = await client.get<never, JD>(`/admin/public-career/jd/${task.research_result.jd_version_id}`); }
}
async function search() {
  if (!requestKey) requestKey = crypto.randomUUID();
  await action('正在搜索公开网页…', async () => {
    discovery.value = await client.post<never, Task>('/admin/public-career/search', { company: company.value, job_title: title.value,
      keywords: keywords.value, count: count.value, sites: sites.value.split(',').map(s => s.trim()).filter(Boolean),
      need_content: needContent.value, published_after: after.value || null, published_before: before.value || null, request_key: requestKey }, { timeout: 100000 });
    requestKey = ''; selected.value = null; jd.value = null;
    if (!results.value.length) notice.value = '没有可用公开结果。请调整关键词，不会自动扩大搜索。';
  });
}
async function select(result: Result) {
  await action('正在取得正文，优先复用已有内容…', async () => {
    selected.value = await client.post<never, Task>(`/admin/public-career/tasks/${discovery.value!.id}/select`,
      { source_url: result.url, document_type: documentType.value, source_type: sourceType.value }, { timeout: 100000 });
    jd.value = null; notice.value = '正文已保存，尚未调用模型或发布。';
  });
}
async function extract() {
  await action('正在抽取事实并校验证据，结果仍须审核…', async () => {
    await client.post(`/admin/public-career/tasks/${selected.value!.id}/extract`, { retry_failed: selected.value!.status === 'FAILED' }, { timeout: 300000 });
    await load(selected.value!.id);
    if (selected.value?.status === 'FAILED') error.value = `抽取失败：${selected.value.error || '请查看任务详情'}。未发布新资料。`;
  });
}
async function review(actionName: string) {
  await action('正在提交审核…', async () => {
    await client.post(`/admin/public-career/jd/${jd.value!.id}/review`, { action: actionName, reason: reason.value });
    await load(selected.value!.id); notice.value = actionName === 'publish' ? '已发布到共享公共库。' : '审核决定已保存，未发布。';
  });
}
function publicUrl(url: string) { return /^https?:\/\//i.test(url) ? url : undefined; }
onMounted(() => action('正在读取采集配置…', async () => { config.value = await client.get('/admin/public-career/status'); }));
</script>

<template>
  <section class="web-collection">
    <header><h1>职业资料采集与审核</h1><p>搜索公开来源，选择正文，再抽取和审核。只有发布后的资料才会进入共享公共库。</p></header>
    <p v-if="config && !config.credential_configured" class="warning">未配置独立 DeepSeek 官方 Web Key。不能使用方舟模型 Key 代替；真实搜索暂不可用，已有任务仍可查看。</p>
    <p class="muted">搜索与抓取单独计费；日上限 {{ config?.daily_search_limit ?? '—' }} 次搜索、{{ config?.daily_fetch_limit ?? '—' }} 次抓取，含失败和重试。请勿输入私人简历、回答或记忆。</p>
    <form @submit.prevent="search" @input="requestKey = ''" @change="requestKey = ''">
      <div class="fields"><label>公司<input v-model="company" maxlength="40" placeholder="公开公司名称"></label><label>岗位<input v-model="title" maxlength="40" placeholder="例如 AI应用工程师"></label><label>关键词<input v-model="keywords" maxlength="80" placeholder="招聘 / 面经 / 技术面试"></label></div>
      <details><summary>搜索范围与选项</summary><div class="fields"><label>限定域名<input v-model="sites" placeholder="career.example.com（用逗号分隔）"></label><label>起始日期<input v-model="after" type="date"></label><label>截止日期<input v-model="before" type="date"></label><label>结果数<input v-model.number="count" type="number" min="1" max="10"></label></div><label><input v-model="needContent" type="checkbox">请求网页正文，避免重复抓取</label></details>
      <button class="primary" :disabled="!!busy || !config?.credential_configured || !(company || title || keywords)">搜索公开来源</button>
    </form>
    <p v-if="busy" role="status">{{ busy }} 请勿重复提交；刷新后可从任务记录恢复。</p><p v-if="error" role="alert" class="warning">{{ error }}</p><p v-if="notice" role="status">{{ notice }}</p>
    <section v-if="discovery"><h2>搜索结果 <small>{{ discovery.provider_mode === 'REPLAY' ? 'DEMO / Replay 回放模式' : 'Live 实时来源' }}</small></h2>
      <div class="fields"><label>资料类型<select v-model="documentType"><option value="job_jd">岗位 JD</option><option value="interview_experience">公开面经</option></select></label><label>声明的来源身份<select v-model="sourceType"><option value="official">官方来源（仍须核实域名）</option><option value="community">社区来源（人工审核）</option></select></label></div>
      <article v-for="result in results" :key="result.url"><h3>{{ result.title || '未提供标题' }}</h3><a :href="publicUrl(result.url)" target="_blank" rel="noopener noreferrer">{{ result.url }}</a><p>{{ result.snippet }}</p><p class="muted">{{ result.body_available ? '含网页正文，足够时不再 Fetch' : '只有搜索摘要，选择后需取得正文' }}。排名信号不代表内容真实。</p><button :disabled="!!busy" @click="select(result)">选择并取得正文</button></article>
    </section>
    <section v-if="selected"><h2>正文与结构化处理</h2><p>{{ selected.provider_mode === 'REPLAY' ? 'DEMO / Replay 回放模式' : 'DeepSeek Web 实时采集' }} · {{ selected.status === 'COMPLETED' ? '抽取已完成' : selected.status === 'FAILED' ? '处理失败，可安全重试' : '待抽取或处理中' }}</p><p v-if="selected.error" role="alert">{{ selected.error }}</p><details><summary>查看管理员正文预览（外部不可信数据）</summary><pre>{{ selected.body_preview }}</pre></details><button :disabled="!!busy || !['READY_FOR_EXTRACTION', 'FAILED'].includes(selected.status)" @click="extract">{{ selected.status === 'FAILED' ? '重试结构化抽取' : '抽取事实并校验证据（调用模型）' }}</button>
      <p v-if="selected.manifest">来源：<a :href="publicUrl(selected.manifest.source_url)" target="_blank" rel="noopener noreferrer">{{ selected.manifest.source_url }}</a>（声明为{{ selected.manifest.source_type === 'community' ? '社区来源，必须人工核实' : '官方来源，仍须核实域名与主体' }}）</p>
      <template v-if="jd"><h3>JD 要求与来源证据</h3><p>{{ jd.company }} · {{ jd.title }}</p><p>{{ jd.publication_status === 'PUBLISHED' ? '已发布' : jd.publication_status === 'REJECTED' ? '已拒绝' : '尚未发布，请审核' }}</p><ul><li v-for="req in jd.requirements" :key="req.id">{{ req.text }}</li></ul><details><summary>查看逐条依据</summary><blockquote v-for="(e, i) in jd.evidence" :key="i">{{ e.excerpt }}</blockquote></details><label>审核理由<textarea v-model="reason" placeholder="核实公司、岗位及正文证据，记录审核依据" /></label><div class="actions"><button :disabled="!!busy || !reason.trim() || jd.publication_status === 'PUBLISHED'" @click="review('publish')">审核通过并发布</button><button :disabled="!!busy || !reason.trim() || jd.publication_status === 'PUBLISHED'" @click="review('request_correction')">要求修正</button><button :disabled="!!busy || !reason.trim() || jd.publication_status === 'PUBLISHED'" @click="review('reject')">拒绝</button></div></template>
      <p v-if="selected.research_result?.experience_id">面经须人工审核。<router-link :to="{path:'/admin/experience-review', query:{experience_id:selected.research_result.experience_id}}">前往已有面经审核页</router-link></p>
      <p v-if="jd?.metadata?.quality_assessment">质量分析：{{ jd.metadata.quality_assessment.suitable ? '模型判断适合候选入库，但不代表真实认证' : '模型提示不适合直接入库，请核查' }}。风险：{{ jd.metadata.quality_assessment.risk_flags.join('；') || '未报告风险，仍须审核来源与证据' }}</p>
    </section>
    <section><h2>最近采集任务</h2><p v-if="!tasks.length">还没有采集任务。配置 Web Key 后从小范围搜索开始。</p><button :disabled="!!busy" @click="action('刷新任务…', history)">刷新状态</button><ul><li v-for="task in tasks" :key="task.id"><button :disabled="!!busy" @click="action('读取任务…', () => load(task.id))">{{ task.target_type === 'WEB_DISCOVERY' ? '网页搜索' : task.target_type === 'JOB_JD' ? 'JD抽取' : task.target_type === 'INTERVIEW_DATA' ? '面经抽取' : '正文采集' }} #{{ task.id }}</button> · {{ task.status }} <span v-if="task.error">（{{ task.error }}）</span></li></ul></section>
  </section>
</template>

<style scoped>
.web-collection{max-width:1040px;margin:auto;line-height:1.65}.web-collection>section,.web-collection>form{padding:22px 0;border-top:1px solid #dce3ee}header p,.muted{color:#596579;font-size:.93rem}.fields{display:flex;flex-wrap:wrap;gap:16px;margin:12px 0}label{display:flex;flex-direction:column;gap:6px}input,select,textarea{padding:9px;border:1px solid #bac5d5;border-radius:6px;background:#fff}textarea{min-height:90px}button{padding:9px 14px;border:1px solid #b8c5d8;border-radius:6px;background:#fff;cursor:pointer;margin:10px 8px 0 0}button.primary{background:#2456a6;color:white;border-color:#2456a6}button:disabled{opacity:.5;cursor:not-allowed}article{padding:18px 0;border-bottom:1px solid #e1e7ef}a{overflow-wrap:anywhere}pre{white-space:pre-wrap;max-height:360px;overflow:auto;max-width:80ch;font:inherit}blockquote{border-left:3px solid #c2d2e8;margin:12px 0;padding:8px 14px;white-space:pre-wrap}small{font-size:.8rem;color:#596579}.warning{color:#934019;background:#fff4e9;padding:12px}summary{cursor:pointer}h1{font-size:1.65rem}h2{font-size:1.25rem}h3{font-size:1.05rem}
</style>
