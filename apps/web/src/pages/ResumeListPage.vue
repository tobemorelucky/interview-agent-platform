<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { listResumes, uploadResume, deleteResume } from "../api/resume";
import { ApiError } from "../api/client";
import type { Resume } from "../types/resume";
import { statusLabel, statusColor } from "../types/resume";

const router = useRouter();
const resumes = ref<Resume[]>([]);
const total = ref(0);
const loading = ref(false);
const uploading = ref(false);
const error = ref("");
const uploadError = ref("");

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function fetchList() {
  loading.value = true;
  error.value = "";
  try {
    const data = await listResumes(1, 50);
    resumes.value = data.items;
    total.value = data.total;
    managePolling();
  } catch (e) {
    if (e instanceof ApiError) {
      error.value = e.message;
    } else {
      error.value = "加载失败";
    }
  } finally {
    loading.value = false;
  }
}

function managePolling() {
  const hasPending = resumes.value.some(
    (r) => r.status === "UPLOADED" || r.status === "PROCESSING"
  );
  if (hasPending && !pollTimer) {
    pollTimer = setInterval(fetchList, 3000);
  } else if (!hasPending && pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

async function handleUpload(file: File) {
  uploading.value = true;
  uploadError.value = "";
  try {
    await uploadResume(file);
    await fetchList();
  } catch (e) {
    if (e instanceof ApiError) {
      uploadError.value = e.message;
    } else {
      uploadError.value = "上传失败";
    }
  } finally {
    uploading.value = false;
  }
}

async function handleDelete(id: number) {
  if (!confirm("确定要删除这份简历及其报告吗？")) return;
  try {
    await deleteResume(id);
    await fetchList();
  } catch {
    alert("删除失败");
  }
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) {
    handleUpload(file);
  }
  input.value = "";
}

function goReport(id: number) {
  router.push(`/resumes/${id}`);
}

onMounted(fetchList);
onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
});
</script>

<template>
  <div class="resume-list-page">
    <h2>简历模拟面试</h2>
    <p class="subtitle">上传简历，系统将解析您的工作经历与技术栈，结合面试知识库生成针对性的模拟面试问题。</p>

    <!-- Upload -->
    <div class="upload-card">
      <label class="upload-zone" :class="{ disabled: uploading }">
        <input
          type="file"
          accept=".pdf,.docx,.txt"
          :disabled="uploading"
          @change="onFileChange"
          style="display:none"
        />
        <span v-if="uploading">上传中...</span>
        <span v-else>点击上传简历（支持 PDF / DOCX / TXT，最大 10MB）</span>
      </label>
      <p v-if="uploadError" class="error-msg">{{ uploadError }}</p>
    </div>

    <!-- Polling indicator -->
    <p v-if="pollTimer" class="polling-hint">自动刷新中...</p>

    <!-- List -->
    <div v-if="loading && resumes.length === 0" class="loading">加载中...</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else-if="resumes.length === 0" class="empty">
      暂无简历，请上传您的第一份简历。
    </div>
    <table v-else class="resume-table">
      <thead>
        <tr>
          <th>文件名</th>
          <th>类型</th>
          <th>状态</th>
          <th>上传时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="r in resumes" :key="r.id">
          <td class="filename">{{ r.filename }}</td>
          <td>
            <span class="badge-type">{{ r.file_type.toUpperCase() }}</span>
          </td>
          <td>
            <span class="badge-status" :style="{ background: statusColor(r.status) }">
              {{ statusLabel(r.status) }}
            </span>
            <span v-if="r.error_message" class="error-tip" :title="r.error_message">
              ⚠
            </span>
          </td>
          <td class="time">{{ r.created_at ? new Date(r.created_at).toLocaleString("zh-CN") : "-" }}</td>
          <td class="actions">
            <button
              v-if="r.status === 'COMPLETED'"
              class="btn-action btn-view"
              @click="goReport(r.id)"
            >
              查看报告
            </button>
            <button
              v-else-if="r.status === 'FAILED'"
              class="btn-action btn-view"
              @click="goReport(r.id)"
            >
              查看详情
            </button>
            <button class="btn-action btn-delete" @click="handleDelete(r.id)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.resume-list-page {
  max-width: 960px;
  margin: 0 auto;
}

h2 {
  font-size: 22px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 6px;
}

.subtitle {
  font-size: 14px;
  color: #999;
  margin-bottom: 24px;
}

.upload-card {
  margin-bottom: 24px;
}

.upload-zone {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100px;
  border: 2px dashed #d9d9d9;
  border-radius: 8px;
  color: #888;
  font-size: 14px;
  cursor: pointer;
  transition: border-color 0.2s;
}

.upload-zone:hover {
  border-color: #409eff;
  color: #409eff;
}

.upload-zone.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.error-msg {
  font-size: 13px;
  color: #f56c6c;
  margin-top: 8px;
}

.polling-hint {
  font-size: 12px;
  color: #409eff;
  margin-bottom: 12px;
}

.loading,
.error,
.empty {
  text-align: center;
  padding: 40px 0;
  color: #999;
  font-size: 14px;
}

.error {
  color: #f56c6c;
}

.resume-table {
  width: 100%;
  border-collapse: collapse;
  background: #fff;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 1px 8px rgba(0, 0, 0, 0.06);
}

.resume-table th {
  background: #fafafa;
  padding: 12px 16px;
  text-align: left;
  font-size: 13px;
  font-weight: 500;
  color: #888;
}

.resume-table td {
  padding: 12px 16px;
  font-size: 14px;
  border-top: 1px solid #f0f0f0;
}

.filename {
  font-weight: 500;
  color: #1a1a2e;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badge-type {
  display: inline-block;
  padding: 2px 8px;
  font-size: 11px;
  color: #666;
  background: #f5f5f5;
  border-radius: 4px;
}

.badge-status {
  display: inline-block;
  padding: 2px 10px;
  font-size: 11px;
  color: #fff;
  border-radius: 10px;
}

.error-tip {
  margin-left: 6px;
  cursor: help;
  font-size: 14px;
}

.time {
  color: #aaa;
  font-size: 13px;
}

.actions {
  white-space: nowrap;
}

.btn-action {
  padding: 4px 12px;
  font-size: 12px;
  border-radius: 4px;
  border: 1px solid #d9d9d9;
  background: #fff;
  cursor: pointer;
  margin-right: 6px;
}

.btn-view {
  color: #409eff;
  border-color: #409eff;
}

.btn-view:hover {
  background: #ecf5ff;
}

.btn-delete {
  color: #f56c6c;
  border-color: #f56c6c;
}

.btn-delete:hover {
  background: #fef0f0;
}
</style>
