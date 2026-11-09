# Evaluation 引用数量与 Usage 异常可靠性收尾

2026-10-29。仅离线修复、Replay 与隔离 PostgreSQL；真实 DeepSeek/其他收费模型请求 **0**。不改评分阈值、Memory、模型配置、200,000 Token 上限、数据库定义或冻结 Prompt，不迁移、不提交、不推送。

## 1. E07 的真实根因

证据：docs/65、66；本地 ignored `.runtime/acceptance/v232_live_20261028_a.json` 的 E07_old / E07_new 原始业务 JSON、实际 request_messages；旧验收库 `interview_agent_v232_calibration_20261028_a`。

`adaptive.KnowledgeObservation.answer_quotes` 自提交 `1404f248` 起为 `Field(max_length=3)`，每条 ShortText 最多400字符。`resolve_observations()` 要求引用是本轮 Answer 的非空连续原文，正向状态必须有引用，旧 gap 必须真实。这是有界、可定位的回答证据摘要，不是要求逐项摘抄全部覆盖点。没有找到“恰好3条是语义最优”的实验记录，不作此声称；本例也没有证明必须放宽上限。

2.3.1、2.3.2 Evaluation System 都要求短的精确连续引文，**没有显式写最多3条**，但请求 JSON Schema 明示 maxItems=3。两版使用同一 AdaptiveEvaluation、同一 resolve_observations 字面/目标/gap 校验；2.3.2 只额外有父问答语境。不是新版另设了更严的引用上限。

新版把同一回答拆为4个片段：

1. 用订单ID唯一约束并在同一事务写订单及去重记录
2. 提交成功再ACK
3. 提交后ACK前崩溃则重复投递，由唯一约束阻止重复落库
4. 提交前失败回滚重试

四条都能字面定位，没有未知知识点或 gap；唯一 Pydantic 错误是 `knowledge_observations.0.answer_quotes / too_long / actual_count=4 / limit=3`。1和2是同一事务时序描述的细分，不需要每个子句各占一条。但程序不能代替模型随意丢末项，更不能将1、2合并成一个并未在原输出中出现的新 quote。

旧版3条中的第一条合并了事务与ACK；另外两条覆盖失败窗口。两版 **只有五维分数均4/4/4/5/4且观察状态均PARTIALLY_DEMONSTRATED**，完整 JSON 不同：evaluation、covered/missing、follow_up_focus、missing_mechanisms 和引用拆分均不同。

原本只有通用修复：精确保留所有字符串及路径。移除任何一条 quote 都违反该契约；即便放行旧通用 Repair，也没有合法的“选子集”通道。上次实际还因验收器遇非法结构即停止，抛出 BudgetStop，所以实际 Repair HTTP=0，不能声称上次模型 Repair 已试过但无效。

## 2. 本次最小改动与机制

| 文件（均在 apps/api/ 下，除本文） | 核心入口 / 修改 |
|---|---|
| src/interview_api/modules/interview/evaluation_repair.py（新增） | EvaluationQuotesRepair.prepare / validate |
| src/interview_api/infrastructure/models/structured.py | generate_structured、_validation_errors、_invocation_usage |
| src/interview_api/modules/interview/agents.py | InterviewAgent.call / failure_usage / interrupted，AgentResult.usage_complete |
| src/interview_api/modules/public_career/research/audit.py | ResearchModelCalls.call |
| src/interview_api/infrastructure/models/provider.py | CompatibleModelProvider._generate |
| src/interview_api/infrastructure/models/governance.py | governed_call |
| src/interview_api/modules/interview/core_service.py | MockInterviewService.call_agent |
| src/interview_api/modules/interview/controller.py | settle_model_call，仅结算参数/净额，不改状态机 |
| tests/test_evaluation_quote_reliability.py（新增） | 真实失败 Replay、公共结构生成、service/Controller/PG/HTTP配额边界 |

只有 AdaptiveEvaluation 显式选择 `EVALUATION_QUOTE_SUBSET_V1`，不影响 V1 普通评价或 Main 选择契约。prepare 必须满足：

- 只有一个错误，且是本题唯一 observation 的 answer_quotes 数组过长。
- 每条原 quote 自身符合 ShortText，能在当前原始 Answer 中定位。
- 原知识目标、gap、观察状态等业务约束合法。内部只对“唯一数组过长”对象作不持久化的业务探测；它不会成为正式评价。

