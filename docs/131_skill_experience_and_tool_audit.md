# R131：专业 Skill、公司面经数据库与主面试官工具调用审计

日期：2026-11-18 · 基线：本地 HEAD `4ff38d2`（R127–R130 已提交并 Push，本轮未改任何代码）
模型 HTTP：**0**（只读数据库 + 本地模块离线重算）· 外部抓取：**0** · 展示数据修改：**0**

本轮只做三件事：读展示库、读正式库做对照、用**同一套代码**离线重算历史请求投影。没有新增 Agent、没有改 Skill/Prompt/Evaluation/前端，也没有提交。

---

## 0. 结论速览

| 核心问题 | 结论 | 一句话依据 |
| --- | --- | --- |
| 10 个专业 Skill 是否真正进入面试链路并影响提问 | **部分实现（链路真实，覆盖不足）** | 10 个 Skill 全部可加载、可冻结、指令逐字进入 Main 请求；但 627 场会话只实际用过 2 个（`ai_agent_application` 431 场、`java_backend` 190 场），其余 8 个**零运行时证据** |
| 数据库是否收录不同公司的真实面经，当前岗位能否使用 | **部分实现（有 5 条，业务库为 0）** | 展示库 `interview_experiences=0`、`interview_questions=0`；正式库仅 5 条牛客社区面经、65 道可用题，且 4/5 没有 `company_id`、0/5 没有 `job_id` |
| 主面试官能否主动调用工具检索公司面经 | **尚未实现** | Main Harness 只有 3 个工具，**没有**面经检索工具；且 Harness 从未在展示库/正式库启用过（`main_harness_version` 快照 = 0） |

一句话总结：**Skill 是一条真链路，面经是一条断掉的数据链路，工具是一条只在测试库里跑过的旁路。** 三者不能互相替代。

---

## 1. 审计方法与数据源

| 项 | 值 |
| --- | --- |
| 运行中的展示库 | `interview_agent_showcase_r123_20261117`（`start-showcase.bat` 强制 `interview_agent_showcase_*`，自检 50 岗位） |
| 对照库 | `interview_agent`（正式库，4 公司 / 4 岗位 / 5 条面经） |
| 工具调用历史库 | `interview_agent_wb_r26_test_20261115`（R92–R94 的 harness 回归库） |
| 连接方式 | `asyncpg` 直连，会话级 `SET default_transaction_read_only = on` |
| 离线重算 | `data/r131_audit/project.py`、`compare_skills.py`、`skill_audit.py`、`recommend_audit.py`（全部只读，0 HTTP） |
| 产物 | `data/r131_audit/wire_projection.json`、`skill_projection_compare.json`、`skill_registry.json`、`skill_manifests.json`、`job_skill_recommendation.json` |

一个必须先说清的限制：**线上请求体没有落库**。`experience_agent_step_runs.input_json` 只存 `route / prompt_hash / thinking_type`，不存完整 payload。所以"Skill 是否进入请求"的证据由三部分拼成：

1. 会话创建时冻结的**完整快照**（含 Skill 定义、指令、指纹）；
2. 用当前代码对同一快照重放 `planning_payload → agent_payload → model_payload`，得到**逐字 wire**；
3. 历史步骤里的 `prompt_version + prompt_hash` 与本地 Prompt 常量逐位比对。

这能证明"按冻结快照，模型收到的就是这些内容"，但**不等于**抓到了当时的原始报文。报告中凡属重算的地方都会标注。

---

## 2. A. Skill 真实性核查

### 2.1 实际注册且可用的 Skill（10 个）

`SkillRegistry.options()` 输出：`version=1.1.0`，`quarantined_count=0`，10 个 Skill，全部 `1.0.0`。

| # | Skill ID | 名称 | 专业分类 | 主题数 | 领域数 | 指令长度 | 指纹（前 16） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `ai_agent_application` | AI / Agent 应用开发 | ai_application | 5 | 4 | 333 | c70fb98dae065187 |
| 2 | `bank_it` | 银行信息科技岗位 | bank_it | 5 | 4 | 300 | 8c1aefb3e6cf3273 |
| 3 | `data_engineering` | 数据工程 | data_engineering | 6 | 4 | 281 | 4b806e9676893aad |
| 4 | `frontend_web` | 前端开发 | frontend | 5 | 4 | 288 | 75478b3cc845ed90 |
| 5 | `general_cs` | 通用计算机基础 | general_cs | 6 | 4 | 285 | 10145f9df9421e3c |
| 6 | `java_backend` | Java 后端开发 | java_backend | 5 | 4 | 296 | 78215c57a90fa570 |
| 7 | `python_backend` | Python 后端开发 | python_backend | 6 | 4 | 314 | 9c01d49d8c6c3ab1 |
| 8 | `rag_retrieval` | RAG 与检索工程 | rag_retrieval | 6 | 4 | 291 | 3242a298cbcdec80 |
| 9 | `recommendation_ml` | 推荐系统与机器学习 | recommendation_ml | 6 | 4 | 292 | bf92d27afda7ed9b |
| 10 | `testing_qa` | 测试开发 | testing_qa | 5 | 4 | 288 | 158db4790a887018 |

补充事实：

- 10 个 `manifest.json` 全部通过 `SkillManifest(extra="forbid")` 校验，目录名与 `id/version` 一致，`required_tools=[]`（V1.1 明确不授予任何可执行工具）。
- 10 个 `skill.json` 的 `evaluation_anchors` 都是**同一组五维**（technical_correctness / coverage / depth / relevance / communication），但**锚点文案按专业不同**；`freeze()` 会强制校验这五个键，缺一即 503。
- 10 个内容指纹互不相同，主题序列也互不相同（10 组）。
- **根目录 `interviewer_skills/manifest.json` 只列了 3 个 Skill**（ai_agent / java / bank_it）。`discover()` 只扫 `*/*/manifest.json`，这个根文件**不被任何代码读取**，属于历史遗留；不影响运行，但会误导人工审查。

### 2.2 逐个加载校验

对每个 Skill 实际执行 `freeze(skill_id, "technical_screen", "standard", requirements=..., jd_source=None)`：

| 校验点 | 结果 |
| --- | --- |
| manifest 读取 | 10/10 成功 |
| `skill.json` 读取且身份/版本一致 | 10/10 成功 |
| `SKILL.md` 读取 | 10/10 成功（281–333 字符） |
| 五维评分锚点齐全 | 10/10 |
| 轮次权重和为 1 | 通过（`technical_screen` / `project_deep_dive`） |
| 隔离失败 | 0 个 |
| 内容指纹唯一 | 10/10 唯一 |

