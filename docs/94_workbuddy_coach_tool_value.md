# 第十五轮：Coach 工具查询的必要性与增量价值

本轮真实模型 **2 次 HTTP / 10,505 Token**（上限 4 / 150,000），
未改 OFF 的 Prompt / Schema / 生成键，未新增工具或调用轮次，未改 Controller / CapabilityMemory，
未触碰正式库、归档 Live 库或已完成的 Coach 报告。

开工 HEAD `7e5e2d7`，工作区干净（仅 `.workbuddy/` 未跟踪）。

---

## 1. 零模型成本的工具信息增量审查

审查对象：`COACH_EVIDENCE_V2` 对第十四轮真实 18 条评价的投影（84,970 字节，同一评价集；
第十四轮真实运行时带 `adaptive_review` 与 completion，为 86,849 字节）。

**V2 已经给出**：18 条评价完整 id / `evidence_ref` / 冻结 `fingerprint`；题目全文（不裁剪）；
五维分数全量；`answer` 前 600 字；`evaluation` 前 800 字；`covered_points`/`missing_points`
各前 4 条（每条 140 字）；`factual_or_reasoning_issues` 前 3 条；`follow_up_focus` 前 3 条；
以及每个字段的 `*_total` 与 `omitted` 计数。裁掉的字段（`reference_answer`、
`answer_framework`、`issues`、`provider`、`model` 等）本来就不允许 Coach 引用。

**真实会话里 V2 真正隐藏了多少**：自由文本合计 **34 字符**（只有第 18 条答案超出 600 字 34 字），
列表项合计 **32 条**（多为 `covered_points` 超出 4 条的部分）。也就是说，V2 几乎没漏东西。

### 工具一：`read_evaluation_detail`

| 项 | 结论 |
|---|---|
| 调用前已知 | 题目、分数、答案前 600 字、评价前 800 字、四类要点前 4/4/3/3 条 |
| 修复前返回的"新增" | **实际上更少**：第 18 条答案 401 字 < V2 的 601 字；`follow_up_focus` 1 条 < V2 的 3 条；整个结果 4,199 字节。原因是 4KB 大半花在重复 `question`/`scores`/`question_kind`/`session_question_id` 上，然后 `_fit` 把真正的新内容裁掉 |
| 对应正式证据 | `interview_evaluations` 同一行；`evaluation_fingerprint` 冻结校验 |
| 不查可能的错误 | 在**确实被裁剪**的评价上，可能只看前半段的正面结论、漏掉后半段的关键缺口；在真实归档上这个风险几乎不存在（34 字符） |
| 额外成本 | 工具本身 0 次模型调用；若模型因此进入第二轮，需再付一次完整 payload 的模型调用（第十四轮场景 ≈ +87KB 输入 / +3.5% token；本轮小 payload 下 ON 单次即 +28%） |

**已修（`6d65a33`）**：改为只返回增量——`answer_hidden` / `evaluation_hidden` 尾部与 `extra_*`
列表项，带 `hidden_counts` / `shown_by_projection` / `no_additional_evidence`；
整个结果（含封装）≤ 4KB；裁剪下限不低于 V2 已展示的长度。同一真实第 18 条评价的结果
从 4,199 字节降到 **1,411 字节**，且全部是新增内容（34 字符答案尾部 + 2 条额外要点）。

### 工具二：`read_capability_history`

| 项 | 结论 |
|---|---|
| 调用前已知 | 会话起点冻结的 `capability_context`：state、priority、needs_recheck、memory_revision、reason_tags、最多 3 条 observation_refs、history_summary。**第十四轮真实会话该结构 0 条 items**（该场没有需要独立验证的知识点），但 `training_scope` 里有报告期 5 个目标的状态与合法模式 |
| 查询后新增 | `revoked`、`independent_sessions`、`last_observed_at`、冲突明细、最多 5 条带 session/evaluation 引用的观察、`captured_at`、捕获指纹 |
| 对应正式证据 | 报告期一次性捕获（`CAPTURED_AT_REPORT_GENERATION_NOT_A_LIVE_QUERY`），不查库 |
| 不查可能的错误 | 可能不知道某 topic 已 `revoked` 而不该建议 WEAKNESS；但**决定合法性的是 resolver 的 `training_scope` + `modes()`**，模型猜错也只会被拒绝，不会产出非法建议 |
| 额外成本 | 工具本身 0 次模型调用；同样只在模型进入第二轮时才产生一次额外模型调用 |