只允许一次 Repair。独立的修复指令明确 max3、只能选择原 selectable_quotes 的必要子集，不得合并、改写、新增；不是程序截断。Repair仍走相同 provider、结构生成、准入与逐HTTP账本。

修复后重新校验完整 Schema；用确定性 JSON 比较排除 quote 数组以外的**全部字段**，包括五维数值/类型、理由、状态、gap、建议、参考答案；拒绝4→4.0等隐式类型替换。子集必须非空、无重复、全来自原集合，再次执行真实目标/gap/原文校验。最终 Controller 仍按完整服务端状态提交。

审计只新增 policy、original_count、retained_count，不保存整份模型响应或新的敏感回答原文。原 Main SupportingRefsRepair 规则不变。为了保持本轮对照和旧 Session，**本轮不改生产/冻结 Prompt**；数量约束在原 Schema 和本次专用 Repair 中明确。正常生成仍可能越界，故不能承诺以后永不需要 Repair。

## 3. Usage 丢失位置及结算规则

实际旧链路：首个HTTP已成功并有3279 Token → Schema失败 → 验收器在Repair前抛普通BudgetStop → generate_structured只给ModelProviderError附着累计usage，普通异常裸穿 → StepRun存{} → 会话把19806预留当未知消耗。逐HTTP ModelAdmission当时已正确结算3279；不是服务商多收19806。

修复：

1. structured按每个 invocation 汇总可验证的非负整数 Usage，成功、Schema失败、业务拒绝、修复失败、普通异常及取消都携带累计值；保留每一次调用，不用最后一次覆盖前一次。`usage_complete`独立表示是否仍有未知请求。
2. provider在解析choices之前提取usage；HTTP200但choices为空等响应封装错误也保留可验证usage。坏的可选reasoning详情不会抹掉已取得的total。
3. governance给明确的准入/发送前拒绝标记request_sent=False；发送后失败不能冒称没发送。既有每HTTP准入和一次性settle保持不变。
4. ResearchModelCalls对取消也在shield中保存StepRun usage、完整性、调用轨迹；Agent从持久审计回传，超时包装不丢它。若没有持久审计，不能凭内存中的usage释放会话预算。
5. 会话已知部分由StepRun计数；全部已知则释放临时预留。有未知请求时保留 `max(预留−已持久化已知tokens, 0)`，避免已知部分既算actual又算整笔unknown。原call_key取出后重复settle不会再次记账。配额账本仍逐HTTPsettle，不用会话净额回改用户账本。

例：首次100，Repair成功100但仍非法 → StepRun真实200，无未知预留；首次100，Repair发送后结果未知 → StepRun保留100，剩余预留继续保守计数；首次100，Repair明确发出前拒绝 → 累计100且无额外未知数。**普通异常无发送证据时仍UNKNOWN**，绝不因有一次已知响应就假设后续失败免费。

原E07 BudgetStop没有可信request_sent标记；不做历史推断或回填。进程硬退出、断电、数据库持续不可用无法保证Python finally执行，仍依赖既有持久预留/lease/UNKNOWN保护。本轮验证了协作取消、超时、过期预留、HTTP失败；没有强杀进程或模拟真实断电，不把它说成精确恢复全部未知账单。

## 4. 相同失败样本与离线验收

保留原E07完整业务JSON及原请求做固定Replay：

- 旧默认字符串保留契约＋同一子集输出：拒绝，因为删除原引用违反通用修复。
- 新显式子集契约＋相同输入输出：通过，一次Repair，4→3；除quote选择外字段完全相同。
- 非法引用、超长quote项、未知目标/gap、额外结构、改评分/状态/理由、合并quote、修复仍4条：拒绝，不靠截断通过。

这只证明来源/字段契约与事务可靠，不证明模型会选择最优引用或评分准确。原始Live文件继续ignored，测试在本机额外读取它；仓库内还包含不依赖该文件的可移植固定4条示例。无原始文件的环境会明确跳过本地证据复核单项，不冒充已读真实样本。