`freeze()` 生成的结构包含：`engine_version`、`registry_manifest`、`skill`（含 `instructions` 与 `content_fingerprint`）、`round`、`style`、`company_background`、`prompt_versions`，最后再对整个对象取一次 `content_fingerprint`。**指令是冻结对象的一部分，不是 UI 文案。**

### 2.3 会话创建与 Prompt 实际组装

创建链路（`context.py::build_context`）：

```
job / company / PUBLISHED JD / requirements
  → SkillRegistry().freeze(skill_id, round, style, requirements, jd_source)
  → adaptive_blueprint（目标 = skill.priority_topics）
  → adaptive_policy.prompts = freeze_prompts(...)   # 整段 system 文本 + 指纹入快照
  → 快照落库（InterviewContextSnapshot.context_json）
```

每次模型调用时（`agents.py::call`）：

```
planning_payload(快照, 题目, 当前题, 计划)
  → agent_payload(route)          # Evaluation 换成 interviewer_evaluation 合同
  → model_payload(route)          # 角色投影 + 预算 + 去指纹
  → PromptRegistry.get(route)     # 用快照里的 version + fingerprint 校验后返回 system
  → provider.call(system, packet)
```

历史会话的 Prompt 版本与指纹（步骤表实测，非推测）：

| 会话 | Main 版本 | Main prompt_hash（前 16） | 本地常量比对 | Evaluation |
| --- | --- | --- | --- | --- |
| #625 | 2.5.0 | 95c0ee1d2ea2eb5c | 一致 `MAIN_CONVERSATIONAL` | 2.3.4 |
| #627 | 2.6.0 | a46b7d4d27cbc288 | 一致 `MAIN_CONVERSATIONAL_V2` | 2.3.4 |
| #628 | 2.7.0 | 4b9ff14a7ca13e3a | 一致 `MAIN_CONVERSATIONAL_V3` | 2.3.4 |
| #629 | 2.8.0 | cf7af56bc5743b47 | 一致 `MAIN_CONVERSATIONAL_V4` | 2.3.4 |
| #630 | 2.9.0 | 92bac0ab9a5be71 | 一致 `MAIN_INTERVIEWER_V9` | 2.3.4 |
| #632 | 3.0.0 | a3f375abd1770e98 | 一致 `MAIN_INTERVIEWER_V10` | 2.3.4 |

Evaluation 全部是 `e7704357b5fc37ba…`，与 `EVALUATION_BOUNDARIES_PROMPT(2.3.4)` 完全一致。**"冻结版本 → 实际调用"这条链是闭合的。**

#### 2.3.1 #625–#632 的冻结与调用记录（逐场）

| 会话 | Skill（版本） | Main 调用 | Evaluation 调用 | 冻结题数 | 题源构成 | 公共题 |
| --- | --- | --- | --- | --- | --- | --- |
| #625 | `ai_agent_application` 1.0.0 | 5 | 4 | 5 | 简历 2 + 追问 3 | 0 |
| #627 | `ai_agent_application` 1.0.0 | 7 | 6 | 6 | 简历 1 + 动态 1 + 追问 4 | 0 |
| #628 | `ai_agent_application` 1.0.0 | 2 | 2 | 3 | 自我介绍 1 + 简历 1 + 追问 1 | 0 |
| #629 | `ai_agent_application` 1.0.0 | 3 | 3 | 4 | 自我介绍 1 + 简历 1 + 追问 2 | 0 |
| #630 | `java_backend` 1.0.0 | 3 | 3 | 3 | 动态 1 + 追问 2 | 0 |
| #632 | `ai_agent_application` 1.0.0 | 6 | 6 | 6 | 简历 1 + 动态 1 + 追问 3 + **JD 1** | 0 |

#625 的 Main 调用比 Evaluation 多 1，是最后一道题未评分即结束（`USER_ENDED`）。6 场里唯一一次 `JOB_REQUIREMENT` 题源出现在 #632 最后一题（`requirement:210`），其余全部来自候选人简历、追问或模型动态生成。**没有一场使用 `PUBLIC_QUESTION`。**

### 2.4 选定 Skill 的内容确实进入模型请求

用 #630 的快照重放，`model_payload(..., "interview_main")` 的结果（节选）：

| 检查项 | 结果 |
| --- | --- |
| `snapshot.interviewer_config.skill.instructions` | 与 `java_backend/1.0.0/SKILL.md` **逐字相同**（296 字符） |
| `priority_topics` | `["java","mysql_consistency","redis","kafka_reliability","system_design"]` |
| `domains` | Java 与并发基础 / 数据库事务与一致性 / 缓存与消息可靠性 / 后端接口和系统设计 |
| `evaluation_anchors` | 仅出现在 Evaluation 合同，Main 侧被移除 |
| `requirements`（JD 原文） | 4 条（id 86/88/89/90；快照共 6 条，按蓝图来源筛后 4 条） |
| `candidate_facts` | 7 条 |
| `public_questions` | **0** |
| `interview_experiences` | **不存在该键** |
| wire 大小 | 20,082 字节 |

同一个函数对 6 场会话的重放结果：

| 会话 | Skill | wire 字节 | 候选数 | 快照内 JD 要求 | 快照内公共题 | 面经键 |
| --- | --- | --- | --- | --- | --- | --- |
| #625 | ai_agent_application | 31,904 | 26 | 2 | 0 | 无 |
| #627 | ai_agent_application | 34,707 | 22 | 3 | 0 | 无 |
| #628 | ai_agent_application | 23,739 | 19 | 3 | 0 | 无 |
| #629 | ai_agent_application | 22,137 | 12 | 3 | 0 | 无 |
| #630 | java_backend | 20,082 | 14 | 4 | 0 | 无 |
| #632 | ai_agent_application | 38,466 | 26 | 3 | 0 | 无 |

结论：**Skill 不是"只显示在 UI"**。它的指令、主题、领域、评分锚点都在冻结对象里，指令原文随每次 Main 请求发出。

### 2.5 一次会话只用一个 Skill

对 6 场会话的完整 wire 逐个搜索 10 个 `SKILL.md` 的一级标题：

| 会话 | 命中的 Skill 标题 | 其他 9 个 |
| --- | --- | --- |
| #625 / #627 / #628 / #629 / #632 | 仅 `AI / Agent 应用开发面试 Skill 1.0.0` | 0 |
| #630 | 仅 `Java 后端开发面试 Skill 1.0.0` | 0 |