顺带修掉一个真实越界：结果曾为 1,214 字节（上限 1,200），因为字节上限只量了 `data`
没量封装，且循环结束后才追加 `truncated` 字段。现按整个封装计量并稳定收敛。

**是否存在"工具只重复已有信息"**：修复前 `read_evaluation_detail` 在真实数据上**确实几乎只重复，
甚至更少**；`read_capability_history` 在计划非空时主要提供状态明细而非新决策依据。

---

## 2. 场景 B：正式 Evaluation 被合法裁剪（合成场景）

**构造方式（未手插任何正式评价或记忆行）**：`LongEvaluationProvider`（replay）通过
`MockInterviewService.create/start/answer` 产生一条正式评价；评价正文约 940 字，
决定性事实「消费位点必须在幂等事务提交之后手动提交，否则重平衡会重复扣款」
被放在 800 字裁剪点之后，并作为第 6 条 `missing_points`（V2 只给 4 条）。
**明确标注：合成压力测试，不是真实用户历史。**

离线验证（`test_coach_tool_value.py`，0 次真实模型调用）：

- V2 投影确实不含该决定性事实，`clipped_text_fields > 0`，评价工具可用；
- 工具返回该事实（`evaluation_hidden` / `extra_missing_points`），`hidden_total > 0`；
- `evaluation_fingerprint(row)` 与冻结值一致；改动指纹 → `EVALUATION_CHANGED`；
  越界 id → `EVALUATION_OUT_OF_SCOPE`；
- 结果不含 `reference_answer` / `provider` / `model` / 其他用户数据，≤4KB；
- 不查工具时，一个 Replay Coach 产物不会凭空写出该隐藏事实。

**结论**：这是一个**工具确能带来真实信息增量**的场景，但只在刻意构造的长评价上成立。

## 3. 场景 C：真实规则产生的跨场次能力冲突（合成压力测试）

**构造方式**：复用第十一轮的确定性序列 `GOOD, GOOD, WEAK, GOOD, WEAK`，
经五场正式 replay 会话与 `CapabilityMemoryService` 合法生成 `CONFLICTED`；
再创建一场报告会话。**明确标注：合成压力测试，不是真实用户历史；归档中没有 CONFLICTED/RECHECK_DUE。**

- V2 冻结计划（当该知识点进入计划时）已给出 `state=CONFLICTED`、`needs_recheck=true`，
  合法模式由 `modes()` 决定为 `[FULL, RECHECK]`；
- `read_capability_history` 新增的是逐条观察引用与冲突原因，**不改变合法模式**；
- resolver 接受 RECHECK 建议（`LEGAL_AT_REPORT_TIME_ADVISORY_ONLY`），
  拒绝 WEAKNESS（`MODE_NOT_ALLOWED_FOR_STATE`）；
- 工具结果无答案原文、≤1.2KB；确定性基线直接给出 RECHECK。

**结论**：**该工具设计价值不足**——它提供的是更细的引用，不是新的决策依据。按本轮规则，
不为凑调用次数强迫模型查询，因此场景 C **未做真实模型试验**。

---

## 4. 确定性建议基线（零模型调用）

`deterministic_suggestions(training_scope, evaluations)` 只用现有
`CapabilityMemory` 状态、`modes()`、知识点优先级与有效评价引用，按固定排序产出结构化候选项。

| 输入 | 基线输出 |
|---|---|
| 第十四轮真实会话（3 个 PRACTICING） | 3 个合法 WEAKNESS 候选，包含真实 ON 两次给出的 `agent` 与 `rag_retrieval` |
| 场景 C（CONFLICTED） | 1 个合法 RECHECK 候选 |
| 场景 B（CONFLICTED） | 1 个合法 RECHECK 候选 |

它不调用模型、不调用工具、不改 OFF 契约。这说明：**「结构化训练建议」这个产品价值，
与「让模型自主查工具」是两件事**；前者用现有规则即可零成本获得。

---

## 5. 小规模真实 Live（仅在场景 B，离线通过后才做）

新隔离库 `interview_agent_wb_r15_real_20261113`（新建 + 0027 显式授权迁移），
仅 Coach 使用真实 ModelScope Provider（`deepseek-ai/DeepSeek-V4.1-Flash-1017`）。
同一会话、同一冻结输入，OFF ×1 + ON ×1；模型选 Final 后**不重复提交同一场景**。

