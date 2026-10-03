<template>
  <div class="public-layout">
    <div class="public-card">
      <h1 class="public-title">秋招面试助手</h1>
      <p class="product-caption">面向秋招的多 Agent 求职与模拟面试系统</p>
      <slot />
    </div>
  </div>
</template>

<style scoped>
.product-caption {text-align:center;color:#667085;font-size:13px;margin-bottom:20px;}
.public-layout {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f2f5;
  padding: 24px;
}

.public-card {
  background: #fff;
  border-radius: 12px;
  padding: 40px 32px;
  width: 100%;
  max-width: 420px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
}

.public-title {
  text-align: center;
  font-size: 22px;
  font-weight: 700;
  color: #1a1a2e;
  margin-bottom: 32px;
}
</style>
