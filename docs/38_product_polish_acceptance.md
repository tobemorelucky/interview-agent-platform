# 最后一轮产品化优化与验收

本轮边界：不新增 Agent，不修改数据库 Schema/migration，不重做 Profile/Research，不恢复 Live Search，不 commit/push。保留此前未提交工作。

## 模型与性能

- 所有正式 LIVE route 继承 `doubao-seed-2-1-turbo-260628`；没有 DeepSeek 默认或 Doubao 2.0 route override。
- Research、Profile、Matching、Interview Main、Evaluation、Coach 各 route 的 thinking 均为 disabled。不是在 Agent 中硬编码模型。
- 本轮真实在线验证 Matching/Main/Evaluation/Coach。Profile/Research 保持冻结，没有为验证配置重新处理用户简历或导入数据。
- MatchRun 9 属于真实用户 38、ProfileVersion 1；Matching latency 15,915 ms，input 20,800 / output 954 / reasoning 0 tokens，repair 0。
- 已保存 MatchRun 9 详情 Service 读取三次为 16.79 / 7.94 / 7.84 ms，JobMatchingAgent 调用数增量为 0。这不是浏览器端到端网络耗时。
- 2026-10-07 服务恢复后 Service 再读为 29.14 / 14.23 / 21.90 ms，调用数增量仍为 0；浏览器 reload 至华为推荐卡片可见测得 274 ms，仅代表本地该次已保存结果读取，不代表新匹配耗时。
- 模型输入只投影固定 Snapshot：保留真实事实/证据配对、excerpt、JD/Requirement 与所选题目来源；移除重复 statement、非本题公共题目、冗余面经摘要、服务端记忆 provenance 包装。数据库 Snapshot 不修改。
- 不把模型串行生成改成未经校验的预先推题，也不增加每题参考回答的独立模型调用。
- Session 62 固定完整 Snapshot 为 50,546 UTF-8 bytes，Main/Evaluation 通用模型投影为 23,621 bytes；32 个真实事实/32 条 Evidence 的 key 与 excerpt 均保持一致。这不是 tokenizer 或完整 HTTP 请求长度。

## 面试与参考回答

- Main Prompt 保留旧版本；当前 `interview_main@1.0.11` 明确 Controller 的 requested_action 优先于 Evaluation 建议，强制准确来源引用，并区分 DEEP_DIVE 动作与 DYNAMIC_GENERATED 来源。补问/深挖不重新声明候选事实，使用父题由 Controller 继承的真实 provenance；候选人来源题仍要求精确事实/证据配对。技术追问针对单个机制，不以整份简历未提到的模块反复考查。
- `answer_evaluation@1.0.4` 评分针对本题，并在同一次调用内提供参考回答：回答框架、面试版示范、加分点、可能追问。
- 示例回答不写入候选人事实，不伪造生产指标；UI 明确标注“用于练习，不代表真实经历或唯一正确答案”。
- 参考回答在本题有效 Evaluation 提交后才开放，后端作答前返回 409。Report 复用持久化参考内容，不再次调用模型。
- 历史没有完整参考的题目只显示原有框架，不伪造历史生成结果。
- UI 展示提交、分析、生成评价、准备下一题阶段与真实已等待秒数；较慢时提示处理状态和刷新恢复。实际处理 lease/heartbeat、幂等键和 Controller 并发校验继续有效。
- 迭代验证发现 Main 来源/动作校验失败，均保留 FAILED audit，没有绕过 validator 或推进 Session。新增动作/来源诊断只保存 requested/received enum 与 question 是否存在，不保存隐藏推理或完整原始响应。
- 下一题调用失败后，成功且已验证的 Evaluation 按同一 Answer + payload hash + 模型/provider + Prompt 复用；重试不生成重复当前 Evaluation，不重复计费或伪造新调用审计。

## 报告与记忆

