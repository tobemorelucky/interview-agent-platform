# 85 · WorkBuddy 第六轮：18 轮真实评价的长场 Coach Live 验收

- 日期：2026-11-11
- 基线：`069b6d9`
- 真实模型 HTTP：**3**（Coach 1 + Evaluation 探针 2），合计 **52,660 Token**
- 离线回归：**264 passed / 0 failed / 0 skipped**（隔离库 `interview_agent_wb_r6_20261111`，Alembic `0026`）
- 首要目标：**18 条已归档正式 Evaluation → COACH_EVIDENCE_V1 投影 → 真实 Coach → 正式 Report → 下一场 MemoryGuidedPlan**：**达成**
- 次要目标：Evaluation 推理与输出预算在真实请求中生效：**达成**（1 条合法结构化评价）；思考/非思考配对样本各 1 条，**不作质量结论**

---

## 1. 原始 18 轮数据：来源与复制方式

源库 `interview_agent_wb_full_live_20261111`（第四轮真实验收，Alembic `0026`）**全程只读**：本轮只用
`pg_dump` 取快照，未执行任何 `UPDATE/INSERT/DELETE`，未迁移、未重建、未修改 `.runtime/acceptance/wb_full_live_20261111/**`。

```
docker exec pg pg_dump -U interview_agent -Fc interview_agent_wb_full_live_20261111  > source.dump   # 631 KB
docker exec pg createdb  -U interview_agent interview_agent_wb_coach_live2_20261111
docker exec -i pg pg_restore -U interview_agent -d ... --no-owner < source.dump
```

一致性不是"看起来一样"，而是逐表摘要比对（`preflight` 断言 + 收尾复验）：

| 表 | 行数 | md5(内容摘要) 源 = 副本 |
|---|---:|---|
| `interview_evaluations` | 18 | `a856d079…` ✅ |
| `interview_session_questions` | 18 | `86d90eec…` ✅ |
| `interview_sessions` 1–3（id/状态/版本/completion_json） | 3 | ✅ 逐行一致 |
| `interview_context_snapshots` | 3 | ✅ |

副本里唯一新增的会话是 `memory` 步骤为验证下一场所建的 **session 4（READY，零模型调用）**；正式评价仍是 18 条，ID 与正文与源库逐字相同。

**第一次尝试的现场被保留，没有当成成功**：`.runtime/acceptance/wb_coach_attempt1_20261111/`（库
`interview_agent_wb_coach_live_20261111`）记录了 0 次真实 HTTP 的失败，原因见 §8.2。

## 2. 两项离线检查（本轮开工要求）

### 2.1 Coach 数字校验：`一次` 不能无条件放行（**发现缺陷，已修**）

原实现把中文 `一次` 一律当惯用语放行（`ONE_TIME_IDIOM`）。实测：

| 文本 | 修复前 | 修复后（会话计数=18） |
|---|---|---|
| `本场只回答一次` | 放行 ❌ | **拒绝**（`FABRICATED_STATISTIC`, unit=次, value=1.0） |
| `本场一共只答了一次` | 放行 ❌ | **拒绝** |
| `只回答了一次` | 放行 ❌ | **拒绝** |
| `至少一次投递` / `至多一次` / `第一次调用` / `每一次重试` / `再试一次` | 放行 | 放行 ✅ |
| `建议每题只答一次` | 放行 | 放行 ✅（无完成体、无会话作用域，属通用建议） |
| `本场回答 18 次` | 放行 | 放行 ✅（`answered_count=18`） |
| `本场回答 19 次` | — | **拒绝** |
| `整体正确率 85%` / `本场总分 5 分` / `追问了 7 次` | 拒绝 | 拒绝 ✅ |

改法（`interview_report/resolver.py`，最小改动）：

