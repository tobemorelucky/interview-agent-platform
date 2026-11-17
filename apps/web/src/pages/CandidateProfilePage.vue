<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import { displayLabel, modelModeLabel, userError } from '../i18n/zh-CN';
import { listResumes } from '../api/resume';
import type { Resume } from '../types/resume';
import { correctProfile, generateProfile, readProfile, readVersion, readVersions } from '../api/profile';
import type { ProfileFact, ProfileState, ProfileVersion, VersionSummary } from '../api/profile';

const resumes = ref<Resume[]>([]);
const state = ref<ProfileState>({ current: null, generations: [] });
const profile = ref<ProfileVersion | null>(null);
const versions = ref<VersionSummary[]>([]);
const busy = ref(false);
const error = ref('');
const notice = ref('');
const resumeId = ref<number | null>(null);
const edit = ref<{ path: string; value: string; reason: string } | null>(null);
const opened = ref<{ sectionKey: string; index: number; fact: ProfileFact } | null>(null);
const showGeneration = ref(false);
const isCurrent = computed(() => profile.value?.id === state.value.current?.id);
const evidence = computed(() => new Map(profile.value?.evidence.map(item => [item.key, item]) || []));

const SECTION_LABELS: Record<string, string> = {
  education: '教育背景', projects: '项目经历', work_experience: '工作经历',
  internship_experience: '实习经历', strengths: '简历中的自我描述',
};
const ORDER = ['education', 'skills', 'projects', 'work_experience', 'internship_experience', 'strengths'];

