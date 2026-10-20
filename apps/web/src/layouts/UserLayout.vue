<script setup lang="ts">
import { useRoute, useRouter } from "vue-router";
import { useAuthStore } from "../stores/auth";
import { computed, watch } from "vue";
import { lastApiDiagnostic } from "../api/client";
import { userNavigation as navItems, adminNavigation as adminNavItems, navigationActive } from "../i18n/zh-CN";

const auth = useAuthStore();
const router = useRouter();
const route = useRoute();
watch(() => route.path, () => { lastApiDiagnostic.value = null; });

const isDemo = computed(() => /(?:^|[-_ ])demo(?:[-_ @]|$)/i.test((auth.currentUser?.email || '') + ' ' + (auth.currentUser?.username || '')));

function handleLogout() {
  auth.logout();
  router.push("/login");
}
</script>

<template>
  <div class="user-layout">
    <header class="topbar">
      <div class="topbar-left">
        <span class="brand">秋招面试助手</span>
        <nav class="nav-links" aria-label="用户主导航">
          <router-link
            v-for="item in navItems"
            :key="item.path"
            :to="item.path"
            :class="['nav-link', { active: navigationActive(route.path, item.path) }]"
          >
            {{ item.label }}
          </router-link>

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
    <div v-if="isDemo" class="demo-banner" role="status">DEMO 账号 · 演示数据请勿视为真实求职经历；Replay 回放模式与实时模型以各记录标识为准。</div>
    <details v-if="auth.isAdmin" class="admin-area" :open="route.path.startsWith('/admin/')"><summary>管理员区域</summary>
      <nav aria-label="管理员导航"><router-link v-for="item in adminNavItems" :key="item.path" :to="item.path" :class="['nav-link', { active: navigationActive(route.path, item.path) }]">{{ item.label }}</router-link></nav>
      <p>职业资料采集与审核仅限管理员；普通用户只读取已发布资料。</p>
    </details>
    <main class="main-content">
      <details v-if="lastApiDiagnostic" class="ux-technical"><summary>本次错误技术详情</summary><p>错误代码：{{ lastApiDiagnostic.code }} · 请求标识：{{ lastApiDiagnostic.requestId || '未返回' }}</p></details>
      <slot />
    </main>
  </div>
</template>

<style scoped>
.demo-banner { padding:8px 24px; background:#fff3cd; color:#765600; font-size:13px; }
.admin-area {padding:8px 24px; background:#fff; font-size:13px;}
.admin-area nav {display:flex;flex-wrap:wrap;padding:10px 0;}
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
  min-height: 60px;
  padding: 10px 24px;
  background: #fff;
  border-bottom: 1px solid #e8e8e8;
  flex-shrink: 0;
  gap: 20px;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 24px;
  min-width: 0;
}

.brand {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a2e;
  flex-shrink: 0;
}

.nav-links {
  display: flex;
  flex-wrap: wrap;
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
  color: #b7791f;
}

.nav-link.nav-admin.active {
  background: #fdf6ec;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
  flex-shrink: 0;
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
  min-width: 0;
}

@media (max-width: 900px) {
  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .topbar-left {
    align-items: flex-start;
    flex-direction: column;
    gap: 10px;
    width: 100%;
  }

  .topbar-right {
    width: 100%;
    justify-content: space-between;
  }

  .main-content {
    padding: 16px;
  }
}
</style>
