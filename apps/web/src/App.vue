<script setup lang="ts">
const features = [
  {
    title: "知识库问答",
    description: "基于面试知识库的智能问答，覆盖后端、AI、RAG、Agent 等高频面试话题。",
    icon: "📚",
  },
  {
    title: "简历模拟面试",
    description: "上传您的简历，系统自动识别技术栈与项目亮点，生成针对性面试追问与参考答案。",
    icon: "📄",
  },
  {
    title: "面经库查询",
    description: "查询已发布的牛客、小红书、抖音等多平台真实面经，了解最新面试趋势。",
    icon: "🔍",
  },
];
</script>

<template>
  <div class="page">
    <header class="header">
      <h1 class="title">Interview Agent Platform</h1>
      <p class="subtitle">智能面试准备平台 — 让每一次面试都更有把握</p>
    </header>

    <section class="features">
      <div v-for="f in features" :key="f.title" class="card">
        <span class="icon">{{ f.icon }}</span>
        <h2 class="card-title">{{ f.title }}</h2>
        <p class="card-desc">{{ f.description }}</p>
      </div>
    </section>

    <footer class="footer">
      <p>Phase 0 · 工程骨架</p>
    </footer>
  </div>
</template>

<style>
*,
*::before,
*::after {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, "Noto Sans SC", sans-serif;
  background: #f5f7fa;
  color: #333;
  min-height: 100vh;
}

.page {
  display: flex;
  flex-direction: column;
  align-items: center;
  min-height: 100vh;
  padding: 60px 20px 40px;
}

.header {
  text-align: center;
  margin-bottom: 48px;
}

.title {
  font-size: 32px;
  font-weight: 700;
  color: #1a1a2e;
  margin-bottom: 8px;
}

.subtitle {
  font-size: 16px;
  color: #666;
}

.features {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  justify-content: center;
  max-width: 960px;
}

.card {
  background: #fff;
  border-radius: 12px;
  padding: 32px 24px;
  width: 280px;
  text-align: center;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  transition: box-shadow 0.2s;
}

.card:hover {
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
}

.icon {
  font-size: 40px;
  display: block;
  margin-bottom: 16px;
}

.card-title {
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 12px;
  color: #1a1a2e;
}

.card-desc {
  font-size: 14px;
  color: #888;
  line-height: 1.6;
}

.footer {
  margin-top: auto;
  padding-top: 48px;
  font-size: 13px;
  color: #bbb;
}
</style>
