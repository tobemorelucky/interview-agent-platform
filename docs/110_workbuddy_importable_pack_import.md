# 第 31 轮：可入库纠正版数据包的实际导入与离线验证（真实模型 HTTP = 0）

日期：2026-11-16　基线：`3237290`　**真实模型 HTTP = 0**

本轮做了三件实事：**实际导入 147 行测试数据**、**在导入后的数据上跑解析/主题/去重/匹配合法性测试**、**把「不能用它替代真实 JD」这件事在字段和表层面钉住**。
未改 Prompt 2.3.4、未新增 Agent、未推进 L3/L4、未写正式库、未删除/归档任何历史资产。

---

## 0. 校验与基线

| 项 | 结果 |
|---|---|
| 包内 12 个文件 SHA-256 | **12/12 通过**（`sha256sum -c`） |
| 数量自述（MANIFEST） | 15 来源改写 / 50 合成 / 3 流程 / 39 问题 / 40 回答 / 逐字原文 **0** |
| 沙箱库 | 新建 `interview_agent_wb_sandbox_pack_20261116`，迁移 **0029 / 68 表**，导入前业务表全 0 |
| 库总量 | 245 → **246**（只新增这一只，未删除任何库） |
| 正式库 `interview_agent` | 未变：5 users / 38 resumes / 19 sessions / 41 evaluations / **4** JD 版本 |

---

## 1. 实际导入记录数

导入脚本：`apps/api/scripts/r30_import_pack_to_sandbox.py`。
包内 `08_仅隔离PostgreSQL导入.py` 依赖 `psycopg`（本环境未安装），故用项目已有的 `asyncpg` **复刻同样的三道守卫**：① 库名必须含 `_sandbox_`；② `--confirm` 必须与库名逐字相等；③ 只建写 `sbx_*` 表。

| 表 | 来源文件 | 行数 | 读回校验 |
|---|---|---|---|
| `sbx_jds_sourced` | 01（15 份改写 JD） | **15** | 15/15，sha256 不一致 **0** |
| `sbx_jds_synthetic` | 02（50 份合成 JD） | **50** | 50/50，0 |
| `sbx_interview_flows` | 03（3 场面经元数据） | **3** | 3/3，0 |
| `sbx_interview_questions` | 04（39 道改写题） | **39** | 39/39，0 |
| `sbx_answer_pairs` | 05（40 份模拟回答） | **40** | 40/40，0 |
| **合计** | | **147** | 0 条哈希不一致 |

**导入后业务表行数（15 张全查）**：`users / resumes / candidate_profiles / companies / jobs / job_description_versions / job_requirements / user_job_targets / interview_sessions / interview_questions / interview_answers / interview_evaluations / final_interview_reports / capability_memories / model_admissions` **全部为 0**。

---

## 2. 字段映射

### 2.1 实际落库（夹具表）

| JSONL 字段 | `sbx_*` 列 | 说明 |
|---|---|---|
| `jd_id` / `flow_id` / `question_id` / `answer_id` | `id` TEXT PK | 一一对应，重复导入走 upsert |
| `provenance`（或 `source_type` / `question_provenance`） | `provenance` TEXT NOT NULL | 原样保留，是「真实性标签」的唯一入口 |
| 整行 JSON | `payload_json` JSONB | 原样保存，不裁剪、不改写 |
| 整行的规范化 JSON（`sort_keys` + 紧凑分隔符）sha256 | `content_sha256` | 读回逐条比对，0 不一致 |

### 2.2 若要进正式业务表（**本轮未执行，且当前不具备条件**）

| 目标表/列 | 可映射字段 | 阻断原因 |
|---|---|---|
| `companies.canonical_name` | `company` | 合成 JD 的公司名是「合成测试企业NN号」，不允许作为企业主体入库 |
| `jobs.canonical_title` | `job_title` | 合成岗位同理 |
| `job_description_versions.raw_text` | `jd_text` | **正文是改写**（`full_verbatim_from_official=false`）→ 无法通过逐字 Evidence 校验 |
| `job_description_versions.publication_status` | — | 只能 `PUBLISHED` 才有匹配资格；本包任何一条都不该被置为 PUBLISHED |
| `job_requirements.statement` | `requirements[]` | 是自然语言列表，没有 `external_ref`，未走 `normalize_external_requirements()` |
| `user_job_targets.*` | — | 没有用户/岗位归属，无法建立 owner 作用域 |

四条阻断已写入 `data/incoming_20261116b/pack_analysis.json::formal_import_blockers`，并由契约测试断言存在。

---

## 3. 离线验证结果

### 3.1 数量 / 字段 / 空正文 / 重复

