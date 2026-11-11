# V2.3.3 长场 Live 批次验收记录

## 结果

批次 `p1_long_live_20261109_b` 已封存为 `UNKNOWN_PREVIOUS_SEND`。两个隔离会话中，最长连续 **1 个有效回答和正式 Evaluation**。未达到 15～20 答，未运行终局 Coach，未创建用于验证 MemoryGuidedPlan 的下一场。上一批的真实 Harness 工具闭环证据继续保留，本批两场都使用 Harness OFF。

本批最多 75 次 HTTP、700,000 Token、60 分钟和两个长场。实际落盘 5 个请求记录，其中 **4 个收到 HTTP 200，1 个发送状态未知**。收到响应的用量合计 **28,289 Token**，按 2 魔粒/次估算约 8 魔粒，若未知请求也计费则约 10 魔粒。未知用量未算作零，也未混入实测总数。请求 #5 的采集侧保守预留为 27,303 Token。没有 Repair 和已确认的 Evaluation 截断。

## 离线修复与验证

- docs/77 HTTP #4 的模型最终文本在本地解析前已含顶层 `tool_feedback: []`。可以确认字段来自 API 响应文本，无法进一步区分上游模型与托管平台处理。历史原文和 SHA256 保留。
- `apps/api/src/interview_api/modules/interview/main_harness.py` 的 `MainHarnessAction.accept_identical_tool_echo` 只在顶层恰好为工具动作的三个字段加空数组回显时移除该字段，重新执行完整 Action Schema，并记录原始和规范化动作的 SHA256。非空反馈、其他未知字段、非法参数和 FINAL 中的反馈仍拒绝。
- `apps/api/tests/test_main_harness.py` 新增原始响应回归和拒绝边界，并扩展同键恢复测试。定向离线与隔离 PostgreSQL 回归共 15 passed、18 deselected。原始响应和规范化动作的独立指纹见 `preflight.json`。
- docs/76 HTTP #35 返回 `finish_reason=length`。原 6,000 completion Token 含 4,766 推理 Token，最终 JSON 仅 2,434 字符且未闭合。Provider 在业务校验前拒绝。新批次仅对隔离 Evaluation 采用 12,000 输出上限，未更改生产 Prompt、Schema 或推理参数。唯一收到的 Evaluation 响应使用 2,568 completion Token，不能据此证明长场截断已解决。
- docs/76 HTTP #28 的已选 Agent 候选合法，但问题以“检索这块先放一放”起头，被题面分类器归入冷却中的 RAG。docs/77 已通过去掉该过渡句的离线对照确认原因。本批未修改冷却规则。

## 两场实际进度

使用全新的隔离 PostgreSQL `interview_agent_long_accept_20261109_b`，Alembic 0026。两场使用已发布 JD 的只读副本、合成候选人资料和逐题现场撰写的合成回答。两场冻结 Main 和 Evaluation 2.3.3、P0 和 Harness OFF，目标 20 答，单场测试预算 460,000 Token。正式配置、业务数据与历史采集未写入。

| 会话 | 岗位来源 | 业务结果 |
| --- | --- | --- |
| 1 | 华为 AI 应用工程师，公开 JD 原记录 4 | Main 选择合法 RAG 候选 `C_97295cc97dadd5913110926d`，使用 `fact:2`。题面“为什么选择按排名融合”被识别为求职动机，`topics.validate_topic` 返回 `INTERVIEW_BACKGROUND_LIMIT`。会话 READY、版本 0、无题目或答案。 |
| 2 | 中国移动浙江嘉兴 AI 工程师，公开 JD 原记录 1 | 开场题提交。首答正式评价为 `PARTIALLY_DEMONSTRATED`，保留四项技术缺口。Main 以 FOLLOW_UP 选择 `C_1ffb9bd4fccbbc314e6de229`、`fact:11` 和一个冻结 gap，追问如何判定引用片段是否支持具体结论以及如何处置不支持的答案。Controller 提交第 2 题。随后提交第 2 答时，Evaluation 请求 #5 在预留后未留下服务端响应。 |

首答陈述了向量召回、限定引用范围和低相关时拒答，也承认只检查了引用编号。第二答补充了逐条蕴含校验、实体和数字检查、重检索或拒答策略，只有合成回答和请求记录可确认，不能据此计为正式 Evaluation。完整题面和候选人回答保存在 `transcript/`。

第一场的题面误判是独立的业务阻断。将实际输出交给现有 `classify` 和 `validate_topic` 离线重放，原题得到 BACKGROUND / motivation 和 `INTERVIEW_BACKGROUND_LIMIT`。仅在离线对照中把触发短语替换为“按排名融合的技术依据是什么”，分类变为 FOUNDATION / rag_retrieval，校验通过。建议后续用原题及真实求职动机题建立对照，区分技术取舍与求职动机，保留合法题源、冷却和 Controller 校验。第二场的发送状态未知是本批停止的直接原因。

## 请求用量

| 请求 | 会话与阶段 | 输入 / 输出 / 总 Token | 延迟 | 结果 |
| --- | --- | ---: | ---: | --- |
| 1 | 1 · Main 开场 | 5,860 / 1,346 / 7,206 | 12,288 ms | HTTP 200，业务拒绝题面 |
| 2 | 2 · Main 开场 | 5,749 / 581 / 6,330 | 5,300 ms | 开场题提交 |
| 3 | 2 · 首答 Evaluation | 2,901 / 2,568 / 5,469 | 21,445 ms | 正式 Evaluation 提交 |
| 4 | 2 · 下一题 Main | 8,341 / 943 / 9,284 | 8,080 ms | 合法追问提交 |
| 5 | 2 · 第二答 Evaluation | 未知 | 未知 | 发送预留已落盘，响应未知，批次停止 |

Main 已知 **22,820 Token**，Evaluation 已知 **5,469 Token**，合计已知 HTTP 延迟 47,113 ms。第二场已知用量 21,083 Token，最后确认状态为 IN_PROGRESS、版本 3、活动题 2。批次未到第 5 答，因此没有每 5 答检查点。docs/70 的 251,982 Token 是含 Coach 的 20 答中性估算，本批只有短段实测，无法计算有意义的整场差值。

## 采集、Replay 与数据库核对

新目录 `.runtime/acceptance/p1_long_live_20261109_b/` 含 `captures.jsonl`、`manifest.json`、`index.json`、原子落盘的 `requests/`、业务 `outcomes/` 和 `transcript/`。4 条收到的响应按完整请求指纹各精确命中两次，改动输入均被拒绝，结果见 `replay_check.json`。第 5 条只可重建原请求，缺少模型结果，不能用于输出 Replay。采集不含密钥、认证头或隐藏推理文本。

`db_receipt.json` 是第 4 次请求结束时的隔离库回执。4 笔 ModelAdmission 均为 SETTLED，Token 和采集一致，两场当时无残留会话预留。批次中断后只读重查时，本机 PostgreSQL 5432 拒绝连接。Docker 显示原容器 `interview-agent-platform-postgres-1` 于 22:13:45（上海时间）以 255 退出，原卷挂载关系仍在。该时间晚于第 5 次请求的预留记录，不能据此归因模型请求中断。容器保持现场，第 5 次请求的最终数据库状态和账本结算仍待核对。
