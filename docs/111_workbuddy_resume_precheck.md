# 第 32 轮：真实简历 × 真实 JD 的零 HTTP 预检（真实模型 HTTP = 0，未跑任何面试）

日期：2026-11-16　基线：`1431747`

**本轮没有拿到简历文件**：消息里没有附件，`Downloads` / `Desktop` / 项目目录均无新增 PDF/DOCX（最新的仍是 12:45 的两个数据包）。
按规则**不构造、不脑补**任何简历内容，因此第 1、3、5 项只做到「格式与路径已验、候选已定」，实际写入等文件到达。

---

## 1. 格式、文本抽取与字段映射（已用同一代码路径验证）

| 项 | 实测 |
|---|---|
| 支持格式 | `pdf` / `docx`（`ResumeParser`；`.doc` 不支持） |
| 抽取库 | `pypdf 6.12.0`（PDF）、`python-docx`（DOCX），均在 `apps/api/.venv` 可用 |
| OCR | **无**。扫描版 PDF 会抽出空/极少文本，平台不兜底 → 直接判失败 |
| 大小限制 | `resume_max_file_size_mb=10` |
| 存储 | 上传即写 MinIO `resumes/{user_id}/{uuid}.{ext}`，同时落 `resumes` 行（`_validate_file` 只校验空/超限/类型） |

**字段映射（目标结构，来自 `CandidateEvidenceSelection` / `CandidateProfileDelta`）**

| 简历段落 | 抽取证据 | 画像字段 | 缺口处理 |
|---|---|---|---|
| 教育背景 | 逐字段落 → `evidence_ids` | `education[]`（`label` + `statement` + `evidence_ids`） | 缺则 `unknowns`，不推断学校层次 |
| 实习经历 | 同上 | `internship_experience[]` | 只有工作经历时留在 `work_experience`，不混写 |
| 工作经历 | 同上 | `work_experience[]` | 时间/公司缺失则整条不入，进 `unknowns` |
| 项目经历 | 同上 | `projects[]` | **只保留简历里写了的**技术栈与职责；不补「推测做过」 |
| 专业技能 | 同上 | `skills[]`（带 `level` / 证据状态） | 简历没写的技能不生成 |
| 获奖/竞赛 | 同上 | `strengths[]` 或 `unknowns` | 不把获奖当成能力证明 |

每条事实都必须带 `evidence_ids`（简历原文片段），**没有原文片段的事实不会入库**——这是「不编造」的技术保证，不是口号。

**解析探针（不是简历）**：用 `.runtime/r31_parser_probe/PARSER_PROBE_NOT_A_RESUME.docx` 走同一路径，抽取 196 字、命中教育/实习/项目/技能/获奖 5 段、PII 0 项，判定 `TEXT_TOO_SHORT`（阈值 200 字生效）。说明：抽取链路可用、阈值可失败、PII 扫描有效。

---

## 2. JD 选择：麒麟软件「AI研发工程师」（主选）／华为「AI应用工程师」（备选）

| 指标 | 麒麟 AI研发工程师 | 华为 AI应用工程师 |
|---|---|---|
| 正文来源 | 官方校招页逐字（`jobAdId=70b44d1d-…`） | 人工策展原件（`advertisementId=36390`，sha256 已核） |
| 正文字数 | 1,279 | 1,093 |
| 系统主题 | **7 个**：`agent`、`rag_retrieval`、`llm`、`python`、`java`、`system_design`、`data_security` | 6 个：`rag_retrieval`、`llm`、`python`、`java`、`system_design`、`mysql_consistency` |
| Agent 关键词 | **Agent×2、工具调用×3、记忆×1、RAG×3** | Agent×0、工具调用×0、记忆×0、RAG×1 |
| 规则解析要求 | 11 条 | 11 条 |
| 技能识别 | `RECOMMENDED` → `ai_agent_application`（依据「AI研发、RAG、智能体」） | `RECOMMENDED` → `ai_agent_application`（依据「AI应用、RAG」） |

**结论**：若简历主栈是 Python + LLM/Agent 工程（LangChain/LangGraph、RAG、工具调用），选**麒麟 AI研发工程师**——它是 8 份里唯一在正文里同时出现 Agent 框架、工具调用、记忆管理、RAG 评测的岗位；若简历是 Java 后端转 AI 应用，选华为 AI应用工程师更贴。**最终以简历实际栈定，不预先假设**。

---

## 3. JD 的合法性核查（逐项）

