<script setup lang="ts">
import { useAuthStore } from "../stores/auth";
import { useRouter, useRoute } from "vue-router";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();

const navItems = [
  { path: "/dashboard", label: "首页" },
  { path: "/qa", label: "知识问答" },
  { path: "/resumes", label: "简历面试" },
];

const adminNavItems = [
  { path: "/admin/kb/documents", label: "知识库管理" },
];

function handleLogout() {
  auth.logout();
  router.push("/login");
}
</script>

<template>
  <div class="user-layout">
    <header class="topbar">
      <div class="topbar-left">
        <span class="brand">Interview Agent Platform</span>
        <nav class="nav-links">
          <router-link
            v-for="item in navItems"
            :key="item.path"
            :to="item.path"
            :class="['nav-link', { active: route.path === item.path }]"
          >
            {{ item.label }}
          </router-link>
          <template v-if="auth.isAdmin">
            <router-link
              v-for="item in adminNavItems"
              :key="item.path"
              :to="item.path"
              :class="['nav-link', 'nav-admin', { active: route.path === item.path }]"
            >
              {{ item.label }}
            </router-link>
          </template>
        </nav>
      </div>
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

.topbar-left {
  display: flex;
  align-items: center;
  gap: 24px;
}

.brand {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a2e;
  flex-shrink: 0;
}

.nav-links {
  display: flex;
  gap: 4px;
}

.nav-link {
  padding: 6px 14px;
  font-size: 14px;
  color: #555;
  text-decoration: none;
  border-radius: 6px;
  transition: background 0.15s;
}

.nav-link:hover {
  background: #f0f2f5;
}

.nav-link.active {
  color: #409eff;
  background: #ecf5ff;
}

.nav-link.nav-admin {
  color: #e6a23c;
}

.nav-link.nav-admin.active {
  background: #fdf6ec;
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
