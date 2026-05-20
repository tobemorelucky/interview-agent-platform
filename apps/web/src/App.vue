<script setup lang="ts">
import { useRoute } from "vue-router";
import { computed } from "vue";
import PublicLayout from "./layouts/PublicLayout.vue";
import UserLayout from "./layouts/UserLayout.vue";

const route = useRoute();
const layout = computed(() => (route.meta.layout as string) || "user");
</script>

<template>
  <PublicLayout v-if="layout === 'public'">
    <router-view />
  </PublicLayout>
  <UserLayout v-else>
    <router-view />
  </UserLayout>
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
  background: #f0f2f5;
  color: #333;
  min-height: 100vh;
}
</style>
