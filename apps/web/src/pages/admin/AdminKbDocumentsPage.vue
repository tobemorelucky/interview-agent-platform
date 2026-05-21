<script setup lang="ts">
import { ref, onMounted, onUnmounted, computed } from "vue";
import {
  uploadKbDocument,
  getKbDocuments,
  getKbDocument,
  deleteKbDocument,
} from "../../api/admin";
import type { KbDocument, KbDocumentDetail } from "../../types/qa";

const documents = ref<KbDocument[]>([]);
const total = ref(0);
const uploading = ref(false);
const uploadError = ref<string | null>(null);
const selectedDoc = ref<KbDocumentDetail | null>(null);
const actionError = ref<string | null>(null);

// Single-delete confirm
const deleteConfirmId = ref<number | null>(null);
const deletingIds = ref<Set<number>>(new Set());

// Batch upload
interface UploadResult {
  filename: string
  status: "success" | "error" | "duplicate"
  message?: string
}
const uploadResults = ref<UploadResult[]>([]);

// Batch delete
const selectedIds = ref<Set<number>>(new Set());
const batchDeleting = ref(false);
interface BatchDeleteResult {
  success: number
  failed: number
}
const batchDeleteResult = ref<BatchDeleteResult | null>(null);

let pollTimer: ReturnType<typeof setInterval> | null = null;

const hasPendingDocs = computed(() =>
  documents.value.some((d) => d.status === "UPLOADED" || d.status === "PROCESSING")
);

const allSelected = computed({
  get: () => documents.value.length > 0 && selectedIds.value.size === documents.value.length,
  set: (val: boolean) => {
    if (val) {
      selectedIds.value = new Set(documents.value.map((d) => d.id));
    } else {
      selectedIds.value = new Set();
    }
  },
});

function toggleSelect(id: number) {
  const next = new Set(selectedIds.value);
  if (next.has(id)) {
    next.delete(id);
  } else {
    next.add(id);
  }
  selectedIds.value = next;
}

