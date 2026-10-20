<script setup lang="ts">
import { ref, onMounted } from 'vue';
import client from '../api/client';
type Job = { company_id: number; company: string; job_id: number; title: string; source_url?: string; source_type?: string; updated_at: string };
type Experience = { id: number; company?: string; title?: string; summary?: string; source_url?: string; updated_at: string };
type Detail = Job & { description: string; requirements: { id: number; text: string }[] };
const jobs = ref<Job[]>([]), experiences = ref<Experience[]>([]), detail = ref<Detail | null>(null);
const loading = ref(false), error = ref(''), selecting = ref(false);
const jobOffset = ref(0), experienceOffset = ref(0);
async function load() {
  loading.value = true; error.value = '';
  try {
    const [a, b] = await Promise.all([client.get<never, Job[]>('/public-career/jobs', { params: { offset: jobOffset.value } }), client.get<never, Experience[]>('/public-career/experiences', { params: { offset: experienceOffset.value } })]);
    jobs.value = a; experiences.value = b;
  } catch (e) { error.value = e instanceof Error ? e.message : '公共资料暂时不可用，请稍后重试。'; }
  finally { loading.value = false; }
}
async function show(id: number) {
  selecting.value = true; error.value = ''; detail.value = null;
  try { detail.value = await client.get<never, Detail>(`/public-career/jobs/${id}`); }
  catch (e) { error.value = e instanceof Error ? e.message : '岗位已撤回或不可用。'; }
  finally { selecting.value = false; }
}
function source(url?: string) { return url && /^https?:\/\//i.test(url) ? url : undefined; }
function date(value: string) { return new Date(value).toLocaleDateString('zh-CN'); }
onMounted(load);
</script>

<template>
  <section class="career-library">
    <header><h1>公共职业资料</h1><p>所有用户共享已发布岗位与已审核面经，个人简历、面试和记忆互相隔离。</p></header>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
    <p v-if="loading" role="status">正在加载公共资料…</p>
    <template v-else>
      <h2>公司与岗位</h2><p v-if="!jobs.length">当前岗位库暂无可用岗位。</p>
      <article v-for="job in jobs" :key="job.job_id" class="career-card">
        <h3>{{ job.company }} · {{ job.title }}</h3><p class="source">更新于 {{ date(job.updated_at) }} · {{ job.source_type === 'official' ? '官方来源' : job.source_type === 'community' ? '社区来源（已审核）' : '来源身份未标注' }} <a v-if="source(job.source_url)" :href="source(job.source_url)" target="_blank" rel="noopener noreferrer">原始来源</a></p>
        <div class="actions"><router-link class="primary" :to="{ path: '/interview', query: { company_id: job.company_id, job_id: job.job_id } }">开始模拟面试</router-link><button :disabled="selecting" @click="show(job.job_id)">查看岗位要求</button><router-link to="/jobs/matches">查看我的岗位匹配</router-link></div>
        <section v-if="detail?.job_id === job.job_id" class="detail"><h3>岗位要求</h3><ul><li v-for="r in detail.requirements" :key="r.id">{{ r.text }}</li></ul><details><summary>查看 JD 原文节选</summary><p class="body">{{ detail.description }}</p><p>完整内容及最新信息请查看原始来源。</p></details><button @click="detail = null">收起岗位详情</button></section>
      </article>
      <div class="actions"><button :disabled="jobOffset === 0" @click="jobOffset -= 20; load()">上一页岗位</button><button :disabled="jobs.length < 20" @click="jobOffset += 20; load()">下一页岗位</button></div>
      <p v-if="selecting" role="status">正在读取岗位要求…</p>
      <h2>已审核面经</h2><p v-if="!experiences.length">当前暂无已发布面经。</p>
      <article v-for="experience in experiences" :key="experience.id" class="career-card"><h3>{{ experience.company || '公司未确认' }} · {{ experience.title || '岗位未确认' }}</h3><p>{{ experience.summary || '暂无公开摘要' }}</p><p class="source">更新于 {{ date(experience.updated_at) }} <a v-if="source(experience.source_url)" :href="source(experience.source_url)" target="_blank" rel="noopener noreferrer">原始来源</a></p></article>
      <div class="actions"><button :disabled="experienceOffset === 0" @click="experienceOffset -= 20; load()">上一页面经</button><button :disabled="experiences.length < 20" @click="experienceOffset += 20; load()">下一页面经</button></div>
      <p class="source">此页不触发采集或实时搜索；资料更新由管理员核实和审核。</p>
    </template>
  </section>
</template>

<style scoped>
.career-library { max-width:1050px; margin:auto; } header p,.source { color:#64748b; font-size:14px; } .career-card { background:white; border:1px solid #e2e8f0; border-radius:10px; padding:20px; margin:16px 0; } h3 { margin-top:0; } .actions { display:flex; gap:16px; flex-wrap:wrap; align-items:center; } button { cursor:pointer; padding:7px 12px; } .body { white-space:pre-wrap; line-height:1.8; max-width:80ch; } li { margin:10px 0; } [role=alert] { color:#b91c1c; }
button { background:white; border:1px solid var(--line); border-radius:6px; color:var(--ink); } .primary { display:inline-block; background:var(--brand); color:white; padding:8px 14px; border-radius:6px; text-decoration:none; } .detail { margin-top:20px; padding-top:16px; border-top:1px solid var(--line); } .detail h3 { margin-bottom:10px; }
</style>
