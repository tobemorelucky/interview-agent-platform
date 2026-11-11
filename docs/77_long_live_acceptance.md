# 长面试 Live 验收

新批次 `p1_long_live_20261109_a` 在短程门禁停止。实际 **4 次 HTTP、30,355 Token、约 52 秒**，按 2 魔粒／次估算 **8 魔粒**。四笔 ModelGovernance 账本均为 SETTLED，实际与扣减一致，没有会话预留或未知发送。无 Repair、重试、Provider 回退或第二批。长场未创建，15～20 答、真实 Coach 和下一场 MemoryGuidedPlan 均未验收。

## 原阻断的离线定位

| 原批次记录 | 原样 Replay 与确定性诊断 |
|---|---|
| docs/76 HTTP #28 | 有 13 个合法候选，Main 选中了允许 NEXT 的 Agent 目标。题首“检索这块先放一放”被 `topics.validate_topic` 归为已冷却的 `rag_retrieval`，Controller 在提交新题时拒绝并回滚。离线仅去掉该过渡句后，同一题通过主题校验。实际失败由题面分类与考察目标不一致引起，合法目录没有耗尽，Controller 也没有错误地写入或推进。 |
| docs/76 HTTP #35 | HTTP 200，`finish_reason=length`。原请求 `max_tokens=6000`、`response_format=json_object`，没有思考强度覆盖。6,000 输出 Token 含 4,766 推理 Token，收到 2,434 字符的非完整 JSON。Provider 在业务解析前返回 `model_output_truncated`，Replay 原样复现。 |

保持原 Controller、题源、聚焦 gap、Memory 和生产模型设置。新隔离库 `interview_agent_long_accept_20261109_a` 从空库建立至 Alembic 0026，正式库和历史验收库没有迁移或写入。仅新批隔离 Evaluation 实例使用 12,000 输出 Token 上限，Main 仍为 6,000。每次发送前保留会话预留并检查批次硬预算。

## 短程逐请求

| HTTP | 场景 | 输入／输出／总 Token | HTTP 耗时 | 结果 |
|---:|---|---:|---:|---|
| 1 | RAG 冷却，Evaluation | 3,955／4,785／8,740 | 30,857 ms | 有效，正式 Evaluation 3 |
| 2 | RAG 冷却，Main | 6,323／672／6,995 | 5,181 ms | 请求冻结 JD 详情，实际执行 |
| 3 | RAG 冷却，Main | 6,545／1,310／7,855 | 10,716 ms | NEXT 到 Agent，提交 Question 4 |
| 4 | 历史 gap，Main | 6,609／156／6,765 | 2,824 ms | 合法 JSON，但动作 Schema 拒绝 |

四次均为 HTTP 200、`finish_reason=stop`。输入 **23,432**、输出 **6,923** Token。短程 8 次上限使用 4 次，完整批次 70 次上限剩 66 次。当前只有一个短程 Live 正式评价，没有任何长程五答检查点。

### RAG 冷却案

Session **2** 的前两答由真实归档输出明确 Replay，第三答为本次合成候选人作答。候选人解释了换模型后不能复用 0.8 阈值，并提出分层标注、重设阈值、用留出集检查误拒与无证据回答比例。真实评分 **4／3／3／5／4**。P0 只关闭当前两个冻结聚焦 gap 中的 `gap_8a8a9317c37f43d30df944ba`，另一个聚焦 gap 保持开放。累计开放缺口为 11，评价未误标为充分展示。

当前投影仍将 RAG 标为冷却，提供 12 个合法组合。Main 主动调用 `inspect_job_requirement`（`requirement_id=7`），读取到初始消息没有的冻结要求正文。结果先持久化，再送入下一次 Main。FINAL 选择 `C_65b31ec77dda9d92f0c32f08`，引用 `requirement:7`，询问 Agent 写操作超时、重试幂等及状态恢复。题面分类为 system_design，没有命中冷却的 RAG。Controller 在版本 **7** 提交新题，正式评价总数为 3，其中 2 个是 Replay 历史，1 个是本次 Live。该样本验证一次合法切换和工具闭环，不能证明更高的 Evaluation 上限本身消除了截断，本次实际输出低于旧上限。

### 历史 gap 案与新阻断

Session **4** 的 Main 返回：

```json
{"kind":"tool","name":"read_active_knowledge_gaps","arguments":{},"tool_feedback":[]}
```

实际 Replay 定位为 `tool.read_active_knowledge_gaps.tool_feedback: extra_forbidden`。这是出站动作带回了服务端输入字段，和旧 `_raw` 同值回显是不同形状。工具执行前被拒，未生成反馈或 FINAL。原短程答复为 **FAILED**，正式评价仍只有 1 条 Replay 历史，活跃题目 **6**、state_version **5**，无遗留 lease 或预留。没有把已校验的第二答 Replay 评价算成正式评价。短程门禁标记 `SHORT_TOOL_OR_DECISION_BLOCKED` 后停止。没有放宽 Schema 或再次发送请求。

若后续修复这一阻断，最小方案是在新版本 Main Prompt 明确工具动作只允许 `kind`、`name`、`arguments`，并说明 `tool_feedback` 只由服务端生成且不得回显。可先用本次失败原文做边界回归，保留严格拒绝和现有题目校验。

## 可回放交付

`.runtime/acceptance/p1_long_live_20261109_a/` 保留 4 条独立请求 JSON、`captures.jsonl`、`manifest.json`、`index.json`、立即写入的工具记录 `tools/8_1.json`、`offline_preflight.json`、`short_gate.json` 及 `short_transcript.json`。问答记录来自隔离库只读导出，区分真实正式评价、Replay 历史和未提交回答。没有长场问答记录可补写。

`run_long.py verify` 在离线网络限制下对 4 条响应按完整指纹各重放两次，改动输入均拒绝，1 个工具结果按动作与 Snapshot 指纹校验通过。失败响应及精确 Schema 错误保留，可独立复现。归档含最终正文与用量，不含密钥、认证头或隐藏推理文本。

**结论：已有短程真实工具闭环与 P0 正式提交证据，仍无真实长面试、Coach 和跨场次 Memory 的本批完整闭环证据。** 未修改生产代码或提交 Git。