async function loadDocuments() {
  const result = await getKbDocuments(0, 100);
  documents.value = result.items;
  total.value = result.total;

  if (hasPendingDocs.value && !pollTimer) {
    startPolling();
  } else if (!hasPendingDocs.value && pollTimer) {
    stopPolling();
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(async () => {
    try { await loadDocuments(); } catch { /* silent */ }
  }, 3000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// ---- Batch upload ----

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = input.files;
  if (!files || files.length === 0) return;

  uploading.value = true;
  uploadError.value = null;
  uploadResults.value = [];

  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    try {
      await uploadKbDocument(file);
      uploadResults.value.push({ filename: file.name, status: "success" });
    } catch (e: any) {
      const msg = e?.message || "上传失败";
      const status = msg.includes("已存在") || msg.includes("already exists") || msg.includes("DUPLICATE")
        ? "duplicate"
        : "error";
      uploadResults.value.push({ filename: file.name, status, message: msg });
    }
  }

  uploading.value = false;
  input.value = "";
  await loadDocuments();
}

// ---- Single delete ----

function isDeleting(id: number): boolean {
  return deletingIds.value.has(id);
}

async function handleDelete(docId: number) {
  actionError.value = null;
  deleteConfirmId.value = null;
  deletingIds.value = new Set([...deletingIds.value, docId]);

  try {
    await deleteKbDocument(docId);
    if (selectedDoc.value?.id === docId) {
      selectedDoc.value = null;
    }
    await loadDocuments();
  } catch (e: any) {
    // 404 → doc already gone, just refresh
    if (e?.code === "NOT_FOUND" || (e?.response?.status === 404) || (e?.message && e.message.includes("404"))) {
      if (selectedDoc.value?.id === docId) {
        selectedDoc.value = null;
      }
      await loadDocuments();
    } else {
      actionError.value = e?.message || "删除失败";
    }
  } finally {
    const next = new Set(deletingIds.value);
    next.delete(docId);
    deletingIds.value = next;
  }
}

// ---- Batch delete ----

async function handleBatchDelete() {
  if (selectedIds.value.size === 0) return;
  actionError.value = null;
  batchDeleteResult.value = null;
  batchDeleting.value = true;

  const ids = [...selectedIds.value];
  let success = 0;
  let failed = 0;

  for (const id of ids) {
    try {
      await deleteKbDocument(id);
      success++;
    } catch (e: any) {
      if (e?.code === "NOT_FOUND" || (e?.response?.status === 404) || (e?.message && e.message.includes("404"))) {
        success++; // already deleted = success
      } else {
        failed++;
      }
    }
  }

  batchDeleteResult.value = { success, failed };
  batchDeleting.value = false;
  selectedIds.value = new Set();
  await loadDocuments();
}

// ---- Detail ----

async function handleViewDetail(docId: number) {
  actionError.value = null;
  selectedDoc.value = await getKbDocument(docId);
}

function statusTag(status: string): string {
  const map: Record<string, string> = {
    UPLOADED: "待处理",
    PROCESSING: "处理中",
    INDEXED: "已索引",
    FAILED: "失败",
  };
  return map[status] || status;
}

onMounted(loadDocuments);
onUnmounted(stopPolling);
</script>

<template>
  <div class="kb-page">
    <h2 class="page-title">知识库文档管理</h2>

    <!-- Upload -->
    <div class="upload-section">
      <label class="upload-btn" :class="{ disabled: uploading }">
        {{ uploading ? "上传中..." : "上传文档" }}
        <input
          type="file"
          accept=".md,.txt,.markdown"
          multiple
          :disabled="uploading"
          @change="handleUpload"
        />
      </label>
      <span class="upload-hint">支持 .md / .txt 文件，可多选</span>
      <span v-if="pollTimer" class="poll-indicator">自动刷新中...</span>
      <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
    </div>

    <!-- Upload results -->
    <div v-if="uploadResults.length > 0" class="results-panel">
      <div v-for="r in uploadResults" :key="r.filename" :class="['result-item', r.status]">
        <span class="result-icon">{{ r.status === 'success' ? 'OK' : r.status === 'duplicate' ? '~' : '!' }}</span>
        <span class="result-filename">{{ r.filename }}</span>
        <span class="result-msg">{{ r.status === 'success' ? '上传成功' : r.status === 'duplicate' ? '已存在' : r.message }}</span>
      </div>
    </div>

    <!-- Action error -->
    <div v-if="actionError" class="action-error">
      {{ actionError }}
      <button class="btn-dismiss" @click="actionError = null">x</button>
    </div>

    <!-- Batch delete bar -->
    <div v-if="selectedIds.size > 0" class="batch-bar">
      <span>已选择 {{ selectedIds.size }} 个文档</span>
      <button
        class="btn-batch-delete"
        :disabled="batchDeleting"
        @click="handleBatchDelete"
      >
        {{ batchDeleting ? "删除中..." : `批量删除 (${selectedIds.size})` }}
      </button>
      <button class="btn-cancel-select" @click="selectedIds = new Set()">取消选择</button>
    </div>

    <!-- Batch delete result -->
    <div v-if="batchDeleteResult" class="batch-result">
      批量删除完成：成功 {{ batchDeleteResult.success }}，失败 {{ batchDeleteResult.failed }}
    </div>

    <!-- Document list -->
    <table v-if="documents.length > 0" class="doc-table">
      <thead>
        <tr>
          <th class="col-check">
            <input type="checkbox" v-model="allSelected" :disabled="batchDeleting" />
          </th>
          <th>标题</th>
          <th>类型</th>
          <th>状态</th>
          <th>Chunks</th>
          <th>上传时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="doc in documents" :key="doc.id" :class="{ 'row-selected': selectedIds.has(doc.id) }">
          <td class="col-check">
            <input
              type="checkbox"
              :checked="selectedIds.has(doc.id)"
              :disabled="batchDeleting"
              @change="toggleSelect(doc.id)"
            />
          </td>
          <td class="col-title">{{ doc.title }}</td>
          <td>{{ doc.source_type }}</td>
          <td>
            <span :class="['status-tag', doc.status.toLowerCase()]">
              {{ statusTag(doc.status) }}
            </span>
          </td>
          <td>{{ doc.chunk_count }}</td>
          <td>{{ doc.created_at ? new Date(doc.created_at).toLocaleDateString() : '-' }}</td>
          <td class="col-actions">
            <button class="btn-detail" @click="handleViewDetail(doc.id)">详情</button>
            <template v-if="deleteConfirmId === doc.id">
              <button
                class="btn-delete-confirm"
                :disabled="isDeleting(doc.id)"
                @click="handleDelete(doc.id)"
              >
                {{ isDeleting(doc.id) ? "删除中..." : "确认删除" }}
              </button>
              <button class="btn-cancel" :disabled="isDeleting(doc.id)" @click="deleteConfirmId = null">
                取消
              </button>
            </template>
            <button
              v-else
              class="btn-delete"
              :disabled="isDeleting(doc.id) || batchDeleting"
              @click="deleteConfirmId = doc.id"
            >
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty-state">暂无知识文档</div>

    <!-- Detail panel -->
    <div v-if="selectedDoc" class="detail-overlay" @click.self="selectedDoc = null">
      <div class="detail-panel">
        <h3>{{ selectedDoc.title }}</h3>
        <div class="detail-meta">
          状态: {{ statusTag(selectedDoc.status) }} | 类型: {{ selectedDoc.source_type }} |
          Chunks: {{ selectedDoc.chunk_count }}
        </div>
        <div v-if="selectedDoc.error_message" class="detail-error">
          错误: {{ selectedDoc.error_message }}
        </div>
        <div class="chunks-list">
          <div v-for="chunk in selectedDoc.chunks" :key="chunk.id" class="chunk-item">
            <div class="chunk-header">
              #{{ chunk.chunk_index }} ({{ chunk.embedding_status }})
            </div>
            <pre class="chunk-content">{{ chunk.content }}</pre>
          </div>
        </div>
        <button class="btn-close" @click="selectedDoc = null">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.kb-page {
  max-width: 1060px;
  margin: 0 auto;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 24px;
}

/* Upload */
.upload-section {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}

.upload-btn {
  position: relative;
  display: inline-block;
  padding: 8px 20px;
  font-size: 14px;
  color: #fff;
  background: #409eff;
  border-radius: 6px;
  cursor: pointer;
}

.upload-btn.disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.upload-btn:hover:not(.disabled) {
  background: #337ecc;
}

.upload-btn input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}