| 检查 | 结果 |
|---|---|
| 必填字段完整性（两套 JD 的 `jd_id/company/job_title/jd_text/responsibilities/requirements/sha256`） | **65/65 齐全** |
| 空正文或 <200 字 | **0 条**（最短也远超 200 字） |
| 缺职责或缺要求 | **0 条** |
| 正文指纹重复 | **0**（65 条 `sha256` 互不相同） |
| 近重复（SequenceMatcher ≥0.9） | **0**：15 内部 105 对 + 50 内部 1,225 对 + 与既有 4 份原件 60 对，共 **1,390 对比对** |
| 同企业同岗位重复 | **0** |

### 3.2 真实性标注（这是本轮最硬的约束）

| 约束 | 实测 |
|---|---|
| 15 份改写 JD 声称逐字原文 | **0 条**（`full_verbatim_from_official=false` 全量） |
| 15 份改写 JD 声称 Evidence 已验证 | **0 条**（`evidence_verified=false` 全量） |
| 50 份合成 JD 被标为真实岗位 | **0 条**（`is_real_job=false` 全量） |
| 合成 JD 带来源链接 | **0 条**（`source_url=null` 全量） |
| 合成 JD 公司名未标明合成 | **0 条**（均含「合成」或「非真实」） |
| 40 份回答被标为人工金标 | **0 条**（`is_human_gold=false` 全量） |
| 回答含个人数据 | **0 条**（`contains_private_personal_data=false` 全量） |

`provenance_problems` 为**空数组**——这是脚本逐条判定的结果，不是抽样。

### 3.3 JD 解析（规则路径，明确非模型质量）

用 `job_target.service.rule_extract()`（零模型，输出 `source=RULE`）：

| 集合 | 岗位 | 解析出要求 | 平均要求条数 |
|---|---|---|---|
| 15 份改写 JD | 15 | **15** | 7.0 |
| 50 份合成 JD | 50 | **50** | 11.0 |
| 解析不出任何要求的岗位 | **0** | | |

> 这只证明「文本结构可解析出可用要求」，**不证明模型解析质量**。真实模型解析要发 `public_research_extract`，本轮 HTTP=0，未执行。

### 3.4 Requirement → Skill 识别

| 集合 | RECOMMENDED | AMBIGUOUS | NO_APPLICABLE_SKILL | 推荐结果分布 |
|---|---|---|---|---|
| 15 份改写 JD（用包内 requirements） | 9 | 0 | 6 | ai_agent_application 7、java_backend 1、bank_it 1 |
| 15 份改写 JD（用规则解析出的 requirements） | 8 | 1 | 6 | ai_agent_application 7、java_backend 1 |
| 50 份合成 JD（用包内 requirements） | 6 | 0 | 44 | ai_agent_application 5、java_backend 1 |
| 50 份合成 JD（用规则解析出的 requirements） | 9 | 0 | 41 | ai_agent_application 8、java_backend 1 |

两个可解释的观察：

- **合成 JD 的 `NO_APPLICABLE_SKILL` 高达 41–44 条**：这些岗位写的是「企业审批编排」「数据同步」这类通用工程场景，术语被拆到 `system_design / mysql_consistency / data_security` 等主题上，但没有一条能压过 `MIN_SCORE / MIN_CONTENT / MIN_MARGIN`。**这不是数据错误，而是规则对「通用岗位描述」不给结论**——与产品「宁可不选，也不按标题猜」的设计一致。
- 改写 JD 有 **6 条**同样落不了档（如「AI 仿真与验证平台」「车端 Agent 运行时基座」），说明技能目录只覆盖 3 个方向，覆盖面本身是缺口。

### 3.5 主题覆盖

| 指标 | 结果 |
|---|---|
| 至少命中 1 个系统主题的岗位 | **65/65** |
| 主题分布（按出现次数） | mysql_consistency 58、system_design 55、data_security 52、python 47、rag_retrieval 19、agent 18、java 14、redis 11、llm 6、kafka_reliability 5 |

主题层面 100% 有覆盖，但**主题 ≠ 蓝图 target**：能否出题还要看 requirement 是否被冻结进快照、检索是否有对应知识。这一步本轮没有验证（需要真实模型）。

### 3.6 匹配输入合法性（实测，不是推断）

在沙箱里真实调用一次 `JobMatchingService.create`（`r30_matching_legality.py`）：

```
refusal: {code: MATCH_PROFILE_REQUIRED, message: 请先生成候选人画像, status: 409}
verdict: REFUSED_BEFORE_MODEL_CALL
业务表行数 before == after（全 0），no_rows_written = true
```

结论：**这份包不能直接作为 Job Matching 的输入**。原因有三层：没有候选人画像（409 拒绝）、`sbx_jds_*` 的 TEXT 主键不是 `job_description_versions.id`、没有 owner 作用域。它只能作为「量级负载/检索文本」素材。

