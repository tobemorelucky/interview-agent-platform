# V2.2 长期能力记忆与自适应面试联动

本轮只实现规则驱动、可解释的最小闭环。**没有新增 MemoryAgent，没有收费模型调用，没有迁移正式库。**

## 1. 真实复用点与调用关系

源码中原有的是 MemoryService、MemoryProposalService 和 MemoryContextBuilder，并非独立 MemoryAgent。

原有流程保留：用户确认的 GOAL / PREFERENCE / EXPERIENCE 等 → MemoryProposal approve/correct/reject → UserMemoryItem → 创建时冻结 approved memory。CandidateProfile / UserSkill 的稳定事实不由本功能修改。

新增链路：

```text
Answer → AnswerEvaluationAgent → Controller 验证的 KnowledgeObservation
       → Evaluation + 会话知识状态一起提交
       → Session COMPLETED（包括最后一题跳过）
       → CapabilityMemoryService 在同一事务重算

创建新 Session
  → 锁定用户的能力聚合命名空间、重算有效历史
  → 根据近期独立验证降低重复主题的 Blueprint 候选优先级
  → 冻结 Blueprint 与 MemoryGuidedPlan
  → Main 在合法 candidate_id 中自主选择
  → Controller 仍校验版本、题源、预算和状态
```

历史 Session 的 Prompt、Snapshot 和决策契约不自动升级。新自适应场次的 Main Prompt 是 **2.2.0**；沿用 `TARGET_SOURCE_PAIR_V1`，Evaluation Prompt 仍为 2.1.0。保留 Main 2.1.0 / 2.1.1 定义和执行测试。

## 2. 数据结构和事务

新增 Alembic **0026**，前置 0025；不修改历史 migration。

| 表 | 职责 |
| --- | --- |
| capability_memories | user_id + canonical topic 唯一的当前投影、revision、排除的 Evaluation IDs、用户更正说明及 sticky revoked 标记 |
| capability_memory_versions | 每次真正变化的旧状态、新投影、规则版本、来源引用、冲突/复测任务和 actor；唯一 memory_id + revision |

冲突和待验证任务使用版本化 JSON，不另建通用任务引擎或聊天历史副本。原始事实仍是 Answer / Evaluation / 已解析观察。保存的是来源 ID、时间、场景指纹、缺失机制等诊断，而非复制简历和聊天正文。

`pg_advisory_xact_lock(2202, user_id)` 仅串行化同一用户的能力重算/更正。获取锁后再读取有效来源；无内部 commit，完成事务由现有 Controller / service 管理。原有 Session 锁、乐观版本、幂等键和预算账本不替换。

聚合输入指纹不包含“本次重算时间”；相同有效观察、规则结果和用户控制不会追加版本。时间跨过复测阈值才产生新状态。报告 regenerate 不构造新能力观察。

新增 API：

- `GET /api/v1/memory/capabilities`：本人状态、观察摘要及最近 20 个版本。GET 的时效展示不偷偷写库。
- `PATCH /api/v1/memory/capabilities/{id}`：expected_revision、excluded_evaluation_ids、note、revoke。更正排除争议观察，不允许用户或模型直接设置 VERIFIED。

越权返回 404；同一 revision 并发更正一条成功、另一条 409，不重复累加。客户端刷新后重试。撤销为持久 tombstone，不因重新生成报告、重算或新 Session 被复活；当前版本没有“自动恢复撤销”入口。

## 3. 有效观察与状态规则

只有本人的 COMPLETED Session、COMPLETED Answer 和 Controller 已解析且确实提交的 Evaluation 可参与。核对 session/question/answer/message 的关联、Snapshot 所有权、知识目标在冻结 Blueprint 中，以及引用片段确为当前回答连续原文。失败/未完成会话、未知知识点、伪造关联不能参与。

目前按现有 Director 规范技术主题聚合（例如 kafka_reliability / agent），不是任意自由文本技能认证。没有足够观察时为 UNKNOWN，不表示用户不会。

| 状态 | 判定要点 |
| --- | --- |
| GAP_SUSPECTED | 近期回答缺少机制/支持，不由五维均分换算 |
| PRACTICING | 部分支持、提示/追问后补齐，或只有一场独立支持 |
| VERIFIED | 至少两场独立的、具有有效回答引用且无未解决缺口的支持；当前不依赖提示，且无开放冲突 |
| RECHECK_DUE | 已有验证，但距上次有效验证超过可配置时效 |
| CONFLICTED | 相同可比较语境出现独立相反表现，或“充分支持”诊断同时保留事实错误等争议 |

FOLLOW_UP / DEEP_DIVE 或 UI 明确提供 guidance 均保守视为有辅助，不将其算作独立掌握。并不因为“严格”风格就扣分。