| 要求 | 麒麟 AI研发工程师 | 结论 |
|---|---|---|
| 完整正文 | 官方页逐字提取，含 7 条职责 + 任职要求 + 投递地址 | ✅ 有 |
| 正文指纹 | 抽取文本 sha256 已记录（`jd_acquired.json`） | ✅ 有 |
| 来源证据 | 官方 URL + `jobAdId`；**未存 `source_snapshots` 行** | ⚠️ 缺一步（见阻断 B4） |
| Requirement | `rule_extract` 得 11 条，`source=RULE` | ⚠️ 只有规则级，正式 `job_requirements` 需模型解析 1 次 |
| Skill | `RECOMMENDED / ai_agent_application`（确定性规则） | ✅ 有 |
| 是否用了合成 JD | **没有**；50 份合成 JD 一律不参与 | ✅ |

---

## 4. 零 HTTP 预检结果（沙箱 `interview_agent_wb_r26_alt_20261115`）

脚本：`apps/api/scripts/r31_resume_intake_precheck.py`（`--env-only` 可单独跑）。

| 检查 | 实测 |
|---|---|
| Sandbox | 0029 / 68 表 / 业务表**全 0 行** |
| Provider | `modelscope` / `deepseek-ai/DeepSeek-V4.1-Flash-1017` / Key 前缀 `ms-` |
| 闸门 | `purge_execution_enabled=false`、`interview_coach_harness=false` |
| 账本路由 | 进程库 = 期望账本库 = 沙箱（`MODEL_LEDGER_DATABASE` 显式声明） |
| Owner | 沙箱业务表 0 行，无既有 owner 可串；调用方在发送前显式设 scope（`job_target/service.py:287`），`assert_ledger_target` 逐次校验 |
| MinIO | bucket 存在，48 对象（未新增） |
| 冻结上下文 | Prompt **2.3.4**、7,452 字符、指纹 `e7704357b5fc37ba`，与 docs/108 一致 |
| 简历 | **缺失** → 唯一阻断 |

```
ready: false
blocking: [RESUME_NOT_PROVIDED]
```

---

## 5. 3 题完整链路的精确预算

| 步骤 | 路由 | HTTP | Token（实测锚点） |
|---|---|---|---|
| 简历结构化 | `resume_report` | 1 | 1,700–2,300 |
| 候选人画像 | `resume_profile` | 1 | 2,300–2,800 |
| JD 解析（麒麟 JD → Requirement） | `public_research_extract` | 1 | 1,100–1,400 |
| 出题 ×3 | `interview_main` | 3 | 27,300–28,850 |
| 评价 ×3（2.3.4） | `answer_evaluation` | 3 | 24,900–25,500 |
| Coach ×1 | `interview_coach` | 1 | 5,800 |
| **基线合计** | | **10** | **63.1K–66.6K** |
| 建议硬上限 | | **13** | **90,000** |
| 允许的结构化修复 | 每次 +1 HTTP | ≤3 | +3.5K／次（引用子集）～+10K（整轮重发） |

- 时长上限 25 min；单场 token 预算 200,000（`interview_max_model_tokens`），3 题远低于上限。
- 幂等：同 `answer_id` + 同键重复提交 **0 次新调用**；评价成功后重试下一题复用已存评价。
- 失败即停：`UNKNOWN` / `MODEL_USER_QUOTA` / 连续 2 次失败 → 停，不换 Provider、不自动重试。

---

## 6. 全部阻断项

| # | 阻断 | 影响 | 解除方式 |
|---|---|---|---|
| B1 | **简历未提供** | 无法做格式校验、字段映射、上传、画像 | 提供 1 份脱敏 PDF/DOCX（≤10MB，非扫描件） |
| B2 | `.env` 的 `DATABASE_URL` 仍指正式库 | 直接跑会把账本写进正式库，或被 409 拒绝 | 运行时同时 export `DATABASE_URL` 与 `MODEL_LEDGER_DATABASE` 指向沙箱 |
| B3 | `resume_report` 走全局 `async_session_factory` | 只改 harness factory 无效 | 同 B2：进程级变量必须一致 |
| B4 | 麒麟 JD 未存 `source_snapshots` | 来源证据链少一环（只有 URL+jobAdId+文件 sha256） | 导入前补一条快照记录（可离线写，0 HTTP） |
| B5 | 正式 `job_requirements` 需要模型解析 | 没有它就没有合法的 Requirement / Skill 匹配输入 | 预算里已含 1 次 `public_research_extract` |
| B6 | 评分金标 `DRAFT_UNREVIEWED` | 分数只能记录，不能当通过判据 | 人工复核后另行启用 |
| B7 | `covered_points` 已知过度给分（RAG-A2） | 不能当「已掌握」证据 | 验收时逐条回原文核对引用 |
| B8 | 真实原始 JD 仍 8 份 | 不能用合成/改写凑 50 | 继续按官方页补，缺 42 |

---

## 7. 本轮边界

未调用任何模型（HTTP=0）；未上传文件、未建用户/会话、未写业务表；未跑 3 题、未跑 18 题；未改 Prompt 2.3.4、未新增 Agent、未动 L3/L4；未 push/reset/clean；沙箱与正式库行数均未变。