本次曾发现并修复一个真实回归：Research取消审计先标FAILED/CancelledError，Agent整体超时收尾原来只更新RUNNING，导致Step错误名与Run的model_timeout不同。现在仅允许该取消占位错误改成实际超时/请求取消分类，保留usage，不改其他业务失败或成功记录。

### 最终重新运行结果

- 核心/Director/投影/校准/预算/Provider/Research/系统设计/Skill/Matching/面经链路：**229 passed / 0 failed / 0 skipped，80.03秒**。
- M4、Report、Memory审批、跨场闭环、能力规则与集成、V2.3训练、V1.3治理：**106 passed / 0 failed / 0 skipped，82.93秒**。
- 两组不重叠，共**335 passed / 0 failed / 0 skipped**，不是全仓所有测试。迁移测试未执行，本轮没有迁移授权；不是把未执行项计作通过。
- 最后给通用数量诊断增加union虚拟路径的防御性处理，防止诊断本身KeyError遮蔽usage；随后本次35项引用/用量、Main修复及原Provider/Evidence测试合并重新跑 **71 passed / 0 failed / 0 skipped，12.17秒**，属于上述集合的重跑，不累加为406。完整原E07旧默认契约拒绝/新子集契约通过也在此最终重跑中。
- 前端 `node --test apps/web/tests/*.test.mjs`：**43 passed / 0 failed / 0 skipped**。这是既有Node测试，未执行真实浏览器或新的Live面试，不混淆。
- git diff --check通过；无付费模型请求、无正式migration。

TDD最初18项真实RED：缺少新policy、普通异常/取消不带usage；增加实现后通过。扩大回归一度111通过/1失败，暴露上述超时错误分类，修复后112项通过。后续增加严格类型/quote项/HTTP账本测试并纳入最终229。

### 数据库环境与失败如实记录

重启后原PG/Redis已停止；只读确认原完整ID、镜像、原pgdata/redisdata挂载及日志后仅启动两个原容器。没有Compose、重建、prune或VHDX操作。

最初复用 `interview_agent_v231_context_20261026_a`。一组扩大回归88通过/23失败，均在Matching fixture生成排除公司列表时触发max1000：旧测试库长期累积1078+公司，不是本次Matching业务回归。**没有放宽上限、删断言或清库**。

随后新建空的 `interview_agent_v232_reliability_20261029_a`，用原测试库pg_dump/psql复制现有0026 schema定义及alembic_version元数据，不迁移、不改定义、不复制业务历史；旧库完整保留。正式库和历史Live库只读；测试子进程DATABASE_URL和M1_TEST_DATABASE_URL均指向新库，不改.env。

命令类别：Docker inspect/logs/start/pg_isready/Redis ping；专用空测试库CREATE DATABASE；pg_dump --schema-only及单独alembic_version数据复制；现有venv的pytest（每个子命令170秒截止保护）；Node测试；只读SQL/hash。一次嵌套Shell只读数据库名查询因引号语法失败，未执行修改；改用参数化只读检查后新建空库。

最终回归前后正式库64表/1699行SHA256均为 `cf4faece3c47468fb8cd9aa05427477f619450042a7f4516788f5e44fc40fcaa`，仍0025。历史验收库66表/231行SHA256均为 `c5a3432fa87fd46ac560145460b8501ad8027ae67959dcad66c8cd63c1ae651a`，仍0026。按表名排序、逐行row_to_json排序后摘要，不包含系统表/WAL/序列，不宣称整个物理库逐字节未变。

历史E07仍Session6 IN_PROGRESS/v4/active8，Answer8 FAILED、当前正式Evaluation0、unreported19806；历史8条配额actual=charged=28642。未改成成功，未调整历史保守计数。

## 5. E03 状态差异：证实、推测与不可判定

同一问题考“为什么不能直接相加原始分数、无依据怎么办”。当前回答继续直接相加、让模型补齐并造引用，末尾承认没考虑量纲、融合和拒答。父答也是同类错误；没有正确机制或新解决方案。

