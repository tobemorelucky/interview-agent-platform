import type { MatchCandidate, MatchEvidence, MatchSupport, MatrixRow } from './job-matching'
import type { InterviewQuestionSummary } from './interview'
import { displayLabel } from '../i18n/zh-CN.ts'

export interface DisplayFact { id: number; label: string; category: string; evidence_state?: string | null }
const normal = (text: string) => text.normalize('NFKC').replace(/\s+/g, ' ').trim().toLowerCase()
const short = (text: string, limit = 82) => text.length > limit ? text.slice(0, limit) + '…' : text
const literal = (text: string, term: string) => Boolean(term && new RegExp(`(?<![a-z0-9_])${normal(term).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?![a-z0-9_])`).test(normal(text)))

/** Per-root budget ordinal, not the whole interview's follow-up statistic. */
export function followUpOrdinal(questions: InterviewQuestionSummary[], questionId: number): number | undefined {
  const target = questions.find(q => q.id === questionId)
  if (!target?.question_kind || !['FOLLOW_UP', 'DEEP_DIVE'].includes(target.question_kind)) return undefined
  const byId = new Map(questions.map(q => [q.id, q]))
  const root = (question: InterviewQuestionSummary): number | undefined => {
    let q: InterviewQuestionSummary | undefined = question
    const seen = new Set<number>()
    while (q?.parent_question_id) {
      if (seen.has(q.id)) return undefined
      seen.add(q.id)
      q = byId.get(q.parent_question_id)
    }
    return q?.id
  }
  const rootId = root(target)
  if (rootId == null) return undefined
  return questions.filter(q => q.question_index <= target.question_index &&
    ['FOLLOW_UP', 'DEEP_DIVE'].includes(q.question_kind || '') && root(q) === rootId).length
}
export function presentMatch(candidate: MatchCandidate, facts: DisplayFact[], evidence: MatchEvidence[]) {
  const groups = new Map<number, MatrixRow>()
  for (const row of candidate.evidence_matrix) {
    const previous = groups.get(row.job_requirement_id)
    groups.set(row.job_requirement_id, previous ? { ...previous, supports: [...previous.supports, ...row.supports] } : { ...row, supports: [...row.supports] })
  }
  const byFact = new Map(facts.map(f => [f.id, f]))
  const byEvidence = new Map(evidence.map(e => [e.key, e]))
  const usedFacts = new Set<number>(), usedEvidence = new Set<string>()
  const highlights: string[] = [], gaps: string[] = []
  const requirements = [...groups.values()].map(row => {
    const supports: MatchSupport[] = []
    const explanations = new Set<string>(), localFacts = new Map<number, MatchSupport>()
    for (const s of row.supports) {
      const old = localFacts.get(s.candidate_fact_id)
      if (old) old.evidence_ids = [...new Set([...old.evidence_ids, ...s.evidence_ids])]
      else { const copy = { ...s, evidence_ids: [...s.evidence_ids] }; supports.push(copy); localFacts.set(s.candidate_fact_id, copy) }
      explanations.add(s.explanation)
    }
    if (row.status === 'SUPPORTED' || row.status === 'PARTIAL') {
      // Only labels backed by THIS matrix row and literally relevant to its JD.
      // A shared excerpt mentioning Redis must not turn a Java match into Redis fit.
      const labels = [...new Set(supports.map(s => byFact.get(s.candidate_fact_id)?.label)
        .filter((s): s is string => Boolean(s && literal(row.statement, s))))].slice(0, 4)
      highlights.push(labels.length ? `${labels.join(' / ')} 有相关材料依据${row.status === 'PARTIAL' ? '（部分覆盖）' : ''}`
        : `已有材料支持：${short(row.statement)}`)
    } else if (row.status === 'NO_EVIDENCE') gaps.push(`${short(row.statement)}：当前材料未覆盖`)
    const visibleFacts = supports.filter(s => !usedFacts.has(s.candidate_fact_id))
    visibleFacts.forEach(s => usedFacts.add(s.candidate_fact_id))
    const sources: MatchEvidence[] = []
    for (const key of [...new Set(supports.flatMap(s => s.evidence_ids))]) {
      const item = byEvidence.get(key)
      if (!item) continue
      const identity = normal(item.excerpt)
      if (usedEvidence.has(identity)) continue
      usedEvidence.add(identity); sources.push(item)
    }
    return { ...row, supports: visibleFacts, explanations: [...explanations], evidence: sources.slice(0, 3), moreEvidence: sources.slice(3) }
  })
  const keyFields = new Set(['education_minimum', 'majors', 'graduation_years', 'locations', 'campus_type', 'level', 'explicit_exclusion', 'excluded_tags', 'tags'])
  const eligibility = candidate.eligibility_reasons.filter(r => keyFields.has(r.field) && r.status !== 'NOT_APPLICABLE').map(r => {
    const value = candidate.matching_constraints?.[r.field]
    const required = typeof value === 'string' ? value : Array.isArray(value) && value.every(v => ['string', 'number'].includes(typeof v)) ? value.join('、') : ''
    return { ...r, required }
  })
  const unknown = eligibility.filter(r => r.status === 'UNKNOWN').map(r => displayLabel(r.field, '资格条件'))
  return { requirements, highlights: [...new Set(highlights)].slice(0, 4),
    risks: [...new Set([...(eligibility.filter(r => r.required && r.status !== 'SATISFIED').map(r => `${displayLabel(r.field, '资格条件')}：${r.required}（${displayLabel(r.status)}）`)), ...gaps, ...(unknown.length ? [`硬资格待确认：${unknown.join('、')}`] : [])])].slice(0, 3), eligibility,
    gaps: [...new Set(gaps)], preparation: [...new Set([...new Map((candidate.recommendation?.analysis.preparation_focus || [])
      .map(r => [r.job_requirement_id, short(r.statement)])).values()])] }
}

export function canReviewQuestion(questionId: number | undefined, evaluatedIds: number[]) {
  return questionId !== undefined && evaluatedIds.includes(questionId)
}
