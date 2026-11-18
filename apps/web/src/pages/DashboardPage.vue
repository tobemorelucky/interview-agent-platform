<script setup lang="ts">
import { onMounted, ref } from "vue"
import { useAuthStore } from "../stores/auth"
import { listResumes } from "../api/resume"
import { readProfile } from "../api/profile"
import { listSessions } from "../api/interview"
import { listInterviewReports, listMemoryProposals } from "../api/interview-report"
const auth = useAuthStore()
const loading = ref(true), error = ref("")
const resumeName = ref("还没有上传简历"), profileState = ref("尚未生成")
const completed = ref<number | null>(null), latestPerformance = ref<number | null>(null)
const pending = ref<number | null>(null), recentReportId = ref<number | null>(null)
const actions = [
  { title: "上传 / 更新简历", description: "保留原始材料，生成可追溯的候选人画像。", path: "/resumes" },
  { title: "查看岗位推荐", description: "基于画像和公开岗位要求，了解匹配依据与待补充信息。", path: "/jobs/matches" },
  { title: "开始模拟面试", description: "选择岗位，在真实的提问和追问中练习表达。", path: "/interview" },
]
async function load() {
  loading.value = true; error.value = ""
  const results = await Promise.allSettled([listResumes(), readProfile(), listSessions(), listInterviewReports()])
  const [resumes, profile, sessions, reports] = results
  if (resumes.status === "fulfilled") resumeName.value = resumes.value.items[0]?.filename || "还没有上传简历"
  if (profile.status === "fulfilled") profileState.value = profile.value.current ? `已生成 · 第 ${profile.value.current.version_number} 版` : "尚未生成"
  if (sessions.status === "fulfilled") completed.value = sessions.value.filter(s => s.status === "COMPLETED").length
  if (reports.status === "fulfilled") {
    const current = reports.value.items.find(r => r.is_current && (r.generation_status || r.status) === "COMPLETED")
    recentReportId.value = current?.id || null
    latestPerformance.value = current?.statistics?.overall_average ?? null
    try {
      const lists = await Promise.all(reports.value.items.filter(r => r.is_current).map(r => listMemoryProposals(r.id)))
      pending.value = lists.reduce((count, list) => count + list.items.filter(p => p.status === "PENDING").length, 0)
    } catch { error.value = "记忆建议状态暂时无法读取，请在面试报告中查看。"; pending.value = null }
  }
  if (results.some(r => r.status === "rejected")) error.value = "部分求职状态暂时无法读取，请刷新重试；不会将读取失败当作空数据。"
  loading.value = false
}
onMounted(load)
</script>
<template>
  <div class="dashboard">
    <header><h1>准备你的下一场技术面试</h1><p>欢迎，{{ auth.currentUser?.username || auth.currentUser?.email }}。从真实材料出发，通过面试练习持续成长。</p></header>
    <p v-if="loading" role="status">正在读取求职状态…</p>
    <p v-if="error" role="alert">{{ error }} <button @click="load">重试</button></p>
    <section class="status"><h2>当前求职状态</h2><div class="status-grid">
      <div><span>当前简历</span><strong>{{ resumeName }}</strong></div>
      <div><span>候选人画像</span><strong>{{ profileState }}</strong></div>
      <div><span>已完成模拟面试</span><strong>{{ completed ?? '暂未读取' }}</strong></div>
      <div><span>最近面试表现</span><strong>{{ latestPerformance == null ? '暂无有效报告均分' : latestPerformance + ' / 5' }}</strong></div>
      <div><span>待你确认的记忆建议</span><strong>{{ pending ?? '暂未读取' }}</strong></div>
    </div></section>
    <section class="feature-grid"><router-link v-for="item in actions" :key="item.path" :to="item.path" class="feature-card"><h2>{{ item.title }}</h2><p>{{ item.description }}</p></router-link>
      <router-link :to="recentReportId ? '/interview/reports/' + recentReportId : '/interview/reports'" class="feature-card"><h2>查看最近报告</h2><p>分别查看本场数据、Coach 分析与下一步练习建议。</p></router-link>
    </section>
    <section class="workflow"><h2>从简历到长期成长</h2><p>上传简历 → 生成候选人画像 → 选择 / 推荐岗位 → 模拟面试 → 回答评价 → 面试报告 → 长期成长记录</p><p>面试表现不会自动改写你的画像；是否把某条建议记入成长记录，由你确认。</p><router-link to="/qa">使用知识问答辅助准备</router-link></section>
  </div>
</template>
<style scoped>
.dashboard{max-width:1120px;margin:auto}.dashboard header{margin-bottom:24px}.dashboard p{color:#667085;margin-top:10px}
.status,.workflow{padding:24px;background:white;border:1px solid #e2e8f0;border-radius:12px;margin:20px 0}
.status-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:20px;margin-top:20px}
.status-grid span,.status-grid strong,.status-grid small{display:block}.status-grid span,.status-grid small{color:#667085}.status-grid strong{margin-top:8px;overflow-wrap:anywhere}
.feature-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}.feature-card{padding:24px;border:1px solid #d9e2ec;border-radius:12px;background:white;text-decoration:none;color:#243047}.feature-card:hover{border-color:#315be5}

</style>
