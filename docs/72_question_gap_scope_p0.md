# P0：题级 gap 与目标累计状态分离

范围：只修 docs/69 的 `UNRESOLVED_KNOWLEDGE_GAP`。模型请求 **0**。无正式库写入、迁移、Provider 变更、Harness、任务计划或提交推送。

## 原始证据与修复

历史第二答只证明 RRF 公式及 k 的影响。Evaluator 的五维 5/5/4/5/5 合法，但旧产物把“当前题充分展示”与“整个目标没有剩余 gap”用同一个字段表达。原输出只关闭 `gap_ebf04b4e255cc571e5412c3e`；`resolve_observations` 正确按旧规则拒绝，未调用下一次 Main。

新建自适应 Session 冻结 `QUESTION_GAP_SCOPE_V1`，Main / Evaluation Prompt 均为 **2.3.3**；保留合法目标题源组合、ROLE_CONTEXT_V1 和既有父问答语境：

- Main 为选中的目标声明 `focus_gap_ids`（最多五个），必须属于本次已校验目标的开放 gap，不能重复。已有缺口的 FOLLOW_UP 不得用空数组绕开聚焦。普通新题没有旧缺口时明确传 `[]`。问题及已验证决策由原 Controller 在同一事务提交，沿用 `candidate_refs_json.adaptive_decision`，**无需改 Controller**。
- Evaluation 从题目冻结的决策读取作用域，并保留完整开放 gap 列表；Evaluator 的输出 Schema 不允许它返回作用域。空数组表示明确不考察旧缺口；缺少可靠元数据则为未知，不按空数组放行，保留旧的保守校验。无效、跨目标或过期 ID 被拒绝。
- 五维分数、原始观察和明确关闭的 gap ID 不改写。被关闭的 gap 仍须引用合法开放 ID 且存在当前回答的原文证据；服务端验证归属、原文与状态一致性，不冒称能确定性验证引用的技术语义。
- 本题所有聚焦 gap 均关闭且没有新的机制缺口，才允许本题充分展示。存入知识观察的 `question_state` 表示本题；供 Director 和 Memory 消费的 `state` 仍是目标累计状态：存在其他未解决 gap 时保持部分展示，开放 gap 不删除。不存在新分数阈值或长期 VERIFIED 规则变更。
- 旧会话仍使用旧 Prompt/Schema、严格累计状态校验及原输入投影。旧知识记录序列化不添加空字段，既有审计和复用身份不漂移。Main 受约束引用 Repair 与 Evaluation 引用 Repair 保留；Main 复用会按其冻结契约解析相应决策 Schema。

同一真实失败的固定 Replay 结果：

| 契约 / 范围 | 结果 |
|---|---|
| 原 2.3.2 | 同一输出继续拒绝，第二答不提交 |
| 2.3.3，冻结 RRF 公式这一个 gap | 相同原始 Evaluation JSON 提交；五维 5/5/4/5/5；题级充分，目标累计部分展示 |
| 2.3.3，另将单路偏差/漏召回纳入聚焦范围 | 相同输出仍拒绝，因为题内缺口尚未解决 |

成功用例保留两个原 gap：

- `gap_a304838ff6b4c044b3f363f8`：未解释融合如何降低单一检索方式的偏差或漏召回。
- `gap_e966b66c767a0f23b3615679`：未说明相近 chunk 重复占位对最终排序的具体影响。

## 文件与验证方式

生产修改：`interview/adaptive.py`、`adaptive_prompts.py`、`context.py`、`context_projection.py`、`agents.py`。未改 Controller、Memory、模型配置、数据库 Schema 或历史验收。原始 Demo 业务输出保存在 `apps/api/tests/fixtures/v232_gap_failure.json`；无密钥、请求 Header 或真实候选人隐私。兼容性 Replay fixture 仅补新 Schema 的选题元数据，旧版本断言继续保留。

专项与定向回归使用进程内 `DATABASE_URL` 和 `M1_TEST_DATABASE_URL`，均指向已有隔离库 `interview_agent_v232_reliability_20261029_a`；连接后先只读验证实际库名及 Alembic **0026**，未迁移。测试进程阻断外部 DNS 与 Socket，只使用本地数据库和模型边界 Replay/MockTransport。真实 Session service 和 Controller 执行创建、提交、失败恢复、同键重试、报告及记忆聚合。

专项覆盖初始空范围、多 gap 全部或部分关闭、缺失作用域、非法 ID、Evaluator 不能自设范围、目标未充分时的硬拒绝。通过服务的真实旧/新样本检查题目元数据与 Snapshot 冻结、非法选题不推进、失败后复用已验证 Evaluation、会话版本和 Usage 结算。原始输出合法提交后没有升为长期 VERIFIED。

20 轮 Replay 沿用 docs/70 的固定问答场景，在新版契约下运行；Coach 引用来自实际正式评价，下一场冻结 MemoryGuidedPlan。该回归验证流程与不变量，不验证真实模型采纳新 Prompt 的稳定性、评分准确度或全场 Live 成本。

最终运行 11 个相关 pytest 文件，**154 passed、0 failed、0 skipped**（60.40 秒）。隔离库 Replay Session **545** 记录 20 次正式评价和 8 次 FOLLOW_UP，自然结束时 `state_version=41`，生成 Coach Report **54**，下一场 Session **546** 冻结了新的 MemoryGuidedPlan。账本中的 4,000 Token 是 Replay 模拟用量，真实模型请求为 0。`git diff --check` 通过。

进入 P1 只指具备进一步设计与离线实施的工程前置条件，须另行批准。P1 需证明真实的按需信息收益：初始 Prompt 已提供同一内容时，再读取不是有效工具使用；与 Harness OFF 基线对照，不自动发起任何模型或每日评测请求。