同时校验 `blueprint.skill_id == interviewer_config.skill.id`，6/6 一致。代码层面也没有"把所有 Skill 拼进 system"的路径：`freeze()` 只接受一个 `skill_id`，`build_context` 只写一次 `interviewer_config`。

### 2.6 四个 Skill 的零 HTTP 投影对比（同一份真实 JD/画像，只换 Skill）

控制变量：都用 #630 的候选人画像 + 麒麟软件「后端研发工程师（Go/Java）」的 6 条要求，只替换 `interviewer_config`。

| 指标 | AI / Agent | Java 后端 | RAG 与检索 | 推荐系统 |
| --- | --- | --- | --- | --- |
| priority_topics | rag_retrieval, agent, llm, python, system_design | java, mysql_consistency, redis, kafka_reliability, system_design | rag_retrieval, agent, llm, python, system_design, **data_security** | recommendation, algorithm, python, mysql_consistency, system_design, llm |
| 蓝图目标 | system_design, python, agent | java, redis, kafka_reliability | system_design, python, agent | system_design, mysql_consistency, python |
| 候选组合数 | 17 | **23** | 17 | 19 |
| 题型 | PROJECT / FUNDAMENTALS / SCENARIO | PROJECT / FUNDAMENTALS / SCENARIO | PROJECT / FUNDAMENTALS / SCENARIO | FUNDAMENTALS / SCENARIO（无 PROJECT 源） |
| 合法来源类型 | 候选人事实 / JD 要求 / Skill | 同上 | 同上 | 同上 |
| wire 字节 | 17,323 | 16,209 | 17,035 | 16,154 |
| 指令在 wire 内 | 是 | 是 | 是 | 是 |
| 指令哈希（前 12） | e8f93db765ea / 333 字符 | 5b8aaa0e7d53 / 296 | 60e595a390f7 / 291 | 8bec99a7934e / 292 |

实质差异是存在的：**考察目标集合、候选组合、可用题型、评分锚点都随 Skill 变化**。`ai_agent_application` 与 `rag_retrieval` 有 4 个主题重叠，但 RAG 多出 `data_security`、顺序不同，蓝图选出的目标也不同。

需要说清"合法题源"的差异层次：四者的**来源类型集合相同**（候选人事实 / JD 要求 / 通用 Skill），但**具体来源实例不同**——同一份 JD 的 6 条要求、同一份画像的 7 条事实，会按不同 Skill 的主题词挂到不同目标上，候选组合数从 17 到 23 不等。所以"合法题源"不是变多了或变少了，而是**同一批冻结材料被重新分配到了不同的考察目标**。

### 2.7 运行时覆盖：10 个里只跑过 2 个

展示库 627 个快照的 Skill 分布：

| Skill | 会话数 |
| --- | --- |
| `ai_agent_application` | 431 |
| `java_backend` | 190 |
| 无 Skill（早期） | 6 |
| 其余 8 个 | **0** |

61 场自适应会话里：`ai_agent_application` 59、`java_backend` 2。也就是说 **RAG、推荐、前端、测试、数据、银行、Python、通用基础 8 个 Skill 从未在真实会话中被使用过**，目前只能证明"可加载、可投影"，不能证明"在真实多轮对话里考察得好"。

### 2.8 一个必须记录的推荐质量缺陷

用当前 `skill_recommendation.py` 对 50 个岗位离线复算：

| 状态 | 数量 |
| --- | --- |
| RECOMMENDED（自动选中） | 32 |
| AMBIGUOUS（要求人工选） | 6 |
| NO_APPLICABLE_SKILL | 12 |

其中至少两处是**词面碰撞导致的错配**：

- 百度「Agent Harness 研发工程师」→ `frontend_web`。推荐理由原文："岗位的 React、状态管理与该专业规则相符。" **R132 复核发现真正原因是 `ReAct`（Agent 推理范式）被 `normalized()` 统一小写后当成 `React`**，"状态管理"只是同时命中的第二个词；R131 当时的归因不完整。#628 会话里用户手动改成了 `ai_agent_application`。
- 上海华为「AI模型工程师-Agent技术」→ `frontend_web`。理由："岗位的性能优化与该专业规则相符。"

另外「JAVA后端开发工程师」被判 `NO_APPLICABLE_SKILL`：标题命中 java_backend，但要求与职责里的内容分只有 3（低于 `MIN_CONTENT=6`），触发 `TITLE_ONLY_LEADER`，系统拒绝"按标题自动选"。这是设计上的保守，但对真实 JD 偏严。

还有一个可审计性问题：#625 快照里保存的推荐（rank1 `ai_agent_application` 6 分、`contextual=false`、无 `frontend_web`）与当前代码复算（rank1 `frontend_web` 9 分）**不一致**，但两边 `policy_version` 都是 `1.2.0`。推荐实现变过而版本号没变，历史推荐不可复现。这不影响 Skill 冻结，但影响"自动推荐"的可追溯性。

---

## 3. B. 公司面经数据库真实性核查

### 3.1 展示库：0 条面经

直接读 `interview_agent_showcase_r123_20261117`：

| 表 | 行数 |
| --- | --- |
| `interview_experiences` | **0** |
| `interview_questions` | **0** |
| `experience_source_items` | **0** |
| `sources` | 47 |
| `source_snapshots` | 50 |
| `companies` | 21 |
| `jobs` | 50 |
| `job_description_versions` | 50（全部 PUBLISHED） |
| `job_requirements` | 252 |

50 个岗位、252 条要求、50 份 JD，**没有一条面经、没有一道公共题**。`build_context` 里那两段查询在展示库永远返回空列表——这不是"匹配不到"，是"表里没有"。

### 3.2 正式库：5 条面经、67 道题

| 指标 | 值 |
| --- | --- |
| 面经总数 | 5 |
| 面经已审核（APPROVED） | 5（100%） |
| 面经问题总数 | 67 |
| 已审核且可索引（APPROVED + should_index） | **65** |
| REJECTED | 1 |
| WAITING_REVIEW | 1 |
| 来源条目 `experience_source_items` | 97（5 条手工导入、17 条已抓取、72 条仅发现、3 条抓取失败） |
| 有面经的公司（按名称） | 5：工行、上海银行、中国移动研究院、华为、字节跳动 |
| 有面经的公司（按 `company_id` 主数据） | **1**（华为，id=4） |
| 精确关联到岗位（`job_id` 非空）的面经 | **0** |
| 题目侧 `company_id` / `job_id` | 20 / **0** |

### 3.3 按公司与岗位的面经覆盖（当前 50 个展示岗位）

按公司汇总（每家公司下面所有岗位的面经数均为 0）：

