<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { displayLabel, modelModeLabel, userError } from "../i18n/zh-CN";
import { listResumes } from "../api/resume";
import type { Resume } from "../types/resume";
const resumes = ref<Resume[]>([]);
import { correctProfile, generateProfile, readProfile, readVersion, readVersions } from "../api/profile";
import type { ProfileFact, ProfileState, ProfileVersion, VersionSummary } from "../api/profile";

const state = ref<ProfileState>({ current: null, generations: [] });
const profile = ref<ProfileVersion | null>(null);
const versions = ref<VersionSummary[]>([]);
const busy = ref(false);
const error = ref("");
const notice = ref("");
const resumeId = ref<number | null>(null);
const edit = ref<{ path: string; value: string; reason: string } | null>(null);
const isCurrent = computed(() => profile.value?.id === state.value.current?.id);
const sections = [
  { key: "skills", label: "技能" }, { key: "projects", label: "项目" },
  { key: "education", label: "教育" }, { key: "work_experience", label: "工作" },
  { key: "internship_experience", label: "实习" }, { key: "strengths", label: "简历支持的优势陈述" },
];
const evidence = computed(() => new Map(profile.value?.evidence.map(item => [item.key, item]) || []));

function facts(key: string): ProfileFact[] {
  return (profile.value?.snapshot[key] as ProfileFact[] | undefined) || [];
}
async function load() {
  busy.value = true;
  error.value = "";
  try {
    const [profileState, versionList, resumeList] = await Promise.all([readProfile(), readVersions(), listResumes(1, 50)]);
    state.value = profileState; versions.value = versionList; resumes.value = resumeList.items.filter(item => item.status === "COMPLETED");
    resumeId.value ??= resumes.value[0]?.id || null;
    profile.value = state.value.current;
    resumeId.value ??= profile.value?.source_resume_id || null;
  } catch (err) { error.value = err instanceof Error ? err.message : "读取失败"; }
  finally { busy.value = false; }
}
async function showVersion(event: Event) {
  busy.value = true;
  try { profile.value = await readVersion(Number((event.target as HTMLSelectElement).value)); }
  catch (err) { error.value = err instanceof Error ? err.message : "读取失败"; }
  finally { busy.value = false; }
}
async function saveCorrection() {
  if (!edit.value || !profile.value) return;
  busy.value = true;
  error.value = "";
  try {
    await correctProfile({ base_version_id: profile.value.id, field_path: edit.value.path,
      new_value: edit.value.value, reason: edit.value.reason });
    edit.value = null;
    notice.value = "修正已生成完整新版本，旧版本保持不变。";
    await load();
  } catch (err) { error.value = err instanceof Error ? err.message : "修正失败"; }
  finally { busy.value = false; }
}
async function generate(regenerate: boolean) {
  if (!resumeId.value) return;
  busy.value = true;
  error.value = "";
  try {
    await generateProfile(resumeId.value, regenerate);
    notice.value = "画像任务已入队，稍后点击刷新查看结果。";
  } catch (err) { error.value = err instanceof Error ? err.message : "提交失败"; }
  finally { busy.value = false; }
}
onMounted(load);
</script>

