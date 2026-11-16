# R36 Evidence 分块修复、蔚来有限重试、JD 待审修订机制

基线 `e639bec`。本轮**真实模型 2 次 HTTP / 6,075 token**（授权 2 / 8,000，未超、无 Repair、无回退），全部发生在蔚来重试；其余阶段 HTTP=0。

三句话总结：

1. **F1 已落地并通过回归**——切点改按原文行边界，正文、offset、Hash 一字未动，样本内"被切行 11→0"；
2. **蔚来重试仍失败，但这次可以确定问题不在分块**——同一份文档 38/38 行现在都能用单块证明，失败只能来自模型输出的语句本身不是原文子串；
3. **M1 的校验/幂等/审计部分已实现并离线验证通过，但写库被一个既有唯一约束挡住**，需要一次迁移（未做，按指令停止并报告）。

---

## 一、F1：改了什么，没改什么

### 1.1 改动（`research/evidence.py`，+44 / −3 行）

新增纯函数 `chunk_bounds(text, span_chars, limit)`，`EvidenceCatalog.__init__` 改为按它给出的区间取切片：

```
hard = position + span_chars
若 hard 之后还有正文：
    上一换行距 hard 的损失 ≤ 300  → 回退到该换行（切点向前挪一点）
    否则下一换行距 position ≤ 900 → 前探到该换行（切点向后挪一点）
    否则                          → 硬切（单行本身超过 900 字，无法整行保留）
```

一个副作用顺手修掉了：旧实现按 `range(0, …, span_chars)` 递增 `start`，当预算在中间截断时，后续 `start` 仍 +600，会**跳过一段正文**；新实现按实际切片长度前进，预算截断只发生在最后一刀。

### 1.2 明确没有改的东西

| 不变式 | 状态 |
|---|---|
| 原始 JD 正文、Snapshot、正文 SHA-256 | **未动**（切片仍是 `content_text[start:end]`） |
| offset 语义 | 未动（仍为 unicode codepoint，`text[start:end] == excerpt` 恒成立） |
| `supports_literal` | **一个字没改**（仍是单块连续文本的子串判定，相邻块可拼接、跨缺口不可） |
| attribute 逐字与身份校验 | 未改 |
| 已发布 JD 的历史证据 | 未改（存的是 offset + excerpt，不依赖 chunk id） |
| 历史模型结果 | 未改（`experience_agent_step_runs` 只读） |

唯一代价：`evidence_key` 是 `hash(snapshot,start,end,text)`，切点变了 → **新导入**的证据 id 会变。已存证据不受影响：`publish_jd` 用 offset+excerpt 校验，`build_context` 用 statement+excerpt，全仓库没有任何地方把旧 `evidence_key` 拿回新目录里重新解析（已核查 `evidence_key` 的读写点）。

---

## 二、回归结果

### 2.1 新增与更新的测试

| 文件 | 用例 | 结果 |
|---|---|---|
| `tests/test_r36_evidence_chunking.py` | 16（10 类要求逐条对应） | **16 passed** |
| `tests/test_r35_span_boundary_defect.py`（改写为"修复前/修复后"） | 5 | **5 passed** |

10 类要求 → 用例映射：

1. 跨 600 边界的完整句 → `test_sentence_that_crossed_the_old_cut_is_now_inside_one_chunk`
2. 普通短句 → `test_short_sentence_sits_in_one_chunk`
3. 超 600 字单行 → `test_single_line_longer_than_span_is_still_chunked`、`test_a_line_far_longer_than_the_slack_is_cut_but_still_exact`
4. CRLF / LF → `test_crlf_and_lf_keep_exact_offsets`（参数化 2 例）
5. 中文 / 英文 / 混合标点 → `test_cjk_english_and_mixed_punctuation_are_supported_verbatim`
6. 原文空格与换行 → `test_whitespace_and_blank_lines_are_not_rewritten`
7. 跨块引用 → `test_cross_chunk_reference_is_allowed_but_a_gap_is_not`
8. offset 与正文一致 → `test_offsets_are_monotonic_non_overlapping_and_exact`、`test_multiple_snapshots_share_the_budget_without_overlapping_ids`
9. 已发布麒麟/沐瞳回归 → `test_published_kylin_and_moonton_evidence_is_byte_identical`
10. 异常输入/超长文本 → `test_empty_and_whitespace_only_bodies_produce_no_spans`、`test_invalid_configuration_is_refused`、`test_oversized_body_respects_the_budget_and_terminates`

