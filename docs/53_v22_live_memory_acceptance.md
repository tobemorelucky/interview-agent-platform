# V2.2 DeepSeek Live 记忆联动验收

日期：2026-10-24。结论：**两组 Replay 历史 + Live 决策验收通过**，不等于完整多场次 Live。

## 1. 授权、环境与实际范围

用户明确授权单批最多 6 逻辑请求、10 HTTP、60,000 Token、480 秒；不改功能、正式库、Prompt 或模型配置。

- 核对后仅启动原 PostgreSQL 和 Redis 容器。PostgreSQL ID `359a220e…`，postgres:16-alpine，原卷 `interview-agent-platform_pgdata`；Redis ID `e19d4887…`，redis:7-alpine，原 redisdata 卷。
- PostgreSQL 自身启动执行 WAL recovery 后 ready；未人工修复 WAL、初始化、migration 或创建容器。
- 正式库 `interview_agent` 仍为 0025；隔离库 `interview_agent_v21_v22_live_prepare_20261023_a` 为 0026。
- Session 4/9 原为 READY，started_at=null，指纹与准备时一致，模型和题目计数为零；不需替换，不重置预算。
- 新增验收脚本 `apps/api/scripts/accept_deepseek_v22.py`，没有修改 V2.2 业务实现。复用既有 HTTP 计量与正常 core.start/core.answer/Controller/配额流程，不新增 Planner。
- 输出文件不得覆盖；Windows 文件锁限制单批；外层 480 秒，逻辑/HTTP/Token 每次发送前检查，最多一次整批 repair，禁止 fallback。启动前执行了零网络的 Token 预算拒绝分支自检和语法检查。

当前使用已有 `openai_compatible / deepseek-v4-pro-ga-260813`，实际请求 thinking=disabled。所有 routes 检查符合配置，无修改 `.env`。

## 2. 冻结记忆确实进入实际请求

不是只检查预览：已保存并离线解析全部 6 次真实发送 messages。四次 Main 请求均含冻结 MemoryGuidedPlan 和 9 个合法组合；两次 Evaluation 均不含 memory_guided_plan，避免历史能力结论污染当前评分。

- A/user 1：两次独立有效 Replay 观察，Kafka VERIFIED/revision=2；items=[]，deprioritized_targets 包含 Kafka、RECENT_INDEPENDENT_VERIFICATION。Kafka 仍有合法组合，未被硬排除。
- B/user 2：两次正确、一次同条件相反 Replay 观察，Kafka CONFLICTED/revision=3；items 包含 Kafka、INDEPENDENT_VERIFICATION、needs_recheck=true，引用 evaluation:3/4/5。只读查验这些历史观察全部属于 user 2。
- 本轮未调整历史、Prompt、优先级或候选顺序来强迫某个输出。

## 3. 场景 A：避免机械重复已验证 Kafka

Session **4**，Main Prompt **2.2.0**。

### 开场 Live 决策

- strategy：NEXT。
- knowledge_id：`java_backend:redis`。
- candidate_id：`C_6bc52f55f257d8e051166770`。
- 来源：`fact:1`，对应真实 DEMO 简历证据“订单系统：使用 Python 和 Redis 实现缓存。”，不是伪造公司面经。
- 问题：“你在订单系统里用 Redis 做缓存，能具体说说一次查询请求进来时，缓存命中、未命中以及回源更新缓存的完整链路是怎样的吗？”

输入测试回答随实际 Redis 题目作答，描述缓存未命中读 MySQL、写回、TTL、更新删除，但承认未完善并发和失效保护；未强塞 Kafka 回答。

### Evaluation 与下一题

- 本场知识观察：PARTIALLY_DEMONSTRATED，引用该实际回答可定位的片段；评分 3/3/2/4/3。
- 下一策略 FOLLOW_UP，knowledge_id 仍为 Redis。
- candidate_id：`C_499c65b631cf4bba407d1779`，来源 `skill:java_backend@1.0.0`。
- 问题：“你提到更新时删除缓存，那如果两个更新请求几乎同时发生，一个删缓存、另一个写数据库，查询请求又刚好在中间回源，这时可能出现什么不一致？你会怎么避免？”
- Controller verdict=ACCEPTED，实际执行 FOLLOW_UP；非随机重复 Kafka。

## 4. 场景 B：冲突后主动独立诊断

Session **9**，Main Prompt **2.2.0**。

### 开场 Live 决策

- strategy：NEXT。
- knowledge_id：`java_backend:kafka_reliability`。
- candidate_id：`C_8ae2d54d32076407a25940b8`。
- 来源：`requirement:4`，隔离 DEMO JD 的 Kafka 消息可靠消费要求。
- 问题：“在 Kafka 消费场景中，如果业务处理成功但提交 offset 前进程崩溃，重启后会发生什么？你会如何设计消费逻辑来避免重复处理带来的问题？”

这直接检验冻结记忆里的 ACK/事务边界争议，而不是向用户宣告能力下降。

测试回答：“Kafka 消费失败可以重试，成功以后手动 ACK，保证消息不丢失。我还没有说明业务事务失败和重复投递的处理。”（有明确 DEMO Live 输入标记。）