<template>
  <section class="profile-page">
    <div class="heading"><div><h1>我的画像</h1><p>简历与本人确认的事实；不等同于模拟面试能力评分。</p></div>
      <button :disabled="busy" @click="load">{{ busy ? "处理中…" : "刷新" }}</button></div>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <div class="card generation">
      <router-link to="/resumes">上传 / 查看简历</router-link>
      <label>选择已完成的简历 <select v-model="resumeId"><option :value="null">请选择简历</option><option v-for="item in resumes" :key="item.id" :value="item.id">{{ item.filename }}</option></select></label>
      <button :disabled="busy || !resumeId" @click="generate(false)">生成 / 重试画像</button>
      <button :disabled="busy || !resumeId" @click="generate(true)">明确重新生成新版本</button>
    </div>
    <div v-if="!profile && !busy && !error" class="card">还没有候选人画像，上传简历后系统会生成画像，也可选择已完成的简历报告生成。</div>
    <template v-if="profile">
      <div class="card">
        <label>画像版本 <select :value="profile.id" :disabled="busy" @change="showVersion">
          <option v-for="version in versions" :key="version.id" :value="version.id">第 {{ version.version_number }} 版 · {{ displayLabel(version.producer, "画像生成") }}</option>
        </select></label>
        <p>当前版本 {{ profile.version_number }} · {{ displayLabel(profile.producer, "画像生成") }} · {{ modelModeLabel(profile.provider, profile.model) }} · {{ profile.created_at }}</p>
        <details class="ux-technical"><summary>技术详情</summary><p>版本 ID {{ profile.id }} · 模型 {{ profile.model }} · 提示版本 {{ profile.prompt_version }}</p></details>
        <p v-if="profile.producer === 'REPLAY_FIXTURE'" class="replay">DEMO · Replay 回放模式：此版本来自本地 fixture，不是真实模型验收。</p>
        <p v-if="!isCurrent">历史版本只读。</p>
      </div>
      <article class="card"><h2>简历摘要</h2>
        <template v-if="profile.snapshot.candidate_summary">
          <p>{{ profile.snapshot.candidate_summary.statement }}</p>
          <button v-if="isCurrent" :disabled="busy" @click="edit = { path: 'candidate_summary.statement', value: profile.snapshot.candidate_summary.statement, reason: '' }">修正摘要事实</button>
          <details v-for="key in profile.snapshot.candidate_summary.evidence_ids" :key="key"><summary>查看依据 · {{ displayLabel(evidence.get(key)?.kind) }}</summary><pre>{{ evidence.get(key)?.excerpt }}</pre></details>
        </template><p v-else>当前简历材料中未提供相关证据</p>
      </article>
      <article v-for="section in sections" :key="section.key" class="card"><h2>{{ section.label }}</h2>
        <p v-if="!facts(section.key).length">当前简历材料中未提供相关证据</p>
        <div v-for="(fact, index) in facts(section.key)" :key="index" class="fact">
          <h3 v-if="fact.label">{{ fact.label }} <small v-if="fact.evidence_state">{{ displayLabel(fact.evidence_state) }}</small></h3>
          <p>{{ fact.statement }}</p>
          <small>{{ profile.provenance[`${section.key}.${index}`] === 'USER_CONFIRMED' ? '包含本人确认 / 修正；未修改部分保留原依据' : '简历文本抽取（非能力认证）' }}</small>
          <div v-if="isCurrent" class="actions">
            <button v-if="fact.label" :disabled="busy" @click="edit = { path: `${section.key}.${index}.label`, value: fact.label, reason: '' }">修正名称</button>
            <button :disabled="busy" @click="edit = { path: `${section.key}.${index}.statement`, value: fact.statement, reason: '' }">修正描述</button>
          </div>
          <details v-for="key in fact.evidence_ids" :key="key"><summary>查看依据 · {{ displayLabel(evidence.get(key)?.kind) }} · {{ displayLabel(evidence.get(key)?.section) }}</summary>
            <pre>{{ evidence.get(key)?.excerpt }}</pre>
            <small v-if="evidence.get(key)?.kind === 'RESUME'">原始简历 · 文本位置 {{ evidence.get(key)?.start_offset }}–{{ evidence.get(key)?.end_offset }} · 页码 {{ evidence.get(key)?.page ?? '未知' }}</small>
            <small v-else>修正原因：{{ evidence.get(key)?.reason }}</small>
          </details>
        </div>
      </article>
      <article class="card"><h2>当前材料未覆盖信息</h2><p v-for="item in profile.unknowns" :key="item.topic">{{ item.topic }}：{{ item.message }}</p><p v-if="!profile.unknowns.length">暂无记录；不表示其他能力已被证实。</p></article>
    </template>
    <form v-if="edit" class="card correction" @submit.prevent="saveCorrection"><h2>确认 / 修正明确事实</h2>
      <p>基于画像版本 {{ profile?.version_number }}，保存后创建完整新版本。</p>
      <label>新值<textarea v-model="edit.value" required maxlength="5000" /></label>
      <label>原因<input v-model="edit.reason" required maxlength="1000" /></label>
      <button :disabled="busy">保存新版本</button> <button type="button" :disabled="busy" @click="edit = null">取消</button>
    </form>
    <article v-if="state.generations.length" class="card"><h2>画像处理状态</h2><p v-for="generation in state.generations" :key="generation.id">简历画像任务 · {{ displayLabel(generation.status) }} <span v-if="generation.error">· {{ userError(generation.error) }}<details class="ux-technical"><summary>错误详情</summary>{{ generation.error }}</details></span></p></article>
  </section>
</template>

<style scoped>
.profile-page { max-width: 1000px; margin: auto; color: #253047; }
.heading, .generation, .actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.heading { justify-content: space-between; }
h1 { margin-bottom: 8px; } h2 { font-size: 19px; } h3 { font-size: 16px; }
.card { background: white; border: 1px solid #e2e6ee; border-radius: 10px; padding: 20px; margin: 16px 0; }
.fact { border-top: 1px solid #eef0f5; padding: 14px 0; }
button, input, select, textarea { padding: 8px 12px; border: 1px solid #ccd4e0; border-radius: 6px; }
button { cursor: pointer; background: #f5f8ff; } button:disabled { opacity: .5; cursor: default; }
small { color: #596880; } details { margin-top: 10px; } summary { cursor: pointer; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f5f7fa; padding: 12px; }
.error { color: #b42318; } .replay { color: #975600; background: #fff5df; padding: 12px; }
.correction label { display: block; margin-bottom: 12px; } .correction textarea { display: block; width: 95%; min-height: 100px; }
</style>
