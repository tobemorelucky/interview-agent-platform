export const capabilityLabels: Record<string, string> = {
  UNKNOWN: "信息不足", GAP_SUSPECTED: "疑似缺口", PRACTICING: "练习改善中",
  VERIFIED: "已有独立验证", RECHECK_DUE: "建议复测", CONFLICTED: "表现存在分歧",
}
export const capabilityReasons: Record<string, string> = {
  INDEPENDENT_RECHECK_REQUIRED: "相同语境出现不同表现，需要独立复测",
  MULTI_SESSION_INDEPENDENT_SUPPORT: "至少两场独立回答提供了支持",
  ASSISTED_SUCCESS_NOT_MASTERY: "经过提示或追问才补齐，暂不认定为独立掌握",
  ONE_INDEPENDENT_SESSION_ONLY: "已有一次独立验证，仍需另一场验证",
  RECENT_MISSING_EVIDENCE: "近期回答中仍有未覆盖的机制",
  HINT_DEPENDENCE: "提示后答对，但独立回答仍有缺口",
  VERIFICATION_EXPIRED: "距离上次独立验证较久，建议换场景复测",
}
export interface CapabilityMemory {
  id: number; topic: string; label: string; revision: number; revoked: boolean; state: string
  correction_note?: string; last_verified_at: string | null; last_observed_at: string | null
  reason_tags: string[]; independent_sessions: number
  conflicts: { status: string; scope: string }[]
  scope_differences: { scope: string }[]
  observations: { evaluation_id: number; session_id: number; observed_at: string; assisted: boolean; state: string; open_gaps: string[] }[]
  history: { revision: number; previous_state: string; state: string; actor: string; created_at: string }[]
}