- `SESSION_SCOPE`（本场/这场/该场/全场/本次/整场/总共/一共/合计/累计）出现在 `一次` 之前 8 字内 → 视为统计断言；
- `ONLY_COUNT_VERB`（`只|仅` + `回答|作答|答|说|讲|做|写|用` + `了`）紧邻 `一次` → 视为统计断言；
- 命中后走原有白名单：`1` 必须在服务端提供的计数里，否则拒绝。会话确实只有 1 次有效回答时（`answered_count=1`）该句合法。

第四轮真实被拒的整份 Coach 响应在新规则下仍然全部通过（`tests/test_coach_number_guard.py` 23 passed）。

### 2.2 归档 Evaluation 的 Token 口径

**结论：`12000` 是 `max_tokens`（completion 上限），推理与正文共用这一份预算；
`reasoning_tokens` 由托管端放在 `completion_tokens_details.reasoning_tokens` 返回，且计入 `completion_tokens`。**

| 请求 | max_tokens | thinking | completion | 其中 reasoning | 正文 | finish | 正文长度 |
|---|---:|---|---:|---:|---|---|---|
| 2（第 1 答，首次） | 3,000 | enabled | 3,000 | 3,000 | **0** | length | 0 字符 |
| 9（第 3 答，首次） | 8,000 | enabled | 8,000 | 8,000 | **0** | length | 0 字符 |
| 10（第 3 答，重试） | 12,000 | enabled | 8,196 | 6,880 | 1,316 | stop | 2,699 字符 |
| 46（第 18 答） | 12,000 | enabled | 7,397 | 5,723 | 1,674 | stop | 3,904 字符 |
| 47（Coach，第四轮） | 12,000 | enabled | 8,450 | 6,351 | 2,099 | stop | 5,375 字符 |

因此：`completion_tokens` **不能**当作纯正文 Token；截断判据是 `finish_reason=length` 且正文为空。
`docs/84` §5 的表格用的是推断值（把 reasoning 写成约 8,300、把 completion 写成 11,879），已在该文件追加更正说明。

## 3. Coach 输入：投影、字节与真实 Token

| 项 | 第四轮（旧） | 本轮（COACH_EVIDENCE_V1） |
|---|---:|---:|
| user 内容字节 | 223,769 | **87,342** |
| 完整信封字节（input+schema+system） | 227,061 | **90,634** |
| 输入 Token（真实） | 59,798 | **22,303** |
| 评价条数 | 18 | 18（ID 1–18 全保留） |
| 是否超过 `interview_coach_max_input_bytes=200000` | 是（被拒） | 否 |

分节字节：`evaluations 69,963` / `questions 9,205` / `snapshot 2,114` / `adaptive_review 1,798` /
`evidence_projection 977` / `statistics 534` / `session_completion 249` / `fingerprint 66`。

删减项在真实请求里可见（`input.evidence_projection.omitted`）：`reference_answer`、`answer_framework`、
重复的 `issues`、`standard_answer`、`candidate_refs`、`job_requirement_id`、`adaptive_blueprint`、
`adaptive_policy`、candidate 证据全集、provider/model、以及 `review_missing_mechanisms` 51 条。
`clipped_text_fields=1`（一处自由文本按长度上限截断，未丢条目）。

保留且可核验：18 条正式评价的 ID、题目与考察维度、五维分、作答原文、评价结论、覆盖/遗漏点、
事实与推理问题、追问方向、未解决 gap、有效题数与终止原因。

## 4. Report 状态与证据核验

- `final_interview_reports.id=1`：`COMPLETED`、`is_current=true`、`producer=SUMMARY_COACH_AGENT`、
  `provider=modelscope`、`model=deepseek-ai/DeepSeek-V4.1-Flash-1017`、`attempt_version=5`。
  同一个报告行从第四轮的 4 次失败（COACH_INPUT_BUDGET / COACH_COMPUTED_NUMBER / CAPTURE_GUARD / MODEL_USER_QUOTA）
  延续到本轮成功，历史失败记录一条未改。
- 结构化质量：JSON 合法，`strengths 5 / weaknesses 6 / recurring_issues 3 / job_alignment 5 /
  recommended_actions 5 / practice_plan 6 / memory_proposals 3`。