## 4. 遗忘、场景和冲突

`capability_rules.DEFAULT_POLICY` 是规则配置入口，service/project/plan 均支持注入 policy；测试支持固定 clock，不等待真实时间。

- 同一语境的验证默认 30 天后建议复测。
- 至少两个语境的独立支持默认 60 天。
- 至少三场且跨语境支持默认 90 天。
- 超过 180 天的非岗位重点历史不进入规划；当前 JD 对应的重点目标可保留。
- 不降低任何历史分数、不删除错误、不衰减用户确认的稳定事实。

“相同条件”采用岗位、轮次、难度、真实题源、规范化空白后的题干和评分要点指纹。不同问题/要点只保留场景差异，不用文本相似度冒充等价条件。因此可能漏掉语义相同但不同措辞的冲突，选择保守暂缓判断，而非误报冲突。

先错后对可以体现进步；提示后正确不提升为独立验证；独立正确后同条件独立错误形成冲突。两场后续同条件独立支持可以解决冲突，原冲突记录保留为 RESOLVED_BY_INDEPENDENT_RECHECK。并不宣称程序能独立判断评价模型的技术结论必然正确；争议可由本人排除或安排复测。

GET 依据当前时钟展示 RECHECK_DUE，但不改旧版本。下一次新建/完成 Session 重算时记录实际状态变化；旧 Snapshot 永远使用冻结时结果。

## 5. MemoryGuidedPlan 与 Director 的契约

创建新自适应 Session 时最多提供 3 个相关复测候选、候选内容最多 6000 字符。仅来自当前 Blueprint 目标，携带 knowledge_id、状态、历史缺口摘要、独立验证意图、优先级、memory_revision 和真实 Evaluation 引用。

近期 VERIFIED 降低新 Blueprint 选题优先级；如果仍在候选范围，`deprioritized_targets` 提醒 Main 优先考察其他未覆盖技术点。它不是强制动作或新的题源权限，特定岗位需要时仍允许复核。

Main 继续自主选择 FOLLOW_UP / REQUEST_EXAMPLE / DEEP_DIVE / NEXT 和已有合法组合；没有新增每轮规划或 Memory 模型调用。历史观察引用**不能**加入允许题源、伪装公共面经或替换 candidate_id 对应来源。`UNKNOWN_DIRECTOR_SOURCE` 及 pair/version 校验保留。

V2.2 的 Evaluation 模型输入移除能力规划、历史 approved memory 和近期表现观察，避免历史弱点污染本轮独立评分；旧版请求保持兼容。V2.2 Main 仅使用新的能力规划层，不接收旧的 recent_performance_observations；旧表现也不再偷偷影响 V2.2 Blueprint 权重，防止撤销后由旧层重新流入。

审批后的个人目标/偏好仍可按原规则进入 Main，不能作为学历或技能硬事实。本功能的“撤销推断”不等于删除一条独立的、本人明确批准的偏好；后者在原记忆列表管理。

## 6. 前端

成长记录新增“能力记忆”：中文等级、上次验证、独立支持场次、历史变化、冲突、复测原因、相关面试链接及更正/撤销。

同一天多条观察使用序号 + 时间 + 表现描述区分，避免误选；原始内部 ID 不作为默认文案。参考、历史版本放折叠区。面试页“本场使用的历史记忆”展示冻结的能力复测参考及原因，明确系统推断不等于本人确认事实。

## 7. 实际验收记录

所有新增场次均在 `interview_agent_v21_v22_*` 隔离库。收费 DeepSeek / Search / Fetch 请求均为 **0**。Replay 测试证明确定性规则、上下文与业务接线，不证明真实模型的规划质量。

开发验收库 `interview_agent_v21_v22_browser_20261023_a` 的浏览器记录：

| Session | 操作和结果 |
| --- | --- |
| 2 | Kafka 回答只有 ACK/重试 → GAP_SUSPECTED |
| 3 | 初答仍有缺口 → 针对 ACK/事务 DEEP_DIVE → 补齐唯一键/事务 → PRACTICING，独立支持 0 |
| 4 | 项目深挖轮独立回答 → PRACTICING，独立支持 1 |
| 5 | 新的支付回调场景：“提交成功但未 ACK 就宕机，如何避免重复扣款？”→ 第二场独立支持 → VERIFIED |
| 6 | 与 Session4 相同题目/条件的反向表现 → CONFLICTED，未覆盖历史 |
| 更正/撤销 | 页面排除第六条观察恢复验证；撤销整项后刷新仍撤销。旧 Session5 保留 revision4 的规划，Session7 新规划为空 |

只读核对：Session2–6 的 Answer/Evaluation 数分别为 1/1、2/2、1/1、1/1、1/1；没有重复有效记录。Session5 报告真实通过 API 生成，Replay Coach 不重新算分；原 MemoryProposal 审批模块未改动。

