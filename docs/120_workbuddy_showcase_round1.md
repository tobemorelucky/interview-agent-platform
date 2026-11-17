# SHOWCASE-R1：展示版建设第一轮（实测报告）

> 口径：**【事实】**= 本轮命令 / 数据库 / 文件实测；**【推断】**= 基于事实的判断；**【建议】**= 后续方案，非现状。
> 本轮 Git HEAD 始终为 `1d87a0b`，未提交、未 push、未改 `src`（只新增脚本并参数化了一个既有审核脚本）。
> 真实模型 HTTP 实测 **12 次 / 29,230 token**（全部来自第二部分 JD 导入）；第三、四部分模型 HTTP **0**。
> 第五部分（首场真实模型面试）**未执行**，理由见 §5。

---

## 0. 一句话结论

52 份原始 JD 完成全量清单化；6 份零 HTTP 复核后真实导入得到 **5 COMPLETED / 1 FAILED**，逐条严格审核后 **4 份发布、1 份扣留（沐瞳 Unity，8 条语句被加了原文没有的"（加分项）"）**；展示沙箱现有 **9 份 PUBLISHED**；59 道面经题完成零模型核查，结论是 **0 道可直接入库、33 道仅作参考、26 道需回原帖取原文、0 道拒绝**；三场 Replay 全流程（A Agent 岗 / B 后端岗 / C 跨场次 WEAKNESS）在真实业务 Service + 真实 PostgreSQL 上 **全部通过且 0 条模型账本**。前端页面截图与用户交互验收因运行服务被取消而**未完成**，第五部分因此按前置条件阻断，未消耗任何额度。

---

## 1. 第一部分：仓库与环境核对（零 HTTP）

| 检查项 | 实测结果 |
|---|---|
| Git | HEAD `1d87a0b`，`master...origin/master` 同步；工作区只有未跟踪数据产物；本轮无 src 改动 |
| Alembic（代码 head） | `0029`（`0027_user_job_targets` / `0028_archive_lifecycle` / `0029_purge_requests`） |
| 正式库 `interview_agent` | `0025`，64 表，**落后代码 4 个迁移**；users 5 / resumes 38 / sessions 19 / evaluations 41 / reports 5 / JD 4 / admissions 0。本轮**未迁移、未写入** |
| JD 沙箱 `...wb_jd_r31_20261116` | `0029`，68 表；起始 5 PUBLISHED + 2 WAITING_REVIEW + 2 FAILED 任务，账本 20 行 / 52,228 token |
| 其它库 | 共 247 个 `interview_agent*` 库；历史 Live / acceptance / 封存库均未触碰 |
| MinIO | 生产桶 `interview-agent` 48 对象；`interview-agent-r31-jd` 0；其余桶数量未变 |
| Provider / 路由 | `LLM_PROVIDER=modelscope`；`public_research_extract` 与 `public_research_quality` 均解析为 `modelscope/deepseek-ai/DeepSeek-V4.1-Flash-1017`，`governed=True`，fallback 为空 |
| 治理准入 | 导入脚本显式校验 `DATABASE_URL`、`MODEL_LEDGER_DATABASE`、provider/model/governed/fallback；每份 JD 前后做账本增量测量 |
| API / 前端可启动 | 展示 API 与 Vite dev server 曾启动并返回 HTTP 200（`/api/v1/health`、`/`），随后按用户要求取消；**未重启** |

**未能量化的一项**：远端 ModelScope 免费额度的剩余量没有本地可读取的接口；本轮通过 12 次全部 `SETTLED`、无 `QUOTA/UNKNOWN` 反证"至少还能支撑 12 次"。第五部分因此仍要求逐次治理检查，不能假设额度充足。

---

## 2. 第二部分：52 份 JD 与 6 份真实导入

### 2.1 52 份统一清单

产物：`data/r56_showcase/inventory_52.json`（零 HTTP、哈希全部现场重算）。