// Group by what the user is looking for, using the skill statement's own words.
// Untagged skills fall into 其他 rather than being hidden.
const TECH_GROUPS: { key: string; label: string; match: RegExp }[] = [
  { key: 'lang', label: '编程语言', match: /Java|Python|C\+\+|Golang|Go\b|JavaScript|TypeScript|C#|Rust|PHP/i },
  { key: 'backend', label: '后端与框架', match: /Spring|FastAPI|Django|Flask|微服务|服务端|后端|接口|并发|Kafka|消息|分布式/i },
  { key: 'data', label: '数据库与缓存', match: /MySQL|PostgreSQL|Redis|MongoDB|Elasticsearch|数据库|缓存|SQL|索引/i },
  { key: 'ai', label: 'AI / 大模型 / 检索', match: /大模型|LLM|Agent|RAG|LangChain|LangGraph|Milvus|向量|检索|Transformer|Prompt|微调|深度学习|机器学习/i },
  { key: 'tools', label: '工程与工具', match: /Linux|Git|Docker|Kubernetes|K8s|CI|测试|运维|部署/i },
];

function facts(key: string): ProfileFact[] {
  return (profile.value?.snapshot[key] as ProfileFact[] | undefined) || [];
}
// The parser can emit the same sentence more than once (e.g. one "技术栈" line
// per project). Show it once, but keep the original index so corrections still
// target the stored fact.
function deduped(key: string): { fact: ProfileFact; index: number; duplicates: number }[] {
  const seen = new Map<string, { fact: ProfileFact; index: number; duplicates: number }>();
  facts(key).forEach((fact, index) => {
    const fingerprint = (fact.statement || '').normalize('NFKC').replace(/\s+/g, ' ').trim().toLowerCase();
    const existing = seen.get(fingerprint);
    if (existing) existing.duplicates += 1;
    else seen.set(fingerprint, { fact, index, duplicates: 1 });
  });
  return [...seen.values()];
}
function provenance(sectionKey: string, index: number) {
  return profile.value?.provenance?.[`${sectionKey}.${index}`];
}
const skillGroups = computed(() => {
  const items = deduped('skills');
  const groups = TECH_GROUPS.map(g => ({ ...g, items: [] as { fact: ProfileFact; index: number; duplicates: number }[] }));
  const others: { fact: ProfileFact; index: number; duplicates: number }[] = [];
  items.forEach(entry => {
    const text = `${entry.fact.label || ''} ${entry.fact.statement}`;
    const hit = groups.find(g => g.match.test(text));
    (hit ? hit.items : others).push(entry);
  });
  if (others.length) groups.push({ key: 'other', label: '其他技术', match: /./, items: others });
  return groups.filter(g => g.items.length);
});
const factCount = computed(() => ORDER.reduce((n, key) => n + deduped(key).length, 0));

async function load() {
  busy.value = true;
  error.value = '';
  try {
    const [profileState, versionList, resumeList] = await Promise.all([readProfile(), readVersions(), listResumes(1, 50)]);
    state.value = profileState; versions.value = versionList;
    resumes.value = resumeList.items.filter(item => item.status === 'COMPLETED');
    resumeId.value ??= resumes.value[0]?.id || null;
    profile.value = state.value.current;
    resumeId.value ??= profile.value?.source_resume_id || null;
  } catch (err) { error.value = err instanceof Error ? err.message : '读取失败'; }
  finally { busy.value = false; }
}
async function showVersion(event: Event) {
  busy.value = true;
  try { profile.value = await readVersion(Number((event.target as HTMLSelectElement).value)); }
  catch (err) { error.value = err instanceof Error ? err.message : '读取失败'; }
  finally { busy.value = false; }
}
function openFact(sectionKey: string, index: number, fact: ProfileFact) { opened.value = { sectionKey, index, fact }; }
function correctFromDrawer(field: 'label' | 'statement') {
  if (!opened.value) return;
  const { sectionKey, index, fact } = opened.value;
  edit.value = { path: `${sectionKey}.${index}.${field}`,
    value: field === 'label' ? (fact.label || '') : fact.statement, reason: '' };
  opened.value = null;
}
async function saveCorrection() {
  if (!edit.value || !profile.value) return;
  busy.value = true;
  error.value = '';
  try {
    await correctProfile({ base_version_id: profile.value.id, field_path: edit.value.path,
      new_value: edit.value.value, reason: edit.value.reason });
    edit.value = null;
    notice.value = '修正已生成完整新版本，旧版本保持不变。';
    await load();
  } catch (err) { error.value = err instanceof Error ? err.message : '修正失败'; }
  finally { busy.value = false; }
}
async function generate(regenerate: boolean) {
  if (!resumeId.value) return;
  busy.value = true;
  error.value = '';
  try {
    await generateProfile(resumeId.value, regenerate);
    notice.value = '画像任务已入队，稍后点击刷新查看结果。';
  } catch (err) { error.value = err instanceof Error ? err.message : '提交失败'; }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template>
  <section class="profile-page">
    <header class="page-head">
      <div><h1>我的画像</h1><p>来自简历与本人确认的事实，用来匹配岗位；不等同于面试能力评分。</p></div>
      <button class="ghost" :disabled="busy" @click="load">{{ busy ? '处理中…' : '刷新' }}</button>
    </header>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status" class="notice">{{ notice }}</p>

    <div v-if="!profile && !busy && !error" class="card empty">
      <p>还没有候选人画像。先上传简历，系统会生成画像；也可以选择一份已完成的简历生成。</p>
      <router-link class="primary" to="/resumes">去上传简历</router-link>
    </div>

    <template v-if="profile">
      <section class="card summary-card">
        <div class="summary-head">
          <div>
            <h2>个人摘要</h2>
            <p class="summary-text">{{ profile.snapshot.candidate_summary?.statement || '简历中没有可用的自我介绍。' }}</p>
          </div>
          <div class="summary-actions">
            <button v-if="isCurrent && profile.snapshot.candidate_summary" class="ghost"
              @click="openFact('candidate_summary', 0, profile.snapshot.candidate_summary)">来源与修正</button>
          </div>
        </div>
        <p class="meta">
          画像第 {{ profile.version_number }} 版 · {{ displayLabel(profile.producer, '画像生成') }} ·
          {{ modelModeLabel(profile.provider, profile.model) }} · 共 {{ factCount }} 条事实
          <template v-if="!isCurrent">· 历史版本只读</template>
        </p>
        <p v-if="profile.producer === 'REPLAY_FIXTURE'" class="banner">DEMO · Replay 回放模式：此版本来自本地 fixture，不是真实模型验收。</p>
        <div class="summary-tools">
          <label>切换版本
            <select :value="profile.id" :disabled="busy" @change="showVersion">
              <option v-for="version in versions" :key="version.id" :value="version.id">
                第 {{ version.version_number }} 版 · {{ displayLabel(version.producer, '画像生成') }}
              </option>
            </select>
          </label>
          <label>用已完成简历生成
            <select v-model="resumeId">
              <option :value="null">请选择简历</option>
              <option v-for="item in resumes" :key="item.id" :value="item.id">{{ item.filename }}</option>
            </select>
          </label>
          <button class="ghost" :disabled="busy || !resumeId" @click="generate(false)">生成 / 重试</button>
          <button class="ghost" :disabled="busy || !resumeId" @click="generate(true)">重新生成新版本</button>
        </div>
        <details class="advanced"><summary>高级信息</summary>
          <p>版本 ID {{ profile.id }} · 来源简历 #{{ profile.source_resume_id }} · 提示版本 {{ profile.prompt_version }}</p>
          <p>模型 {{ profile.model || '未记录' }} · 生成时间 {{ profile.created_at }}</p>
          <p v-if="profile.snapshot.candidate_summary">摘要依据 {{ profile.snapshot.candidate_summary.evidence_ids?.length || 0 }} 条</p>
        </details>
      </section>

      <section v-if="skillGroups.length" class="card">
        <h2>技术栈</h2>
        <div class="tech-grid">
          <div v-for="group in skillGroups" :key="group.key" class="tech-group">
            <h3>{{ group.label }}</h3>
            <ul>
              <li v-for="entry in group.items" :key="entry.index">
                <span>{{ entry.fact.statement }}</span>
                <button class="link" @click="openFact('skills', entry.index, entry.fact)">来源{{ isCurrent ? '与修正' : '' }}</button>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <section v-for="key in ORDER.filter(k => k !== 'skills')" :key="key" class="card">
        <h2>{{ SECTION_LABELS[key] }}</h2>
        <p v-if="!deduped(key).length" class="meta">简历中没有识别到这一部分。</p>
        <ul class="fact-list">
          <li v-for="entry in deduped(key)" :key="entry.index">
            <div>
              <strong v-if="entry.fact.label">{{ entry.fact.label }}</strong>
              <p>{{ entry.fact.statement }}</p>
              <span v-if="provenance(key, entry.index) === 'USER_CONFIRMED'" class="tag">本人已确认</span>
              <span v-if="entry.duplicates > 1" class="tag muted">同一内容出现 {{ entry.duplicates }} 次</span>
            </div>
            <button class="link" @click="openFact(key, entry.index, entry.fact)">来源{{ isCurrent ? '与修正' : '' }}</button>
          </li>
        </ul>
      </section>

      <section class="card">
        <h2>尚未覆盖的信息</h2>
        <p v-for="item in profile.unknowns" :key="item.topic" class="meta">{{ item.topic }}：{{ item.message }}</p>
        <p v-if="!profile.unknowns.length" class="meta">暂无记录；这不表示其他能力已被证实。</p>
      </section>
    </template>

    <div v-if="opened" class="overlay" role="dialog" aria-modal="true" @click.self="opened = null">
      <div class="drawer">
        <header>
          <h2>{{ opened.fact.label || '事实来源' }}</h2>
          <button class="ghost" @click="opened = null">关闭</button>
        </header>
        <p class="statement">{{ opened.fact.statement }}</p>
        <p class="meta">
          {{ provenance(opened.sectionKey, opened.index) === 'USER_CONFIRMED'
            ? '包含本人确认或修正；未修改部分保留原依据' : '简历文本抽取，不代表能力认证' }}
        </p>
        <h3>简历中的原文依据</h3>
        <p v-if="!opened.fact.evidence_ids?.length" class="meta">这条事实没有可展示的原文依据。</p>
        <blockquote v-for="id in opened.fact.evidence_ids" :key="id">
          {{ evidence.get(id)?.excerpt }}
          <footer>
            {{ displayLabel(evidence.get(id)?.kind) }} · {{ displayLabel(evidence.get(id)?.section) }}
            <template v-if="evidence.get(id)?.kind === 'RESUME'">
              · 文本位置 {{ evidence.get(id)?.start_offset }}–{{ evidence.get(id)?.end_offset }}
              · 页码 {{ evidence.get(id)?.page ?? '未知' }}
            </template>
            <template v-else-if="evidence.get(id)?.reason">· 修正原因：{{ evidence.get(id)?.reason }}</template>
          </footer>
        </blockquote>
        <div v-if="isCurrent" class="drawer-actions">
          <button class="ghost" v-if="opened.fact.label" :disabled="busy" @click="correctFromDrawer('label')">修正名称</button>
          <button class="ghost" :disabled="busy" @click="correctFromDrawer('statement')">修正描述</button>
        </div>
      </div>
    </div>

    <form v-if="edit" class="card correction" @submit.prevent="saveCorrection">
      <h2>确认 / 修正事实</h2>
      <p class="meta">基于画像第 {{ profile?.version_number }} 版，保存后会生成一个完整的新版本，旧版本保持不变。</p>
      <label>新值<textarea v-model="edit.value" required maxlength="5000" /></label>
      <label>修正原因<input v-model="edit.reason" required maxlength="1000" /></label>
      <div class="drawer-actions">
        <button class="primary" :disabled="busy">保存新版本</button>
        <button type="button" class="ghost" :disabled="busy" @click="edit = null">取消</button>
      </div>
    </form>

    <section v-if="state.generations.length" class="card">
      <button class="ghost" @click="showGeneration = !showGeneration">
        {{ showGeneration ? '收起' : '查看' }}画像处理状态（{{ state.generations.length }}）
      </button>
      <template v-if="showGeneration">
        <p v-for="generation in state.generations" :key="generation.id" class="meta">
          简历画像任务 · {{ displayLabel(generation.status) }}
          <span v-if="generation.error"> · {{ userError(generation.error) }}</span>
        </p>
      </template>
    </section>
  </section>
</template>

<style scoped>
.profile-page { max-width: 1000px; margin: auto; }
.page-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 16px; flex-wrap: wrap; }
.page-head p { color: var(--muted); margin: 6px 0 0; }
.card { background: white; border: 1px solid var(--line); border-radius: 12px; padding: 20px; margin: 16px 0; }
.summary-card { border-color: var(--brand-soft, #d7e0ff); }
.summary-head { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
.summary-text { font-size: 15px; line-height: 1.8; margin: 8px 0 0; }
.summary-tools { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; margin-top: 16px; }
.summary-tools label { display: grid; gap: 6px; font-size: 13px; color: var(--muted); }
select, input, textarea { padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px; font: inherit; }
.tech-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr)); gap: 16px; }
.tech-group h3 { font-size: 14px; color: var(--muted); margin: 4px 0 8px; }
.tech-group ul { list-style: none; padding: 0; margin: 0; }
.tech-group li { display: flex; justify-content: space-between; gap: 10px; padding: 8px 0; border-top: 1px solid var(--soft); line-height: 1.7; }
.fact-list { list-style: none; padding: 0; margin: 0; }
.fact-list li { display: flex; justify-content: space-between; gap: 16px; padding: 12px 0; border-top: 1px solid var(--soft); }
.fact-list p { margin: 4px 0 0; line-height: 1.75; }
.link { background: none; border: 0; color: var(--brand); cursor: pointer; padding: 0; font-size: 13px; white-space: nowrap; }
.ghost { background: white; border: 1px solid var(--line); border-radius: 8px; padding: 8px 14px; cursor: pointer; color: var(--ink); }
.ghost:disabled { opacity: .5; cursor: default; }
.primary { background: var(--brand); color: white; border: 0; border-radius: 8px; padding: 9px 16px; cursor: pointer; text-decoration: none; display: inline-block; }
.meta { color: var(--muted); font-size: 13px; }
.tag { display: inline-block; font-size: 12px; color: var(--brand); background: var(--soft); border-radius: 999px; padding: 2px 10px; }
.tag.muted { color: var(--muted); }
.banner { background: #fff5df; color: #975600; padding: 10px 12px; border-radius: 8px; }
.advanced { margin-top: 14px; }
.advanced summary { cursor: pointer; color: var(--muted); font-size: 13px; }
.empty { text-align: center; }
.empty p { color: var(--muted); }
.overlay { position: fixed; inset: 0; background: rgba(16, 24, 40, .45); display: flex; justify-content: flex-end; z-index: 40; }
.drawer { background: white; width: min(520px, 100%); height: 100%; overflow-y: auto; padding: 22px; }
.drawer header { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
.drawer h2 { font-size: 18px; margin: 0; }
.drawer h3 { font-size: 14px; margin: 20px 0 8px; color: var(--muted); }
.statement { line-height: 1.8; }
blockquote { background: var(--soft); border-radius: 8px; padding: 12px; margin: 10px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
blockquote footer { color: var(--muted); font-size: 12px; margin-top: 8px; }
.drawer-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }
.correction label { display: grid; gap: 6px; margin-bottom: 12px; font-size: 13px; color: var(--muted); }
.correction textarea { min-height: 110px; }
.error { color: #b42318; }
@media (max-width: 640px) {
  .fact-list li { flex-direction: column; gap: 6px; }
  .summary-head { flex-direction: column; }
}
</style>
