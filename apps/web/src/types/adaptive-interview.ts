export const knowledgeStateLabels: Record<string, string> = {
  NOT_ASSESSED: '尚未考察', INSUFFICIENT_EVIDENCE: '本轮回答依据不足', PARTIALLY_DEMONSTRATED: '本场部分展示',
  DEMONSTRATED_IN_SESSION: '本场已有充分回答依据', NEEDS_CLARIFICATION: '仍需澄清',
}
export function knowledgeStateLabel(state: string): string {
  return knowledgeStateLabels[state] || '尚无明确观察'
}