| 运行 | HTTP | Token | 延迟 | 首轮选择 | 工具调用 | 结果 |
|---|---|---|---|---|---|---|
| OFF (1.0.3) | 1 | 4,600 | 5,173 ms | — | — | COMPLETED |
| ON (1.1.0) | 1 | 5,905 | 6,700 ms | `SINGLE_CALL_MODEL_CHOSE_FINAL` | **0** | COMPLETED |
| 合计 | **2 / 4** | **10,505 / 150,000** | | | **0** | |

- 两个工具都可用（`read_evaluation_detail` + `read_capability_history`），模型仍直接终稿。
- 两份产物引用均为 `[1]`、无无依据数字、无跨岗位掌握度断言、
  `epistemic_status` 保持 `AGENT_SYNTHESIS_OF_THIS_INTERVIEW_NOT_STABLE_CANDIDATE_FACTS`。
- **隐藏事实未被使用**（两份结论都没有 `DECISIVE`）。但也没有编造"已完全掌握"：
  ON 反而从 V2 已可见的 `missing_points` 推断出"发送失败/提交失败时状态一致性未讲清"，
  与隐藏事实方向相近。因此"不查工具"在本例**没有造成可证实的结论错误**。
- **ON 的训练建议被 resolver 拒绝**：模型提了 WEAKNESS，而该知识点状态是 CONFLICTED、
  只允许 FULL/RECHECK（`MODE_NOT_ALLOWED_FOR_STATE`）；确定性基线直接给出 RECHECK。
  即：本场景下 ON **没有产出任何可用建议**，确定性基线反而正确。
- ON 单次调用成本 5,905 vs OFF 4,600（**+28%**，因小 payload 下附录占比更大）；
  第十四轮大 payload 下为 +3.5%。这是提示词/附录的固定开销，与是否用工具无关。
- 准入账本：当日 2 条 `SETTLED`，`actual = charged = 4,600 / 5,905`，
  无 `UNKNOWN`、无 `RESERVED` 残留、无 `MODEL_USER_QUOTA`、`structured_retry = 0`。
  正式库当日准入计数为 0（未触碰）。
- Capture/Replay：本轮未使用历史 Live Capture 伪造输出；两份报告都是现跑。
  Replay 只用于构造场景（评价/面试边界）与离线测试。请求指纹、工具轨迹（0 次）、
  最终报告、generation_key、usage 均落在隔离库与 `.runtime/r15/live_results.json`。

**场景 C 未做 Live**：离线已证明该工具只提供引用明细、不改变决策，按规则不为刷调用次数重复提交。

---

## 6. 核心评测逐项对照

| 评测项 | 场景 B（真实 Live） | 场景 C（离线） |
|---|---|---|
| 1 原始证据与缺口 | 决定性事实在 800 字裁剪点之后、第 6 条 missing_points | CONFLICTED 状态由 5 场正式评价生成 |
| 2 工具是否可用 | 两个都提供 | 历史工具可用 |
| 3 首轮 Tool / Final | **Final** | 未做 Live（离线判定无需查询） |
| 4 若调用，新增什么 | 未调用（离线已证工具能返回该事实） | 观察引用 + 冲突原因 |
| 5 Final 是否利用新增证据 | 否；但也未编造掌握度 | 不适用 |
| 6 suggested_training 是否合法 | **ON 的 WEAKNESS 被拒绝**；基线 RECHECK 合法 | resolver 接受 RECHECK、拒绝 WEAKNESS |
| 7 无依据能力断言 | 0 | 0（scope_note 与 resolver 约束） |
| 8 多目标建议不稳定 | 本场只有一个合法候选，未复现第十四轮的漂移 | 规则排序稳定 |
| 9 HTTP/Token/延迟 | 2 / 10,505 / 5.17 s + 6.70 s | 0（离线） |
| 10 相比确定性基线的实质增量 | **无**：基线给出合法 RECHECK，ON 给出被拒的 WEAKNESS | **无**：基线同模式 |

**只在"查询确实帮助避免错误、解决证据不足或改善目标选择"时才算收益——本轮三项都没有发生。**

---

## 7. 生成键、冻结与 OFF 契约

本轮**未修改** `coach_generation_key`、OFF 提示词、OFF schema 或任何既有报告。
OFF 报告 prompt 1.0.3、ON 1.1.0，键不同、两代并存（id 1 / 2）；
会话起点计划与报告期捕获的冻结语义保持第十四轮结论。
工具改动只影响 ON 路径的返回内容，且不影响 OFF 是否复用。

---

## 8. 测试结果