.upload-hint {
  font-size: 13px;
  color: #999;
}

.poll-indicator {
  font-size: 12px;
  color: #409eff;
  margin-left: auto;
}

.upload-error {
  color: #f56c6c;
  font-size: 13px;
  width: 100%;
}

/* Upload results */
.results-panel {
  background: #fff;
  border-radius: 8px;
  padding: 10px 16px;
  margin-bottom: 12px;
  max-height: 200px;
  overflow-y: auto;
}

.result-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
}

.result-item.success .result-icon { color: #67c23a; }
.result-item.duplicate .result-icon { color: #e6a23c; }
.result-item.error .result-icon { color: #f56c6c; }

.result-icon {
  font-weight: 700;
  width: 18px;
}

.result-filename {
  color: #333;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.result-msg {
  color: #999;
  flex-shrink: 0;
}

.result-item.error .result-msg { color: #f56c6c; }

/* Action error */
.action-error {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  color: #f56c6c;
  font-size: 13px;
  background: #fef0f0;
  border-radius: 6px;
  margin-bottom: 12px;
}

.btn-dismiss {
  font-size: 14px;
  color: #999;
  background: none;
  border: none;
  cursor: pointer;
  padding: 0 4px;
}

/* Batch bar */
.batch-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #ecf5ff;
  border: 1px solid #b3d8ff;
  border-radius: 8px;
  margin-bottom: 12px;
  font-size: 14px;
  color: #333;
}

.btn-batch-delete {
  padding: 6px 16px;
  font-size: 13px;
  color: #fff;
  background: #f56c6c;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.btn-batch-delete:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-cancel-select {
  padding: 6px 12px;
  font-size: 13px;
  color: #666;
  background: #fff;
  border: 1px solid #d9d9d9;
  border-radius: 4px;
  cursor: pointer;
}

.batch-result {
  padding: 10px 16px;
  font-size: 13px;
  color: #333;
  background: #f0f9eb;
  border-radius: 6px;
  margin-bottom: 12px;
}

/* Table */
.doc-table {
  width: 100%;
  background: #fff;
  border-radius: 8px;
  border-collapse: collapse;
}

.doc-table th,
.doc-table td {
  padding: 10px 14px;
  text-align: left;
  font-size: 14px;
  border-bottom: 1px solid #f0f0f0;
}

.doc-table th {
  color: #999;
  font-weight: 500;
  background: #fafafa;
}

.row-selected {
  background: #ecf5ff;
}

.col-check {
  width: 36px;
  text-align: center;
}

.col-title {
  max-width: 180px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.col-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;
}

.status-tag.indexed { background: #f0f9eb; color: #67c23a; }
.status-tag.processing { background: #fdf6ec; color: #e6a23c; }
.status-tag.uploaded { background: #ecf5ff; color: #409eff; }
.status-tag.failed { background: #fef0f0; color: #f56c6c; }

.btn-detail,
.btn-delete,
.btn-delete-confirm,
.btn-cancel {
  padding: 4px 10px;
  font-size: 12px;
  border-radius: 4px;
  cursor: pointer;
  white-space: nowrap;
}

.btn-detail {
  color: #409eff;
  background: none;
  border: 1px solid #b3d8ff;
}

.btn-delete {
  color: #f56c6c;
  background: none;
  border: 1px solid #fab6b6;
}

.btn-delete-confirm {
  color: #fff;
  background: #f56c6c;
  border: 1px solid #f56c6c;
}

.btn-cancel {
  color: #666;
  background: #f5f5f5;
  border: 1px solid #d9d9d9;
}

.btn-detail:disabled,
.btn-delete:disabled,
.btn-delete-confirm:disabled,
.btn-cancel:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.empty-state {
  text-align: center;
  color: #bbb;
  padding: 48px;
}

/* Detail overlay */
.detail-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.3);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.detail-panel {
  background: #fff;
  border-radius: 12px;
  padding: 32px;
  width: 700px;
  max-height: 80vh;
  overflow-y: auto;
}

.detail-meta {
  margin: 12px 0;
  font-size: 13px;
  color: #888;
}

.detail-error {
  color: #f56c6c;
  font-size: 13px;
  padding: 8px 12px;
  background: #fef0f0;
  border-radius: 6px;
  margin-bottom: 12px;
}

.chunks-list {
  margin-top: 16px;
  border-top: 1px solid #eee;
  padding-top: 16px;
}

.chunk-item {
  margin-bottom: 12px;
}

.chunk-header {
  font-size: 12px;
  color: #999;
  margin-bottom: 4px;
}

.chunk-content {
  font-size: 13px;
  white-space: pre-wrap;
  background: #fafafa;
  padding: 8px 12px;
  border-radius: 6px;
  line-height: 1.5;
  max-height: 200px;
  overflow-y: auto;
}

.btn-close {
  margin-top: 16px;
  padding: 8px 24px;
  font-size: 14px;
  color: #666;
  background: #f5f5f5;
  border: 1px solid #d9d9d9;
  border-radius: 6px;
  cursor: pointer;
}
</style>