- **52 条记录**：48 份官方页面抓取（`OFFICIAL_CAND_*` / `KYLINOS_*` / 企业 POS 编号）+ 4 份人工策展（`MANUAL_JD01` 华为官方页 1 份；`MANUAL_JD02-04` 为社区转载 3 份）。
- **技术方向分类**：技术研发 **40**、其他真实岗位 **8**（赛事商务、内饰空间体验经理、公共事务、开发工程师等非研发措辞）、无法从标题归类 **4**（含工行"科技菁英"，保留为 UNCLASSIFIED，**不冒充软件开发岗**）。
- **完整性**：48 份有 DOM + 正文且哈希一致；4 份策展件正文在 `data/manual_sources/2026-10-05/`，无 DOM（如实标注，不用空壳补齐）。
- **入库状态**：与沙箱 7 个历史版本按 URL + (公司, 标题) 双路对齐后，5 PUBLISHED / 2 WAITING_REVIEW；其余未导入。
- **技能推荐**：只对已发布记录读取，保留 `NO_APPLICABLE_SKILL`，未把任何一条强制升为 `RECOMMENDED`。

### 2.2 6 份下一批的逐份零 HTTP 复核

产物：`data/r56_showcase/inventory_52.json → batch4_recheck`，以及 `data/r57_batch4_run/batch4_report.json`（dry-run 部分）。

| JD | 正文 Hash | 正文=原语料 | DOM Hash | 页面字面标题 | job_id 在 URL | F1 后仍被切行 |
|---|---|---|---|---|---|---|
| 麒麟安全 `5480b909` | ✅ | ✅ | ✅ | ✅ | ✅ | 0 |
| 麒麟嵌入式 `cd3ac686` | ✅ | ✅ | ✅ | ✅ | ✅ | 0 |
| 沐瞳 AI测试 `008` | ✅ | ✅ | ✅ | ✅ | ✅ | 0 |
| 蔚来视觉 `023` | ✅ | ✅ | ✅ | ✅ | ✅ | 0 |
| 蔚来后训练 `018` | ✅ | ✅ | ✅ | ✅ | ✅ | 0 |
| 沐瞳 Unity `012` | ✅ | ✅ | ✅ | ✅ | ✅ | 0 |

补充：与库内 7 个版本的 `content_fingerprint` 和规范化标题逐条比对，**0 重复**；6 个 `prepare()` 调用全部返回 `READY_FOR_EXTRACTION` 且账本零增量（0 HTTP）。
注意两个哈希口径：正文/语料以 **LF 归一化文本**计算，DOM 以 `read_text(errors="ignore")` 的解码文本计算；按字节重算会产生假不匹配，本轮已按原口径复核通过。

### 2.3 真实导入结果（12 HTTP / 29,230 token）

命令：`r57_run_batch4.py`（预算 12 正常 / 16 硬 / 30K 正常 / 45K 硬，连续 2 FAILED 停止）。

| JD | 结果 | HTTP | Token | jd_version |
|---|---|---:|---:|---:|
| 麒麟安全 | COMPLETED | 2 | 3,746 | 16 |
| 麒麟嵌入式 | COMPLETED | 2 | 4,761 | 17 |
| 沐瞳 AI测试 | COMPLETED | 2 | 6,359 | 18 |
| 蔚来视觉 | **FAILED** | 2 | 4,487 | — |
| 蔚来后训练 | COMPLETED | 2 | 3,556 | 19 |
| 沐瞳 Unity | COMPLETED | 2 | 6,321 | 20 |
| **合计** | 5/6 | **12** | **29,230** | — |

- 账本 20 → 32 行、52,228 → **81,458** token，全部 `SETTLED`，单一模型，无 Repair、无回退、无额外调用。
- 蔚来视觉失败错误：`EvidenceValidationError: Job metadata must be literal and cite exact evidence`，与 R36 蔚来车端 Agent 同类。**证据类失败未 Repair**，任务保留 FAILED，未重试。
- 【推断】F1 已消除分块假阴性，失败仍指向 `job_jd@1.0.1` 对 attributes 没有逐字约束；要救需升 prompt 版本，不是再花钱重试。

### 2.4 逐条人工审核与发布

脚本：`r42_audit_publish_batch2.py`（参数化 `--manifest/--report/--label`，审核规则与 R34 完全相同）。产物 `data/r58_showcase_audit/audit.json` 与 `publish.json`。

