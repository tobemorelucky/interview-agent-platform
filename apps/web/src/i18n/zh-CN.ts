/** Presentation only: API/database enums and deterministic policy remain unchanged. */
export const userNavigation = [
  { label: '首页', path: '/dashboard' }, { label: '我的简历', path: '/resumes' },
  { label: '我的画像', path: '/profile' }, { label: '岗位推荐', path: '/jobs/matches' },
  { label: '自定义岗位', path: '/jobs/targets' },
  { label: '岗位库', path: '/career' },
  { label: '专项训练', path: '/training' },
  { label: '模拟面试', path: '/interview' }, { label: '面试报告', path: '/interview/reports' },
  { label: '成长记录', path: '/memory' },
]
export const adminNavigation = [
  { label: '职业资料采集', path: '/admin/public-career' },
  { label: '知识库管理', path: '/admin/kb/documents' }, { label: '面经采集', path: '/admin/experiences' },
  { label: '题库 / 面经审核', path: '/admin/experience-review' }, { label: '审计日志', path: '/admin/audit' },
]
export function navigationActive(path: string, target: string): boolean {
  if (target === '/interview') return path === '/interview'
  return path === target || path.startsWith(target + '/')
}
export const dimensionLabels: Record<string, string> = {
  technical_correctness: '技术正确性', coverage: '覆盖度', depth: '深度', relevance: '相关性', communication: '表达',
}
export const fitLabels = { STRONG_MATCH: '高度匹配', GOOD_MATCH: '较为匹配', EXPLORATORY: '可尝试', UNCERTAIN: '信息不足' }
export const eligibilityLabels = { SATISFIED: '已满足', NOT_SATISFIED: '明确不满足', UNKNOWN: '暂无法判断' }
export const labels: Record<string, string> = {
  ...fitLabels, ...eligibilityLabels, ...dimensionLabels,
  PENDING: '待处理', READY: '待开始', IN_PROGRESS: '进行中', COMPLETED: '已完成', FAILED: '失败', RUNNING: '处理中',
  UPLOADED: '已上传', PROCESSING: '处理中', APPROVED: '已批准', CORRECTED: '已修改并批准', REJECTED: '已忽略',
  ASKED: '待回答', ANSWERED: '已回答', EVALUATED: '已评价', SKIPPED: '已跳过',
  FOLLOW_UP: '追问', DEEP_DIVE: '深挖', NEXT: '下一题', COMPLETE: '完成面试', SKIP: '跳过',
  MENTIONED: '材料提及', DEMONSTRATED: '有具体使用依据', STRONG_EVIDENCE: '多处具体使用依据',
  SUPPORTED: '有匹配证据', PARTIAL: '部分匹配', NO_EVIDENCE: '当前材料未提供证据', NOT_APPLICABLE: '不适用',
  EASY: '基础', MEDIUM: '适中', HARD: '深入', LOW: '较低', HIGH: '较高',
  RESUME: '简历依据', USER_CONFIRMED: '本人确认', AGENT: 'Agent 抽取', REPLAY_FIXTURE: 'Replay 回放模式',
  PUBLIC_QUESTION: '公共题库', JOB_REQUIREMENT: '岗位要求', CANDIDATE_PROFILE: '候选人画像', DYNAMIC_GENERATED: '动态问题',
  GOAL: '求职目标', PREFERENCE: '偏好', EXPERIENCE: '经历', SKILL_OBSERVATION: '本场表现观察', PREPARATION_FOCUS: '准备重点',
  SEMANTIC: '长期事实', SKILL: '历史表现估计', EPISODIC: '历史事件', PROCEDURAL: '流程记录', SAFETY: '安全 / 同意',
  INTERVIEW: '模拟面试', GLOBAL: '通用', PRIVATE: '仅本人可见', MEMORY_PROPOSAL: '本人批准的记忆建议', USER: '本人',
  skills: '技能', projects: '项目经历', education: '教育经历', work_experience: '工作经历', internship_experience: '实习经历',
  location: '地区', locations: '地区', campus_type: '校招类型', job_family: '岗位类别', degree: '学历', major: '专业', graduation_year: '毕业年份',
  education_minimum: '学历要求', majors: '专业要求', graduation_years: '毕业年份', level: '岗位级别',
  explicit_exclusion: '明确排除项', excluded_tags: '排除标签', tags: '指定技术标签',
  strengths: '优势', candidate_summary: '个人摘要', statement: '描述', label: '名称',
  PublicDataResearchAgent: '公共数据 Research Agent', ResumeProfileAgent: '简历画像 Agent', JobMatchingAgent: '岗位匹配 Agent',
  InterviewMainAgent: '面试官 Agent', AnswerEvaluationAgent: '回答评价 Agent', SummaryCoachAgent: '复盘教练 Agent',
  TECHNICAL: '技术问题', PROJECT: '项目问题', BEHAVIORAL: '行为问题', SYSTEM_DESIGN: '系统设计',
  technical: '技术问题', project: '项目问题', behavioral: '行为问题', system_design: '系统设计',
  work: '工作经历', internship: '实习经历', general: '基本材料', skills_section: '技能材料',
  public_question: '公共题库', job_requirement: '岗位要求', candidate_profile: '候选人画像',
  AI: 'AI 应用', BACKEND: '后端开发', CAMPUS: '校招', AI_APPLICATION: 'AI 应用开发',
  submitting: '正在提交回答…', evaluating: '正在分析回答…', generating_evaluation: '正在生成评价…', generating_first_question: '正在准备第一题…', generating_next_question: '正在准备追问 / 下一题…',
    idle: '已就绪', validating_design: '正在选择验证工具 / 执行方案验证…',
  KB_RETRIEVED: '知识库召回', LLM_GENERATED: '大模型生成', HYBRID: '混合生成',
  QUEUED: '已加入队列', PARSING_RESUME: '解析简历文件', STRUCTURING_RESUME: '提取结构化信息',
  GENERATING_RETRIEVAL_QUERIES: '生成检索查询', RETRIEVING_KB: '检索知识库', GENERATING_QUESTIONS: '生成面试问题', SAVING_REPORT: '保存分析报告',
}
export function publicName(value?: string | null): string {
  if (!value) return '未说明'
  if (value.includes(' · ')) return value.split(' · ').map(publicName).join(' · ')
  if (/^(M6 DEMO REPLAY|M4 Replay Fixture) /.test(value)) return 'DEMO 演示公司'
  const demo = value.startsWith('DEMO ')
  const original = demo ? value.slice(5) : value
  const title = ({ 'AI Application Engineer': 'AI 应用工程师', 'Java Backend Engineer': 'Java 后端工程师', 'Data / Risk Analyst': '数据 / 风险分析师', 'Data Risk Analyst': '数据 / 风险分析师' } as Record<string, string>)[original]
  return title ? (demo ? 'DEMO ' : '') + title : value
}
export function displayText(value: string): string {
  return value.replace(/\b(SUPPORTED|PARTIAL|NO_EVIDENCE|NOT_APPLICABLE|SATISFIED|NOT_SATISFIED|UNKNOWN)\b/g, key => displayLabel(key))
    .split('CandidateProfile / ResumeEvidence').join('候选人画像 / 简历依据')
}
export function displayLabel(value: unknown, fallback = '未说明'): string {
  if (value == null || value === '') return fallback
  const key = String(value)
  return labels[key] || (/^[A-Z][A-Z_\d]*$/.test(key) ? fallback : key)
}
export function modelModeLabel(provider?: string | null, model?: string | null): string {
  if (provider === 'replay') return 'DEMO · Replay 回放模式（不是 Live 模型验收）'
  if (model?.startsWith('deepseek')) return 'DeepSeek 实时模型'
  if (model === 'doubao-seed-2-1-turbo-260628') return 'Doubao 2.1 Turbo 实时模型'
  return provider ? 'Live 实时模型' : ''
}
export const errorLabels: Record<string, string> = {
  AUTH_INVALID_CREDENTIALS: '邮箱或密码不正确，请检查后重试。', AUTH_TOKEN_EXPIRED: '登录已过期，请重新登录。',
  AUTH_MISSING_TOKEN: '请先登录后再继续。', AUTH_PERMISSION_DENIED: '没有访问权限，请联系管理员。',
  DUPLICATE_EMAIL: '这个邮箱已注册，请直接登录。', DUPLICATE_USERNAME: '这个用户名已被使用，请换一个。',
  UNSUPPORTED_SKILL_STATE: '候选人画像生成结果与简历证据不一致，请重新生成或检查简历内容。',
  MODEL_USER_QUOTA: '今日模型使用额度不足，请稍后再试。',
  MODEL_USER_CONCURRENCY: '你已有模型任务运行中，请等待完成后重试。',
  MODEL_IDENTITY_REQUIRED: '模型任务缺少可信归属信息，请联系维护者。',
  RATE_LIMIT_UNAVAILABLE: '限流服务暂时不可用，请稍后再试。',
  UNSUPPORTED_STRENGTH: '优势陈述缺少简历依据，请检查材料后重试。',
  PROFILE_NOT_FOUND: '还没有候选人画像，请先上传简历并生成画像。',
  CANDIDATE_PROFILE_REQUIRED: '请先生成候选人画像，再开始模拟面试。',
  PROFILE_REQUIRED: '请先上传简历并生成候选人画像，再选择训练目标。',
  TRAINING_TARGET_INVALID: '该知识点不在当前岗位范围内，或状态已变化，请刷新训练推荐。',
  TRAINING_NO_TARGETS: '当前岗位与专业规则没有合法考察目标，请更换专业方向。',
  TRAINING_REQUIRES_ADAPTIVE: '训练需要选择专业方向并启用自适应面试。',
  TRAINING_REQUEST_CONFLICT: '同一创建请求的训练配置已变化，请确认配置后重新开始。',
  INDEPENDENT_RECHECK_STYLE: '独立复测请使用标准或严格风格，避免开场提示影响验证。',
  JOB_NOT_AVAILABLE: '该岗位当前不可用，请选择其他岗位。', JOB_UNAVAILABLE: '该岗位当前不可用，请选择其他岗位。',
  COVERAGE_INSUFFICIENT: '当前公开岗位数据不足，暂时无法生成可靠推荐。',
  NETWORK_ERROR: '网络连接异常，请检查连接后重试。',
  UNAUTHORIZED: '登录已失效，请重新登录。', INVALID_CREDENTIALS: '邮箱或密码不正确，请检查后重试。',
  FORBIDDEN: '没有访问权限，请联系管理员。', NOT_FOUND: '内容不存在或已不可用。',
  CONFLICT: '当前状态已变化，请刷新后重试。', VALIDATION_ERROR: '请检查填写内容后重试。',
  RESUME_REFERENCED: '该简历已作为画像依据保留，不能删除。',
  SESSION_HAS_REPORT: '该面试已有报告，为保留复盘依据不能删除。',
  COACH_COMPUTED_NUMBER: '报告分析混入了应由程序计算的统计，请安全重试；本场评分与完成状态已保留。',
  model_timeout: '模型处理超时，已保存的数据不会丢失，请安全重试。',
}
export function userError(code: string, raw = ''): string {
  if (errorLabels[code]) return errorLabels[code]
  // Show ordinary Chinese business messages; never surface stacks/raw provider payloads.
  if (/[\u4e00-\u9fff]/.test(raw) && !/Traceback|\b[A-Z][A-Z_]{5,}\b/.test(raw)) return raw
  return '操作未完成，请刷新后安全重试。'
}