- 引用合法性：14 条表现观察的 `supporting_evaluation_ids` 全部落在 1–18，重复问题各引 3–4 条不同评价
  （`RECURRING_SUPPORT_REQUIRED` 在服务端强制）。
- **人工证据核对（不只看 Schema）**：
  - 优势「在追问中指出并修正机制性错误，随后把 outbox 的发送所有权、状态条件回写和消费侧幂等落点讲清楚」→ 引 `[3,12]`；
    评价 3 正是"租约 + fencing 发送所有权"，评价 12 是"纠正 Kafka exactly-once 越界"那一次，语义对得上。
  - 优势「检索失败能分级处置」→ 引 `[6,13]`：评价 6 是引用支持度改写，13 是三分支降级，正确。
  - 弱项「曾把 Kafka 事务型语义扩展到外部数据库」→ 引 `[11,12]`：正是第 11 答说错、第 12 答纠正的那两轮，不是编造。
  - 弱项「条件自增只在上限边界挡并发」→ 引 `[17,18]`：第 17 答的方案有漏洞、第 18 答改为 PENDING→PROCESSING 条件更新，吻合。
  - 弱项「Redis 只讲先更新库再删缓存」→ 引 `[1,10]`；「引用校验最初把相似度当支持度」→ 引 `[5,6]`；「阈值维护骨架但缺离线校准闭环」→ 引 `[14,15]`。
  - 全文没有出现百分比、总分、题量之外的数字，也没有出现"X 年经验""某公司"等候选人事实；
    `COACH_COMPUTED_NUMBER` 一次都没有触发。

## 5. 下一场 MemoryGuidedPlan

`memory` 步骤通过正式入口 `MockInterviewService.create` 建 session 4（零模型调用）：

- 计划 3 条，`observation_refs` = `evaluation:10…evaluation:18`（9 条），
- 全部 `evaluation:<id>` 且 `id ∈` 本场 18 条正式评价；无编造引用、无跨会话引用。

## 6. Evaluation 定向探针（真实调用）

同一份归档输入（第四轮请求 9：第 3 答，曾被 8,000 上限整段吃光）原样重发，只改 `max_tokens` 与 `thinking`：

| 探针 | thinking | max_tokens | 结果 | 分数（技术/覆盖/深度/相关/表达） | 引用 | 延迟 | Token |
|---|---|---|---:|---|---|---|---:|---:|
| A | enabled | 12,000 | **合法 JSON** | 5/5/5/5/5 | 3 条，全部命中原文 | 69.1 s | 15,850 |
| B | disabled | 12,000 | **Schema 不合法**：`answer_quotes` 4 条 > 上限 3 | —（未产生评分） | — | 9.5 s | 6,149 |
| 归档基线（第三轮真实评价 id=3） | enabled | 12,000 | 合法 | 5/5/5/5/5 | 3 条 | — | — |

- **推理配置生效**：请求体确认 `thinking={"type":"enabled"}` 与 `max_tokens=12000`，`finish_reason=stop`，正文完整；
  之前"8,000 被推理吃光"的问题在同一输入上不再出现。
- **与历史评价大体一致**：五维分与归档评价完全相同，解开的 gap 也相同（`gap_9cd2fe85…`、`gap_24bcedb1…`）。
- **非思考模式这一条样本不合法**：4 条引用超限，服务端会走校验/修复而**不会**落任何分数——失败是安全的，
  但这一个样本不足以说明"disabled 更差"，只能说该样本上不安全。**不作质量结论，也不改生产默认。**
- 探针只写 `model_admissions` 与审计，**没有**写入 `interview_evaluations`：副本评价仍是 18 条，源库未动。

## 7. 全部真实 HTTP