| jd_version | 逐字分布 | 审核结论 |
|---|---|---|
| 16 麒麟安全 | 9 VERBATIM | 发布；2 条强度投影问题见 D1 |
| 17 麒麟嵌入式 | 12 VERBATIM | 发布，干净 |
| 18 沐瞳 AI测试 | 7 VERBATIM + 6 HEADING_JOINED（"加分项"作小节标题，页面确有） | 发布 |
| 19 蔚来后训练 | 7 VERBATIM + 1 FORMAT_VARIANT | 发布；1 条强度投影问题见 D1 |
| 20 沐瞳 Unity | 13 VERBATIM + **8 PARAPHRASE** | **扣留 WAITING_REVIEW** |

**扣留理由【事实】**：原文"加分项"是独立小节标题，8 条项目本身没有该字样；模型在每条 statement 末尾凭空加了"（加分项）"。同时这 8 条的 `stored_strength` 是 `UNKNOWN`（原文明确是优先项），逐字与强度双重不合格。没有直接 SQL 改写、没有放宽 Evidence、没有发布。

**D1 已知缺陷【事实】**：3 条语句（jd16 的 83/84、jd19 的 117）存储强度正确为 `PREFERRED`，但 `consumer_strength` 把 "A，或B者优先" 拆块后判为 `REQUIRED`（`effective_strength=REQUIRED`）。这是消费层投影偏严，**不影响已存的原文和 Evidence**，本轮未改规则（会影响已发布的 5 份与评测口径，需单独授权）。

**Skill 推荐**：18 号 AI测试岗自动推荐 `ai_agent_application`，依据是原文加分项中的 RAG/Agent；规则为评分制并带歧义检测，**未把任何 `NO_APPLICABLE_SKILL` 改成推荐**。

**发布后 API 验证【事实】**：`/api/v1/interview/targets` 返回 9 个岗位，`/api/v1/public-career/jobs` 200 且 count=9，9 个详情全部 200；`publish_jd` 重复调用返回 `reused=true`（幂等）。沙箱累计 12 版本（9 PUBLISHED / 3 WAITING_REVIEW）、140 requirement、210 claim、215 evidence、10 条审计。

---

## 3. 第三部分：6 场 / 59 道面经题源核查（零模型）

产物：`data/r59_showcase_experiences/experience_audit.json`。

**身份原样保留【事实】**：6 场全部 `PUBLIC_USER_REPORTED_INTERVIEW`、`independently_verified_event=false`、`question_wording=PARAPHRASED_BY_ASSISTANT`、`complete_original_transcript=false`；59 题全部 `is_actual_human_answer=false`、`is_human_gold=false`、`is_model_generated_interview_record=false`。

- 6 场来自 **3 家企业**（字节 3、阿里 1、美团 2），**5 个唯一 URL**；`BYTEDANCE_20260923` 与 `BYTEDANCE_20260921` 共用同一牛客帖 → 26 道题需回原帖重新取原文。
- 题干规范化后 **0 组重复**、近似重复（≥0.80）**0 组**。主题覆盖 Agent/RAG 16、检索/向量 15、后端/工程 22、项目/行为 7、网络/协议 6、算法/ML 5、LLM 3、质量 2、系统设计 1，无"无主题"题。
- 四类名单：**可导入 0 / 只能作为参考 33 / 需要重新获取原文 26 / 拒绝 0**。没有任何一道能直接作为已审核公开题，因为全部是改写题、无逐字原文、无真人回答或金标。

**合法性核对【事实】**：`context.py::build_context` 只接受 `job_id == 当前 job` 或 `job_id IS NULL AND company_id == 当前 company` 且 `company_id` 为空或相等；自适应 V2 进一步要求 `q.job_id == job.id` 且 `source_anchor.kind != REPORTED_TOPIC`；`ExperienceReviewService.approve` 需 ADMIN，题目必须 `review_status=APPROVED` 且 `should_index=true`。字节/阿里/美团在 JD 沙箱**没有 company/job 行**，因此**不能**把这 59 道绑定到麒麟、沐瞳、蔚来岗位；本轮未绑定、未写入任何面经业务表。

---

## 4. 第四部分：三场 Replay 预演（零 HTTP / 零模型账本）

### 4.1 展示沙箱

