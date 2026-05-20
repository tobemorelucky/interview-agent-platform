<script setup lang="ts">
import { useAuthStore } from "../stores/auth";
import { useRouter } from "vue-router";

const auth = useAuthStore();
const router = useRouter();

function handleLogout() {
  auth.logout();
  router.push("/login");
}
</script>

<template>
  <div class="user-layout">
    <header class="topbar">
      <span class="brand">Interview Agent Platform</span>
      <div class="topbar-right">
        <span v-if="auth.currentUser" class="user-info">
          {{ auth.currentUser.email }}
          <span v-if="auth.isAdmin" class="badge-admin">管理员</span>
        </span>
        <button class="btn-logout" @click="handleLogout">退出</button>
      </div>
    </header>
    <main class="main-content">
      <slot />
    </main>
  </div>
</template>

<style scoped>
.user-layout {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background: #f0f2f5;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
  padding: 0 24px;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  flex-shrink: 0;
}

.brand {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a2e;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}

.user-info {
  font-size: 14px;
  color: #555;
}

.badge-admin {
  display: inline-block;
  margin-left: 6px;
  padding: 0 6px;
  font-size: 11px;
  line-height: 20px;
  color: #fff;
  background: #e6a23c;
  border-radius: 4px;
}

.btn-logout {
  padding: 4px 14px;
  font-size: 13px;
  color: #666;
  background: #f5f5f5;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  cursor: pointer;
}

.btn-logout:hover {
  color: #e6a23c;
  border-color: #e6a23c;
}

.main-content {
  flex: 1;
  padding: 24px;
}
</style>
