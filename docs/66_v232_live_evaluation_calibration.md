# V2.3.2 Live Evaluation 父问答语境配对验收

日期：2026-10-28。**唯一收费批次已停止，不追加第二批。**未修改业务代码、评分规则、Controller、Memory、模型路由、密钥或历史Live Session。仅新增验收脚本及本文。

## 1. 总结与停止原因

- 实际8逻辑/8HTTP，全部DeepSeek deepseek-v4-pro-ga-260813，thinking=disabled、temperature=0、max_tokens=3000、单请求配置180秒；无fallback。
- 实际input **23030**、output **5612**、total **28642 Token**，reasoning与缓存Token均0；8个HTTP均200，finish_reason均stop。
- 批次墙钟87.707秒；8个HTTP响应耗时合计86.195秒。授权8逻辑/10HTTP/160000 Token/720秒，未超限。
- E03/E12/E02三组完成两版业务校验、新版Controller提交和幂等核对。
- E07旧版通过业务校验；新版HTTP200但返回4条answer_quotes，超过Schema最大3条。**新版输出不构成正式Evaluation**，未提交当前回答、未推进会话。
- 按“非法结构即停止”，验收器阻止后续Repair，实际Repair HTTP为0。不能将本轮记为四组完整通过或完整长面试通过。

结论：未见五维评分普遍退化，但E03的状态仍偏宽，E07有结构输出失败，参考答案也暴露双方共有的技术质量问题。**尚不具备直接宣称可稳定完成15–20轮的条件。**父语境有可核对的上下文收益，不能据四组单次对照证明评分准确性提高。

## 2. 实验及真实来源记录

新隔离库：interview_agent_v232_calibration_20261028_a，Alembic0026。正式库只读核对0025，64张表前后行数/内容摘要完全一致。没有正式库写入、恢复、迁移或容器/卷重建。

准备期通过既有Resume/Profile Replay fixture、Interview service、Main Replay、父回答提交、Evaluation Replay、Controller生成追问。父记录确实持久化、有效且有关联，但属于**明确标注Replay的合成准备历史**，不冒充真实过去用户经历。收费部分只有Evaluation，没有Live Main/Coach或完整跨场次Live。

| 案例 | 用户 | Session | 父Question | 父Answer | 当前Question | 当前Answer | 父题/父答字符数 |
|---|---:|---:|---:|---:|---:|---:|---|
| E03 错后仍错 | 1 | 1 | 1 | 1 | 2 | 2 | 24 / 33 |
| E12 不完整后改善 | 2 | 4 | 3 | 3 | 4 | 4 | 23 / 22 |
| E02 只认识缺陷 | 1 | 5 | 5 | 5 | 6 | 6 | 22 / 18 |
| E07 引导后正确 | 2 | 6 | 7 | 7 | 8 | 8 | 68 / 29 |

父题、父答均未触发512/1024字符截断。新语境来自evaluation_context对同用户、同Session、合法父子关系、COMPLETED父Answer、对应正式Evaluation及USER Message的查询；不由模型生成ID。

旧臂是公开Agent边界的2.3.1角色投影副本：只去掉evaluation_context标记/scoring_context，冻结对应2.3.1 Prompt；没有修改存储Snapshot。新臂使用真实新Session冻结的2.3.2和FOLLOWUP_SCORING_V1。每对Question、原始Answer、Rubric、知识目标、open gaps及模型配置相同。模型请求前逐一核对准备时输入指纹。

E07父题真实持久化文本：
> Kafka 重复投递怎样避免重复落库？可以考虑订单ID唯一约束，把去重记录与订单写入放在同一事务，成功后再提交消费位点；请解释失败窗口。

这包含人可识别的明显引导，但系统没有独立可靠的提示事件记录。assistance_status始终UNKNOWN，未从题干推断成有提示/无提示。E07仍标为**提示感知能力受限**；不能因为模型说“按提示”就认定已实现可信提示追踪。