- Coach 当前 `interview_coach@1.0.3`：少量真实引用支持的优势、严重问题优先、去重、具体机制/练习产物，不输出模型计算的数字；避免准备任务里也出现“一次”等被当前数字规则拒绝的表达，没有修改 resolver。
- Report 分离总体统计与 Coach 分析，逐题复盘直接使用持久化 Answer/Evaluation/参考回答。
- “本场建议记住”提供批准、修改后批准、忽略；审批继续通过原有原子事务。
- 面试页展示“本场使用的历史记忆（开始时固定）”；批准记忆与近期表现观察分区，后者不是用户长期事实。
- PENDING 不参与长期记忆；批准只影响之后新建的 Snapshot，不更新旧 Session。

## 自动化

- backend：最终 325 passed（独立 `interview_agent_f4_regression_20261007_memory_prompt11`，172.63 s）；此前 324 passed 保留，新增一条补问父题 provenance 继承与错误引用拒绝回归，没有删减原有回归。
- frontend：29/29 passed；TypeScript + production build 通过；Python compile 通过。
- 正式 DB revision 0024；冻结 migrations 0017–0024 hash 无变化。
- Secret scan：无 `.env` 实际 secret 进入 tracked/untracked 非 ignored 文件或 diff；`.env`、`.runtime`、浏览器截图继续 ignored。
- `git diff --check` 通过；Git 的 LF/CRLF 提示不是 whitespace 错误。

## 真实浏览器验收记录

使用真实登录用户 38、Resume 38 / ProfileVersion 1、已审核华为 AI应用工程师岗位；模型 LIVE，回答标记为验收技术方案，不冒充真人履历。不使用 Replay 模型。

- MatchRun 9：真实 Doubao Matching 成功。
- Session 61：迭代验收中有效评价 4 条。额外失败调用消耗本场模型预算后，继续回答被预算保护拒绝；Session 未被错误 COMPLETE。所有失败/成功审计及已保存回答保留，没有重置预算。
- Session 62：COMPLETED，4 条唯一 Answer / 4 条有效 Evaluation；全部问题 COMPLETED、active_question_id=NULL，没有未评价问题泄漏。先项目介绍，随后 RRF 排名融合 vs 原始分数加权的具体 FOLLOW_UP，再进入 k/候选数量取舍 DEEP_DIVE，最后 NEXT 到岗位资格核对。中间 Main 来源类型失败一次，使用同一 Answer 与已验证 Evaluation 安全恢复，没有再次评分。
- 刷新后恢复前三轮评价和第四题；四题均有持久化的四部分参考回答。最终 deterministic overall_average=4.35，五维为 4.5/4.25/3.75/5/4.25，补问 1、深挖 1。
- Report 4 的 Coach@1.0.2 失败保留：`practice_plan.1` 中“一次”触发 COACH_COMPUTED_NUMBER。Session COMPLETED 与统计不改变。
- 2026-10-07 重启后以正常 Report Service 执行新 Prompt@1.0.3 一次，Report 37 COMPLETED/current，provider=openai_compatible，model=doubao-seed-2-1-turbo-260628，23,621 ms，input 36,320 / output 1,034 / reasoning 0 tokens，repair 0。旧 Report 4 仍存在。
- Report 37 初始生成的 Proposal 5 为 PREPARATION_FOCUS、PENDING，引用 Evaluation 24：项目深挖时补充 RRF k 值与候选数量的匹配逻辑，不宣称稳定能力；生成时 memory_item_id=NULL。5 条本场表现观察保存，未写 CandidateProfile/UserSkill。
- 重启后的浏览器登录与自动化连接曾中断；用户恢复登录、Codex 重启后，应用进程退出造成 connection refused。用现有安全 stop/start 脚本恢复 API/Worker/Web，PostgreSQL/Redis/MinIO/Milvus 数据保留，SearXNG 未启动。未改凭据或绕过鉴权。
- 2026-10-07 登录恢复后通过正常报告页批准 Proposal 5，生成 Memory 33；仅一个 USER/CREATED 确认事件。新建同一真实用户 38 的华为验收 Session 63 / Snapshot 14，固定读取 Memory 33 原内容，近期 Observation 18/17/16 独立分层。旧 Snapshot 13 hash 不变且不含 Memory 33。
- Session 63 首答评价@1.0.4 成功（19,539 ms），Main@1.0.10 补问错误引用未配对事实 2/10，失败为 UNSUPPORTED_CANDIDATE_QUESTION，保留审计；未创建虚假有效 Evaluation、未推进。Prompt@1.0.11 仅明确补问继承父题来源，validator 不变，后续安全重试复用原评价。
- Session 63 安全重试成功：Main@1.0.11 为 10,172 ms，input 13,750 / output 272 / reasoning 0，repair 0；此前 Evaluation 仅调用一次，原 Answer 未重复生成，提交后有效 Evaluation 恰为 1 条。
- 第二场实际补问：你刚才提到 RRF 暂用 k=60、两路各取 Top50，具体的 k 与召回规模匹配逻辑是什么、如何考虑权重均衡？与新批准的准备重点一致，不把过去观察断言为长期能力。新 Snapshot 14 hash 为 `96c073d15d2aca1197b4d103fd7ad2e90084e8feca39f211f2fffeff7a6912bb`，运行/刷新后保持不变。
- 浏览器验证：报告统计与 Coach 分区、完整四部分参考回答、正常页面批准、成长记录可读、第二场历史记忆展开、具体 FOLLOW_UP、错误后的刷新/同一回答恢复、四岗位推荐页均通过。Session 63 有意停在真实可回答的补问，不冒充已完成第二场。
- 页面凭证继续 ignored：`output/playwright/product-polish-memory-approved.png`、`product-polish-memory-follow-up.png`、`product-polish-report-reference.png`。正常 LIVE 服务保留，供用户继续手测；未 commit/push。

