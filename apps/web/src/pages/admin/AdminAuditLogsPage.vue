<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { listAuditLogs, type AuditLog } from "../../api/admin";

const logs = ref<AuditLog[]>([]);
const total = ref(0);
const loading = ref(false);
const error = ref<string | null>(null);

const filters = reactive({
  action: "",
  resource_type: "",
  request_id: "",
});

async function loadLogs() {
  loading.value = true;
  error.value = null;
  try {
    const result = await listAuditLogs({
      action: filters.action || undefined,
      resource_type: filters.resource_type || undefined,
      request_id: filters.request_id || undefined,
      offset: 0,
      limit: 100,
    });
    logs.value = result.items;
    total.value = result.total;
  } catch (e: any) {
    error.value = e?.message || "加载审计日志失败";
  } finally {
    loading.value = false;
  }
}

function formatTime(value?: string) {
  return value ? new Date(value).toLocaleString() : "-";
}

function actorLabel(item: AuditLog) {
  if (!item.actor_user_id) return "-";
  return `${item.actor_user_id}${item.actor_role ? ` / ${item.actor_role}` : ""}`;
}

function resourceLabel(item: AuditLog) {
  if (!item.resource_type && !item.resource_id) return "-";
  return [item.resource_type, item.resource_id].filter(Boolean).join(" #");
}

onMounted(loadLogs);
</script>

<template>
  <div class="audit-page">
    <header class="page-header">
      <div>
        <h2>审计日志</h2>
        <p>查看关键管理操作、请求 ID 和失败原因。</p>
      </div>
      <button class="btn-primary" :disabled="loading" @click="loadLogs">
        {{ loading ? "刷新中..." : "刷新" }}
      </button>
    </header>

    <section class="filter-bar">
      <label>
        <span>操作</span>
        <input v-model.trim="filters.action" placeholder="memory.item.create" @keyup.enter="loadLogs" />
      </label>
      <label>
        <span>资源</span>
        <input v-model.trim="filters.resource_type" placeholder="experience_task" @keyup.enter="loadLogs" />
      </label>
      <label>
        <span>Request ID</span>
        <input v-model.trim="filters.request_id" placeholder="X-Request-ID" @keyup.enter="loadLogs" />
      </label>
      <button class="btn-secondary" :disabled="loading" @click="loadLogs">查询</button>
    </section>

    <div v-if="error" class="error-message">{{ error }}</div>
    <div class="summary">共 {{ total }} 条，当前显示 {{ logs.length }} 条</div>

    <div class="table-wrap">
      <table class="audit-table">
        <thead>
          <tr>
            <th>时间</th>
            <th>操作者</th>
            <th>操作</th>
            <th>资源</th>
            <th>状态</th>
            <th>Request ID</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in logs" :key="item.id">
            <td class="time-cell">{{ formatTime(item.created_at) }}</td>
            <td>{{ actorLabel(item) }}</td>
            <td class="mono">{{ item.action }}</td>
            <td class="mono">{{ resourceLabel(item) }}</td>
            <td>
              <span :class="['status-tag', item.status.toLowerCase()]">{{ item.status }}</span>
            </td>
            <td class="request-cell mono" :title="item.request_id">{{ item.request_id || "-" }}</td>
            <td class="error-cell" :title="item.error_message">{{ item.error_message || "-" }}</td>
          </tr>
          <tr v-if="!loading && logs.length === 0">
            <td colspan="7" class="empty-state">暂无审计日志</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<style scoped>
.audit-page {
  max-width: 1180px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-header h2 {
  margin: 0 0 6px;
  color: #1f2937;
  font-size: 22px;
}

.page-header p {
  margin: 0;
  color: #6b7280;
  font-size: 14px;
}

.filter-bar {
  display: grid;
  grid-template-columns: repeat(3, minmax(160px, 1fr)) auto;
  gap: 12px;
  align-items: end;
  padding: 16px;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  margin-bottom: 12px;
}

.filter-bar label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  color: #4b5563;
  font-size: 13px;
}

.filter-bar input {
  height: 34px;
  padding: 0 10px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 14px;
}

.btn-primary,
.btn-secondary {
  height: 34px;
  padding: 0 14px;
  border-radius: 6px;
  cursor: pointer;
}

.btn-primary {
  color: #fff;
  background: #2563eb;
  border: 1px solid #2563eb;
}

.btn-secondary {
  color: #1f2937;
  background: #f9fafb;
  border: 1px solid #d1d5db;
}

.btn-primary:disabled,
.btn-secondary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.summary {
  margin: 10px 0;
  color: #6b7280;
  font-size: 13px;
}

.error-message {
  padding: 10px 12px;
  color: #b91c1c;
  background: #fee2e2;
  border-radius: 6px;
  margin-bottom: 12px;
}

.table-wrap {
  overflow-x: auto;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
}

.audit-table {
  width: 100%;
  border-collapse: collapse;
  min-width: 980px;
}

.audit-table th,
.audit-table td {
  padding: 10px 12px;
  border-bottom: 1px solid #f3f4f6;
  text-align: left;
  font-size: 13px;
  vertical-align: top;
}

.audit-table th {
  color: #6b7280;
  background: #f9fafb;
  font-weight: 600;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}

.time-cell {
  white-space: nowrap;
}

.request-cell,
.error-cell {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  background: #ecfdf5;
  color: #047857;
}

.status-tag.failed {
  background: #fef2f2;
  color: #b91c1c;
}

.empty-state {
  text-align: center;
  color: #9ca3af;
  padding: 28px;
}

@media (max-width: 860px) {
  .page-header {
    align-items: flex-start;
    flex-direction: column;
  }

  .filter-bar {
    grid-template-columns: 1fr;
  }
}
</style>