所有12项校准样本仍DRAFT_UNREVIEWED。暂定参考边界不充当Gold，不以新版接近参考范围自动宣称更好。

## 3. 四组实际评分与状态

维度顺序：技术正确性/覆盖度/深度/相关性/表达。

| 案例 | 2.3.1旧 | 2.3.2新 | 旧→新状态 | 最终业务结果 |
|---|---|---|---|---|
| E03 | 2/2/2/4/3 | 2/2/2/4/3 | NEEDS_CLARIFICATION → PARTIALLY_DEMONSTRATED | 两版合法，仅新版提交 |
| E12 | 3/2/2/4/3 | 3/2/2/4/3 | PARTIALLY_DEMONSTRATED → 相同 | 两版合法，仅新版提交 |
| E02 | 1/1/1/2/2 | 2/1/1/3/3 | INSUFFICIENT_EVIDENCE → 相同 | 两版合法，仅新版提交 |
| E07 | 4/4/4/5/4 | 原始JSON为4/4/4/5/4，**无效输出** | 两份原始JSON均PARTIALLY_DEMONSTRATED | 旧版合法，新版因4条引用被拒，不能当作正式评分 |

### E03：先答错，追问后仍错

父答：直接相加BM25与余弦分数，缺资料时模型补全并生成引用。
当前答继续该错误方案，承认未考虑量纲、融合、拒答，没有给出正确机制。

两版都识别量纲、RRF和无证据拒答缺口，均未解决原gap，均建议FOLLOW_UP。新版明确说“几乎没有给出正确机制”，却仍标PARTIALLY_DEMONSTRATED；旧版为NEEDS_CLARIFICATION。这是**状态偏宽风险仍存在**，不能用分数相同掩盖。

新版把若干“缺失解释”列进factual_or_reasoning_issues，旧版更明确指出直接相加和造引用这两个实际错误。没有证据证明新语境提升了这个案例的错误分类。原始quote均可定位，但quote真实不等于技术正确或充分展示。

### E12：首答不完整，后答部分改善

父答仅说收到消息处理、失败重试。当前答补充成功后手动ACK，并指出数据库提交后ACK失败会重复执行，但未给幂等方案。

两版同分同状态、原gap未解决。新版较清楚区分“已识别重复窗口”和“尚未给出机制”，factual issues为空，较少把缺失内容直接当已说错；旧版将缺乏保证条件列为问题。属于可解释的评价差异，不能仅凭此认定新版更准确。

**双方参考答案的共同技术风险**：都出现“本地事务/发件箱模式保证业务提交与Kafka位点提交原子性”或类似混淆。普通事务性Outbox不会自动把外部数据库事务与Kafka消费组位点提交变成一个原子事务。Kafka官方说明外部系统的输出与位置需要额外协调，可将位置与输出共同存储；Debezium Outbox主要描述数据库业务状态与发出事件的一致性。不能把这个共同错误归因于新投影，也不能作为合格技术标准展示给用户。