| 项 | 结果 |
|---|---|
| 新增专项 `test_coach_tool_value.py` | **4 passed**（场景 B、场景 C、确定性基线、工具不编造） |
| Coach/训练/报告/幂等子集 | **105 passed / 0 failed** |
| 全量业务段 @ `interview_agent_wb_r15_test_20261113`（0027 / 67 表） | **822 passed / 0 failed / 1 skipped**（272.68 s） |
| 迁移段 @ 空库 `interview_agent_v21_wb_r15_20261113` | **10 passed / 0 failed**（63.59 s） |
| 前端 `vue-tsc && vite build` | 通过，0 类型错误（本轮无前端改动） |
| 数据库安全闸 | 复制库/测试库/迁移空库分离；正式库当日模型准入计数 0、归档库未写入 |
| 用例总数 | 818 → **822**（+4 条专项），未删除或跳过任何既有用例 |

---

## 9. 最终回答

**1. 两个工具各自提供了什么新增证据？**
修复后 `read_evaluation_detail` 只返回 V2 裁剪之外的文本尾部与额外要点，并带冻结指纹校验；
在真实 18 条评价上净增量仅 34 字符 + 2 条要点，在合成 B 场景下能返回决定性事实。
`read_capability_history` 提供逐条观察引用、冲突原因与 `revoked`/时间信息，
但合法模式由 V2 状态与 `modes()` 已决定，属于**明细而非新决策依据**。

**2. 真实模型是否自主选择调用？**
**否。** 本轮 0/2；连同第十四轮 0/2，累计 **0/4**。即便决定性证据被刻意藏起来、
两个工具都可用，模型仍直接终稿。

**3. 调用是否改善最终建议？**
没有调用，因此没有改善。更关键的是：ON 提出的 WEAKNESS 因 CONFLICTED 状态被 resolver 拒绝，
而确定性基线给出合法 RECHECK——本场景下 Harness 的建议质量**低于**零成本基线。

**4. 一个确定性建议基线是否已能满足主要需求？**
**能。** 复用现有状态、`modes()`、优先级与有效引用，零模型调用即可产出合法结构化候选项；
第十四轮真实 ON 的两次建议都落在它的候选集内，CONFLICTED 场景它直接给出正确的 RECHECK。
结构化建议本身有价值，但这份价值**不来自工具调用**。

**5. Coach Harness 值得继续开发、保持实验功能，还是暂时搁置？**
**暂时搁置，并保持 `INTERVIEW_COACH_HARNESS=false`。** 理由：工具路径在真实模型上 0 次触发、
真实数据几乎没有信息增量、建议质量不优于规则基线、ON 还有固定 +3.5%~+28% 的开销。
不建议再改 Prompt 诱导调用，也不建议扩工具或加轮次。已有的 `suggested_training`
若要做成产品特性，应改走**确定性规则生成 + 现有 resolver 校验**，而不是再投 Harness。

**6. 下一阶段应该转向 Research、Matching 还是其他产品能力？**
**都不建议现在转向 Research / Matching**——两者都会新增 Agent 与架构面，而本轮证据恰恰说明
"再加自主能力"没有用户侧收益。建议转向**普通用户闭环**：
① 简历上传 → 解析 → 画像链路（至今只在 replay 下验证过）；
② 私人 JD 的 Skill 推荐接口（后端 `recommend_for_target` 已存在、未暴露）；
③ 能力记忆增加岗位维度，消除跨岗位状态混用对训练建议的影响；
④ 请求记录 ↔ 业务审计三向 join（`docs/89` §10.1）。这四项都不新增 Agent。

---

## 10. 剩余风险与边界

- 结论基于 **1 个真实归档会话 + 1 个合成场景 + 2 次本轮真实调用**，样本量小；
  不能排除存在"证据极长"的真实会话让模型愿意查询，但当前归档没有。
- 修改后的工具输出形状是 ON 专用；若未来弃用 Harness，这两个函数可以一并删除。
- 场景 C 是合成压力测试，**不能称为真实用户历史**；归档里没有 CONFLICTED/RECHECK_DUE。
- 确定性基线目前只是测试内的对照实现，**未接入产品**；若要产品化需单独审批。
- 能力记忆仍按 `(user, topic)` 聚合，无岗位维度；`scope=CURRENT_JOB_TARGET` 的语义因此偏弱。
- 本轮未做浏览器 UI 验证（无前端改动），未新增截图。
- 未修改 OFF 契约、未删除任何 generation、未触碰正式库与历史 Live 数据。

**本轮完成后停止；不自动开始第十六轮，不自动开启 Harness 默认 ON。**