### 2.2 全量回归

| 范围 | 结果 |
|---|---|
| 全部测试 | **799 passed / 407 skipped / 1 failed** |
| 失败项 | `test_runtime_target_safety.py::test_launcher_verify_accepts_an_e2e_name_without_touching_a_database` |
| 该失败的性质 | **环境相关，非本轮回归**：启动器只在 shell 导出了 `DATABASE_URL` 且与参数不同时才打印"命令行 --database 优先"；本 shell 未导出。**导出该变量后该文件 20/20 通过**（已实测） |
| 证据/研究相关子集 | 207 passed / 79 skipped / 0 failed |

### 2.3 样本内的 11 → 0

`r52_f1_before_after.py`（旧规则副本 vs 上线后的 `EvidenceCatalog`，同一批 7 份真实正文）：

| JD | 正文 | 被切行（前） | 被切行（后） | 切片长度（后） |
|---|---|---|---|---|
| NIO_AIA_025 | 1,462 | 2 | **0** | 569 / 598 / 295 |
| KYLINOS_SEC_5480b909 | 920 | 1 | **0** | 340 / 580 |
| KYLINOS_EMB_cd3ac686 | 845 | 1 | **0** | 297 / 548 |
| MOONTON_QA_008 | 1,871 | 3 | **0** | 101 / 585 / 588 / 597 |
| MOONTON_PM_013 | 899 | 1 | **0** | 331 / 568 |
| NIO_CV_023 | 823 | 1 | **0** | 233 / 590 |
| NIO_SUPPLY_024 | 1,699 | 2 | **0** | 511 / 593 / 595 |
| **合计** | | **11 / 206 行** | **0 / 206 行** | |

（该表的长度按投影后的 LF 文件计算；蔚来重试时 Snapshot 里是 CRLF 版，切片为 585 / 576 / 339，结论相同。）

**这只说明这 206 行不再被切，不等于"未来所有证据假阴性归零"。** 仍存在的通道：单行本身超过 900 字（会被硬切，必须引相邻两块）；以及模型把两条不连续的原文拼成一句（与分块无关，属改写）。

---

## 三、原文 / offset / Hash 一致性

`r51_f1_published_regression.py`（只读，逐条回查沙箱全部 130 条证据）：

| 检查 | 结果 |
|---|---|
| 证据条数 | 130 |
| `text[start:end] == excerpt` 失败数 | **0** |
| Snapshot 哈希与 `content_hash` 不一致 | **0**（全部 8 个快照） |
| 7 个 JD 版本正文与 `raw_text` 逐字节相同 | 7/7 |
| 重建目录后每条切片 offset 精确 | 7/7 版本 `offsets_exact=True` |
| Requirement 逐字分类（与 R34 对比） | **未变**：#1 10V；#2 11V+1F；#3 7V+2F+8H；#4 5V+1P；#5 10V；#6 3V+7F+3P；#7 9V |
| 行切数（旧规则 → F1） | **6 → 0** |

结论：**原文、offset、Hash 三者一致；历史证据没有被移动或改写。**

---

## 四、蔚来有限重试：执行了，仍失败，但结论变了

### 4.1 前置（全部零 HTTP）

| 门槛 | 结果 |
|---|---|
| F1 自检：第 17 行能否被单块证明 | **能**（切片 585 / 576 / 339，行 585–649 完整落在第 2 块） |
| 冻结输入未变 | 正文 `aef49bb3…8055`、URL、职位 ID `A25338A` 与 R34 记录**逐项一致** |
| 修订任务复用 | `prepare_identity_revision` 两次都返回 task **#4**（`reused=true`），0 HTTP |
| 身份字面 | `校招-车端 AI Agent 与应用生态系统架构工程师`（页面字面，未改） |

### 4.2 执行与结果

