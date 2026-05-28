# 15 开发路线图

## 1. 总体原则

- 先搭骨架，再做业务；
- 先完成单条完整链路，再扩来源；
- 先 Mock（已废弃），直接使用真实数据；
- 先人工审核，再考虑自动发布；
- 先工程正确，再谈优化。

---

# 2. Phase 0：工程骨架

目标：

- Monorepo
- Docker Compose
- FastAPI 最小服务
- Vue3 最小应用
- Worker 最小应用
- `.env.example`
- README
- 基础健康检查

验收：

- 所有基础容器可启动；
- API / Web / Worker 各自可运行；
- 文档与目录匹配。

---

# 3. Phase 1：认证与用户系统

目标：

- 注册
- 登录
- JWT
- 当前用户
- USER / ADMIN
- seed admin
- 登录页 / 注册页

---

# 4. Phase 2：面试知识库问答

目标：

- 文档导入
- chunk
- embedding
- Milvus KB collection
- 检索问答
- SSE
- 会话历史
- QA 页面

---

# 5. Phase 3：简历模拟面试

目标：

- 文件上传
- 简历解析
- 结构化简历
- 问题生成
- 答案生成
- 报告存档
- 报告页面

---

# 6. Phase 4：管理员触发的近期面经更新与整理 Agent 工作流

Phase 4 重新定义为"管理员驱动的面经搜索、抓取、Agent 处理、审核、入库"全链路。

核心决策（见 Decision Log）：
- 不使用付费搜索 API
- 第一版使用真实数据，不使用 Mock
- 默认 SearchProvider 使用自托管 SearXNG
- 默认 ContentFetcher 使用 httpx + trafilatura
- 默认人工审核，可配置自动审核
- 默认不写入向量库，可配置开启
- Query Planner Agent / Summary Agent 暂缓

## 6.1 Phase 4.1：关键词预设 + SearXNG 搜索 + HTTP 内容抓取

- experience_keyword_presets 管理
- SearXNG SearchProvider
- 候选 URL 过滤
- httpx + trafilatura ContentFetcher
- experience_collection_tasks / experience_source_items 数据模型

## 6.2 Phase 4.2：Extraction / Routing / Reliability 三 Agent 工作流

- Extraction Agent：从网页正文抽取面经、题目、答案；无答案时补充 standard_answer
- Routing Agent：判断题目进入哪些题库、岗位方向、技术分类、是否可入向量库
- Reliability Agent：判断内容可信度、广告/卖课风险、是否适合发布
- interview_experiences / interview_questions 表持久化

## 6.3 Phase 4.3：管理员审核与用户近期面经总结页

- 管理员审核页面（待审核列表、抽取题目预览、可靠性评分、操作按钮）
- 用户端 GET /api/v1/experiences/recent 查询已发布的近期面经
- review_actions 表审计日志

## 6.4 Phase 4.4：可选题库入库与 Milvus 向量索引

- 配置 EXPERIENCE_WRITE_TO_QUESTION_DB 控制是否写入结构化题库
- 配置 EXPERIENCE_WRITE_TO_VECTOR_INDEX 控制是否写入 Milvus
- interview_questions_current Milvus collection
- 配置 INTERVIEW_USE_EXPERIENCE_QUESTION_BANK 控制简历面试是否使用面经题库（默认关闭）

## 6.5 Phase 4.5：BrowserFetcher 增强（可选）

- Crawl4AI / Playwright / CloakBrowser 作为可选 BrowserFetcher
- 默认使用 httpx，BrowserFetcher 仅在 JS 渲染页面需要时启用
- 配置 EXPERIENCE_ENABLE_BROWSER_FETCH / EXPERIENCE_BROWSER_FETCHER

## 6.6 Phase 4.6：平台适配器与视频转写（后续）

- Nowcoder / Xiaohongshu / Douyin 平台适配器
- 图片 OCR、视频 ASR
- 多媒体文本融合

---

# 7. Phase 5：牛客真实采集（合并至 Phase 4.6）

牛客采集不再作为独立 Phase，作为 Phase 4.6 的平台适配器实现。

---

# 8. Phase 6：小红书图文采集（合并至 Phase 4.6）

---

# 9. Phase 7：抖音视频采集（合并至 Phase 4.6）

---

# 10. Phase 8：评测与优化

目标：

- RAG 测试集
- 简历分析测试集
- 面经抽取测试集
- 评分一致性评估
- 性能优化
- 监控与部署打磨

---

# 11. 每阶段交付物格式

Claude Code 完成每阶段后，需要汇报：

1. 完成内容
2. 修改文件
3. 新增 migration
4. 新增 API
5. 运行命令
6. 测试结果
7. 已知问题
8. 下一步建议

---

# 12. 当前立即要做的事

你已经完成：

- 项目初步设计；
- 协同开发方案；
- 正式文档包生成。

下一步：

1. 将本正式文档包放入仓库；
2. 启动 Claude Code；
3. 发送 Phase 0 kickoff 指令；
4. 让 Claude Code 先输出计划，不要直接修改；
5. 将计划发回 ChatGPT 审查；
6. 通过后再让 Claude Code 实施。