Session 62 真实调用数值（全部 reasoning=0 / structured repair=0）：

| role / 阶段 | Prompt | latency ms | input tokens | output tokens | result |
| --- | --- | ---: | ---: | ---: | --- |
| Main 首题 | 1.0.9 | 9873 | 11145 | 514 | SUCCEEDED |
| Evaluation 首答 | 1.0.4 | 20715 | 12838 | 904 | SUCCEEDED |
| Main FOLLOW_UP | 1.0.9 | 11566 | 13409 | 387 | SUCCEEDED |
| Evaluation RRF | 1.0.4 | 23341 | 12787 | 1041 | SUCCEEDED |
| Main DEEP_DIVE 初次 | 1.0.9 | 9069 | 13628 | 375 | FAILED: QUESTION_SOURCE_POLICY |
| Main DEEP_DIVE 安全恢复 | 1.0.10 | 9240 | 13715 | 381 | SUCCEEDED / 未重评 |
| Evaluation 深挖 | 1.0.4 | 28804 | 12883 | 1268 | SUCCEEDED |
| Main NEXT | 1.0.10 | 7682 | 14024 | 331 | SUCCEEDED |
| Evaluation 最后题 | 1.0.4 | 10402 | 12308 | 561 | SUCCEEDED |

## 仍有边界

- 在线模型不是即时返回；完整参考回答会增加 Evaluation 输出，显示阶段与秒数，不伪装真实模型已经完成。
- 预算保留失败请求的真实使用量；不足时不能靠刷新重置，不会把未评价的 Answer 当成完成。
- 跨历史记录保留当时真实模型名称与失败事实，不把旧 DeepSeek audit 改写成 Doubao。
- Live Search 冻结；本轮不重新验证 Profile/Research 的全新真实输入。
- 岗位库仅有 4 个候选，coverage insufficient 如实显示。湖北移动博士资格等仍 UNKNOWN：当前冻结画像没有成熟的结构化学历硬条件证明，不能从自由文本或模型分析猜成满足，也没有为本次验收扩展 Profile/Matching。
- 已有历史记忆只表示用户批准的练习目标/偏好；本轮验收回答明确标记为测试技术方案，不能当成新增真实履历。

## 直接手测

- 推荐：`http://127.0.0.1:5173/jobs/matches/9`，选择华为后可通过正常按钮创建自己的新面试。
- 已完成面试报告：`http://127.0.0.1:5173/interview/reports/37`。
- 第二场记忆验收：`http://127.0.0.1:5173/interview?session_id=63`，当前停在 RRF 参数匹配补问。
- 成长记录：`http://127.0.0.1:5173/memory`。
