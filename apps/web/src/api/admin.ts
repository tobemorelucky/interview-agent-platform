import client from "./client";
import type { KbDocument, KbDocumentDetail, KbDocumentList } from "../types/qa";

export async function uploadKbDocument(file: File): Promise<KbDocument> {
  const formData = new FormData();
  formData.append("file", file);
  return client.post("/admin/kb/documents/upload", formData, {
    headers: { "Content-Type": "multipart/form-data" },
  });
}

export async function getKbDocuments(offset = 0, limit = 50): Promise<KbDocumentList> {
  return client.get("/admin/kb/documents", { params: { offset, limit } });
}

export async function getKbDocument(id: number): Promise<KbDocumentDetail> {
  return client.get(`/admin/kb/documents/${id}`);
}