| 公司 | 岗位数 | 已发布 JD | 岗位要求 | 面经 | 公共题 |
| --- | --- | --- | --- | --- | --- |
| 蔚来 | 11 | 11 | 55 | 0 | 0 |
| 沐瞳科技 | 9 | 9 | 44 | 0 | 0 |
| 麒麟软件 | 8 | 8 | 42 | 0 | 0 |
| 上海华为技术有限公司 | 3 | 3 | 14 | 0 | 0 |
| 携程 | 2 | 2 | 10 | 0 | 0 |
| 百度 | 2 | 2 | 10 | 0 | 0 |
| Unity中国 / 万兴科技 / 中国电信-天翼云 / 北京汇智有识 / 华为云计算 / 华为云计算技术有限公司 / 华为技术有限公司 / 天猫技术 / 字节跳动 / 快手 / 智慧芽 / 汽车之家 / 联想 / 途虎 / 面壁智能 | 各 1 | 各 1 | 4–6 | 0 | 0 |
| **合计** | **50** | **50** | **252** | **0** | **0** |

逐岗位明细（面经/题目两列全部为 0，故合并；"推荐"列是第 2.8 节的离线复算结果）：

| # | 公司 | 岗位 | Skill 推荐 |
| --- | --- | --- | --- |
| 1 | Unity中国 | AI Agent算法研发工程师 | ai_agent_application |
| 2 | 沐瞳科技 | AI测试工程师（web平台） | AMBIGUOUS |
| 3 | 蔚来 | 供应链算法工程师 | ai_agent_application |
| 4 | 蔚来 | 车端AI Agent与应用生态系统架构工程师 | ai_agent_application |
| 5 | 沐瞳科技 | Unity客户端开发工程师（活动）-MOBA | frontend_web |
| 6 | 麒麟软件 | AI研发工程师 | ai_agent_application |
| 7 | 沐瞳科技 | AI应用开发工程师（游戏性能与研发效能） | ai_agent_application |
| 8 | 沐瞳科技 | Agent开发工程师（平台） | ai_agent_application |
| 9 | 蔚来 | 座舱大模型后训练/Agentic算法工程师 | recommendation_ml |
| 10 | 蔚来 | Agent应用开发工程师（销售运营） | AMBIGUOUS |
| 11 | 麒麟软件 | 安全研发工程师 | NO_APPLICABLE_SKILL |
| 12 | 沐瞳科技 | AI客户端开发工程师-MOBA | frontend_web |
| 13 | 蔚来 | 座舱Agent Harness算法工程师 | ai_agent_application |
| 14 | 沐瞳科技 | 后端开发工程师（AI向/数据平台） | data_engineering |
| 15 | 麒麟软件 | 嵌入式研发工程师 | NO_APPLICABLE_SKILL |
| 16 | 蔚来 | 视觉算法工程师 | AMBIGUOUS |
| 17 | 沐瞳科技 | 后端开发工程师（IT信息化） | java_backend |
| 18 | 麒麟软件 | 后端研发工程师（Go/Java） | java_backend |
| 19 | 蔚来 | AI仿真与验证平台软件开发工程师 | NO_APPLICABLE_SKILL |
| 20 | 麒麟软件 | Python研发工程师 | python_backend |
| 21 | 蔚来 | AI模型部署与性能优化工程师 | NO_APPLICABLE_SKILL |
| 22 | 麒麟软件 | 测试/测试开发工程师 | testing_qa |
| 23 | 蔚来 | AI推理引擎开发工程师 | NO_APPLICABLE_SKILL |
| 24 | 蔚来 | 车端AI Agent运行时基座软件开发工程师 | NO_APPLICABLE_SKILL |
| 25 | 麒麟软件 | Linux内核研发工程师 | NO_APPLICABLE_SKILL |
| 26 | 蔚来 | 智能辅助驾驶大模型算法工程师（业务闭环） | NO_APPLICABLE_SKILL |
| 27 | 面壁智能 | 2027大模型算法工程师-智能体方向 | ai_agent_application |
| 28 | 麒麟软件 | 云原生研发工程师 | NO_APPLICABLE_SKILL |
| 29 | 沐瞳科技 | 全栈开发工程师 | frontend_web |
| 30 | 沐瞳科技 | C++服务器开发工程师（MOBA） | NO_APPLICABLE_SKILL |
| 31 | 百度 | Agent Harness研发工程师(J100815) | **frontend_web（错配）** |
| 32 | 百度 | AI智能化测试开发岗位 | testing_qa |
| 33 | 万兴科技 | AI应用开发工程师（Agent应用与平台方向） | ai_agent_application |
| 34 | 智慧芽 | AI应用开发工程师（Agent方向） | ai_agent_application |
| 35 | 快手 | Ai应用开发 | ai_agent_application |
| 36 | 中国电信-天翼云 | 后端开发工程师（JAVA方向） | java_backend |
| 37 | 联想 | 测试开发工程师 | testing_qa |
| 38 | 途虎 | 测试开发工程师（2027届校招） | testing_qa |
| 39 | 携程 | 测试开发工程师（2027届秋招） | AMBIGUOUS |
| 40 | 汽车之家 | 测试开发工程师（2027校园招聘） | testing_qa |
| 41 | 天猫技术 | AI Agent优化工程师 | ai_agent_application |
| 42 | 华为云计算技术有限公司 | AI应用开发工程师 | ai_agent_application |
| 43 | 华为技术有限公司 | 后端开发工程师（27届校招） | AMBIGUOUS |
| 44 | 上海华为技术有限公司 | 算法工程师（大模型训练方向） | recommendation_ml |
| 45 | 上海华为技术有限公司 | AI模型工程师-Agent技术 | **frontend_web（错配）** |
| 46 | 上海华为技术有限公司 | JAVA后端开发工程师 | NO_APPLICABLE_SKILL（偏严） |
| 47 | 北京汇智有识科技有限公司 | Python后端开发工程师 | python_backend |
| 48 | 华为云计算 | AI应用平台开发工程师 | ai_agent_application |
| 49 | 携程 | Java开发工程师（2027届秋招） | AMBIGUOUS |
| 50 | 字节跳动 | AI Agent开发工程师（客户端研发效能） | NO_APPLICABLE_SKILL |

### 3.4 抽查 5 条面经（正式库全量）

