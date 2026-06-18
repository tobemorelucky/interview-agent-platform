# 19 Agent Platform Evolution Plan

本文记录 Interview Agent Platform 从“面试 Agent 平台”升级到“可用型多 Agent 平台”的阶段性架构、已完成模块、后续路线和开发边界。

## 1. 项目当前定位

当前项目不是简单聊天机器人，而是面向求职场景的智能面试准备平台。它围绕“知识、简历、面试、记忆、面经、审核、治理”形成闭环：

- 用户认证：支持普通用户和管理员角色。
- 知识库 QA：基于结构化知识库和向量检索回答面试相关问题。
- 简历模拟面试：根据用户简历、目标岗位和知识库生成问题、追问与反馈。
- 用户分层记忆：沉淀用户长期背景、偏好、技能画像和历史面试事件。
- 近期面经采集：管理员创建采集任务，发现候选来源，后续进入抓取、抽取和审核链路。
- 管理员审核：候选内容必须经过可追踪、可审核流程后才能进入正式题库或发布库。
- 后续 LangGraph 多 Agent：用于抽取、分类、可靠性评分、追问策略和工具化面试流程。
- 治理能力：权限、限流、审计、并发锁、SSRF 防护，保障平台可运行、可排查、可控成本。

## 2. 当前已完成模块

### 2.1 Phase 1/2/3 基础能力

- 用户认证：完成登录、注册、当前用户获取和 ADMIN / USER 角色基础隔离。
- 知识库：完成文档上传、解析、索引和 QA 检索回答流程。
- 简历模拟面试：完成简历上传、报告生成、会话创建、目标岗位确认、问题生成、对话式模拟面试。

### 2.2 Phase 4 面经采集搜索层

- 关键词预设：支持岗位、公司、平台关键词预设管理。
- 采集任务：管理员可以创建采集任务并查看历史记录。
- SearXNG 搜索：接入自托管 SearXNG 作为第一版真实搜索发现源。
- 平台为空表示通用搜索：不再使用“全网”作为平台选项。
- 平台选择牛客 / 小红书 / 抖音时只保留对应官方域名结果。
- 候选过滤使用 title、snippet、url、domain 综合判断。
- 采集历史和来源列表：管理员可以查看任务状态、统计数据、source items 和搜索元信息。

### 2.3 Memory

- M1 用户分层记忆中心：完成 user_memory_items、user_skill_profiles、user_memory_events 和基础 API。
- M2 面试时只读注入 memory context：开始或继续模拟面试时读取用户长期记忆、偏好和技能画像。
- M3 面试结束后受控写入：面试结束后可受控写入 preference memory、episodic memory 和 skill profile。

### 2.4 Governance

- request_id 中间件：接收或生成 `X-Request-ID`，响应中回传，并记录请求日志。
- 统一错误响应：错误统一返回 `{ error: { code, message, request_id, details } }`。
- audit_logs：记录关键管理操作和敏感行为。
- permission guard：提供角色、权限、owner/admin 隔离工具。
- Redis rate limit：限制高成本或可滥用接口。
- Redis task lock：防止重复执行搜索、记忆合并等任务。
- SSRF URL 安全校验：URL 抓取前校验协议、host 和解析 IP，阻断本机与内网目标。

## 3. 当前核心架构分层

1. Source Discovery Layer：负责搜索发现 URL，例如 SearXNG 查询、query 生成、候选 URL 过滤和去重。
2. Content Fetch Layer：负责网页正文抓取，输入 source item URL，输出原始正文和抓取状态。
3. Content Normalization Layer：负责正文清洗、去重、content_hash、长度校验和格式统一。
4. Multi-Agent Extraction Layer：使用 LangGraph 多 Agent 做抽取、分类、可靠性评分和结构化输出。
5. Human Review Layer：管理员审核候选结果，编辑题目、答案、标签和发布状态。
6. Knowledge Index Layer：审核通过后进入结构化题库和向量库，保留 source_url 证据链。
7. User Interview Layer：基于简历、记忆、题库和知识库驱动模拟面试。
8. Governance Layer：横向覆盖权限、限流、并发锁、审计、安全和链路追踪。

## 4. Memory 模块设计说明

记忆体系采用分层设计：