### Evaluation 与下一题

- 本场知识观察 INSUFFICIENT_EVIDENCE，真实引用上述回答；评分 2/1/1/2/2。指出没有回答 offset 前崩溃与重复消费机制。
- 下一策略 FOLLOW_UP，knowledge_id=Kafka。
- candidate_id：`C_4ddd9173faea5c35d56180ef`，来源 `skill:java_backend@1.0.0`。
- 问题：“你提到成功以后手动 ACK，那如果业务处理已经成功、但还没来得及提交 offset 时进程崩溃，重启后会从哪个 offset 继续消费？这会导致什么结果？”
- Controller verdict=ACCEPTED，执行 FOLLOW_UP，引用本轮 answer:7 / evaluation_run:17 和合法题源。

开场与追问都围绕同一崩溃窗口，追问将未回答的问题缩小到恢复 offset 与后果；相关性成立，但不声称已证明长期避免同义重复的多轮能力。

## 5. 请求、耗时与账本

| 请求 | 场景 / route | HTTP | 输入 Token | 输出 Token | 总 Token | HTTP 墙钟 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | A / Main 开场 | 200 | 4751 | 205 | 4956 | 4.871s |
| 2 | A / Evaluation | 200 | 5416 | 514 | 5930 | 8.315s |
| 3 | A / Main 下一题 | 200 | 6377 | 234 | 6611 | 4.602s |
| 4 | B / Main 开场 | 200 | 4860 | 223 | 5083 | 4.296s |
| 5 | B / Evaluation | 200 | 5416 | 589 | 6005 | 9.031s |
| 6 | B / Main 下一题 | 200 | 6743 | 257 | 7000 | 4.633s |

合计 **6 逻辑 / 6 HTTP / 35,585 Token**（输入 33,563，输出 2,022），整批 **37.197 秒**。所有 finish_reason=stop、reasoning_tokens=0、repair=0。服务商返回 cached_tokens 合计 5,120，包含在输入统计中，未另行重复累计。

数据库 6 条 ModelAdmission 全部 SETTLED，actual=charged，合计等于 HTTP usage。无 RESERVED/UNKNOWN 残留，未发现计费异常；未读取服务商账户账单，未配置价格，不估算人民币或保证免费。

未发生 UNKNOWN_DIRECTOR_SOURCE、非法组合、Schema/Evidence 校验失败、超时或 fallback。全部结构化结果保存；未保存 reasoning 内容或 HTTP Authorization header。

## 6. 提交、幂等与历史保护

两场各 **2 Question / 1 COMPLETED Answer / 1 Evaluation**，状态均 IN_PROGRESS、state_version=3，active_question 分别 7/9；无遗留 answer_processing lease。

对每场同一 Answer 使用原幂等键再次提交，返回完全相同结果，未增加模型请求、有效 Answer/Evaluation 或题目。所有组合继续由原 resolver 与 Controller 验证，题目与目标/题源核对一致；未扩宽任何来源集合。

Session Snapshot 和旧历史 Session/Answer/Evaluation/Question 的内容指纹均保持一致。正式库全部 64 表前后 counts/指纹相同，仍为 0025。未触及旧 Session33。

两场尚未 COMPLETE，故长期能力聚合仍为 A VERIFIED/revision=2、B CONFLICTED/revision=3；本轮新观察已经保存到本场 Evaluation/KnowledgeState，但不假称已经完成长期冲突消解。UserMemoryItem/CandidateProfile 未被自动写入。

## 7. 结论与限制

实证支持：冻结记忆进入真实 Main 请求；A 在 Kafka 仍可选时转向 Redis，B 对 Kafka 冲突主动提出诊断题；二者均根据当前回答合法追问。两组下一策略都为 FOLLOW_UP，但目标、题源与内容不同，不能宣传成验证了所有策略分支。

这不是同一输入去掉记忆的随机对照实验，不能严格量化记忆的因果贡献或推荐准确率。Evaluation A 对保护机制的评价范围略宽于原始四条 expected_points，保留真实输出，不在本轮调整评分或 Prompt；该质量边界可供后续人工体验核对。

本轮未执行第二答、完整结束、Coach、能力重新聚合或新一场 Live，因此**不是完整多场 Live 训练闭环**。没有追加收费调用或借剩余额度继续测试。未重新跑全量业务测试，因为无业务代码更改；前述 510/43 与 Replay 结果仍是历史结果，不冒充本轮新增回归。

产物（ignored）：

- `.runtime/acceptance/deepseek_v22_live_20261024_attempt1.json`：实际 messages、结构化输出、usage、Controller 决策、提交结果与历史指纹。
- `.runtime/acceptance/v22_live_preflight_20261024_a.json`：0026 与待验收快照核对。
- `.runtime/acceptance/v22_live_formal_before_20261024_a.json` / `v22_live_formal_after_20261024_a.json`：正式库未变的只读证据。

原容器保留运行，未启动项目 API/Web 供交互体验。未正式迁移、未改配置、未 commit/push。完成有界验收后停止，不进入 V2.3。