截图位于 ignored 的 `output/playwright/v22-*.png`，包含复测题、验证、冲突、更正和撤销。开发过程中受控重启测试 API 时有短暂网络错误，浏览器刷新后恢复；没有掩盖为模型超时。

最终规则另以全新浏览器库复验，具体最终测试数量和最终库结果见下方补充记录。

## 8. 修改文件

新增：

- `apps/api/alembic/versions/0026_capability_memory.py`
- `apps/api/src/interview_api/modules/memory/capability_models.py`
- `apps/api/src/interview_api/modules/memory/capability_rules.py`
- `apps/api/src/interview_api/modules/memory/capability_service.py`
- `apps/api/tests/test_09_v22_migration.py`
- `apps/api/tests/test_v22_capability_rules.py`
- `apps/api/tests/test_v22_capability_integration.py`
- `apps/api/tests/v22_support.py`
- `apps/web/src/components/CapabilityMemoryPanel.vue`
- `apps/web/src/types/capability-memory.ts`
- `apps/web/tests/capability-memory.test.mjs`
- 本文档。

增量修改：统一 models 注册；memory/router；interview/context、controller、adaptive、adaptive_prompts、skill_engine、skill_registry；Memory API 客户端、MemoryPage、InterviewChatPage；旧 Prompt 兼容测试增加 2.2.0 分支；现有 Replay runtime 增加 `--memory-replay`（默认行为不改）；文档索引。

未更改 `.env`、模型路由、Agent 数量、Profile Evidence Selection、Research/公共发布链路或原审批服务。保留先前全部 tracked/untracked 工作；未 commit/push。

## 9. 测试方式和启动

测试数据库必须使用新名称，不覆盖旧库：

```powershell
$env:REDIS_URL = 'redis://localhost:6379/15'
uv run --project apps/api --no-sync python scripts/run-v21-tests.py --database interview_agent_v21_v22_your_unique_name --prepare tests/test_v22_capability_rules.py tests/test_v22_capability_integration.py
```

迁移往返测试仅在新建空隔离库执行 `tests/test_09_v22_migration.py`，不要对已有业务或验收资料库降级。

保留的最终浏览器 DEMO 环境（不是正式库、不是 DeepSeek）：

```powershell
$env:REDIS_URL = 'redis://localhost:6379/15'
uv run --project apps/api --no-sync python apps/api/scripts/v21_acceptance_runtime.py --database interview_agent_v21_v22_browser_final_20261023_a --memory-replay
# 另一个终端
node scripts/v21_acceptance_web.mjs
```

Web `http://127.0.0.1:5177`，测试 API 8007。DEMO 本地账号 `v21.demo@example.com` / `DEMO-v21-local-only`，不是真实用户凭证。该账号仅属于隔离库。不要对已准备的库再次运行 `--prepare`。

## 10. 交付边界与风险

- 正式 `interview_agent` 仍为 **0025**，只读核对 users5 / resumes38 / profile versions1 / sessions19 / answers45 / evaluations41。0026 只在隔离库 upgrade/downgrade，正式发布前仍需另行安排备份及正式迁移；不能直接把当前正式库宣称已启用 V2.2。
- 自动聚合只覆盖带有效知识诊断的自适应完成场次；旧普通面试可读但不会猜测补出知识点。
- 独立与提示依赖采用保守规则，不是科学能力测量；语义等价条件识别、真实模型是否遵循复测建议仍未 Live 验证。
- 规则没有额外模型请求；代价为新建/完成场次时读取该用户的有效观察、确定性重算及变更时写版本。未做生产规模吞吐/延迟压测；历史很长时可在保持可重算前提下优化投影，不在本轮新建任务平台。
- 复测任务是当前投影内的建议，不是自动后台训练或定时模型任务。没有复杂学习算法、Embedding 或第二个 LLM validator。
- 用户更正可排除观察、保留说明，撤销不可被自动复活；目前未开发撤销恢复及人工直接指定能力等级。
- 自动推断不创建 UserMemoryItem，不改变稳定事实；原 approved memory 的审批和撤销语义继续由原模块负责。

V1.4.2 原面经发布失败记录仍保留，本轮没有将它标记为成功。V2.1.1 的六请求 Live 成功也不被改写为 V2.2 Live 成功。

## 11. 最终冻结代码的实测结果（2026-10-23）

### 后端与数据库

在全新 `interview_agent_v21_v22_final_20261023_a` 分两组执行，单组命令均小于三分钟：