新建 `interview_agent_showcase_r1_20261116`（全新库，alembic upgrade 到 `0029`），从 JD 沙箱复制 14 sources / 14 snapshots / 5 companies / 12 jobs / 12 JD 版本 / 140 requirements / 210 claims / 215 evidence，**ID、正文、offset 原样保留**；215/215 条 evidence 偏移回查一致。9 PUBLISHED / 3 WAITING_REVIEW / 9 selectable jobs。产物：`data/r60_showcase_sandbox/sandbox.json`。

### 4.2 三条路径

驱动：`r61_showcase_rehearsals.py`，真实业务 Service（auth → resume parser → profile agent → controller → evaluation → coach → capability memory → training），模型全部为 `AdaptiveReplayProvider` / `MemoryReplayProvider` / `ReplayModelProvider` fixture。账号与简历文件名显式标注 `SHOWCASE_R1_SYNTHETIC`。产物：`data/r61_showcase_rehearsals/rehearsals.json`。

| 路径 | 岗位 | 结果 |
|---|---|---|
| **A** | 沐瞳 Agent开发工程师（平台）+ `ai_agent_application` + adaptive FULL 3 题 | 注册/登录/me → 合成简历解析 → 画像(Replay) → 从 `/interview/targets` 选中已发布 JD → 冻结快照（`training=FULL`）→ 首题 → 3 轮作答/追问 → COMPLETED（3/3 evaluation）→ 快照未变 → Coach 报告 `REPLAY_FIXTURE` → 能力状态 `agent/rag_retrieval` PRACTICING |
| **B** | 沐瞳 后端开发工程师（AI向/数据平台）+ `java_backend` + adaptive FULL 3 题 | 同上全通过；3/3 evaluation；能力状态 `java/redis` PRACTICING |
| **C** | A 的同一测试用户，合法 WEAKNESS 训练 | 再次登录 → workbench 给出 5 个目标，`ai_agent_application:agent` 状态 PRACTICING → 合法模式 `[FULL, WEAKNESS]` → 以该知识目标建 WEAKNESS 会话，冻结 `memory_guided_plan` **1 条、引用 `evaluation:14`（A 场评价）** → 1 题作答 → COMPLETED → Cover 报告 → 能力状态 `agent` 由 PRACTICING → VERIFIED（revision 2） |

- 三场所有步骤 OK，`completed=true`；会话 6/7/8 均 COMPLETED，评价 3/3/1 条，报告 4/5/6 均 `generation_status=COMPLETED`、`producer=REPLAY_FIXTURE`、prompt `1.0.3`。
- 展示沙箱 `model_admissions = 0`：Replay 未产生任何真实 HTTP 或账本行。Main Harness 与 Coach Harness 均 OFF。
- 【推断】C 的 WEAKNESS 只出 1 题是单知识目标训练的预期行为，不是缺陷；A/B 的 FULL 各 3 题满足"三题以上"。

### 4.3 前端验收未完成

展示 API 与 Vite dev server 曾启动并可访问（API `/api/v1/health` 200、前端 `/` 200），随后用户取消这三个运行任务（API / Vite / 无头 Chrome）；按取消指令**未重启**。因此：

- **没有前端截图**，也没有完成"页面操作与后端持久化一致"的 UI 侧验收；
- 已有的等价证据是 **ASGITransport 真实 HTTP 路由**（注册/登录/me/targets 全 200）与数据库逐行核对，但它们**不等于浏览器页面验收**；
- 该缺口被计为第四部分的未完成项，并直接导致第五部分阻断。

---

## 5. 第五部分：首场真实模型面试——未执行

**阻断原因【事实】**：用户要求"只有前四部分的安全、预算、数据及离线验收全部通过，再执行首场真实模型三题面试"。第四部分的前端页面操作 / 截图验收因运行任务被取消而**未完成**，因此前置条件不成立；按"若前置条件未通过，不执行本场真实调用，只报告阻断原因"执行。

本部分实际消耗 **0 HTTP / 0 token**。未启动任何真实面试、未创建新会话、未切换模型。若后续补齐前端验收，仍需重新确认 ModelScope 剩余额度并单独授权 13 HTTP / 90,000 token 的硬上限。

---

## 6. 交付清单与资产状态

### 6.1 本轮新增文件

