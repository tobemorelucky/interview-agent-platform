export interface Resume {
  id: number
  user_id: number
  filename: string
  file_type: string
  file_size: number
  status: "UPLOADED" | "PROCESSING" | "COMPLETED" | "FAILED"
  error_message: string | null
  task_id: string | null
  processing_stage: string | null
  stage_message: string | null
  created_at: string
  updated_at: string
}

export interface ResumeList {
  items: Resume[]
  total: number
}

export interface ResumeDetail extends Resume {
  raw_text_preview: string | null
}

export interface BasicInfo {
  name: string
  email: string
  phone: string
  location: string
  years_of_experience: number | null
  current_role: string
  target_role: string
}

export interface Education {
  school: string
  degree: string
  major: string
  start_year: number | null
  end_year: number | null
}

export interface Skills {
  languages: string[]
  frameworks: string[]
  databases: string[]
  tools: string[]
  ai_ml: string[]
  other: string[]
}

export interface Project {
  name: string
  role: string
  duration: string
  description: string
  tech_stack: string[]
  key_contributions: string[]
  quantitative_results: string[]
}

export interface Internship {
  company: string
  role: string
  duration: string
  responsibilities: string[]
  tech_stack: string[]
}

export interface RiskPoint {
  area: string
  description: string
  severity: "HIGH" | "MEDIUM" | "LOW"
}

export interface ResumeSummary {
  basic_info: BasicInfo
  education: Education[]
  skills: Skills
  projects: Project[]
  internships: Internship[]
  publications: Record<string, unknown>[]
  highlights: string[]
  risk_points: RiskPoint[]
}

export interface RetrievalQueryItem {
  query: string
  target: string
}

export interface RetrievalHit {
  chunk_id: number
  doc_id: number
  title: string
  preview: string
  score: number
  source_type: string
}

export interface RetrievedQueryResult {
  query: string
  target: string
  hit_count: number
  top_hits: RetrievalHit[]
}

export interface RetrievedContext {
  total_hits: number
  queries: RetrievedQueryResult[]
}

export interface Evidence {
  title: string
  preview: string
  score: number
  source_type: string
  chunk_id: number | null
  doc_id: number | null
}

export interface InterviewQuestion {
  question: string
  category: string
  difficulty: "EASY" | "MEDIUM" | "HARD"
  reason: string
  source: "KB_RETRIEVED" | "LLM_GENERATED" | "HYBRID"
  suggested_answer: string
  follow_up_questions: string[]
  evidence: Evidence | null
}

export interface InterviewSuggestions {
  strengths: string[]
  weaknesses_to_prepare: string[]
  interview_tips: string[]
}

export interface ResumeReport {
  id: number
  resume_id: number
  summary_json: ResumeSummary | null
  retrieval_queries_json: { queries: RetrievalQueryItem[] } | null
  retrieved_context_json: RetrievedContext | null
  questions_json: { questions: InterviewQuestion[] } | null
  suggestions_json: InterviewSuggestions | null
  created_at: string
}

export function sourceLabel(source: string): string {
  switch (source) {
    case "KB_RETRIEVED": return "知识库召回"
    case "LLM_GENERATED": return "大模型生成"
    case "HYBRID": return "混合生成"
    default: return source
  }
}

export function sourceColor(source: string): string {
  switch (source) {
    case "KB_RETRIEVED": return "#67c23a"
    case "LLM_GENERATED": return "#409eff"
    case "HYBRID": return "#e6a23c"
    default: return "#909399"
  }
}

export function statusLabel(status: string): string {
  switch (status) {
    case "UPLOADED": return "待处理"
    case "PROCESSING": return "处理中"
    case "COMPLETED": return "已完成"
    case "FAILED": return "失败"
    default: return status
  }
}

export function stageLabel(stage: string | null): string {
  if (!stage) return ""
  switch (stage) {
    case "QUEUED": return "已加入队列"
    case "PARSING_RESUME": return "解析简历文件"
    case "STRUCTURING_RESUME": return "提取结构化信息"
    case "GENERATING_RETRIEVAL_QUERIES": return "生成检索查询"
    case "RETRIEVING_KB": return "检索知识库"
    case "GENERATING_QUESTIONS": return "生成面试问题"
    case "SAVING_REPORT": return "保存分析报告"
    case "COMPLETED": return "完成"
    case "FAILED": return "失败"
    default: return stage
  }
}

export function statusColor(status: string): string {
  switch (status) {
    case "UPLOADED": return "#909399"
    case "PROCESSING": return "#409eff"
    case "COMPLETED": return "#67c23a"
    case "FAILED": return "#f56c6c"
    default: return "#909399"
  }
}
