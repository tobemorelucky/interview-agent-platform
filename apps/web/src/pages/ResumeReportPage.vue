<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { getResume, getResumeReport } from "../api/resume";
import { ApiError } from "../api/client";
import type { ResumeDetail, ResumeReport } from "../types/resume";
import { sourceLabel, sourceColor, statusLabel } from "../types/resume";

const route = useRoute();
const router = useRouter();
const resumeId = Number(route.params.id);

const resume = ref<ResumeDetail | null>(null);
const report = ref<ResumeReport | null>(null);
const loading = ref(true);
const error = ref("");

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function fetchReport() {
  try {
    const r = await getResume(resumeId);
    resume.value = r;
    if (r.status === "COMPLETED") {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      report.value = await getResumeReport(resumeId);
      loading.value = false;
    } else if (r.status === "FAILED") {
      if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
      loading.value = false;
    } else if (r.status === "UPLOADED" || r.status === "PROCESSING") {
      loading.value = false;
      if (!pollTimer) {
        pollTimer = setInterval(fetchReport, 3000);
      }
    } else {
      loading.value = false;
    }
  } catch (e) {
    if (e instanceof ApiError) {
      error.value = e.message;
    } else {
      error.value = "加载失败";
    }
    loading.value = false;
  }
}

function goBack() {
  router.push("/resumes");
}

const expandedQuestion = ref<number | null>(null);
function toggleQuestion(idx: number) {
  expandedQuestion.value = expandedQuestion.value === idx ? null : idx;
}

const expandedQuery = ref<number | null>(null);
function toggleQuery(idx: number) {
  expandedQuery.value = expandedQuery.value === idx ? null : idx;
}

