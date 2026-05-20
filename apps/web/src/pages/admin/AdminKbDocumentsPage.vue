<script setup lang="ts">
import { ref, onMounted } from "vue";
import {
  uploadKbDocument,
  getKbDocuments,
  getKbDocument,
} from "../../api/admin";
import type { KbDocument, KbDocumentDetail } from "../../types/qa";

const documents = ref<KbDocument[]>([]);
const total = ref(0);
const uploading = ref(false);
const uploadError = ref<string | null>(null);
const selectedDoc = ref<KbDocumentDetail | null>(null);

async function loadDocuments() {
  const result = await getKbDocuments(0, 100);
  documents.value = result.items;
  total.value = result.total;
}

async function handleUpload(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;

  uploading.value = true;
  uploadError.value = null;
  try {
    await uploadKbDocument(file);
    await loadDocuments();
  } catch (e: any) {
    uploadError.value = e?.message || "上传失败";
  } finally {
    uploading.value = false;
    input.value = "";
  }
}

async function handleViewDetail(docId: number) {
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
</script>

<template>
  <div class="kb-page">
    <h2 class="page-title">知识库文档管理</h2>

    <!-- Upload -->
    <div class="upload-section">
      <label class="upload-btn">
        {{ uploading ? "上传中..." : "上传文档" }}
        <input
          type="file"
          accept=".md,.txt,.markdown"
          :disabled="uploading"
          @change="handleUpload"
        />
      </label>
      <span class="upload-hint">支持 .md / .txt 文件</span>
      <div v-if="uploadError" class="upload-error">{{ uploadError }}</div>
    </div>

    <!-- Document list -->
    <table v-if="documents.length > 0" class="doc-table">
      <thead>
        <tr>
          <th>标题</th>
          <th>类型</th>
          <th>状态</th>
          <th>Chunks</th>
          <th>上传时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="doc in documents" :key="doc.id">
          <td class="col-title">{{ doc.title }}</td>
          <td>{{ doc.source_type }}</td>
          <td>
            <span :class="['status-tag', doc.status.toLowerCase()]">
              {{ statusTag(doc.status) }}
            </span>
          </td>
          <td>{{ doc.chunk_count }}</td>
          <td>{{ doc.created_at ? new Date(doc.created_at).toLocaleDateString() : '-' }}</td>
          <td>
            <button class="btn-detail" @click="handleViewDetail(doc.id)">详情</button>
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
  max-width: 960px;
  margin: 0 auto;
}

.page-title {
  font-size: 20px;
  font-weight: 600;
  color: #1a1a2e;
  margin-bottom: 24px;
}

.upload-section {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: #fff;
  border-radius: 8px;
  margin-bottom: 20px;
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

.upload-btn:hover {
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

.upload-error {
  color: #f56c6c;
  font-size: 13px;
}

.doc-table {
  width: 100%;
  background: #fff;
  border-radius: 8px;
  border-collapse: collapse;
}

.doc-table th,
.doc-table td {
  padding: 12px 16px;
  text-align: left;
  font-size: 14px;
  border-bottom: 1px solid #f0f0f0;
}

.doc-table th {
  color: #999;
  font-weight: 500;
  background: #fafafa;
}

.col-title {
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-tag {
  display: inline-block;
  padding: 2px 8px;
  font-size: 12px;
  border-radius: 4px;
}

.status-tag.indexed {
  background: #f0f9eb;
  color: #67c23a;
}

.status-tag.processing {
  background: #fdf6ec;
  color: #e6a23c;
}

.status-tag.uploaded {
  background: #ecf5ff;
  color: #409eff;
}

.status-tag.failed {
  background: #fef0f0;
  color: #f56c6c;
}

.btn-detail {
  padding: 4px 12px;
  font-size: 12px;
  color: #409eff;
  background: none;
  border: 1px solid #b3d8ff;
  border-radius: 4px;
  cursor: pointer;
}

.empty-state {
  text-align: center;
  color: #bbb;
  padding: 48px;
}

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