| 面经 | 公司 / 岗位 | 轮次 | 来源 URL | 抓取/导入记录 | 审核 | 可用题 | 可靠性 | 判断 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 17 | 中国工商银行 / 科技菁英 | 一面 | nowcoder.com/discuss/655487632662622208 | `MANUALLY_IMPORTED`，`EXTRACTED`，raw 1,084 字，hash `baf92e18…`，快照 8 同 hash | APPROVED，reviewer=1，2026-10-05 16:26:22 | 8 | 0.75 | 有正文与哈希，无在线复核 |
| 18 | 上海银行 / 金融科技（应用开发） | 技术一面 | nowcoder.com/feed/main/detail/ffb04e90… | 手工导入，raw 940 字，hash `f8ab6577…`，快照 6 | APPROVED，同一秒 | 14 | 0.75 | 同上 |
| 19 | 中国移动研究院 / AI算法工程师 | 一面 | nowcoder.com/discuss/427769028658184192 | 手工导入，raw 313 字，hash `e4fc8d10…`，快照 7 | APPROVED，同一秒 | 5 | 0.75 | 正文较短，信息量有限 |
| 20 | 华为 / 后端开发岗 | 无 | nowcoder.com/discuss/927377559333933056 | 手工导入，raw 2,276 字，hash `325617fd…`，快照 4 | APPROVED，同一秒 | 20 | 0.75 | 唯一有 `company_id=4`；5 场面试汇编，风险标记含"未来日期""多场拼接""无原始回答" |
| 21 | 字节跳动 / 算法岗 | 无 | nowcoder.com/discuss/922309082860507136 | 手工导入，raw 2,265 字，hash `87b45ddb…`，快照 5 | APPROVED，同一秒 | 20 | 0.75 | 多份面经拼接，部分截断 |

质量控制上的硬事实：

- 5 条的 `reviewed_by` 都是 1，`reviewed_at` 集中在 `2026-10-05 16:26:22.863–.940`（**同一秒批量写入**），`review_note` 全为 NULL。
- 5 条的 `quality_gate_json` 都写着 `"review_status": "WAITING_REVIEW"`、`"recommended_action": "REVIEW"`，而业务字段却是 `APPROVED`。**"审核通过"是批量置位，不是逐条人工复核。**
- `reliability_score` 全部 0.75，`extraction_confidence` 全部 0.9，没有区分度。
- 5 条的 `extraction_output_json` 里**都没有** `source_anchors.reported_topics`（0/5）。代码里 REPORTED_TOPIC 那条路径在现有数据上是空跑。

### 3.5 来源真实性分级

| 数据 | 数量 | 分级 | 依据 |
| --- | --- | --- | --- |
| 展示库 50 份 JD | 50 | **真实招聘页的"忠实概括"** | `sources.source_type=CURATED_PARAPHRASE`；快照 metadata：`provenance=CURATED_PARAPHRASE_FROM_REAL_JOB_POSTING`、`not_verbatim_official=true`、`live_opening_unverified=true`、`requirement_parse=RULE_ONLY_NO_MODEL`；URL 指向企业招聘官网/牛客职位页 |
| 展示库面经 | 0 | — | 表为空 |
| 展示库会话 | 627 | 544 场有消息，其中 **537 场只有 1 道题**，属批量演示 | 11-17 03:26:27–03:32:42 批量创建，抽查每场 4 条消息、1 答、1 评；真正的多轮会话只有 621/625/627/628/629/630/632 |
| 正式库 5 条面经 | 5 | **有来源证据的社区面经** | 牛客 URL + 本地正文快照 + hash 对齐；但无在线复核、无逐条审核记录 |
| 正式库 65 道可用题 | 65 | **模型抽取结果**（基于真实帖子正文） | `extraction_confidence=0.9`，抽取产物落库；不是独立外部来源 |
| 正式库 72 条 DISCOVERED | 72 | **不可用** | 仅搜索结果，未抓取正文 |
| 正式库 17 条 FETCHED | 17 | 原始素材 | 未全部进入面经 |

结论：**没有"编造的面经"记录，但也没有"公司级、岗位级、可复核"的面经库。** 现在能支撑的最强说法是："系统收录了 5 条带牛客链接与正文哈希的社区面经，覆盖 5 家公司；其中只有 1 家与公司主数据关联，0 条与岗位精确关联。"

### 3.6 跨公司、跨岗位匹配

- 题目级：67 道题的 `company/position` 与其所属面经**逐条一致**，没有发现 A 公司题目挂到 B 公司。
- 面经级：4/5 的 `company_id` 为空。`build_context` 的公司级作用域要求 `company_id == 会话公司`，所以这 4 条**永远不会被任何会话读到**（不是"匹配错"，是"匹配不到"）。
- 唯一关联的华为面经 20（`company_id=4`、`job_id=null`）在 V1 时期被 14 场「AI应用工程师」会话读取（会话 52–54、56–66），而其岗位写的是「后端开发岗」。**这是公司级记录跨岗位复用**：同一公司、不同岗位，靠"公司级"兜底放行。V2 自适应要求 `job_id` 精确相等，因此该记录在新链路里不会再生效——但这也意味着新链路下面经命中率为 0。

### 3.7 没有面经时的回退

61 场自适应会话的 `adaptive_blueprint.fallback` **全部是 `GENERIC_SKILL`**（61/61）。含义是：所有考察目标最终都只挂 `skill:<id>@1.0.0` 这一个通用来源，没有一条 `public_question:` 来源。产品行为符合设计（"公司资料不足就用岗位 Skill"），但也说明**面经在自适应链路里从未参与过任何一场面试**。

这种回退在提示词里也是**显式**的：`interview_main 3.0.0` 保留了两句边界——
`GENERIC_SKILL_NOT_COMPANY_EVIDENCE permits a general technical question, never a claim about company interview preferences.`
`REPORTED_TOPIC_NOT_VERBATIM is only a reported direction, never a verbatim company question.`
即：没有面经时允许问通用技术题，但不允许声称"这家公司会这么问"。

---

## 4. C. 公司面经是否真正影响了面试

### 4.1 加载时机

`build_context` 在**会话创建时一次性**读取：

```python
# 示意：省略 scope() 的 or_ 组合细节
experiences = select(InterviewExperience).where(
    scope(InterviewExperience), review_status.in_(("APPROVED", "AUTO_APPROVED"))).limit(30)
questions = select(InterviewQuestion).where(
    scope(InterviewQuestion), review_status.in_(("APPROVED", "AUTO_APPROVED")),
    should_index.is_(True),
    or_(experience_id.is_(None), experience_id.in_(exp_ids))).limit(100)
```

作用域是"精确 `job_id` 相同"或"`job_id` 为空且 `company_id` 相同"，且 `company_id` 不能是别的公司。读到的内容写进不可变快照：