onMounted(fetchReport);
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <div class="report-page">
    <button class="btn-back" @click="goBack">&larr; 返回列表</button>

    <!-- Loading -->
    <div v-if="loading" class="state-box">加载中...</div>

    <!-- Error -->
    <div v-else-if="error" class="state-box error">{{ error }}</div>

    <!-- Processing -->
    <div v-else-if="resume && (resume.status === 'UPLOADED' || resume.status === 'PROCESSING')" class="state-box processing-card">
      <h3>简历正在分析中...</h3>
      <p>当前状态：{{ statusLabel(resume.status) }}</p>
      <p class="hint">分析完成后页面会自动刷新</p>
    </div>

    <!-- Failed -->
    <div v-else-if="resume && resume.status === 'FAILED'" class="state-box error">
      <h3>分析失败</h3>
      <p v-if="resume.error_message">{{ resume.error_message }}</p>
    </div>

    <!-- Report -->
    <template v-else-if="report">
      <!-- 1. Resume Summary -->
      <section v-if="report.summary_json" class="section">
        <h3>简历摘要</h3>
        <div class="summary-grid">
          <div v-if="report.summary_json.basic_info" class="info-card">
            <h4>基本信息</h4>
            <p v-if="report.summary_json.basic_info.name">姓名：{{ report.summary_json.basic_info.name }}</p>
            <p v-if="report.summary_json.basic_info.target_role">目标岗位：{{ report.summary_json.basic_info.target_role }}</p>
            <p v-if="report.summary_json.basic_info.years_of_experience">工作年限：{{ report.summary_json.basic_info.years_of_experience }} 年</p>
            <p v-if="report.summary_json.basic_info.current_role">当前职位：{{ report.summary_json.basic_info.current_role }}</p>
          </div>
          <div v-if="report.summary_json.skills" class="info-card">
            <h4>技术栈</h4>
            <div class="skill-tags">
              <span v-for="s in report.summary_json.skills.languages" :key="s" class="tag lang">{{ s }}</span>
              <span v-for="s in report.summary_json.skills.frameworks" :key="s" class="tag fw">{{ s }}</span>
              <span v-for="s in report.summary_json.skills.databases" :key="s" class="tag db">{{ s }}</span>
              <span v-for="s in report.summary_json.skills.ai_ml" :key="s" class="tag ai">{{ s }}</span>
              <span v-for="s in report.summary_json.skills.other" :key="s" class="tag other">{{ s }}</span>
            </div>
          </div>
        </div>

        <div v-if="report.summary_json.highlights?.length" class="info-card">
          <h4>亮点</h4>
          <ul>
            <li v-for="h in report.summary_json.highlights" :key="h">{{ h }}</li>
          </ul>
        </div>

        <div v-if="report.summary_json.risk_points?.length" class="info-card">
          <h4>风险点</h4>
          <div v-for="r in report.summary_json.risk_points" :key="r.area" class="risk-item">
            <span class="risk-badge" :class="'risk-' + r.severity.toLowerCase()">{{ r.severity }}</span>
            <strong>{{ r.area }}</strong>
            <p>{{ r.description }}</p>
          </div>
        </div>
      </section>

      <!-- 2. Retrieval Process -->
      <section v-if="report.retrieved_context_json" class="section">
        <h3>检索过程</h3>
        <p class="retrieval-summary">
          知识库检索状态：
          <span v-if="report.retrieved_context_json.total_hits > 0" class="hit-count">
            共命中 {{ report.retrieved_context_json.total_hits }} 条
          </span>
          <span v-else class="no-hit">未命中</span>
        </p>

        <div v-if="!report.retrieved_context_json.queries?.length" class="no-kb-hint">
          KB 检索未启用或未生成检索查询。
        </div>

        <div v-for="(qr, i) in report.retrieved_context_json.queries" :key="i" class="query-result">
          <div class="query-header" @click="toggleQuery(i)">
            <span class="query-target-badge">{{ qr.target }}</span>
            <span class="query-text">{{ qr.query }}</span>
            <span class="query-hit-badge">命中 {{ qr.hit_count }} 条</span>
            <span class="expand-icon">{{ expandedQuery === i ? '▾' : '▸' }}</span>
          </div>
          <div v-if="expandedQuery === i && qr.top_hits?.length" class="query-hits">
            <div v-for="hit in qr.top_hits" :key="hit.chunk_id" class="hit-item">
              <div class="hit-title">{{ hit.title }}</div>
              <div class="hit-preview">{{ hit.preview }}</div>
              <div class="hit-meta">
                相关度：{{ (hit.score * 100).toFixed(0) }}% &middot; 来源：{{ hit.source_type }}
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 3. Interview Questions -->
      <section v-if="report.questions_json?.questions?.length" class="section">
        <h3>面试问题列表（共 {{ report.questions_json.questions.length }} 题）</h3>

        <div v-for="(q, i) in report.questions_json.questions" :key="i" class="question-card">
          <div class="question-header" @click="toggleQuestion(i)">
            <span class="question-number">Q{{ i + 1 }}</span>
            <span class="question-text">{{ q.question }}</span>
            <span class="expand-icon">{{ expandedQuestion === i ? '▾' : '▸' }}</span>
          </div>

          <div class="question-meta">
            <span class="source-badge" :style="{ background: sourceColor(q.source) }">
              {{ sourceLabel(q.source) }}
            </span>
            <span class="category-badge">{{ q.category }}</span>
            <span class="difficulty-badge" :class="'diff-' + q.difficulty.toLowerCase()">{{ q.difficulty }}</span>
          </div>

          <div v-if="expandedQuestion === i" class="question-detail">
            <div class="detail-block">
              <strong>追问原因</strong>
              <p>{{ q.reason }}</p>
            </div>
            <div class="detail-block">
              <strong>参考回答</strong>
              <p class="answer">{{ q.suggested_answer }}</p>
            </div>
            <div v-if="q.follow_up_questions?.length" class="detail-block">
              <strong>后续追问</strong>
              <ul>
                <li v-for="f in q.follow_up_questions" :key="f">{{ f }}</li>
              </ul>
            </div>
            <div v-if="q.evidence" class="detail-block evidence-block">
              <strong>引用来源</strong>
              <p class="evidence-title">{{ q.evidence.title }}</p>
              <p class="evidence-preview">{{ q.evidence.preview }}</p>
              <p class="evidence-meta">
                相关度：{{ (q.evidence.score * 100).toFixed(0) }}%
                &middot; {{ q.evidence.source_type }}
              </p>
            </div>
          </div>
        </div>
      </section>

      <!-- 4. Suggestions -->
      <section v-if="report.suggestions_json" class="section">
        <h3>综合建议</h3>
        <div class="info-card">
          <h4>优势</h4>
          <ul>
            <li v-for="s in report.suggestions_json.strengths" :key="s">{{ s }}</li>
          </ul>
        </div>
        <div v-if="report.suggestions_json.weaknesses_to_prepare?.length" class="info-card">
          <h4>需要重点准备</h4>
          <ul>
            <li v-for="w in report.suggestions_json.weaknesses_to_prepare" :key="w">{{ w }}</li>
          </ul>
        </div>
        <div v-if="report.suggestions_json.interview_tips?.length" class="info-card">
          <h4>面试技巧</h4>
          <ul>
            <li v-for="t in report.suggestions_json.interview_tips" :key="t">{{ t }}</li>
          </ul>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.report-page {
  max-width: 900px;
  margin: 0 auto;
}

.btn-back {
  padding: 6px 16px;
  font-size: 14px;
  color: #409eff;
  background: #fff;
  border: 1px solid #409eff;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 20px;
}

.btn-back:hover {
  background: #ecf5ff;
}

.state-box {
  text-align: center;
  padding: 60px 0;
  color: #999;
  font-size: 15px;
}

.state-box.error {
  color: #f56c6c;
}

.processing-card h3 {
  font-size: 18px;
  color: #409eff;
  margin-bottom: 8px;
}

.processing-card .hint {
  font-size: 12px;
  color: #aaa;
  margin-top: 8px;
}

/* Sections */
.section {
  margin-bottom: 32px;
}

.section h3 {
  font-size: 18px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 2px solid #409eff;
}

