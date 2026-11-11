# Main Harness V1 Live 配对验收

## 原批次预检

状态 **BLOCKED_BEFORE_SEND**。实际完成 **0/3 组配对、0/6 个 Main 决策、0 次模型 HTTP、0 Token**。

## 已核对

- 当前 Main 路由为 ModelScope `deepseek-ai/DeepSeek-V4.1-Flash-1017`，Chat Completions 目标为 `https://api-inference.modelscope.cn/v1/chat/completions`。凭证已配置，项目账本已启用，无备用模型路由。单次输出上限为 3,000 Token。未发模型请求验证在线 JSON 兼容性。
- 源码中 SessionController 执行会话预算预留与结算，ModelGovernance 约束本项目用户用量。当前配置为每场 200,000 Token、每用户每日 100 次调用和 1,000,000 Token。这些是项目侧限额。Harness ON 另有限制 2 次工具反馈、3 次逻辑调用、4 次 HTTP，含最多一次引用子集 Repair。
- 官方模型页显示 2 魔粒／次。若按批次 18 次 HTTP 上限规划，需核实账户至少可用 36 魔粒，且同一账户和模型仍有足够的请求余量。公开标价与赠送规则不能证明当前账户可用额度。[魔搭模型页](https://modelscope.cn/models/deepseek-ai/DeepSeek-V4.1-Flash-1017) · [魔粒官方说明](https://www.modelscope.cn/posts/435591)

## 阻塞与结果

Chrome 中存在魔搭用量页。浏览器扩展两次读取超时，尝试绑定 Chrome 窗口时，Computer Use 因无法可靠识别当前 URL 停止。未读取到该账户的剩余魔粒或可核实的账号／模型请求余量。公开用量页呈现未登录视图，其显示值不能用于该账户的预算。按批次准入要求，验收止于预检。[魔搭用量页](https://modelscope.cn/magicube/usage)

| 配对 | 计划验证 | 实际结果 |
|---|---|---|
| A | RRF 信息充分时能否直接 FINAL | 未运行 |
| B | 岗位侧重不明确时能否按需读取冻结 Requirement | 未运行 |
| C | 待复核缺口存在时能否读取细节并避免重复 | 未运行 |

没有产生真实 Action、工具轨迹、最终问题、盲审样本、延迟或配额结算记录。现有 Replay 结果见 [P1 离线验收](73_main_harness_v1.md)。真实自主性、决策收益、额外调用成本与扩大评测的条件仍待验收。

原批次仅新增预检报告，无业务代码、Provider 配置、数据库、历史 Session 或 Git 提交变更。

## 调整授权后的唯一 Live 批次

**部分完成，因 C／OFF 输出截断停止。** 完整配对 2/3，尝试 5/6 个任务级 Main 决策，Controller 接受 4 个。实际 **5 次 HTTP、33,945 Token、45.956 秒**，无 Repair、重试或回退。按 2 魔粒／次估算本次约 10 魔粒，未读取平台实际扣减。未调用 Live Evaluation、Coach 或其他 Agent。

在原有隔离库 `interview_agent_v232_reliability_20261029_a`（Alembic 0026）新建六场。开场、历史问答和本轮 Evaluation 使用有明确标记的 Replay。每组固定同一岗位、JD 版本和要求、Skill、问题、回答、Rubric、有效 Evaluation、知识状态与合法候选目录。组内比对通过，候选数 A/B/C 分别为 13/9/13。比较忽略各会话独立生成的记录 ID 和记忆冻结时间。Live 通过原答题 Service 和原幂等键复用已校验 Evaluation，仅 Main 路由接入真实魔搭。B 的测试 JD 以 J04 公开摘要为依据，录入的是隔离试验用概述，不是原招聘正文。

本地审计材料为 `.runtime/acceptance/p1_harness_live_pair_prepared_v1.json`（冻结配对、完整合法目录和上下文校验指纹）和 `.runtime/acceptance/p1_harness_live_pair_v1.json`（实际发送输入、原始可解析业务输出、StepRun 与结算）。本批发送前检查总 HTTP、保守 Token 预留及 15 分钟截止时间，继续使用原 SessionController 与 ModelGovernance。无正式库写入、迁移、生产配置或业务代码变更。

### 去版本标签评阅表

使用下表时可单独复制本节，按岗位相关性、技术深度、承接上一答、重复程度与事实准确性逐项评阅。以下为真实候选问题；角色背景为隔离 DEMO。没有邀请独立评审或调用模型裁判。

| 样本 | 共同背景 | 待评阅问题 |
|---|---|---|
| P1 | B，已完整说明 RAG 混合召回及无证据回退，岗位涉及 Agent 工具执行、轨迹与恢复 | 如果让 Agent 在多轮里调用工具完成任务，你会怎样保存会话状态和每次工具调用的结果？遇到工具报错或同一调用被重复触发时，你会怎么处理？ |
| P2 | A，刚解释了 RRF 公式、排名融合和 k，候选资料另有 Python／Redis 订单缓存项目 | 简历里提到订单系统用 Python 和 Redis 做缓存。如果一条订单刚写库、缓存还没更新，这时读请求先命中旧缓存，你会怎样在 Python 这一侧控制这种不一致？失效和并发更新分别怎么处理？ |
| P3 | B，与 P1 相同 | 换个方向，聊聊 Agent 调用外部工具。假设一个任务要连续调用几个工具，中途某个工具超时或返回错误，你会怎么设计重试和整体状态管理？失败边界上你会特别注意什么？ |
| P4 | A，与 P2 相同 | RRF 按排名融合，其实丢掉了每一路内部的分数差距。什么情况下这种信息损失会让效果变差？如果两路检索器打分本身可比，或者在候选池深度上做取舍，你会怎么调整？ |

### 逐请求及提交结果

| HTTP | 组／模式 | Session | HTTP／finish_reason | 输入／输出／总 Token | HTTP 耗时 | 原始动作与 Controller |
|---:|---|---:|---|---:|---:|---|
| 1 | A／OFF | 1076 | 200／stop | 5,642／861／6,503 | 6,345 ms | 直接选择，P2，接受 |
| 2 | A／ON | 1077 | 200／stop | 5,504／939／6,443 | 6,822 ms | FINAL，P4，接受 |
| 3 | B／OFF | 1079 | 200／stop | 5,005／697／5,702 | 4,921 ms | 直接选择，P3，接受 |
| 4 | B／ON | 1080 | 200／stop | 4,908／718／5,626 | 5,362 ms | FINAL，P1，接受 |
| 5 | C／OFF | 1082 | 200／length | 6,671／3,000／9,671 | 21,351 ms | 无可解析选择，未推进 |
| — | C／ON | 1083 | 未发送 | — | — | 整批已停止 |

| 样本 | 考察目标／策略 | 合法组合 | 最终题源 | 聚焦 gap |
|---|---|---|---|---|
| P2 | Python／NEXT | `C_fcd5e567d4bb7b2927ca4112` | `fact:2787` | `[]` |
| P4 | RAG／DEEP_DIVE | `C_141253ff327608432dd5d079` | `skill:ai_agent_application@1.0.0` | `[]` |
| P3 | Agent／NEXT | `C_13484edfefc4d176b00a265e` | `requirement:2234` | `[]` |
| P1 | Agent／NEXT | `C_8d994f12d0f0878e34f64ae0` | `skill:ai_agent_application@1.0.0` | `[]` |

四个提交的组合、引用和聚焦范围均通过校验。两次 ON 首包均无 JD 正文或开放 gap 明细，均未发出 TOOL_ACTION，工具名、参数、结果和新增信息轨迹均为空。

### 决策与成本判断

- **A 信息充分。** ON 无工具直接 FINAL。以验证 RRF 追问为目标，P4 更贴合刚才的回答，深化了排名融合丢失分数间隔及候选池取舍，避免重复询问公式和 k。P2 合法切题，缓存项目陈述与冻结简历摘录一致，有助于拓宽覆盖，但不回答本组的 RRF 深挖目标。P4 的生成评分点中“强匹配与勉强入榜的贡献相同”需要限定为同一名次的贡献与原始分数无关，不应解释成不同名次贡献相同。
- **B 岗位侧重不足。** ON 选择了合理的通用 Agent 问题，涉及状态、工具结果和重复调用；它没有查询 JD，未验证主动取数。OFF 用 `requirement:2234` 锚定超时、重试和恢复，岗位依据更强，两个问题都没有直接考察该要求中的工具轨迹评测。就本组目标，P3 更贴近 JD 侧重；P1 对结果持久化和幂等也有具体考察价值。两者均避开已回答的 RAG 内容。
- **C 历史 gap。** 决策输入中，RRF 公式和 k 已解决，单路检索偏差／漏召回、相似 chunk 重复占位仍开放。OFF 在 3,000 个输出 Token 上限处收到 `finish_reason=length`，StepRun 记载这 3,000 均为 reasoning Token。Provider 在结构解析前返回 `model_output_truncated`，没有可用产物进入 Repair 或 Controller。停止整个批次，C 的 ON 是否能通过读取 gap 改善决策没有观测结果。
- **成本。** 完成的两对均为每臂一次 HTTP，无工具额外请求。A、B 的 ON 输入分别少 138（约 2.45%）、97 Token（约 1.94%）；总量分别少 60、76 Token，HTTP 分别慢 477、441 ms。工具反馈的真实增量成本没有样本。全批实际输入 **27,730**、输出 **6,215** Token。

### 账本、失败状态与结论

五条项目用户账本均为 `SETTLED`，实际与扣减均合计 33,945 Token。A、B、C 对应用户账本分别为 12,946、11,328、9,671 Token，与逐请求用量及各 Session 审计一致。无残留会话预留。C／OFF Session 1082 为 `IN_PROGRESS`、state_version 5、active_question_id 1267。第二答仍 `FAILED`，其已校验 Replay Evaluation 留在审计中，未正式提交，也没有创建下一题。C／ON 保持预检准备后的版本 4，未进行 Live 执行。

**初步验证了真实模型能遵循 FINAL 分支，并能在 A 跳过工具。** 本批没有真实工具调用，未验证选对工具、工具反馈改善决策或该额外成本是否值得。A、B 的差异不能单独归因为 Harness，样本也不足以支持统计结论。当前不具备扩大真实评测的证据，仍缺 TOOL_ACTION 成功样本及 C 完整配对。保留本次失败现场和默认模型配置，未启动额外批次。

## P1.1 定向复测

另行授权的一批共 **4 次 HTTP、27,312 Token、30.121 秒**，与上面的原预检和 P1 批次分别计数。B 两臂都提交了同一个 Python／Redis 简历目标，ON 未调用 JD 工具。C 两臂均使用隔离 Main 的 6,000 输出 Token 上限，OFF 成功提交，ON 主动选择 `read_active_knowledge_gaps`，但供应商响应 JSON 多出未声明的 `_raw`，Schema 在工具执行前拒绝，未进入反馈或 FINAL。4 条配额均已结算，无重试或 Repair。原因、逐次成本及配置说明见 [P1.1 诊断记录](75_main_harness_p11_diagnosis.md)。
