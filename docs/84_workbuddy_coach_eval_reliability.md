# 84 · WorkBuddy 第五轮：长场 Coach 与 Evaluation 稳定性

- 日期：2026-11-11
- 基线：`48045c5`；本轮提交 `70da7e6`（Coach 数值与输入）、`67a39b1`（角色推理与输出预算）、本文件
- 隔离库：`interview_agent_wb_r5_20261111`（新建，Alembic `0026`）
- 真实模型 HTTP：**0**
- 离线回归：**257 passed / 0 failed / 0 skipped**

本轮不重跑 Live，只把第四轮暴露的三个 P0 修到可验收，并用第四轮归档的真实输入与真实响应做离线证明。

---

## 1. Coach 数字校验误判（`70da7e6`）

### 1.1 根因

`resolver._qualitative` 用一条正则拦"任何数字 + 单位"：

```
(?:\d+(?:\.\d+)?|[一二三四五六七八九十百]+)\s*(?:分|次|道题|场面试|轮回答|%|/5|out of 5)
```

中文分支把 `一` 也算进去，于是 `至少一次投递`、`一次调用`、`一条链路` 全部命中。第四轮 Coach 真实输出（请求 47，68,248 Token）唯一命中的就是 `至少一次`，报告因此被判 `COACH_COMPUTED_NUMBER`，全场没有报告。

### 1.2 改法（不是删校验，也不是放行所有中文数字）

数字必须**同时**满足"单位属于统计口径"与"数值由服务端算过"：

| 类别 | 判定 |
|---|---|
| 分 / /5 | 只接受已发布的**维度均值或总分**；单题 1–5 分不构成"总分 5 分"的依据 |
| 次 / 道题 / 场面试 / 轮回答 | 只接受本次提供的计数（已评价、已作答、总题数、追问数、预算题数…） |
| % | 一律拒绝（服务端从不提供百分比） |
| `一次`（中文） | 视为技术惯用语放行；`两次` 及以上必须被统计支持 |
| 分数断言 | `score/average/count is N` 同样按上面的白名单校验 |

拒绝时 `diagnostics` 明确给出 `field_path / rule=FABRICATED_STATISTIC / matched_number / unit / value`，可观测、可追踪。

### 1.3 真实样本验证

用第四轮**原始** Coach 响应（`tests/fixtures/coach_live_18.json`，含 18 条真实评价与真实 statistics）离线重放：

- 之前被拒绝的整份响应现在 27 段全部通过，引用 18 条正式评价，没有一处编造；
- `本场共六轮回答`、`本场总分 5分`、`整体正确率 85%`、`追问了 7 次`、`本场共 9 道题` 仍全部拒绝；
- `技术正确性平均 3.89 分`、`本场共 18 道题`、`本场 18 轮回答里 12 次是追问` 允许（这些数字确实来自 statistics）。

**没有改写任何历史输出或历史失败记录**：第四轮的 `final_interview_reports.id=1` 仍是 `FAILED`，本次只是证明"同样的文本换新规则可以通过"。

## 2. 长场 Coach 输入预算（`70da7e6`）

### 2.1 为什么 200KB 不够

第四轮 18 题的真实信封 **227,061 字节**（input 221,493 + schema/system）。按 section 拆开：

| 来源 | 字节 | 说明 |
|---|---:|---|
| `evaluations` | 130,080 | 其中 `reference_answer` 3,648×18 ≈ 65,664，与 `answer_framework`、`standard_answer` 三份重复；`issues` 与 `factual_or_reasoning_issues` 同源同值 |
| `questions` | 49,591 | `candidate_refs` 1,314×18 ≈ 23,652 + `standard_answer` |
| `snapshot` | 21,167 | 含 interviewer_config、adaptive_blueprint、evidence 全集 |
| `adaptive_review` | 19,671 | targets 里 `missing_mechanisms` 全文 |
| schema + system | 5,568 | 协议本身，不可压 |
| statistics / completion / fingerprint | 917 | 都是有效事实 |

也就是说超预算的绝大部分是**重复材料**，而不是"证据太多"。

### 2.2 有界证据投影 `COACH_EVIDENCE_V1`（`interview_report/evidence.py`）

保留（逐条核对）：Evaluation ID、题目与考察维度（`assessment_topic`）、五维分数、作答原文（≤600 字）、评价结论（≤800 字）、覆盖点与遗漏点（各 ≤4 条，每条 ≤140 字）、事实/推理问题（≤3 条，每条 ≤160 字）、追问方向（≤3 条）、未解决 gap（每个目标 ≤3 条，每条 ≤160 字）、有效题数与结束原因、统计与指纹。

移除（并在 `evidence_projection.omitted` 里逐项登记，不静默）：`reference_answer`、`answer_framework`、重复的 `issues`、`standard_answer`、`candidate_refs`、adaptive blueprint、candidate 证据全集、provider/model 等。

硬约束：投影后仍超过 `max_bytes`（默认 120,000）时抛 `COACH_EVIDENCE_BUDGET`，`diagnostics` 带 `bytes / max_bytes / section_bytes / omitted`；生成本身仍受 `interview_coach_max_input_bytes`（200,000）兜底，超限信息现在也带分节字节。

### 2.3 压缩前后（真实归档输入）