| 项 | 值 |
|---|---|
| 方式 | 同一 task #4，`agent.run(force=True)`（服务里唯一允许 FAILED 手工 JD 复跑的口径） |
| HTTP | **2**（`job_jd` 3,230 + `quality` 2,845） |
| Token | **6,075**（上限 8,000） |
| Repair | **0** |
| 结果 | `FAILED` — `EvidenceValidationError: Job metadata must be literal and cite exact evidence` |
| 残留实体 | **0**（company / job / jd_version 均未增加） |
| task#2 | 一字未改（仍 FAILED，错误仍是 R31 的身份不匹配） |

### 4.3 这一轮真正学到的东西

R34 失败时，quality 步骤的 `risk_flags` 里有一条：

> `evidence_fragmentation: JD证据文本在句子中间被截断（如"参与设计车端 AI Agent""对'A'""I 如何成为…"）`

本轮同样的质量步骤**不再出现这条**，取而代之的是身份/学历/UNKNOWN 类型等业务风险。也就是说 **F1 的确消除了证据被截断的现象**。

而错误码没变。同一份文档现在 38/38 行都能用单块证明，所以这一条是硬结论：

> **模型输出的某条元数据语句本身不是原文子串**（改写、把小节标题并进句子、或把两条不相邻的原文拼成一句），与分块无关。

具体是哪一条、写了什么——**仍然查不出来**，因为 `output_json` 按设计不存抽取文本（R35 已核实）。按授权"重复发生元数据逐字验证失败立即停止"，**没有第三次调用**。

### 4.4 建议

这条路径继续投入的边际收益很低：身份问题已修好，剩下的是抽取质量，而 `job_jd@1.0.1` 的 prompt **全文没有一句关于 attributes 的约束**（web 版 1.1.0 才有"exact original statements; unknown stays absent"）。若将来要救这个岗位，正确做法是**升一个 prompt 版本**（1.0.2：加 attributes 逐字/缺失即省略的约束），而不是继续重试同一 prompt。本轮不动 prompt（未获授权）。

---

## 五、M1：实现到写库前一步，被既有约束阻断

### 5.1 已实现（`public_career/corrections.py`，新增模块，未改任何既有服务）

`JdCorrectionService.plan()` / `.apply(dry_run=...)`，10 条要求逐条对照：

| # | 要求 | 落实方式 |
|---|---|---|
| 1 | 原 JD 版本不可变 | 只读前驱；新版本另起 `version_number`；前驱状态/文本/要求一律不动 |
| 2 | 原始模型输出不可覆盖 | 前驱 requirement 保留模型原句；修订句写在新版本上 |
| 3 | 原始 Claim/Evidence 不可伪造 | 新 claim 的 evidence 行**照抄**前驱 claim 的 `excerpt` / `offset` / `evidence_key` |
| 4 | 修订只能来自原文确有的语句 | 新语句必须通过 `literal_within()`（与 `supports_literal` 同语义） |
| 5 | 修订前后都做逐字验证 | `before_supported` / `after_supported` 双记录；after 不为 True 直接 422 |
| 6 | 保存 before/after、操作者、原因、证据、时间 | `audit_logs`（`public_career.jd.correction`）+ 新版本 `metadata_json.correction` |
| 7 | 生成受控新版本 | 同 job、同快照、`version_number = max+1`、`WAITING_REVIEW` |
| 8 | 新版本重新进入审核 | 明确写 `WAITING_REVIEW`，**代码里没有 publish 调用** |
| 9 | 旧版本与失败记录可追溯 | `correction_of` 指向前驱；前驱 FAILED/待审记录原样 |
| 10 | 重复修订幂等 | 指纹 = `sha256([jd_id, 排序后的 (requirement_id, 归一化新句)])`，命中即返回同一后继 |

### 5.2 两个实例的离线验证（`r54_correction_rehearsal.py`）

| 实例 | 条 | 模型写的 | 还原成原文 | before 逐字 | after 逐字 |
|---|---|---|---|---|---|
| 面壁 #4 | r40 | `2027届应届毕业生，计算机…` | `计算机、人工智能或相关专业的2027届应届毕业生，本科及以上学历` | **False** | **True** |
| 蔚来 #6 | r57 | `…包括算子/编译优化…` | `…包括但不限于:算子/编译优化…等` | **False** | **True** |
| 蔚来 #6 | r58 | `…包括KV Cache优化…` | `…推理优化技术，KV Cache优化、模型量化(Quantization)、剪枝(Pruning)，跨机…` | **False** | **True** |
| 蔚来 #6 | r63 | `…主流深度学习框架（如…` | `…主流的深度学习框架(如 PyTorch、TensorFlow)…` | **False** | **True** |

