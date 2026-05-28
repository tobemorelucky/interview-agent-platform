# 16 关键决策记录

## Decision 001：使用模块化单体 + 独立 Worker

### 结论

采用：

- 模块化单体 FastAPI API
- 独立 Celery Worker

### 原因

- 个人项目开发成本更可控；
- 依然能保持高解耦；
- 采集与媒体处理天然适合异步 Worker；
- 后续如规模扩大再局部拆服务。

---

## Decision 002：使用 Milvus 而不是 pgvector 作为向量底座

### 结论

使用 Milvus。

### 原因

- 考虑长期规模；
- 更适合作为独立向量检索基础设施；
- 面经内容持续增长；
- 后续可拓展复杂检索策略。

---

## Decision 003：PostgreSQL 管业务事实，Milvus 仅管正式检索向量

### 结论

业务事实存在 PostgreSQL。  
Milvus 不作为业务事实库。

### 原因

- 发布、审核、状态流必须以关系型数据为准；
- Milvus 更适合向量召回；
- 降低数据一致性风险。

---

## Decision 004：普通用户不触发实时爬虫

### 结论

普通用户查询仅检索已发布面经库。

### 原因

- 用户响应更稳定；
- 成本可控；
- 媒体处理无法实时完成；
- 内容需要先评分和审核。

---

## Decision 005：内容池分为 Raw / Candidate / Published 三层

### 结论

严格三层隔离。

### 原因

- 保留原始可回溯资料；
- Candidate 可供审核；
- Published 保证用户查询质量；
- 方便重跑解析、评分、索引。

---

## Decision 006：只有 Published 内容进入 Milvus

### 结论

Raw 和 Candidate 不入 Milvus。

### 原因

- 保证检索库纯净；
- 避免垃圾和营销内容污染；
- 发布规则更清晰。

---

## Decision 007：第三模块由管理员驱动采集

### 结论

管理员登录后台创建采集任务。

### 原因

- 任务成本高；
- 需要控制采集范围；
- 需要审核与发布流程；
- 不适合向普通用户开放实时触发。

---

## Decision 008：首个真实平台优先做牛客

### 结论

Phase 5 先做牛客。

### 原因

- 文本结构相对清晰；
- 与面经场景最匹配；
- 更适合验证完整流水线。

---

## Decision 009：小红书放在牛客之后

### 结论

Phase 6 接小红书。

### 原因

- 有价值；
- 但图片 OCR 与动态页面让复杂度更高；
- 应在文本链路成熟后扩展。

---

## Decision 010：抖音视频放到第三阶段

### 结论

Phase 7 实现抖音。

### 原因

- 技术亮点高；
- 同时需要视频下载、FFmpeg、ASR、OCR、融合；
- 放在基础流水线稳定后推进。

---

## Decision 011：后端 Python 依赖统一使用 uv

### 结论

使用 uv。

### 原因

- 速度快；
- 易于统一虚拟环境与依赖管理；
- 适合现代 Python 项目；
- 避免 Claude Code 引入多套工具。

---

## Decision 012：前端依赖统一使用 pnpm

### 结论

使用 pnpm。

### 原因

- 依赖管理清晰；
- 避免多 lockfile；
- 更适合 monorepo 风格项目。

---

## Decision 013：Prompt 独立存放并版本化

### 结论

Prompt 不硬编码在 service 中。

### 原因

- 易维护；
- 可评估；
- 可追踪；
- 方便后续迭代。

---

## Decision 014：评分由规则 + AI 共同完成

### 结论

LLM 只输出评分特征，最终分数由程序聚合。

### 原因

- 可解释；
- 可调参；
- 更稳定；
- 不完全依赖 LLM 主观判断。

---

## Decision 015：Phase 4 先实现 MockSourceAdapter

### 结论

先验证任务框架，再接真实平台。

### 原因

- 降低调试复杂度；
- 先跑通状态机和审核发布；
- 避免一开始陷入平台采集细节。

---

## Decision 016：Phase 4 不使用付费搜索 API

### 结论
不使用 Google Custom Search API、Bing Search API 等付费搜索服务。

### 原因
- 避免 API 费用和配额限制；
- 面经搜索量在可控范围内；
- 自托管 SearXNG 可满足需求。

---

## Decision 017：Phase 4 默认 SearchProvider 使用自托管 SearXNG

### 结论
默认搜索提供方使用自托管 SearXNG 实例。

### 原因
- 免费、无配额限制；
- 支持多引擎聚合；
- Docker Compose 可一键部署；
- 通过 EXPERIENCE_SEARCH_PROVIDER 配置可替换。

---

## Decision 018：Phase 4 默认 ContentFetcher 使用 httpx + trafilatura

### 结论
默认网页内容抓取使用 httpx + trafilatura/readability 库。

### 原因
- 轻量、快速；
- 对静态 HTML 页面效果好；
- Playwright / Crawl4AI / CloakBrowser 仅作为可选 BrowserFetcher；
- 通过 EXPERIENCE_FETCHER 配置可切换。

---

## Decision 019：Phase 4 不做人工文本导入

### 结论
不支持管理员手动粘贴面经文本或上传文件导入面经。

### 原因
- 保持系统自动化程度；
- 避免人工数据质量问题；
- 如果后续需要，可以作为独立小功能加入。

---

## Decision 020：Phase 4 重点实现三个 Agent

### 结论
Agent 工作流核心由三个 Agent 组成：
- **Extraction Agent**：从网页正文抽取面经、题目、答案；
- **Routing Agent**：判断题目路由（题库、方向、分类、是否入向量库）；
- **Reliability Agent**：判断内容可信度、广告/卖课风险。

### 原因
- 三个 Agent 覆盖了"抽取-分类-评分"的核心流程；
- 每个 Agent 职责单一，可独立测试和优化；
- Query Planner Agent 和 Summary Agent 暂缓，后续按需加入。

---

## Decision 021：Phase 4 默认人工审核，可配置自动审核

### 结论
默认所有候选面经需要管理员人工审核后才能发布。管理员可选择开启自动审核。

### 条件
自动审核必须满足：
- reliability_score >= EXPERIENCE_AUTO_APPROVE_MIN_SCORE
- is_advertising = false
- is_course_selling = false
- question_count >= 1

---

## Decision 022：Phase 4 默认不写入向量库

### 结论
默认 EXPERIENCE_WRITE_TO_VECTOR_INDEX = false，不将面经写入 Milvus。

### 原因
- 避免污染现有 kb_chunks_current 集合；
- 面经数据量在初期较小，结构化查询即够用；
- 后续可按需开启。

---

## Decision 023：Phase 4 默认不接入简历模拟面试

### 结论
默认 INTERVIEW_USE_EXPERIENCE_QUESTION_BANK = false。

### 原因
- 简历面试当前使用 kb_chunks_current，已可工作；
- 面经题库的质量需要积累和验证；
- 后续 Phase 4.4 完成后再评估是否接入。
