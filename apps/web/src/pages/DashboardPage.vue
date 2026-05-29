<script setup lang="ts">
import { useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const router = useRouter();

const features = [
  {
    title: "知识库问答",
    description: "基于面试知识库的智能问答，覆盖后端、AI、RAG、Agent 等高频面试话题。",
  },
  {
    title: "简历模拟面试",
    description: "上传您的简历，系统自动识别技术栈与项目亮点，生成针对性面试追问与参考答案。",
  },
  {
    title: "面经库查询",
    description: "查询已发布的牛客、小红书、抖音等多平台真实面经，了解最新面试趋势。",
  },
];

function goInterview() {
  router.push("/interview");
}

function goAdminKb() {
  router.push("/admin/kb/documents");
}
function goAdminExperiences() {
  router.push("/admin/experiences");
}
</script>

<template>
  <div class="dashboard">
    <h2 class="welcome">欢迎，{{ auth.currentUser?.username || auth.currentUser?.email }}</h2>

    <!-- Admin section -->
    <div v-if="auth.isAdmin" class="admin-section">
      <h3 class="section-title">管理</h3>
      <div class="feature-grid">
        <div class="feature-card admin-card" @click="goAdminKb">
          <h3>知识库管理</h3>
          <p>上传、查看、删除、重新索引知识库文档。修改分块/embedding配置后需在此重新索引。</p>
        </div>
        <div class="feature-card admin-card" @click="goAdminExperiences">
          <h3>面经更新管理</h3>
          <p>维护公司、岗位、平台关键词，创建近期面经采集任务，审核和发布面经内容。</p>
        </div>
      </div>
    </div>

    <h3 class="section-title">功能</h3>
    <div class="feature-grid">
      <div class="feature-card" @click="router.push('/qa')" style="cursor:pointer">
        <h3>{{ features[0].title }}</h3>
        <p>{{ features[0].description }}</p>
      </div>
      <div class="feature-card resume-card" @click="goInterview">
        <h3>{{ features[1].title }}</h3>
        <p>{{ features[1].description }}</p>
      </div>
      <div class="feature-card">
        <h3>{{ features[2].title }}</h3>
        <p>{{ features[2].description }}</p>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dashboard {
  max-width: 900px;
  margin: 0 auto;
}

.welcome {
  font-size: 22px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 32px;
}

.section-title {
  font-size: 15px;
  font-weight: 500;
  color: #999;
  margin-bottom: 12px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.admin-section {
  margin-bottom: 32px;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
  margin-bottom: 32px;
}

.feature-card {
  background: #fff;
  border-radius: 10px;
  padding: 24px;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.06);
}

.admin-card {
  border: 2px solid #e6a23c;
  cursor: pointer;
  transition: box-shadow 0.2s;
}

.admin-card:hover {
  box-shadow: 0 2px 16px rgba(230, 162, 60, 0.2);
}

.admin-card h3 {
  color: #e6a23c;
}

.feature-card h3 {
  font-size: 17px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 10px;
}

.resume-card {
  border: 2px solid #409eff;
  cursor: pointer;
  transition: box-shadow 0.2s;
}

.resume-card:hover {
  box-shadow: 0 2px 16px rgba(64, 158, 255, 0.2);
}

.resume-card h3 {
  color: #409eff;
}

.feature-card p {
  font-size: 14px;
  color: #888;
  line-height: 1.6;
}
</style>