| 场景 | 投影前 | 投影后 input | 信封 | 结论 |
|---|---:|---:|---:|---|
| 18 轮（真实） | 221,493 | 85,047 | 90,615 | 通过（-60.1%） |
| 20 轮（18 + 2 条同构） | — | 94,181 | 99,749 | 通过；+9,134 字节、比例 1.107 |

20 轮只增加约两条材料的成本，不会随轮数复制整场会话。没有把全局上限改成 1MB：`interview_coach_max_input_bytes` 仍是 200,000。

## 3. Evaluation 推理与输出预算（`67a39b1`）

### 3.1 第四轮的实测记录（归档复核）

| 请求 | max_tokens | thinking | completion | reasoning | finish | 内容 |
|---|---:|---|---:|---:|---|---|
| 2 | 3,000 | enabled | 3,000 | 3,000 | length | 空 |
| 9 | 8,000 | enabled | 8,000 | 8,000 | length | 空 |
| 10 | 12,000 | enabled | 11,879 | ~8,300 | stop | 合法 JSON |

结论：推理与 JSON 共用同一个 completion 预算；3,000/8,000 都会被推理吃光。

### 3.2 两处改动

1. **透传**：`_chat_provider` 在 ModelScope 分支原先把 `thinking_type` 丢掉（配置存在但请求里没有）。现在与兼容 Provider 一致地传入；`None` 仍然不发该字段。
2. **输出预算**：仅当角色 `thinking == "enabled"` 时，`max_output_tokens = max(该角色配置, settings.thinking_output_tokens=12000)`。Main 仍是 3,000，禁用思考的角色完全不变——不是"所有 Agent 都调成 12000"。

离线用 MockTransport 抓请求体验证：enabled 角色发出 `thinking={"type":"enabled"}` 且 `max_tokens=12000`；`None` 角色请求体里没有 `thinking` 字段；`finish_reason=length` 仍抛 `model_output_truncated` 且带 usage——截断不会变成评分。

### 3.3 Evaluation 该不该用非思考模式：**待验证，不下结论**

对第四轮 20 条 Evaluation 响应做离线一致性审计（`.runtime/acceptance/wb_full_live_20261111/evaluation_consistency.json`）：

- 20 条中 18 条可解析、2 条截断（截断的两条没有产生任何分数）；
- 五维分数全部在 1–5；**引用逐条是作答原文的子串**（0 例不支持）；观察全部指向当题的 active knowledge；`resolved_gap_ids` 全部落在冻结的题级作用域内（0 例越界）。

但**这批全是 thinking=enabled**，没有同一道题、同一答案的 thinking/非思考配对样本，因此"关掉思考不降质"没有证据，本轮不作此结论。下一批短程 Live 若要做这件事，必须对同一答案成对采样再比较（评分差、引用合法率、gap 一致率）。

## 4. 验证结果

| 项 | 结果 |
|---|---|
| 17 文件离线回归 | **257 passed / 0 failed / 0 skipped**（隔离库 `interview_agent_wb_r5_20261111`，Alembic 0026） |
| 新增专项 | `test_coach_number_guard.py` 17、`test_coach_evidence_budget.py` 6、`test_role_model_parameters.py` 8 |
| 第四轮失败响应回放 | 请求 47（Coach 成功响应）、2 与 9（两次截断）各精确回放 2 次，改一字节即拒绝 |
| 旧行为 | `BUDGET_LIMIT` 旧值仍被接受；已生成报告按 generation key 复用，不因本轮改输入而重算 |
| Harness OFF/ON | 回归内含 20 轮 OFF/ON Replay 与 Coach 链路，未回归 |

## 5. 关于 `.workbuddy/`

开工时只读检查：该目录只有一个 4KB 的 `memory/2026-11-11.md`，是上一轮我按工具侧"工作区记忆"要求写的**摘要笔记**，内容与 `.codebuddy/CODEBUDDY.md` 的轮次记录重复，不含密钥、Cookie、简历、认证头。它不是 Codex 的记忆，也不是项目规范。

处理：**未删除、未提交、未改 `.gitignore`**。为避免出现第二套规范，后续只在其中写指针（"见 CODEBUDDY.md"），不再写架构结论。如果你希望彻底消除，可选两种做法之一（都需要你或 Codex 拍板，我不擅自执行）：把 `.workbuddy/` 加入 `.gitignore`，或在 WorkBuddy 设置里关闭工作区记忆（"Memory / 工作区记忆"开关）。

## 6. 仍需真实模型验证

1. 新的 Coach 输入（`COACH_EVIDENCE_V1`）在真实模型下的报告质量——压缩后少了 standard_answer 与完整 blueprint，需要一次真实短场确认结论仍然具体。
2. `thinking_output_tokens=12000` 在真实 Evaluation 上是否稳定（本轮只验证了参数构造与截断安全）。
3. Evaluation 思考/非思考的配对比较（见 §3.3）。
4. 长场 Coach 是否能在 200,000 上限内稳定完成（本轮离线 90,615 字节，仍有余量但没有真实调用）。

## 7. 下一批短程 Live 的最小方案（未执行）

- 目标：一场 3–4 题即结束的短场（用户主动结束或小预算），跑 Coach + 下一场记忆；
- 上限建议：≤10 次 HTTP、≤120,000 Token、≤20 分钟；
- 必须核对：Coach 请求字节 < 200,000、`thinking` 参数真实出现在请求体、报告 `COMPLETED`、观察只引用本场正式评价；
- 不做：不为了验证而放宽任何校验、不重开多场刷成功率。
