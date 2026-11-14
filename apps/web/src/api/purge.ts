/* Front-end client for the Round 23 / L3 purge request + status view.
 * There is deliberately NO "execute purge" call here. Execution is a
 * server-side decision gated by configuration + isolated database + allowlist.
 */
import client from './client'

export interface PurgeCapability {
  execution_enabled: boolean
  notice: string
}

export type PurgeUiState = 'PENDING' | 'PROCESSING' | 'COMPLETED' | 'FAILED_OR_BLOCKED'

export interface PurgeRequestView {
  id: number
  resource_type: 'RESUME' | 'PRIVATE_JD'
  resource_id: number
  status: string
  ui_state: PurgeUiState
  policy_version: string
  steps: string[]
  error_code: string | null
  error_diagnostic: string | null
  attempts: number
  created_at: string | null
  completed_at: string | null
  residual_external: string[]
  statement: string
  created?: boolean
  execution_dispatched?: boolean
  execution_gate?: string
}

export async function purgeCapability(): Promise<PurgeCapability> {
  return await client.get('/purges/capability') as unknown as PurgeCapability
}

export async function listPurgeRequests(): Promise<PurgeRequestView[]> {
  return await client.get('/purges') as unknown as PurgeRequestView[]
}

export async function requestPurge(body: {
  resource_type: 'RESUME' | 'PRIVATE_JD'
  resource_id: number
  idempotency_key: string
}): Promise<PurgeRequestView> {
  return await client.post('/purges', body) as unknown as PurgeRequestView
}

export function purgeStateLabel(state: PurgeUiState | undefined): string {
  switch (state) {
    case 'PENDING': return '清除申请已受理'
    case 'PROCESSING': return '清除处理中'
    case 'COMPLETED': return '清除完成（约定范围）'
    case 'FAILED_OR_BLOCKED': return '清除失败或被阻断'
    default: return ''
  }
}

// Honest, stable copy. Never "permanently deleted", never "privacy data erased".
export const PURGE_NOTICE =
  '申请彻底清除与归档不同：归档只是隐藏且资料仍可用于恢复；清除会实际移除约定范围内的个人内容。' +
  '清除完成后仍可能保留历史会话骨架、非内容型模型账本、外部备份与模型服务端日志，' +
  '这些不在本服务可立即清除的范围内。清除申请一经受理，该资料立即停止用于新的面试或训练。'
