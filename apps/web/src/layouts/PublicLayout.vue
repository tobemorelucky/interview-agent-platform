<template>
  <div class="public-layout">
    <div class="public-card">
      <h1 class="public-title">Interview Agent Platform</h1>
      <slot />
    </div>
  </div>
</template>

<style scoped>
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