- 前 25 个测试文件：**135 passed，0 failed，0 skipped，99.75 秒**。包含 0018–0026 迁移链、0026 升降级和前半组旧功能。
- 其余全部测试文件：**375 passed，0 failed，0 skipped，108.82 秒**。
- 合计 **510 passed / 0 failed / 0 skipped**，不是重复相加定向重跑；之前 494 项覆盖保留，新增 16 项。定向回归曾独立通过 50 项，不再计入合计。
- 只读复用保留的 V1.4.2 真实快照测试，因此未把缺少快照或缺少 PostgreSQL 的测试记为通过/跳过。

覆盖：真实 SQL 事务、并发刷新幂等、版本冲突、跨用户 404、争议观察排除、sticky revoke、时钟 1/30/90 天、历史过时检索优先级、冲突与提示依赖、两场后续独立复测解决冲突、报告 regenerate 不累计、投影写入失败整体回滚、同 Answer 安全重试、最后跳题完成仍聚合、旧 Prompt 三版本以及 V1.2/V1.3/V1.4/Skill 回归。

修复期间真实发现并修复：

1. 初版评分隔离误影响旧 M5 输入契约，限定到 V2.2，不删除旧断言。
2. 同一天观察只有日期难以区分，改为序号/时间/状态并提供原场次链接。
3. 完成聚合最初只挂在回答结束，集中到 Controller.complete，覆盖最后跳题。
4. 撤销后的旧 report observation 可能绕过新推断层重新流入，V2.2 Main/Blueprint 不再使用该旧层。
5. 移出复测清单不等于避免重复选题，加入冻结前降优先级与 Main 的 bounded deprioritized_targets。

### 最终真实浏览器 + API + Replay

使用**全新** `interview_agent_v21_v22_browser_final_20261023_a`，重新真实登录、创建、答题、SSE 接收、刷新、读记忆及生成报告，没有拦截 API 或静态 DEMO 页面：

| Session | 已验证结果 |
| --- | --- |
| 2 | 1 Answer / 1 Evaluation，GAP_SUSPECTED |
| 3 | 2 Answer / 2 Evaluation，ACK/事务针对性 DEEP_DIVE；中途刷新恢复；辅助后 PRACTICING，独立支持 0 |
| 4 | 根据冻结记忆选择 Kafka 合法组合，在支付回调宕机语境独立验证；PRACTICING，独立支持 1 |
| 5 | 再次独立验证后 VERIFIED；报告 COMPLETED；报告生成前后能力 revision 均为 4，没有重复累计 |
| 6 | 近期已验证后，新场实际切到 `java_backend:redis` / 缓存工程，而不是继续机械问 Kafka；保留在 Q1，0 Answer / 0 Evaluation，便于体验 |

所有题目均保留合法 `C_*` 组合和真实冻结 Skill/JD 引用；Main 仍选择组合，不新增 Planner 调用。该最终库审计步骤全部 provider=replay（含 fixture Profile 和报告），无 DeepSeek 请求。Replay 提供的是固定的机制/场景输出，不能据此宣称 Live 模型已学会跨场次规划。

最终截图：`output/playwright/v22-final-01-gap.png`、`02-assisted`、`03-memory-question`、`04-verified`、`05-topic-switch`、`06-report`（后五个同样带 `v22-final-` 前缀及 `.png` 后缀）。截图、Playwright 快照和本地日志 ignored，不作为真实用户资料提交。

前端 **43/43**，0 failed/0 skipped；Vue TypeScript 检查、production build、Python compile、`git diff --check` 通过。Windows 行尾提示不是 whitespace 失败；未修改历史 migration，未全局关闭空白检查。

### 亲自体验

当前保留的只是隔离 Replay 服务（8007/5177），不是正式库的新功能上线：

1. 打开 `http://127.0.0.1:5177/memory`，查看消息可靠性的验证和展开历史。
2. 打开 `http://127.0.0.1:5177/interview?session_id=4`，展开“本场使用的历史记忆”，对照实际支付回调复测题。
3. 打开 `http://127.0.0.1:5177/interview/reports?session_id=5`，查看已保存评价统计和报告。
4. 打开 `http://127.0.0.1:5177/interview?session_id=6`，看到已切换到缓存工程，可继续 DEMO 作答。
5. 在成长记录更正/撤销后再创建新场，对比新旧快照。撤销不可自动复活，若希望保留当前展示状态，可先只展开查看。

浏览器中的 Coach fixture 不产生新提案；approve/correct/reject 的完整不回归由 PostgreSQL 原 M5/两场 Replay 测试覆盖，未把本轮浏览器“无提案”宣称为又一次审批验收。

额外模型调用 **0**；本地低数据量下曾观察到 GET capabilities 8ms，但不构成生产延迟或吞吐承诺。当前全量重算在冲突比较时有二次扫描，版本快照存储随历史增长；尚未进行大规模压测或增量物化优化。