负向探针：`熟悉至少一种主流的深度学习框架，了解其编译器后端实现与算子融合`（听起来合理、原文没有）→ **`CORRECTION_NOT_LITERAL` 拒绝**，发生在任何写入之前。

### 5.3 阻断点：一个数据库唯一约束

整个写入（后继版本 + 新 claim + evidence + audit）在 savepoint 里完整执行，数据库拒绝：

```
blocked_by = SCHEMA_UNIQUE_FINGERPRINT
uq_job_description_versions_job_fingerprint = UNIQUE (job_id, content_fingerprint)
```

原因很直白：`content_fingerprint` 由 `raw_text` 派生，而修订**不允许改正文**，所以后继版本与前驱版本指纹必然相同，必然撞唯一键。

回滚后实测：`versions/requirements/claims/evidence/audit = 0/0/0/0/0` 行写入，`before == after`。

### 5.4 结论与最小迁移建议（未执行）

**M1 的"受控新版本"在当前 schema 下无法落库，必须迁移。** 按指令停在这里。

建议的迁移（`0030`，只动一个索引，不掉数据）：

```sql
-- 原意是"同一正文不要重复发布"，改成只对已发布版本生效即可，草稿可并存
DROP INDEX uq_job_description_versions_job_fingerprint;
CREATE UNIQUE INDEX uq_job_description_versions_job_fingerprint
    ON job_description_versions (job_id, content_fingerprint)
    WHERE publication_status = 'PUBLISHED';
```

这样：同一正文仍然只能发布一次（原意保留），修订草稿可以与已发布版本并存，`published_jobs()` 仍取最大已发布版本号，语义不变。**本轮未执行任何 DDL。**

因此：**面壁 #4、蔚来 #6 仍停在 `WAITING_REVIEW`，未发布**，与你的"不批准自动发布"一致。

---

## 六、下一批：6 份最终清单

按你的取舍重排：保留 4 份软件岗，把产品经理与供应链换成 **3.1 节的软件/Agent 岗**，仍只用已核验的 52 份真实 JD（无合成、无改写、无补造）。

| # | jd_key | 企业 | 页面字面岗位名 | 方向 | 正文 | 小节 |
|---|---|---|---|---|---|---|
| 1 | `KYLINOS_SEC_5480b909` | 麒麟软件 | `「2027校招」安全研发工程师` | 安全研发 | 920 | 工作职责 + 任职资格 |
| 2 | `KYLINOS_EMB_cd3ac686` | 麒麟软件 | `「2027校招」嵌入式研发工程师` | 嵌入式/RTOS | 845 | 工作职责 + 任职资格 |
| 3 | `MOONTON_QA_008` | 沐瞳科技 | `【2027届秋招】AI测试工程师（web平台）` | 测试/QA | 1,871 | 岗位职责 |
| 4 | `NIO_CV_023` | 蔚来 | `校招-视觉算法工程师` | 视觉算法 | 823 | 工作职责 |
| 5 | `NIO_POSTTRAIN_018` | 蔚来 | `校招-座舱大模型后训练/Agentic 算法工程师` | LLM 后训练 | 1,025 | **无** |
| 6 | `MOONTON_UNITY_012` | 沐瞳科技 | `【2027届秋招】Unity客户端开发工程师（活动）-MOBA` | 游戏客户端 | 1,315 | **无** |

- 替换理由：`NIO_SUPPLY_024`（供应链算法）与 `MOONTON_PM_013`（AI 产品经理）虽能测泛化，但不是产品主要服务的岗位；换成 018（后训练/Agentic 算法）与 012（Unity 客户端工程）后，6 份**全部是研发岗**，方向覆盖 安全 / 嵌入式 / 测试 / CV / LLM 后训练 / 游戏客户端，与已发布 5 份、待审 2 份无一重复。
- 企业分布 2/2/2（麒麟、沐瞳、蔚来）——**52 份语料里只有这 3 家有可用官方 DOM**（Unity、面壁已用；华为/移动×2/工行 4 份无 DOM 或为社区转载），因此"再增加企业多样性"在本语料内已到上限，不靠补造凑数。
- 校验（零 HTTP，全部硬断言）：语料哈希一致 6/6、DOM 存在 6/6、页面字面标题 6/6、稳定 job_id 6/6、与库内 7 条不重复、投影只加页面自身两行、**新增小节标题 0 个**。
- **风险标注**：018 与 012 没有小节标题（R34 规律：无小节的 JD 模型更容易"整理"语句）→ 这两份审核时必须逐条查逐字。
- 替补：`NIO_HARNESS_017`（座舱 Agent Harness，831 字）、`MOONTON_CLIENT_011`（AI 客户端-MOBA，834 字）。