| 项目 | 2.3.1 | 2.3.2 |
|---|---|---|
| 五维 | 2/2/2/4/3 | 2/2/2/4/3 |
| 状态 | NEEDS_CLARIFICATION | PARTIALLY_DEMONSTRATED |
| 核心理由 | 指出错误做法，没有解释正确机制 | 明说“几乎没有给出正确机制”“缺少可执行的正确方案”，把说出概念名计作部分展示 |
| 错误分类 | 直接相加、无依据生成两个明确错误 | 多列为“未说明/未给出”的遗漏 |
| resolved_gap_ids | [] | [] |
| 父题语境 | 不提供 | 父题/答可见，未截断，assistance UNKNOWN |

**证实**：两版都没有解决既有gap；当前问题要求未真正证明；引用是真的不等于其陈述正确。父答未提供新的正确机制，不能作为本轮进步证据。较低技术分与尚有gap可以同时存在，但并不能自动决定唯一状态。

**合理风险判断**：新版理由与PARTIALLY_DEMONSTRATED之间偏宽，可能把“意识到自己没考虑”当作已展示部分机制；旧状态更符合本轮已认可的暂定边界。相关性4/表达3不应为消除状态差异而压低。

**不能证明**：只有一次配对，且父语境和Prompt说明同时变化，不能确定是父答导致还是模型生成波动；不能声称旧评分就是Gold。本轮不把状态绑定总分，不改阈值或强制改正式评价。12项仍DRAFT_UNREVIEWED。

## 6. 共同参考答案的待人工修订点

定位原证据http数组E12_old、新版E12_new的具体字段：

- 旧 `reference_answer.answer_framework[2]` 声称事务性发件箱/本地消息表实现业务与位点提交原子性；旧 `interview_answer` 把“后台发送并提交位点”混进消费恢复，生产者与消费者边界不清。
- 新 `missing_points[1]` 明说本地事务/发件箱保证业务与位点提交原子性；新 `answer_framework[2]` 的“一致性”仍含混，正文后半却正确说DB已提交而位点未提交仍会重投，前后需要澄清。
- 旧 `missing_points[1]` 和双方 follow_up_focus 不应把“必须提供跨系统原子事务”作为本题唯一正确解；成功处理后提交offset＋业务幂等是有效的至少一次方案，不自动等于端到端exactly-once。

建议人工确认后采用的事实边界：

1. DB业务写入与去重记录可在同一本地事务内原子完成；之后提交Kafka消费offset，中间仍可能崩溃并重投，需幂等应对。并发批量消费还须避免提交越过未完成消息。这不是自动跨系统原子提交。[Kafka设计说明](https://kafka.apache.org/41/design/design/#message-delivery-semantics)
2. Kafka内事务可把输出topics与消费offset一起提交；写外部系统还需与该系统协调，不能把任意DB事务直接纳入Kafka事务。[Kafka设计说明](https://kafka.apache.org/41/design/design/#message-delivery-semantics)
3. Outbox主要解决业务状态与待发布事件在本地事务中的一致性，再可靠转发；它本身不自动原子提交外部DB与Kafka消费组offset。[Debezium Outbox说明](https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html)

这些是修订建议，不回写历史模型输出，不自动升级为人工金标准。E07双方追加的“精确ACK顺序/重试幂等缺失”也需人工核对：当前答已经讲到这些机制，不能无限追加细枝末节来否认既有回答；同时仍不能由高分或UNKNOWN提示认定独立验证。

## 7. 下一步仅 E07 Live 冒烟（本轮未执行）

离线程序可靠性前置已通过，**可以准备一次E07专用短程Live**；尚未证明真实模型修复成功或长面试质量稳定。

建议另行授权：**1次逻辑Evaluation、最多2次HTTP（含唯一Repair）、最多30000 Token、最多3分钟**。使用新隔离数据、同一真实有效父子记录/冻结2.3.2；不重置旧Session，不跑四组A/B，不调模型输出强迫产生4条。每次发送前以实际完整wire及输出上限保守预留，超预算则停止。

若首次输出≤3且全部业务通过，记录“未自然触发Live Repair”，不得伪称已验证；若自然出现唯一合法quote数量越界，才允许一次子集Repair并记录字段不变、实际usage、Controller与账本。其他非法结构/引用或Repair失败立即停止。预期仍是数千到一万量级实际Token，但不是额度承诺，硬限优先。

未新增Agent或评测框架；本轮完成即停止，不继承任何历史收费授权，也不自动开始15–20轮。