### 3.7 面经流程与回答

| 检查 | 结果 |
|---|---|
| 3 场流程声明题数 vs 实到题数 | 16/16、8/8、15/15，**完全一致**，合计 39 |
| `turn_index` 是否连续 | 每场从 1 递增，无跳号 |
| 是否创建 Session | **否**（`interview_sessions=0`），流转素材不冒充 Main Agent 历史 |
| 40 份回答 | 20 GOOD + 20 POOR，20 个题目；`is_human_gold=0`、PII=0 |
| 是否写入 Memory | **否**（`capability_memories=0`） |

---

## 4. 失败项

| # | 失败/未完成项 | 原因 | 影响 |
|---|---|---|---|
| 1 | 包内 `08_仅隔离PostgreSQL导入.py` 未直接运行 | 环境无 `psycopg` | 用 `asyncpg` 复刻同一守卫，见 §1；未安装新依赖 |
| 2 | 真实模型 JD 解析未执行 | 本轮 HTTP=0 | 只验证规则路径；模型解析质量仍未知 |
| 3 | 15 份改写 JD 不能进 `job_description_versions` | 非逐字原文，无法通过 Evidence 校验 | 正式 JD 计数仍为 8，不增加 |
| 4 | 50 份合成 JD 无技能定档 | 规则对通用描述不出结论（41–44 条 `NO_APPLICABLE_SKILL`） | 只能做量级/检索负载，不能当方向标注 |
| 5 | Job Matching 无法用本包 | 缺画像（409）+ 缺 JD 版本 ID + 缺 owner | 需真实简历后再谈 |
| 6 | SQLite 与 PG 未做逐行 diff | 两套脚本实现不同、SQLite 未安装驱动级只读比对 | 已用「同一规范化 JSON + sha256」间接对齐（12/12、147/147） |

---

## 5. 可用于正式业务链路的数据数量

| 用途 | 可用数量 |
|---|---|
| 正式 JD 业务表（`job_description_versions` + `job_requirements`） | **0**（15 份改写不满足逐字 Evidence；50 份合成不允许） |
| 真实候选人匹配输入 | **0**（无画像） |
| 独立面试流程素材（离线） | **3 场 / 39 题**（不得创建 Session） |
| 离线评价素材 | **40 份**（20 对，非金标） |
| 沙箱负载/检索语料 | **65 份 JD**（15 改写 + 50 合成，147 行总量含流程与回答） |

**一句话**：这 147 行让沙箱「有数据可跑」，但没有让「真实 JD」多出一份。

---

## 6. 真实原始 JD 的单独计数

| 项 | 数 |
|---|---|
| 已取得并核验的**原始完整正文** | **8**（既有 4：华为官方 1 + 社区策展 3；第 30 轮新增麒麟软件官方页 4，各带 `jobAdId`） |
| 本包新增真实原始正文 | **0**（包内自述 `authentic_verbatim_full_jd_count=0`） |
| 本包 50 份合成 JD | **不计入**，任何报告都不得用它们凑 50 |
| **距 50 份目标** | **缺 42 份** |

---

## 7. 测试与代码

| 文件 | 作用 |
|---|---|
| `apps/api/scripts/r30_import_pack_to_sandbox.py` | 三道守卫 + 导入 + 读回校验 |
| `apps/api/scripts/r30_pack_analysis.py` | 真实性、空正文、去重、解析、主题、技能、匹配前置 |
| `apps/api/scripts/r30_matching_legality.py` | 匹配输入的实测拒绝路径 |
| `apps/api/tests/test_r30_importable_pack_contract.py` | **11 passed / 0 failed**（0 HTTP / 0 DB） |

契约测试覆盖：12 文件校验和、5 表行数与 0 哈希不一致、15 份不称逐字/不称已验证、50 份不称真实、无重复无空正文、解析 65/65、流程题数一致、回答非金标无 PII、四条导入阻断存在、业务表 0 行。

---

## 8. 结论与下一步

- 数据**真的导进去了**（147 行，可读回、可复查、可重复导入），但只在 `sbx_*` 夹具层；
- 所有「真实性」字段都是**拒绝升级**的：改写不说逐字、合成不说真实、回答不说金标；
- 正式业务链路可用数据 = **0**，因为缺的是「逐字 Evidence + owner + 画像」，不是缺行数；
- 下一步仍是同一件事：**你提供 1–2 份脱敏真实简历** → 零 HTTP 预检 → 单独申请 3 题冒烟预算（13 HTTP / 90K token / 25 min）；JD 侧继续按官方页正文补到 50，不用转载或合成凑数。