| # | 路由 | thinking | max_tokens | 输入 Token | 输出 Token | 其中推导 | 总 Token | 延迟 | finish |
|---|---|---|---:|---:|---:|---:|---:|---:|---|
| 1 | interview_coach | enabled | 12,000 | 22,303 | 8,358 | 6,543 | 30,661 | 46.8 s | stop |
| 2 | answer_evaluation（A） | enabled | 12,000 | 4,731 | 11,119 | 9,784 | 15,850 | 69.1 s | stop |
| 3 | answer_evaluation（B） | disabled | 12,000 | 4,706 | 1,443 | 无 | 6,149 | 9.5 s | stop |

合计 **3 次 HTTP / 52,660 Token**，上限为 8 次 / 220,000 / 30 分钟；HTTP 时间窗约 2 分 5 秒，全批（含预检与人工核对）约 30 分钟级但未超时。
0 次 Repair、0 次重试、0 次回退、0 次非 200。

## 8. 失败与异常（全部保留现场）

### 8.1 第一次尝试的真实计费口径

第一次尝试（`wb_coach_attempt1_20261111`）0 次 HTTP，但库里留下一条
`model_admissions(interview_coach, status=UNKNOWN, charged=105,103)`——因为 `governed_call` 在进入
`operation()` 时已把 `sent=True`，异常又没带 usage，只能按预留保守计费。这正是 `docs/79/80` 记录的
"生产入口缺跨进程发送围栏"的又一个证据，只是出现在验收夹具里。它只影响该副本库的配额计数，不产生真实费用。

### 8.2 采集作用域漏字段把一次真实错误藏成了 `model_request_failed`

第一次尝试的 `execute()` 传给采集器的 scope 少了 `category`，`capture.request` 取 `scope["category"]` 抛
`KeyError`；而 `CompatibleModelProvider._generate` 的兜底分支把 `KeyError` 与 httpx 错误一起映射成
`model_request_failed`，于是报告里只看到"供应商请求失败"，实际请求**根本没有发出**（`requests/` 目录都不存在）。

- 夹具侧已修（补 `category`，并在失败时打印 traceback）；
- 产品侧**未改**错误映射：`KeyError` 也可能是真实的响应结构异常（例如缺 `choices`），改成别的码会误导。
  记录为待评估项：hook / 编程错误的分类不该与网络错误共用同一个码。

## 9. Capture 与 Replay 验收

- `requests/0001–0003.json`：完整请求体、响应正文、`usage`、`finish_reason`、延迟、`request_sha256`；
  密钥在写入前替换，未见 `<think>` 隐藏推理。
- 3 条响应各**精确回放 2 次**；改一个字节即 `REPLAY_INPUT_HASH_MISMATCH`。
- 账本 `known = charged = 52,660`，无未结算准入。
- `manifest`：`captures=3, uncertain=0, categories={COACH:1, EVAL:2}`，`state.stop=COLLECTION_COMPLETE`。

## 10. 未解决 / 下一步

1. **Coach 的思考模式是配置决策**：生产默认 `interview_coach_thinking=disabled` + 6,000 输出，
   本轮验收实例用 `enabled` + 12,000（延续第四轮的组合并已验证请求体）。默认值下长场 Coach 是否稳定，未验证。
2. **Evaluation 的思考模式仍未定**：只有 1 对配对样本，且 disabled 那条 Schema 不合法；要比较质量需要成对采样。
3. **Coach 输入上限仍是 200,000 字节**：18 轮投影 90,634 字节合格，但 30 轮左右的规模需要重新测量（投影是线性增长）。
4. **`COACH_COMPUTED_NUMBER` 现在更严**：会话作用域内 `一次` 需要计数支持；若真实报告出现"本场只谈了一次 X"这类
   表达会被拒，需要观察是否误伤（本轮真实报告未出现该表达）。
5. **发送围栏 P1 仍在**：本轮再次出现 UNKNOWN 保守计费（本次 105,103，仅在副本库）。
6. **`KeyError`→`model_request_failed` 的映射**：建议单独评估，不要与网络错误混用。
7. 20 轮完整 Live 仍未跑（第 19、20 题从未提问）；本批不重复证明同一件事，成功后即封存。