- L0 Working Memory：当前会话上下文，只在当前对话或面试流程中使用。
- L1 Session Summary Memory：单场面试摘要，用于压缩一场面试的关键信息。
- L2 Semantic Memory：长期事实，例如用户背景、目标方向、项目经历摘要。
- L3 Skill Memory：技能画像，例如技能强项、薄弱点、置信度和证据数量。
- L4 Episodic Memory：历史面试事件，例如一次面试中的表现和关键结论。
- L5 Preference Memory：用户偏好，例如严格追问、先提示再答案、希望覆盖的方向。
- L6 Safety / Consent Memory：隐私、安全和授权偏好。

约束：

- M2 阶段只读注入 memory context，不自动写入长期记忆。
- M3 阶段受控写入长期记忆，不把每轮回答无脑写入。
- 自动写入必须有 `source_type`、`source_id` 和 `memory_events`。
- memory context 注入 prompt 时应压缩摘要，不暴露原始存储细节。
- 当前 `target_position` 优先于历史 memory 中可能冲突的目标信息。

## 5. Governance 模块设计说明

已加入的治理能力用于让平台从“能跑”变成“可排查、可约束、可持续运行”：

- request_id：用于链路追踪。每次请求有唯一 ID，错误响应和日志可对齐。
- audit log：用于管理员操作和关键行为追踪，例如 memory 写入、任务创建、搜索执行、删除。
- permission guard：用于 owner/admin 权限隔离，避免只依赖前端隐藏入口。
- Redis rate limit：用于限制高成本接口，例如面试聊天、记忆写入、搜索执行。
- Redis lock：用于防止重复执行搜索、抓取、记忆合并等任务。
- SSRF 防护：用于后续 URL 抓取安全，所有抓取目标必须经过 public HTTP URL 校验。

## 6. 下一步路线

### Step 6：网页正文抓取

- 输入：`experience_source_items` 中 `fetch_status=DISCOVERED` 的 URL。
- 处理：SSRF 校验、httpx 抓取、trafilatura / BeautifulSoup 正文提取。
- 输出：`raw_text`、`content_hash`、`fetched_at`、`FETCHED` / `FETCH_FAILED`。
- 边界：不做 Agent，不做结构化抽取，不做入库发布。

### Step 6.5：抓取质量面板

- 抓取成功率。
- 正文长度分布。
- 失败原因分布。
- 单 URL 重试。
- 抓取批次和任务维度统计。

### Step 7：LangGraph 三 Agent

- Extraction Agent：从网页正文中抽取面经、问题、答案、上下文和标签。
- Routing Agent：判断题目适合进入哪些题库、岗位方向、技术分类和索引目标。
- Reliability Agent：判断内容可信度、广告风险、卖课风险、是否适合进入审核池。
- 输出进入 `WAITING_REVIEW`，不直接入正式题库或向量库。

### Step 8：管理员审核

- 编辑题目。
- 编辑答案。
- 调整标签和岗位方向。
- 通过 / 拒绝 / 退回重处理。
- 审核通过后才可入题库或发布库。

### Step 9：题库入库 + Milvus

- 结构化题库。
- 去重。
- 向量索引。
- 保留 `source_url` 证据链。
- 只允许审核通过、正式发布的数据进入 Milvus。

### Step 10：面试 Agent ReAct 化

- `read_memory`：读取用户长期记忆与技能画像。
- `retrieve_kb`：检索知识库和已发布题库。
- `evaluate_answer`：评估用户回答质量。
- `generate_followup`：生成追问。
- `update_memory_candidate`：生成候选记忆，不直接写入。
- `next_question`：选择下一题或调整面试路径。

## 7. 开发边界

- 搜索、抓取、去重、状态流转属于确定性工程层。
- 抽取、分类、评分、追问策略属于 Agent 层。
- Agent 输出必须可追踪、可审核。
- 长期记忆写入必须受控，不把每轮回答直接写入长期记忆。
- 高成本任务必须走限流和任务锁。
- URL 抓取必须经过 SSRF 防护。
- 不要为了使用 Agent 而把确定性流程 Agent 化。
- 普通用户不触发实时采集、实时抓取或未审核内容查询。
- 未审核候选内容不能进入 Milvus，也不能直接暴露给普通用户。