/* Summary */
.summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
  margin-bottom: 16px;
}

.info-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.05);
  margin-bottom: 12px;
}

.info-card h4 {
  font-size: 14px;
  font-weight: 600;
  color: #555;
  margin-bottom: 8px;
}

.info-card p {
  font-size: 13px;
  color: #666;
  margin: 4px 0;
}

.info-card ul {
  padding-left: 18px;
  font-size: 13px;
  color: #666;
}

.skill-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.tag {
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 4px;
  color: #fff;
}

.tag.lang { background: #409eff; }
.tag.fw { background: #67c23a; }
.tag.db { background: #e6a23c; }
.tag.ai { background: #f56c6c; }
.tag.other { background: #909399; }

.risk-item {
  padding: 8px 0;
  border-bottom: 1px solid #f5f5f5;
}

.risk-item:last-child {
  border-bottom: none;
}

.risk-badge {
  display: inline-block;
  padding: 1px 8px;
  font-size: 10px;
  font-weight: 600;
  border-radius: 4px;
  margin-right: 8px;
  color: #fff;
}

.risk-high { background: #f56c6c; }
.risk-medium { background: #e6a23c; }
.risk-low { background: #409eff; }

.risk-item strong {
  font-size: 13px;
  color: #333;
}

.risk-item p {
  margin-top: 4px;
  font-size: 12px;
  color: #999;
}

/* Retrieval */
.retrieval-summary {
  font-size: 14px;
  color: #666;
  margin-bottom: 16px;
}

.hit-count { color: #67c23a; font-weight: 600; }
.no-hit { color: #f56c6c; }

.no-kb-hint {
  font-size: 13px;
  color: #aaa;
  padding: 12px 0;
}

.query-result {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.05);
  margin-bottom: 8px;
  overflow: hidden;
}

.query-header {
  display: flex;
  align-items: center;
  padding: 10px 16px;
  cursor: pointer;
  gap: 12px;
}

.query-header:hover {
  background: #fafafa;
}

.query-target-badge {
  padding: 2px 8px;
  font-size: 11px;
  background: #ecf5ff;
  color: #409eff;
  border-radius: 4px;
  flex-shrink: 0;
}

.query-text {
  flex: 1;
  font-size: 13px;
  color: #333;
}

.query-hit-badge {
  font-size: 12px;
  color: #67c23a;
  flex-shrink: 0;
}

.expand-icon {
  font-size: 14px;
  color: #aaa;
  flex-shrink: 0;
}

.query-hits {
  border-top: 1px solid #f0f0f0;
  padding: 12px 16px;
}

.hit-item {
  padding: 8px 0;
  border-bottom: 1px solid #f9f9f9;
}

.hit-item:last-child {
  border-bottom: none;
}

.hit-title {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}

.hit-preview {
  font-size: 12px;
  color: #888;
  margin: 4px 0;
}

.hit-meta {
  font-size: 11px;
  color: #aaa;
}

/* Questions */
.question-card {
  background: #fff;
  border-radius: 8px;
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.05);
  margin-bottom: 12px;
  overflow: hidden;
}

.question-header {
  display: flex;
  align-items: center;
  padding: 14px 16px;
  cursor: pointer;
  gap: 12px;
}

.question-header:hover {
  background: #fafafa;
}

.question-number {
  font-size: 14px;
  font-weight: 600;
  color: #409eff;
  flex-shrink: 0;
}

.question-text {
  flex: 1;
  font-size: 14px;
  color: #333;
  font-weight: 500;
}

.question-meta {
  padding: 0 16px 10px;
  display: flex;
  gap: 8px;
}

.source-badge {
  padding: 2px 10px;
  font-size: 11px;
  color: #fff;
  border-radius: 10px;
}

.category-badge {
  padding: 2px 8px;
  font-size: 11px;
  color: #666;
  background: #f5f5f5;
  border-radius: 4px;
}

.difficulty-badge {
  padding: 2px 8px;
  font-size: 11px;
  border-radius: 4px;
  color: #fff;
}

.diff-easy { background: #67c23a; }
.diff-medium { background: #e6a23c; }
.diff-hard { background: #f56c6c; }

.question-detail {
  padding: 0 16px 16px;
  border-top: 1px solid #f0f0f0;
}

.detail-block {
  margin-top: 12px;
}

.detail-block strong {
  font-size: 13px;
  color: #555;
}

.detail-block p,
.detail-block li {
  font-size: 13px;
  color: #666;
  margin: 4px 0;
  line-height: 1.6;
}

.detail-block ul {
  padding-left: 18px;
}

.answer {
  white-space: pre-wrap;
}

.evidence-block {
  background: #f9fafb;
  border-radius: 6px;
  padding: 10px 14px;
}

.evidence-title {
  font-weight: 500;
  color: #333 !important;
}

.evidence-preview {
  color: #888 !important;
}

.evidence-meta {
  font-size: 11px !important;
  color: #aaa !important;
}
</style>