```json
{"public_questions":[{"id":52,"question":"介绍项目，部署情况，技术栈",
  "dimension":"项目","difficulty":"MEDIUM","fingerprint":"17256b…","experience_id":20,
  "expected_points":[]}],
 "interview_experiences":[{"id":20,"summary":"这是一份华为后端开发岗的多面试经历汇编…",
  "fingerprint":"6b743715…"}]}
```

之后每轮只读快照，不再查库。

### 4.2 模型到底能看到什么

| 内容 | Main 是否可见 | 证据 |
| --- | --- | --- |
| 面经全文 `content_text` | **否** | 从未进入快照（快照只存 `summary[:2000]`） |
| 面经摘要 `summary` | **否** | `model_context()` 显式 `result.pop("interview_experiences")`；投影里也没有该键（6/6 会话验证） |
| 面经轮次 / 考察顺序 | **否** | 快照只存 `id/summary/fingerprint`，`interview_round` 不在其中 |
| 公共题原题 `question` | **是**（仅当该题被蓝图引用） | `planning_payload` 按 `public_question:<id>` 过滤后保留；#625–#632 均为 0 |
| 公共题 `expected_points` | **是**（随题面一起） | Main 投影只删 `fingerprint`，未删 `expected_points` |
| REPORTED_TOPIC 片段原文 | **否** | 候选投影只保留 `ref/source_type/source_id/kind` 与 `semantics`；`reported_excerpt` 被裁掉 |
| REPORTED_TOPIC 主题词 | **是**（间接） | 通过 `adaptive.targets[*].label/mention_terms` 出现 |

因此"模型使用面经"只有一种可能的形态：**模型看到公共题原题，自己重写措辞，并把 `source_question_id` 绑回数据库**。它拿不到面经正文，也拿不到轮次顺序。

### 4.3 实际引用证据

| 环境 / 链路 | PUBLIC_QUESTION 题 | REPORTED_TOPIC 题 | 说明 |
| --- | --- | --- | --- |
| 展示库全部 567 题 | **0** | **0** | 表里就没有面经 |
| 展示库自适应 61 场（30 题） | 0 | 0 | 1 题来自 JD、15 追问、6 简历、6 动态、2 自我介绍 |
| 正式库 V1（60 题） | **16** | 0 | 14 场会话（52–54、56–66） |
| 正式库自适应 | 0 | 0 | 无自适应会话使用面经 |

V1 的 16 道题全部绑定 `interview_questions.id = 52/53`，再追溯到 `experience_id = 20`（华为 / 后端开发岗 / 牛客 927377559333933056）：

| 会话 | 题目 ID | 绑定公共题 | 实际问法（模型重写） |
| --- | --- | --- | --- |
| 52 | 29 | 52「介绍项目，部署情况，技术栈」 | "介绍项目，部署情况，技术栈" |
| 53 | 30 | 52 | "先介绍一下你最有代表性的项目吧，重点讲讲项目背景…" |
| 59 | 47 | 53「QuerySet是什么，用过吗」 | "我们换个方向，聊一下 Django…" |
| 61 | 53 | 52 | "请你介绍一下金融财报智能问答系统这个项目…" |
| 66 | 106 | 52 | "先讲讲你最有代表性的项目…" |

这是**唯一**能拿出的"面经真正影响了提问"的证据：题源绑定是真的、可追溯的，但 (a) 只发生在 V1；(b) 只用了 2 道公共题；(c) 题面是模型重写，不是原题照搬。

### 4.4 能否从提问追到数据库与来源

可以，链长为 4 段，且主键完整：

```
interview_session_questions.source_question_id
  → interview_questions.id (review_status=APPROVED, should_index=true)
  → interview_experiences.id (source_url, source_snapshot_id)
  → source_snapshots.content_hash == experience_source_items.content_hash
```

自适应链路还额外保存了 `candidate_refs_json.adaptive_decision`：`question_source_ref`、`candidate_id`、`supporting_refs`、`main_agent_run_id`。例如：