**预算（按 R34/R36 实测拟合 token ≈ 2,500 + 2.2 × 字数）**

| JD | 字数 | 预计 token |
|---|---|---|
| 麒麟安全 | 920 | 4,500 |
| 麒麟嵌入式 | 845 | 4,400 |
| 沐瞳 AI 测试 | 1,871 | 6,600 |
| 蔚来视觉 | 823 | 4,300 |
| 蔚来后训练 | 1,025 | 4,800 |
| 沐瞳 Unity | 1,315 | 5,400 |
| **合计** | | **≈30,000** |

- 正常：**12 HTTP / 30,000 token**；硬上限：**16 HTTP / 45,000 token**
- 停止条件：连续 2 份 FAILED 整批停；UNKNOWN/配额/账本异常立即停；证据类失败不 Repair，schema 类最多 1 次
- **本轮不执行**（未获批准）

---

## 七、沙箱当前状态

| 项 | 值 |
|---|---|
| 企业 / 岗位 / JD 版本 | 5 / 7 / **7**（本轮未新增版本） |
| PUBLISHED | **5**（麒麟 AI研发、沐瞳 Agent、Unity、沐瞳 后端、麒麟 内核） |
| WAITING_REVIEW | **2**（面壁 #4、蔚来推理 #6） |
| FAILED 任务 | **2**（#2 身份不匹配 08:38、#4 元数据非逐字 12:01） |
| Requirement / Claim / Evidence | 77 / 125 / 130（未变） |
| 审计日志 | 6 行（未新增：本轮没有发布，也没有产生修订版本） |
| 模型账本 | **20 行 / 52,228 token**（18→20 行，+6,075）；单一模型 `deepseek-ai/DeepSeek-V4.1-Flash-1017`；全部 `SETTLED` |
| `published_jobs()` | 5 行（#4/#6 正确排除） |

---

## 八、Git 与受保护资产

| 项 | 状态 |
|---|---|
| HEAD | `e639bec`（本轮改动尚未提交，见下） |
| 源码改动 | `research/evidence.py`（F1）、新增 `public_career/corrections.py`（M1 只写不提交写入） |
| 新增脚本 | `r51_f1_published_regression.py`、`r52_f1_before_after.py`、`r53_nio_retry.py`、`r54_correction_rehearsal.py`、`r55_build_batch4.py` |
| 新增测试 | `test_r36_evidence_chunking.py`（16）、`test_r36_correction_contract.py`（11）、`test_r35_span_boundary_defect.py`（改写，5） |
| 正式库 | 5 users / 38 简历 / 19 场 / 41 评价 / 5 报告 / 4 JD / 4 企业 —— **未变** |
| 数据库总数 | 250（未新建） |
| MinIO | 生产桶 `interview-agent` **48 对象**；`interview-agent-r31-jd` 0 对象 —— 未变 |
| Evaluation 2.3.4 / Coach Harness / L3-L4 | 未触碰 |
| 52 份原始 JD、历史面试、Memory、备份 | 未动（48 份抓取正文哈希复核仍一致） |
| 删除 / 归档 / 清库 / push / reset / clean | 均未执行 |

---

## 九、需要你决定

1. **迁移 `0030` 批不批？** 只改一个唯一索引（加 `WHERE publication_status='PUBLISHED'`），是 M1 落库的唯一前提；不批则两份待审稿只能继续挂着。
2. **蔚来这个岗位是否就此打住？** 证据表明剩下的问题是 `job_jd@1.0.1` 没有约束 attributes 的措辞。要救只能升 prompt 版本（1.0.2），那是另一件事。
3. **下一批 6 份是否开跑？** 预算 12 HTTP / 30,000（上限 16 / 45,000）。
