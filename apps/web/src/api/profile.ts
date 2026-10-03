import client from "./client";

export interface ProfileFact {
  statement: string;
  label?: string;
  evidence_ids: string[];
  evidence_state?: "MENTIONED" | "DEMONSTRATED" | "STRONG_EVIDENCE";
}
export interface ProfileEvidence {
  key: string;
  kind: "RESUME" | "USER_CONFIRMED";
  excerpt: string;
  section: string;
  resume_id?: number;
  page?: number | null;
  start_offset?: number;
  end_offset?: number;
  reason?: string;
}
export interface ProfileVersion {
  id: number;
  version_number: number;
  base_version_id: number | null;
  source_resume_id: number;
  producer: string;
  provider: string | null;
  model: string | null;
  prompt_version: string | null;
  created_at: string;
  unknowns: { topic: string; message: string }[];
  snapshot: { candidate_summary: ProfileFact | null } & Record<string, unknown>;
  evidence: ProfileEvidence[];
  provenance: Record<string, string>;
  normalized: Record<string, unknown[]>;
}
export interface ProfileState {
  current: ProfileVersion | null;
  generations: { id: number; resume_id: number; status: string; error: string | null; version_id: number | null }[];
}
export interface VersionSummary { id: number; version_number: number; producer: string; created_at: string }

export const readProfile = () => client.get<never, ProfileState>("/candidate-profile");
export const readVersions = () => client.get<never, VersionSummary[]>("/candidate-profile/versions");
export const readVersion = (id: number) => client.get<never, ProfileVersion>(`/candidate-profile/versions/${id}`);
export const correctProfile = (payload: { base_version_id: number; field_path: string; new_value: string; reason: string }) =>
  client.post<never, ProfileVersion>("/candidate-profile/corrections", payload);
export const generateProfile = (resume_id: number, regenerate = false) =>
  client.post("/candidate-profile/generate", { resume_id, regenerate });