| 题 ID | target | question_source_ref | supporting_refs | Main run |
| --- | --- | --- | --- | --- |
| 562 (#630) | `java_backend:redis` | `skill:java_backend@1.0.0` | `skill…`, `fact:103`, `fact:119` | 1236 |
| 566 (#632) | `ai_agent_application:rag_retrieval` | `fact:118` | `fact:118`, `skill…`, `requirement:210` | 1244 |
| 571 (#632) | `ai_agent_application:llm` | `requirement:210` | `requirement:210`, `answer:566`, `evaluation_run:1253` | 1254 |

571 的 `requirement:210` 原文是"了解Transformer、向量数据库、微调等大模型技术"，题目问的是"一条文本从进模型到变成可检索的向量…"。**JD → 题目**这条追溯是成立的；**面经 → 题目**在当前自适应会话里没有样本。

### 4.5 轮次与考察顺序

`interview_experiences.interview_round` 有值（一面/技术一面），`extraction_output_json.round_name` 也有值，但：

- `build_context` 只投影 `id/summary/fingerprint`，**没有把 round 写进快照**；
- 蓝图只按 `reported_topics` 加权，`reported_topics` 在现有 5 条数据里为 0；
- 面试轮次由用户在创建时选择（`technical_screen` / `project_deep_dive`），与公司面经的"一面/二面"无关。

结论：**第一面/第二面与考察顺序只被保存，没有参与面试策略。**

---

## 5. D. 主面试官能否主动调用工具

### 5.1 三个工具是真实可调用的

`main_harness.py` 定义了 `MainHarnessAction` 判别联合：`read_active_knowledge_gaps`、`inspect_current_skill`、`inspect_job_requirement`、`final`。每个工具都有：

- 严格参数模型（`extra="forbid"`、`strict=True`）；
- 2 秒超时；
- 结果统一走 `_feedback(status, code, data, refs)`，带 `content_hash` 与 `trust="UNTRUSTED_DATA"`；
- 2,048 字节上限，超限返回 `OUTPUT_LIMIT`；
- 重复调用返回 `DUPLICATE_TOOL`；
- 每次调用写 `ExperienceAgentStepRun(step_name="main_harness_tool")` 审计。

三个工具返回的数据都来自**本场冻结快照**，不是现查数据库：

| 工具 | 返回 | 作用域校验 |
| --- | --- | --- |
| `inspect_current_skill` | `id/version/fingerprint/priority_topics/instructions`（≤960 字符） | 只读快照里的 `interviewer_config.skill` |
| `inspect_job_requirement` | `statement`（≤1100 字符）、`importance`、`fingerprint` | 必须同时满足：id 在 `context.requirements`、且该 requirement 的 `ref+fingerprint` 出现在蓝图 `sources` 里，否则 `REQUIREMENT_NOT_IN_FROZEN_SCOPE` |
| `read_active_knowledge_gaps` | 当前目标最多 8 条开放缺口（每条 ≤95 字符） | 只读 `adaptive.knowledge_state` 的当前目标 |

### 5.2 默认关闭，必须显式开启

| 检查点 | 结果 |
| --- | --- |
| `CreateSessionRequest.main_harness` | `default=False`，`strict=True` |
| 与自适应绑定 | `main_harness=True` 且 `adaptive_interview=False` → 422 |
| 快照标记 | 只有显式开启才写 `adaptive_policy.main_harness_version` |
| 前端新建面试 | **不发送该字段**（`InterviewChatPage.vue`、`TrainingPage.vue` 均无 `main_harness`） |
| 训练模式 | 显式 `main_harness=False` |
| 环境开关 | 无全局开关，纯请求级 |

结论：**Harness 是显式 opt-in，前端从未开启。**

### 5.3 历史调用记录

| 数据库 | `main_harness` 快照 | `interview_main_harness` run | `main_harness_tool` 步骤 |
| --- | --- | --- | --- |
| 展示库 `showcase_r123` | 0 | 0 | 0 |
| 正式库 `interview_agent` | 0 | 0 | 0 |
| 回归库 `wb_r26_test` | 52 | 100 | **48** |

回归库的工具分布（这是唯一能证明工具"跑通过"的证据）：

| 工具 | 状态 / 代码 | 次数 |
| --- | --- | --- |
| `read_active_knowledge_gaps` | OK / ACTIVE_GAPS | 20 |
| `read_active_knowledge_gaps` | EMPTY / NO_ACTIVE_GAPS | 8 |
| `inspect_job_requirement` | OK / FROZEN_REQUIREMENT | 6 |
| `inspect_current_skill` | OK / FROZEN_SKILL | 6 |
| `read_active_knowledge_gaps` | ERROR / DUPLICATE_TOOL | 4 |
| `inspect_current_skill` | ERROR / TOOL_TIMEOUT | 2 |
| `inspect_job_requirement` | ERROR / REQUIREMENT_NOT_IN_FROZEN_SCOPE | 2 |

一次完整调用链（run 2564）：

```
step 2740  interview_main      → {"kind":"tool","name":"inspect_job_requirement",
                                  "arguments":{"requirement_id":1431}}
step 2741  main_harness_tool   → OK / FROZEN_REQUIREMENT
                                  statement="RAG 检索和 Agent Tool Calling；…"
step 2742  interview_main      → {"kind":"final","selection":{"candidate_id":"C_0661…"}}
```

但这条链的 `provider_name` 是 `replay` / `openai_compatible(MOCK_*)`，**100 个 harness run 全部是回放或 Mock HTTP，没有一次真实模型自主决策**。所以能说的是："工具体制、作用域校验、审计、二次调用都通"；不能说的是："真实面试官在真实面试里会主动用工具"。

另外，run 2564 的最终选择是 `DEEP_DIVE`（`skill:ai_agent_application` + `answer:774`），**没有使用**它刚查到的 requirement 1431。这符合回放脚本的行为，也提醒我们：工具结果"进入了下一轮输入"与"改变了决策"是两件事，现有数据只能证明前者。

### 5.4 有没有面经检索工具

没有。全代码只有 3 个 Main 工具；报告侧的 `read_evaluation_detail` / `read_capability_history` 属于 `interview_report` 模块，不是面试中的工具。`interview_experiences` / `interview_questions` 没有任何工具入口。

### 5.5 四个能力必须分开看

| 能力 | 现状 | 证据 |
| --- | --- | --- |
| ① 数据库里有面经 | 展示库 0；正式库 5 条 | 3.1 / 3.2 统计 |
| ② 会话创建时读取面经 | 代码路径存在；展示库 0 场；正式库 14 场 V1 | `build_context` + 快照 `public_questions` |
| ③ 面经作为参考进入模型输入 | 仅"公共题原题"可进入；摘要/全文/轮次都进不去 | `model_context` pop + 6 场 wire 验证 |
| ④ 模型主动调用工具查询面经 | **不存在** | 只有 3 个工具 |

---

## 6. E. 结论与下一步

### 6.1 三个核心问题的明确结论

| 问题 | 结论 | 限制 |
| --- | --- | --- |
| 10 个专业 Skill 是否真正进入面试链路并影响提问 | **部分实现，有实际运行证据** | 链路（manifest → freeze → blueprint → wire → prompt hash）完整且可验证；但只有 2/10 被真实会话使用，8 个零运行时样本；推荐器存在词面碰撞错配 |
| 数据库是否收录不同公司的真实面经，当前岗位能否使用 | **部分实现，且业务库为空** | 正式库 5 条社区面经（5 家公司、1 个 company_id、0 个 job_id），65 道可用题；展示库 50 岗位 0 面经。当前岗位只能用 JD + Skill |
| 主面试官能否主动调用工具检索公司面经 | **尚未实现** | 有工具框架，无面经工具；Harness 在展示/正式库 0 启用；真实模型 0 次工具调用 |

补充一条"无足够数据验证"：**REPORTED_TOPIC 面经主题路径**。代码完整，但 5 条面经的 `reported_topics` 全为空，任何会话都没产生过 `reported_topic:` 题源，因此这条路径只有代码证据，没有运行证据。

### 6.2 最小可行设计：`search_reviewed_interview_experiences`

只给方案，不实现；不改 Evaluation、不改现有 Prompt、不新增 Agent。

**目标**：让 Main 在"是否需要参考这家公司真实面试过什么"上拥有一次只读查询能力，同时不引入任意 SQL、不扩大数据作用域、不把"审核通过"说成"公司官方"。

**1）数据前置（必须先做，否则工具永远返回空）**

- 给 5 条面经补 `company_id`（4 条）与 `job_id`（按岗位映射；无精确岗位的保持 NULL 并打标 `COMPANY_LEVEL`）。
- 补 `source_anchors.reported_topics`（当前 0/5），每个片段带 `fragment_id / raw_excerpt / source_snapshot_id / location`。
- 审核补 `review_note` 与真实 reviewer，质量闸门与 `review_status` 必须一致；`WAITING_REVIEW` 不得被当成可用。
- 展示库若要演示这条能力，需要把这 5 条（或新的合规数据）导入，并完成公司/岗位映射；否则工具在 50 个岗位上一律 `EMPTY`。

**2）冻结作用域（会话创建时）**

在快照新增只读块，不新增表：

```json
"reviewed_experience_scope": {
  "version": "REVIEWED_EXPERIENCE_SCOPE_V1",
  "company_id": 1001, "job_id": 2002,
  "items": [
    {"ref":"experience:301","round":"一面","reviewed_at":"...","reliability":0.75,
     "source_snapshot_id":77,"topics":["redis","mysql_consistency"],
     "question_refs":["public_question:401","public_question:402"]}
  ],
  "max_items": 3, "max_questions": 6
}
```

（以上为结构示例，id 非真实数据；真实作用域必须由 `build_context` 按 3.6 的精确公司+岗位规则生成。）

规则：只收 `APPROVED/AUTO_APPROVED` + `should_index=true`；`company_id` 必须精确相等；`job_id` 必须精确相等（V1 不允许公司级兜底，避免重演 3.6 的跨岗位复用）；最多 5 条经验、20 道题；只存引用与主题，不存正文。

**3）工具契约**

```json
{"kind":"tool","name":"search_reviewed_interview_experiences",
 "arguments":{"topics":["redis"],"max_items":3}}
```

- `topics`：0–2 个，必须来自本场 `adaptive.targets[*].topic`，**不接受自由文本**；
- `max_items`：1–3；
- 不接受 company/job/limit/SQL 参数，作用域只能来自快照；
- 返回：

```json
{"status":"OK","code":"REVIEWED_EXPERIENCES",
 "data":{"scope":{"company_id":1001,"job_id":2002},
   "items":[{"experience_ref":"experience:301","round":"一面","reviewed_at":"...",
     "reliability":0.75,"source":{"domain":"example.com",
     "url":"https://example.com/interview/301"},
     "topics":["redis","mysql_consistency"],
     "questions":[{"ref":"public_question:401","text":"介绍项目，部署情况，技术栈"}]}],
   "truncated":false},
 "source_refs":["experience:301","public_question:401"],
 "content_hash":"...","trust":"UNTRUSTED_DATA"}
```

- 上限沿用现有 `_feedback` 的 2,048 字节；题干 ≤140 字符、URL ≤200 字符；**不返回面经全文**；
- 空结果：`EMPTY / NO_REVIEWED_EXPERIENCE_FOR_SCOPE`（明确告诉模型"本场没有可用面经，用 Skill"）；
- 越界：`ERROR / SCOPE_EMPTY` 或 `EXPERIENCE_NOT_IN_FROZEN_SCOPE`；
- 去重、超时、重复调用沿用现有 `DUPLICATE_TOOL` / `TOOL_TIMEOUT`。

**4）引用与出题闸门（不改现有校验）**

- 把 `experience:<id>`、`public_question:<id>` 加入本场 `adaptive.allowed_refs`；
- 工具只返回**已在冻结 `public_questions` 里**的题；模型若要用它出题，仍必须选到对应候选、`validate_question` 仍检查 `source_question_id ∈ public_questions`；
- 工具本身**不能新增题源**。若要支持"查到一条新面经并当场出题"，那是第二阶段（需要 Controller 允许追加来源 + 重新冻结），本轮不建议。

**5）Prompt 与可用性提示**

- `MAIN_HARNESS_CONTEXT_HINTS` 升到 `2.4.0-harness.3`，加一段：
  "若公司面试证据会影响你的选择，可查询已审核面经；返回的是候选人报告过的主题与题目，不是公司官方题库，也不代表必考。查不到就用 Skill。是否查询由你决定，不查询不扣分。"
- `context_availability` 增加 `reviewed_experiences`：`AVAILABLE_ON_DEMAND` / `NO_REVIEWED_EXPERIENCE_FOR_SCOPE`。

**6）预算与审计**

- 复用 `TOOL_LIMIT=2`、`HTTP_LIMIT=4`；工具本身 0 次模型 HTTP；
- 每次调用写 `main_harness_tool` 步骤（input=topics/max_items，output=status/code/refs）；
- 结果 `trust=UNTRUSTED_DATA`，防注入（题干里出现"忽略以上指令"不得改变作用域）。

**7）验收（离线，0 HTTP）**

1. 精确公司+岗位：只返回该岗位的已审核面经；
2. 同公司不同岗位：返回 `EMPTY`（证明不跨岗位）；
3. 未审核/`should_index=false`：不可见；
4. 非本场 topic：参数校验失败；
5. 返回条数 ≤3、题目 ≤6、字节 ≤2048；
6. 题干含注入文本：不改变 refs、不越界；
7. 重复调用：`DUPLICATE_TOOL`；
8. 超时：`TOOL_TIMEOUT`，不阻塞出题；
9. 老会话（`2.4.0-harness.1/.2`）不受影响；
10. 现有 3 个工具行为不变。

**明确不做**：不给模型任意 SQL / 表名 / 自由文本检索；不跨公司聚合；不抓取外部网页；不把面经摘要或全文塞进 system；不改 Evaluation 2.3.4。

---

## 7. 限制与风险

1. **请求体未落库**：Skill 进入请求的证据是"快照 + 同代码重放 + prompt hash"，不是报文抓包。若要更强证据，需要在步骤表增加 `payload_sha256`（本轮不改代码）。
2. **展示库与正式库分裂**：面经只在正式库，50 个岗位只在展示库。任何"面经已接入"的演示，都必须先做数据迁移与映射，否则就是 0。
3. **审核语义**：5 条面经的 `review_status=APPROVED` 与 `quality_gate=WAITING_REVIEW` 矛盾，且同一秒批量置位。建议把"可用"定义为 `APPROVED + quality_gate.passed + review_note 非空`。
4. **推荐器词面碰撞**：`状态管理`、`性能优化` 这类跨领域词会把 Agent Harness 岗位推给前端 Skill。建议对 `frontend_web` 的 `responsibility_terms` 加共现约束（必须同时命中前端专有词），或在推荐结果里标注 `evidence_field` 供人工复核。
5. **8 个 Skill 无运行时样本**：不能因为"目录存在、能 freeze"就宣称"10 个专业方向都已验证"。当前可宣称的是 2 个。

---

## 8. 本轮未做与未改

- 未调用任何模型（HTTP=0），未抓取任何外部页面。
- 未修改展示库/正式库任何数据（所有连接 `default_transaction_read_only=on`）。
- 未改 Skill、Prompt、Evaluation、前端、Controller。
- 未新增 Agent、未新增工具实现（第 6.2 节只是设计）。
- 未提交、未 Push。工作区仅有 `data/r131_audit/` 下的只读审计脚本与 JSON 产物，以及此前已存在的未跟踪数据文件。