只读核对来源：[Apache Kafka设计说明](https://kafka.apache.org/design/)、[Debezium Outbox说明](https://debezium.io/documentation/reference/stable/transformations/outbox-event-router.html)。这里只核查技术边界，未调用额外模型、未修改参考答案或生产评分。

### E02：仅认识缺陷，不能解释机制

父答仅为“检索后交模型”。当前答知道不能随便相加、不能造引用，但明确不知道原因、RRF及证据充分性判断。

两版都说没有机制解释，没有把它标DEMONSTRATED，原gap均未解决。新版技术/相关性/表达各高1，覆盖/深度仍1。新版对诚实承认边界给予有限认可；旧版相关性、表达均2，存在让技术不足同时影响表达维度的偏严疑虑。二者状态均INSUFFICIENT_EVIDENCE，与用户暂定机制题偏NEEDS_CLARIFICATION有差异；草稿边界不是强制正确答案，不能改输出凑一致。

### E07：父题有明确引导，当前回答正确

当前答解释订单ID唯一约束、同一事务、提交后ACK及两个崩溃窗口。两版原始内容都识别旧gap已解决，同时提出更细的实现缺口，故选择PARTIALLY_DEMONSTRATED。

新版理由说“整体已充分回应问题”，但状态仍partial；双方继续要求“时序细节/重试幂等”，其中部分已由当前回答说明。存在对细节追加要求导致偏严/重复追问的风险。单次结果不能判谁正确。

新版实际返回4条answer_quotes：
1. 用订单ID唯一约束并在同一事务写订单及去重记录
2. 提交成功再ACK
3. 提交后ACK前崩溃则重复投递，由唯一约束阻止重复落库
4. 提交前失败回滚重试

各项均能定位当前原答，resolved_gap_ids也是真实既有ID；但Schema明示最多3条，所以结构校验硬拒绝。没有截断、合并、放宽上限或修改原始响应。最终error为INVALID_STRUCTURE_STOP_NO_REPAIR。

## 4. 逐请求真实usage

| 请求 | input | output | total | 延迟秒 | 结构/业务 |
|---|---:|---:|---:|---:|---|
| E03旧 | 3045 | 539 | 3584 | 8.397 | 通过 |
| E03新 | 3417 | 586 | 4003 | 9.221 | 通过 |
| E12旧 | 2333 | 845 | 3178 | 12.826 | 通过 |
| E12新 | 2695 | 891 | 3586 | 13.857 | 通过 |
| E02旧 | 3042 | 779 | 3821 | 11.830 | 通过 |
| E02新 | 3403 | 893 | 4296 | 14.317 | 通过 |
| E07旧 | 2352 | 543 | 2895 | 7.783 | 通过、未正式提交 |
| E07新 | 2743 | 536 | 3279 | 7.964 | 结构失败，未正式提交 |

新旧input分别12258/10772，新语境增加**1486 Token（13.80%）**。逐对增加372/362/361/391；没有回到旧全量上下文的约一万输入Token。新旧总usage分别15164/13478，输出随生成变化，不把总量差全归因于父语境。

这是四组各一次、没有重复采样；增加语境也改变了冻结Prompt说明，不能隔离哪一句或父信息的因果作用。没有总体评分准确率、统计显著性、质量无下降保证或全面提速结论。

## 5. 数据库、幂等与计费核对

- 8条ModelAdmission全部SETTLED，actual_tokens=charged_tokens，合计28642，与真实HTTP usage一致；无新增额度、购买、充值或配置更改。Token不是服务商账单人民币金额，本轮未独立读取账单。
- 7条成功Live StepRun；最后1条FAILED。前三组Snapshot未漂移，当前Answer仅各有1条正式新版Evaluation，旧臂只有审计不作正式评价；同键重试与Evaluation复用均未增加HTTP。
- Session1/4/5：COMPLETED、state_version=5、active_question_id=null；各2 Answer/2正式Evaluation（父Replay＋子Live），正常两题预算结束，不是长面试。
- Session6：IN_PROGRESS、state_version=4、active_question_id=8；父Answer7完成，子Answer8 FAILED；只有父Replay正式Evaluation。无错误新题，无当前正式Evaluation，无处理中lease或未释放预留。
- E07旧成功Evaluation保留在审计，可按旧臂契约复用，但不能把它当新版正式结果。未实际重试E07，不重置时间或Snapshot。
- 正式库64表前后摘要一致，仍0025；隔离库0026。选中四场的Snapshot全文和指纹前后完全相同。

### E07会话预算的保守多记（不能隐瞒）

验收器在结构失败后阻止Repair，抛出的BudgetStop不是生产ModelProviderError，因此结构生成器未将首个成功HTTP的usage附着到这个中止异常。StepRun23保存FAILED、token_usage_json={}；Controller按既有未知消耗策略将该请求预留**19806**计入unreported_model_tokens。

实际该HTTP为3279 Token，用户ModelAdmission已经正确结算3279。会话当前计数因此保守偏高，而不是服务商额外消耗19806；按本场已知旧臂2895加保守19806合计22701，比实际本场6174高16527。

没有悬挂reservation、没有免费重试，也没有改账“修成通过”。这是本次验收中止异常与usage传递的限制，不能宣称所有会话预算精确结算。后续若修复应先离线处理异常携带已知usage的问题；本轮不修改业务代码或历史失败记录。

## 6. 执行前检查与准备期问题

- 核实所有LIVE路由为指定DeepSeek、无fallback；新代码确实冻结2.3.2/FOLLOWUP_SCORING_V1，旧臂2.3.1；每次HTTP前核对输入指纹、模型、逻辑/HTTP数、时间和剩余Token。
- 8正常请求按UTF-8字节、输出硬限和额外余量作保守预留121269，小于160000；还用无网络请求对象检查HTTP/Token/截止边界。没有为探测另付费发请求。
- 模型Schema失败时停止，阻止Repair继续发送；因此实际8HTTP、0Repair HTTP、0fallback、0timeout。
- 已有PostgreSQL/Redis容器启动前核对原ID、镜像与卷，只启动原容器；没有Compose重建、prune或数据盘操作。新隔离库用既有Alembic到0026，不迁移正式库。
- 准备期两次脚本布置失败均发生在真实模型0调用时：误写Controller方法名accept_answer（实际prepare_answer）；AI Profile下两题Blueprint未包含Kafka，Replay无合法候选。保留失败审计及空Session2，没有扩大Blueprint权限或改业务规则。改用现有kafka_seed生成Java专用隔离Profile/user2，再以原幂等键恢复准备流程。
- 所有父答案通过正常service/Controller完成，不直接SQL改Answer/Evaluation为完成，不伪造父子关系。新建隔离数据明确DEMO/Replay，不发布任何虚构真实JD至正式库。

## 7. 产物及复核入口

新增脚本：apps/api/scripts/accept_v232_calibration.py（prepare/live/checkpoint），复用既有预算计量、provider、Agent、Controller与fixture工具，没有新Agent或评测基础设施。

本地ignored证据：

```text
.runtime/acceptance/v232_preparation_20261028_a.json
.runtime/acceptance/v232_live_20261028_a.json
.runtime/acceptance/v232_before_20261028_a.json
.runtime/acceptance/v232_after_20261028_a.json
```

这些记录保留精确请求messages、原始业务JSON、每次usage/延迟/版本/指纹、失败字段、Snapshot/Question/Answer/Evaluation/StepRun及配额。只有DEMO内容，不保存API Key/Authorization或隐藏reasoning。

实际检查：准备Replay、预算边界离线自检、8次真实HTTP、前三组Controller提交和同键幂等、最终只读PG审计、正式64表摘要、脚本AST检查。未重跑全量后端、前端或浏览器，不复用先前262项结果作本轮重跑。

## 8. 五个最终判断

1. **是否明显评分退化？** 没有五维普遍下降证据，但E03新版状态偏宽、E07结构失败，不能下“无退化”结论。
2. **是否更理解追问？** E12和E02新版理由更清楚区分风险认识与机制缺失；尚无足够证据证明普遍提升。E07并未因父引导自动解决提示判定或避免重复细节要求。
3. **UNKNOWN限制？** 只表明缺可靠实质提示来源，绝不等于无提示；不能宣称独立提示识别或因此认证掌握。
4. **实际成本？** 新增input1486，约13.80%；全批28642 Token、8HTTP、87.707秒，未超授权。参考答案本身的输出也有成本且质量待核。
5. **能否直接完整长面试？** 暂不建议。先有界离线处理answer_quotes数量契约与失败usage传递，明确双方共同参考答案技术风险；不要提高上限、裁剪引用或新加模型来绕过。无需无界追加局部A/B；修复后再单独授权恢复完整15–20轮、Coach及Memory闭环。

批次已结束，未重跑、未开始长面试、未commit/push；本轮到此停止。
