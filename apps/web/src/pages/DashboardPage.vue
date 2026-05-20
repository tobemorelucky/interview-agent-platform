<script setup lang="ts">
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();

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
</script>

<template>
  <div class="dashboard">
    <h2 class="welcome">欢迎，{{ auth.currentUser?.username || auth.currentUser?.email }}</h2>
    <div class="feature-grid">
      <div v-for="f in features" :key="f.title" class="feature-card">
        <h3>{{ f.title }}</h3>
        <p>{{ f.description }}</p>
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

.feature-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
  gap: 20px;
}

.feature-card {
  background: #fff;
  border-radius: 10px;
  padding: 24px;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.06);
}

.feature-card h3 {
  font-size: 17px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 10px;
}

.feature-card p {
  font-size: 14px;
  color: #888;
  line-height: 1.6;
}
</style>
