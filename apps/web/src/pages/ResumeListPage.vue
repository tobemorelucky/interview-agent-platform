<script setup lang="ts">
import { ref, onMounted, onUnmounted } from "vue";
import { useRouter } from "vue-router";
import { listResumes, uploadResume, deleteResume, archiveResume, restoreResume } from "../api/resume";
import { listPurgeRequests, purgeCapability, requestPurge, PURGE_NOTICE,
  purgeStateLabel } from "../api/purge";
import type { PurgeCapability, PurgeRequestView } from "../api/purge";
import { ApiError } from "../api/client";
import { userError } from "../i18n/zh-CN";
import type { Resume } from "../types/resume";
import { statusLabel, statusColor, stageLabel } from "../types/resume";

const router = useRouter();
const resumes = ref<Resume[]>([]);
const total = ref(0);
const loading = ref(false);
const uploading = ref(false);
const error = ref("");
const uploadError = ref("");
const purges = ref<PurgeRequestView[]>([]);
const purgeCap = ref<PurgeCapability>({ execution_enabled: false, notice: "" });

function purgeFor(id: number): PurgeRequestView | undefined {
  return purges.value.find((p) => p.resource_type === "RESUME" && p.resource_id === id);
}

async function fetchPurges() {
  try {
    purgeCap.value = await purgeCapability();
    purges.value = await listPurgeRequests();
  } catch { /* capability/status is informational; never blocks the page */ }
}

async function handleRequestPurge(id: number) {
  if (!window.confirm(PURGE_NOTICE + "\n\n确定申请彻底清除这份简历吗？")) return;
  try {
    // Explicit user action only. The server decides whether execution is even
    // enabled; the front end can never turn real deletion on.
    await requestPurge({ resource_type: "RESUME", resource_id: id,
      idempotency_key: `resume-${id}-${Date.now()}` });
    await fetchPurges();
    await fetchList();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "清除申请失败，请重试。";
  }
}

let pollTimer: ReturnType<typeof setInterval> | null = null;

async function fetchList() {
  loading.value = true;
  error.value = "";
  try {
    // The management view opts in, so an archived resume stays visible with a
    // badge and can be restored; everywhere else it is simply absent.
    const data = await listResumes(1, 50, true);
    resumes.value = data.items;
    total.value = data.total;
    managePolling();
    await fetchPurges();
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

const ARCHIVE_NOTICE =
  "归档后将无法用于新的面试或训练，但历史记录及相关数据不会因此被彻底删除。";

async function handleArchive(id: number) {
  if (!confirm(ARCHIVE_NOTICE + " 确定归档这份简历吗？")) return;
  try {
    await archiveResume(id);
    await fetchList();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "归档失败，请重试。";
  }
}

async function handleRestore(id: number) {
  try {
    await restoreResume(id);
    await fetchList();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "恢复失败，请重试。";
  }
}

async function handleDelete(id: number) {
  if (!confirm("确定要删除这份简历及其报告吗？")) return;
  try {
    await deleteResume(id);
    await fetchList();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "删除失败，请重试。";
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
    <h2>我的简历</h2>
    <p class="subtitle">上传简历，系统会解析原始材料并生成可追溯的候选人画像。准备好画像后，可以选择岗位开始模拟面试。</p>

    <div class="purge-notice" role="status">
      <strong>资料生命周期</strong>
      <p>归档仅停止用于新业务，资料仍保留；「申请彻底清除」才会移除约定范围内的个人内容。{{ PURGE_NOTICE }}</p>
      <p v-if="!purgeCap.execution_enabled" class="purge-off">当前环境未启用清除执行：申请仍可提交并建立业务围栏，但不会实际清除数据。</p>
      <p v-else class="purge-on">{{ purgeCap.notice }}（仅受控合成测试用户可用）</p>
    </div>

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
      还没有上传简历，上传后系统会生成候选人画像。
    </div>
    <table v-else class="resume-table">
      <thead>
        <tr>
          <th>文件名</th>
          <th>类型</th>
          <th>状态</th>
          <th>处理阶段</th>
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
            <span v-if="r.error_message" class="error-tip" :title="userError(r.error_message)">
              ⚠
            </span>
            <span v-if="r.archived_at" class="badge-archived" title="归档后不能用于新的面试或训练；历史记录不会被删除">
              已归档
            </span>
            <span v-if="purgeFor(r.id)" class="badge-purge"
              :class="{ 'purge-done': purgeFor(r.id)?.ui_state === 'COMPLETED',
                        'purge-bad': purgeFor(r.id)?.ui_state === 'FAILED_OR_BLOCKED' }"
              :title="purgeFor(r.id)?.statement">
              {{ purgeStateLabel(purgeFor(r.id)?.ui_state) }}
            </span>
          </td>
          <td class="stage">
            <template v-if="r.processing_stage">
              <span class="stage-label">{{ stageLabel(r.processing_stage) }}</span>
              <span v-if="r.stage_message" class="stage-msg">{{ r.stage_message }}</span>
            </template>
            <span v-else class="stage-na">-</span>
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
            <button v-if="!r.archived_at && (r.status === 'COMPLETED' || r.status === 'FAILED')"
              class="btn-action btn-archive" @click="handleArchive(r.id)">归档</button>
            <button v-if="r.archived_at" class="btn-action btn-view" @click="handleRestore(r.id)">恢复</button>
            <button v-if="!purgeFor(r.id)" class="btn-action btn-purge"
              @click="handleRequestPurge(r.id)">申请彻底清除</button>
            <span v-else class="purge-detail" :title="purgeFor(r.id)?.error_diagnostic || ''">
              {{ purgeFor(r.id)?.error_code || purgeStateLabel(purgeFor(r.id)?.ui_state) }}
            </span>
            <button class="btn-action btn-delete" @click="handleDelete(r.id)">删除</button>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.purge-notice {
  background: #fff8e6;
  border: 1px solid #f0d9a0;
  border-radius: 8px;
  padding: 10px 14px;
  margin: 12px 0;
  font-size: 13px;
  color: #5c4a22;
  line-height: 1.6;
}
.purge-notice p { margin: 4px 0; }
.purge-off { color: #8a5a12; }
.purge-on { color: #1d6b3f; }
.badge-purge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 8px;
  border-radius: 10px;
  background: #e8eefc;
  color: #2f54eb;
  font-size: 12px;
}
.badge-purge.purge-done { background: #e4f4ea; color: #1d6b3f; }
.badge-purge.purge-bad { background: #fdeaea; color: #a12b2b; }
.btn-purge { background: #f0f0f4; color: #4a4a63; }
.purge-detail { font-size: 12px; color: #666; margin-left: 4px; }

.badge-archived {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 8px;
  border-radius: 10px;
  background: #eef1f6;
  color: #5b6472;
  font-size: 12px;
}
.btn-archive {
  background: #f0f4ff;
  color: #2f54eb;
}

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

.stage {
  max-width: 180px;
}

.stage-label {
  display: block;
  font-size: 12px;
  color: #409eff;
  font-weight: 500;
}

.stage-msg {
  display: block;
  font-size: 11px;
  color: #999;
  margin-top: 2px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.stage-na {
  color: #ccc;
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
