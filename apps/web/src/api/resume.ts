import client from "./client";
import type { Resume, ResumeList, ResumeDetail, ResumeReport } from "../types/resume";

export async function uploadResume(file: File): Promise<Resume> {
  const form = new FormData();
  form.append("file", file);
  const response = await client.post("/resumes/upload", form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return response as unknown as Resume;
}

export async function listResumes(page = 1, pageSize = 20): Promise<ResumeList> {
  const response = await client.get("/resumes", { params: { page, page_size: pageSize } });
  return response as unknown as ResumeList;
}

export async function getResume(id: number): Promise<ResumeDetail> {
  const response = await client.get(`/resumes/${id}`);
  return response as unknown as ResumeDetail;
}

export async function getResumeReport(id: number): Promise<ResumeReport> {
  const response = await client.get(`/resumes/${id}/report`);
  return response as unknown as ResumeReport;
}

export async function deleteResume(id: number): Promise<void> {
  await client.delete(`/resumes/${id}`);
}