- 脚本：`r56_showcase_inventory.py`（52 份清单 + 6 份复核）、`r57_run_batch4.py`（真实导入）、`r59_showcase_experience_audit.py`（题源核查）、`r60_showcase_sandbox.py`（展示沙箱）、`r61_showcase_rehearsals.py`（三场预演）、`r62_showcase_runtime.py`（前端验收 API 运行时，未继续使用）；`r42_audit_publish_batch2.py` 仅参数化清单/报告路径，审核规则未改。
- 产物：`data/r56_showcase/`、`data/r57_batch4_run/`、`data/r58_showcase_audit/`、`data/r59_showcase_experiences/`、`data/r60_showcase_sandbox/`、`data/r61_showcase_rehearsals/`。
- 测试：R33–R36 契约 **161 passed / 0 failed**。
- Git：HEAD 仍 `1d87a0b`，**无提交、无 push、无 reset/clean**。

### 6.2 库 / 资产边界

| 资产 | 本轮后状态 |
|---|---|
| 正式库 `interview_agent` | 0025、users 5 / resumes 38 / sessions 19 / evals 41 / reports 5 / JD 4 / admissions 0；**未写** |
| JD 沙箱 | 0029、12 版本（9 PUBLISHED / 3 WAITING）、140 req / 210 claim / 215 evidence、32 账本行 81,458 token；**本轮唯一真实写入目标** |
| 展示沙箱 | 0029、9 PUBLISHED 可用于 targets、8 sessions / 20 evals / 6 reports / 12 capability memories、**0 账本行**；本轮新建 |
| MinIO | 生产桶 48、`r31-jd` 0，均**未变** |
| 原始 JD | `data/incoming_20261116c` 52 份语料 + DOM 原样，未删改 |
| Evaluation 2.3.4 / Coach / Memory / 备份 | 未修改；Harness 保持 OFF；未推进 L3/L4 |

### 6.3 距离 45–50 份正式 JD 的缺口

当前沙箱 **9 份 PUBLISHED**，距 45 / 50 还差 **36 / 41 份**。剩余 43 份原始记录中：3 份 WAITING_REVIEW（面壁、蔚来推理、沐瞳 Unity）、1 份 FAILED（蔚来视觉）、8 份其他真实岗位、4 份 UNCLASSIFIED/策展社区源，且只有 3 家企业有可用官方 DOM。**仅靠这 52 份无法达到 45–50 且保持企业多样性**，必须新增其他企业的官方采集。

---

## 7. 已知问题与后续最小计划

**已知问题（按优先级）**
1. P0：前端页面验收/截图缺失（本轮被取消），阻塞首场 Live。
2. P1：面经 59 题全部不可直接入库；26 道需回原帖、33 道仅参考。
3. P1：蔚来系 JD 两次同类元数据失败（视觉、车端），需 prompt 升版而非重试。
4. P2：D1 强度投影把 3 条"或…者优先"折成 REQUIRED。
5. P2：沐瞳 Unity 8 条"（加分项）"注入，需 M1 更正版；M1 仍被 0030 唯一约束阻断（本轮未迁移）。
6. P2：正式库落后代码 4 个迁移（0025 vs 0029），本轮未处理。

**后续最小执行计划（需重新授权，不自动执行）**
1. 补一次前端验收：API + Vite + 浏览器，用本轮已持久化的合成账号查看 targets / profile / report / memory / training，取 5–8 张截图并核对 DOM 文本与数据库行。
2. 单独授权后跑首场真实 3 题面试：13 HTTP / 90,000 上限，逐次治理，Evaluation 2.3.4 落库、Coach 引用真实评价、下一场读 MemoryGuidedPlan；失败即记录不包装。
3. 下一批 JD：先补采非蔚来/沐瞳/麒麟的官方页，再按本轮的"零 HTTP 复核 → 2 HTTP/份 → 严格逐字审核 → ADMIN 发布"流水线推进；单纯扩数量不解决企业多样性与真实简历缺口。
4. 另两场 Live 与真实简历：上游仍缺 1 份脱敏真实简历；在此之前所有"完整用户流程"只能是合成材料预演，不得写成真实验收。

---

## 8. 证据等级说明

本报告中除以下三处外均为【事实】：§2.3 对蔚来失败根因的判断、§4.2 对 WEAKNESS 单题行为的解释、§7 的优先级排序，均标注为【推断】或【建议】。所有数字来自本轮只读命令或本轮脚本产物；历史报告中的通过数未被当作本轮重测结果。
