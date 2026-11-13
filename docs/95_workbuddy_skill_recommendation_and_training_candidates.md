# 95 · 私有 JD 的 Skill 推荐与确定性训练候选 V1（第十六轮）

> 本轮是产品功能整合，不扩展 Agent 框架。真实模型 HTTP = 0；所有模型边界使用离线 replay，浏览器验收由本地离线 API 提供。
> 标注：`事实` = 有代码/命令/测试/浏览器证据；`推断` = 未验证；`待办` = 明确未做。

---

## 一、现有 Skill 推荐的实际实现与复用范围（事实）

**已有能力（本轮未重写）**

| 位置 | 作用 |
|---|---|
| `apps/api/src/interview_api/modules/interview/skill_recommendation.py::recommend` | 纯确定性匹配：`category=10 / title=8 / responsibility=6 / capability_group=3`，`MIN_SCORE=9`，`MIN_MARGIN=3`，`POLICY_VERSION=1.1.0`；同分按 `skill_id` 固定排序 |
| 同文件 `discover()`（`skill_registry.py`） | 只收录合法注册的 Skill：manifest 校验、目录名与版本一致、资产路径在根内、`required_tools` 必须为空、同 ID 取最高版本；非法 manifest 被隔离并记日志 |
| `recommend_for_target(target)` | 用私有目标的 `requirements_json` 与 `structured_json.responsibilities` 复用同一套规则；不读公司名、不写公共表 |
| `POST /api/v1/interview/skill-recommendations` | 既有公共岗位批量推荐接口，本轮未改 |
| `context.py` 创建会话时 | 仍用 `recommend_for_target`/`recommend_jobs` 冻结推荐，并对 `AUTO_RECOMMENDED` 做一致性校验（推荐结果变化 → `409 SKILL_RECOMMENDATION_CHANGED`） |

**本轮新增（最小补充，不改排序/判定）**

| 变更 | 说明 |
|---|---|
| `recommend(..., full_evidence=False)` / `recommend_for_target(..., full_evidence=False)` | 仅私有响应投影传 `True`，避免历史 `hits[:3]` 截断丢掉 requirements 的逐字证据；默认值保持原有信封，公共路径与冻结快照逐字节不变 |
| `target_skill_input_fingerprint(target)` | 对 `version + job_title + requirements(id/statement/evidence) + responsibilities` 做稳定哈希。**`content_fingerprint` 只覆盖 `raw_text`**，编辑要求不会改变它，因此不能用来判断推荐是否过期 |
| `project_target_recommendation(target, recommendation, expected_key=...)` | 只读响应投影：附逐字 JD 证据、证据状态、不确定性标记、过期标记；不重排、不编造相关性、不改 registry |
| `JobTargetService.skill_recommendations()` | owner-scoped 读取（别人的 id 与不存在同为 404）；未确认 JD **不运行匹配**，直接返回 `NOT_CONFIRMED` 空投影 |
| `GET /api/v1/job-targets/{target_id}/skill-recommendations?recommendation_key=...` | 只读接口；`recommendation_key` 用于判断此前的一键选用是否已过期 |

**明确未做**：没有新增推荐系统、没有新增 LLM 调用、没有 Agent Tool Loop、没有数据库迁移。

---

## 二、Skill 推荐的证据与排序语义（事实）

1. **候选来源**：`discover()` 返回的每个 `(skill_id, version)`；响应里 `version` 即本地注册版本。
2. **排序**：沿用现有 `ranking_points` 整数权重之和；`rank` 为排序位次；`excluded=True` 表示该 JD 明确不适用（如排除词命中 title/职责）。
3. **不是置信度**：响应固定携带
   `ranking_semantics = "ranking_points 是公开匹配规则的整数权重之和（category / title / responsibility / capability_group），不是概率、百分比或模型置信度；同分按 skill_id 固定打破，排序可复核。"`
   以及 `confidence_note = "不提供百分比或置信度分数；每个候选的 uncertain 与 evidence_status 标明证据是否可核对。"`
4. **逐字 JD 证据**：每条命中通过 `requirement_id` 关联回 `requirements_json`，给出
   - `verbatim_jd_evidence`：该条要求的逐字原文（`evidence` 字段，入库时已用 NFKC + 空白归一校验为原文子串）；
   - `evidence_status`：`VERBATIM_FROM_JD` / `USER_ADDED_NO_VERBATIM_QUOTE` / `STRUCTURED_DUTY_NOT_VERBATIM_CHECKED` / `USER_SUPPLIED_TITLE_NOT_A_JD_QUOTE` / `REQUIREMENT_NOT_FOUND_IN_CONFIRMED_VERSION`。
   职责与标题命中会如实标注"非逐字校验"或"不是 JD 原文引用"，不冒充原文。
5. **不确定性**：`uncertain = excluded or not contextual or 没有逐字证据`；`status` 沿用
   `RECOMMENDED / AMBIGUOUS / NO_APPLICABLE_SKILL / INSUFFICIENT_DATA`，并新增 `NOT_CONFIRMED`。
   证据不足时只给方向性候选或空列表，不编造相关性。
6. **空列表原因**：`empty_reason ∈ {JD_NOT_CONFIRMED, NO_REGISTERED_SKILL, NO_ELIGIBLE_CANDIDATE}`。
7. **只读性**：响应带 `source="DETERMINISTIC_RULE"`、`model_calls=0`、`skill_registry_read_only=True`；不写 `companies/jobs/job_description_versions/job_requirements`，不改 registry 文件。

---

## 三、确定性训练候选的计算方式（事实）

**纯函数**：`apps/api/src/interview_api/modules/interview/training_candidates.py::build_training_candidates(views, states, ...)`

- 输入 `views` 来自 `training.target_views(context, states)`，其 `available_modes` **直接来自 `interview.training.modes()`** —— 与 `freeze_training` 创建时用的是同一函数。
- 输入 `states` 来自 `CapabilityMemoryService.list(user_id)`（读取时按 `clock` 重新投影，因此 `RECHECK_DUE` 等时间条件反映"当前时刻"）。
- 只为存在非 `FULL` 模式的 view 生成针对性候选；`UNKNOWN` / `VERIFIED` / `revoked` 不会凭空产生弱项或复测。
- **排序确定**：`priority = STATE_PRIORITY[state] * 10 + min(target_priority, 4)`，`STATE_PRIORITY = {CONFLICTED:4, RECHECK_DUE:3, GAP_SUSPECTED:2, PRACTICING:1}`；同分按 `(knowledge_id, mode)` 排序。把输入列表反向传入，输出顺序不变（有单测）。
- **多个候选并列**：不截断为唯一目标；相近目标同时展示。
- **正式引用**：`evidence.evaluation_refs = ["evaluation:<id>", ...]`、`memory_revision`、`observations`（session/evaluation/assisted/state/observed_at）、`conflicts`；无正式评价时该列表为空并写明"不编造弱项"。
- **跨岗位区分**：候选固定 `capability_scope="CROSS_JOB_TOPIC_AGGREGATE"`、`target_relevance="CURRENT_TARGET_BLUEPRINT"`，并附
  `scope_note = "能力状态按 (用户, topic) 跨岗位聚合，不是本岗位掌握度……通用能力 VERIFIED 不能解释为当前岗位已完全胜任。"`
  历史中不在当前目标里的 topic 单列到 `general_only`（`relation="GENERAL_HISTORY_NOT_CURRENT_TARGET"`），不当作当前岗位候选。
- **空原因**：`NO_LEGAL_TARGETS / NO_HISTORY_EVALUATIONS / NO_ACTIONABLE_WEAKNESS_OR_RECHECK / TARGET_EXPIRED_OR_UNAVAILABLE`；无针对性候选时给 `fallback.mode="FULL"`（FULL 恒合法），明确不针对任何编造弱项。
- **服务端组合**：`TrainingService.candidates(user_id, request, *, session_id=None)`
  - 带 `session_id` 时，来源与 Skill 从**该用户会话的冻结快照**读取（不读可变 JD）；跨账号 id 返回 `TARGET_UNAVAILABLE`。
  - 与 `workbench` 一样只做 `build_context` 预览，最后 `rollback()`：不写能力记忆。
  - 目标被编辑/未确认/不可用时返回 `status="TARGET_UNAVAILABLE"`、`empty_reason="TARGET_EXPIRED_OR_UNAVAILABLE"`，不把旧建议当作可执行。
  - 路由：`POST /api/v1/interview/training/candidates?session_id=...`。

---

## 四、模型建议与规则候选的职责边界（事实）

| 维度 | 模型 Coach 建议 | 规则候选 |
|---|---|---|
| 产生时机 | 报告生成时（`coach_analysis.suggested_training`） | 每次读取时按当前状态重算 |
| 性质 | 历史结论（当时的输入） | 当前可执行方向 |
| 页面标注 | `下一场可以练什么（模型 Coach · 报告生成时的历史结论）` + "不是此刻重新计算的可执行状态" | `当前可训练候选（规则生成 · 读取时计算 · 0 次模型调用）` + "与上方模型建议来源不同，二者不等价" |
| 落库 | 随报告 generation 保存 | 不落库、不写 Memory、不覆盖报告 |
| 执行 | 链接只预选，训练中心重建校验 | 链接只预选，训练中心重建校验 |

本轮**未**修改 Harness OFF 的 Prompt、Schema、`coach_generation_key`，未重新生成或覆盖任何既有报告。

---

## 五、最新 Memory 与历史报告如何保持一致（事实）

- 候选响应携带 `computed_at` 与 `state_fingerprint`（对 `(topic, state, revision, last_observed_at, revoked)` 的稳定哈希）。同一快照重复请求，排序与指纹一致（有单测）。
- 训练页每次加载都重新请求候选；点击"开始训练"仍走 `TrainingService.create` → `freeze_training` 重新校验当前 `available_modes`。状态变化后旧候选的 `mode` 会被 `422 TRAINING_TARGET_INVALID` 拒绝（有单测）。
- 报告页的规则区块独立于报告存在与否；它不读取、不改写 `coach_analysis_json`。
- 未把候选写成 `CapabilityMemory` 的正式事实；也没有新增岗位维度的 Memory 聚合（本轮契约明确不改核心聚合）。

---

## 六、私有岗位与公共岗位的兼容情况（事实）

- 公共岗位：`recommend_jobs()`、`POST /interview/skill-recommendations`、`recommend()` 默认信封全部未改；`test_public_job_recommendation_rules_are_unchanged` 断言 `policy_version/job_id/jd_id/ranking` 形状不变。
- 公共训练：`TrainingService.workbench` 与 `candidates` 对 `company_id/job_id` 路径返回 `job_target_id=None`、`source_label=None`（有单测 `test_public_job_training_path_does_not_regress`）。
- 私有岗位：只在 `job_target_id` 路径新增推荐读取与候选投影；不创建公共 Company/Job/JD/Requirement（单测比对四张表计数；浏览器也验证了私有训练不写公共表）。
- 已有面试路径：`context.py` 未改；`AUTO_RECOMMENDED` 的创建期一致性校验仍生效。

---

## 七、权限、幂等与版本冻结（事实）

| 契约 | 实现与证据 |
|---|---|
| 跨账号不可读 | `JobTargetService.read/owned` 以 `(id, user_id)` 过滤，"不存在"与"别人的"同为 `404 JOB_TARGET_NOT_FOUND`；service 单测 + 真实浏览器第二账号 404 |
| 未确认不推荐 | `skill_recommendations` 在 `parse_status != CONFIRMED` 时直接返回 `NOT_CONFIRMED`，不运行匹配 |
| 推荐不强制 | `auto_select` 仅表示规则有明确领先方向；用户可手改；`applySkillRecommendation` 纯 reducer 对 `USER_SELECTED` 直接返回，不被异步结果覆盖（浏览器实测：手动改为 `ai_agent_application` 后 `overwritten=false`） |
| 过期识别 | 响应带 `input_fingerprint` 与 `stale/stale_reason`（`recommendation_key` 对比确认输入）；编辑要求后 `parse_status` 回到 `PARSED`，接口不再给正式推荐；重新确认后 `version+1`、指纹变化 |
| 最终冻结实际选择 | 创建会话仍由 `SkillRegistry().freeze()` 冻结用户实际选择的 `skill_id/version`；`USER_SELECTED` 与 `AUTO_RECOMMENDED` 都能创建（单测验证快照 id/version） |
| 幂等 | 训练创建仍要求 `training_request_key`；同键同内容返回同一 session，同键异内容 `409`，缺键 `422`；候选的 `launch` 只是链接，不自动创建（浏览器实测会话数不变） |
| 不写生产 | 浏览器验收使用独立 E2E 库 `interview_agent_wb_r16_e2e_20261113`；`interview_agent` 未被写入 |

**注意（如实记录）**：`--database` 参数在启动器的 `api` 模式下未生效，首次启动曾连到 `interview_agent`。只发生了登录查询（401，无写入），随即停止并用显式 `DATABASE_URL` 指向 E2E 库重启；之后所有浏览器操作都在 E2E 库完成。

---

## 八、浏览器实际操作与截图（事实）

环境：真实 Chrome 157（headless，CDP 9222）、Vite 5173、离线验收 API 8000（同一 FastAPI 应用，模型边界为 replay，阻断出站 HTTP）。库 `interview_agent_wb_r16_e2e_20261113`。

| # | 操作 | 结果 |
|---|---|---|
| 1 | 登录 `r11.owner@example.com`（E2E 库内重设口令） | 进入 `/dashboard` |
| 2 | 粘贴 JD → 保存 → 解析 → 确认 | `Java后端工程师`，`CONFIRMED` v1 |
| 3 | 打开目标岗位详情 | 显示「推荐的专业方向（规则匹配 · 0 次模型调用）」：`Java 后端开发 v1.0.0`、第 1 位、命中权重 23、系统首推、规则理由、`JD 原文逐字证据`、`ranking_points` 语义、无百分比声明、手动更换说明 |
| 4 | 点「用这个方向开始面试」 | 跳 `/interview`；Skill 预选 `java_backend`；显示"根据岗位规则自动推荐"与规则来源；会话数 0 → 0 |
| 5 | 手动把 Skill 改为 `ai_agent_application` | 显示"用户手动选择"；等待 2s 后仍为手动值（`overwritten=false`） |
| 6 | 点「用这个方向开始训练」 | 跳 `/training`；岗位为私有 JD、专业方向 `java_backend`、模式 FULL、候选区块已加载但为空（无历史评价）并有原因说明；会话数 0 → 0 |
| 7 | 训练页设 1 轮 → 开始 FULL → 作答 | 会话 1 创建并 `COMPLETED`（replay 评价） |
| 8 | 回训练页 | 「下一步训练候选」：`Java 与并发` · 薄弱项专项训练 · 规则优先级 14 · `evaluation:1` · 能力修订 1 · 跨岗位聚合说明 · 复查提示 |
| 9 | 点「选用此候选（仅预选，不创建训练）」 | 模式变 `WEAKNESS`、知识点 `java_backend:java`、Skill `java_backend`、URL 未变、会话数 1 → 1 |
| 10 | 报告页 `/interview/reports?session_id=1` | 规则区块独立显示"规则生成 · 读取时计算"、来源不同、`evaluation:1`、链接"仅预选，不会自动创建训练"；报告存在但 `suggested_training=null`，因此模型建议块未渲染（没有模型建议就不显示，符合预期） |
| 11 | 第二账号请求私有 JD 推荐 | `404 JOB_TARGET_NOT_FOUND`；其目标列表 0 条 |

截图 9 张（`.runtime/ui/shots/`，gitignored）：`r16_01_jd_skill_recommendation.png`、`r16_02_interview_preselect.png`、`r16_03_manual_skill_kept.png`、`r16_04_training_preselect.png`、`r16_05_full_session_done.png`、`r16_06_training_candidates.png`、`r16_07_candidate_preselected.png`、`r16_08_report_candidate_sources.png`、`r16_09_cross_account_404.png`。

---

## 九、后端、迁移与前端测试结果（事实）

- 专项（两段式运行器，隔离库 `interview_agent_wb_r16_test_20261113`）：`tests/test_private_jd_skill_recommendation.py` + `tests/test_training_candidates.py`，**18 passed / 0 failed / 0 skipped**。
- 全量业务回归（修复后，第三轮，库 `interview_agent_wb_r16_full3_test_20261113`）：**840 passed / 0 failed / 1 skipped**。
  过程如实记录：第二轮为 `839 passed / 1 failed / 1 skipped`，唯一失败是新加的 HTTP 断言在 Windows 多事件循环下复用了全局 ASGI engine（`NoneType.send`），**非产品缺陷**；已改为同义的服务层 404 + 路由注册断言，跨账号 HTTP 由真实浏览器覆盖。第一轮 `exit 0`，但其 JUnit 计数未留存，故以第三轮为准。
- 迁移专项（独立空库 `interview_agent_v21_wb_r16c_20261113`）：**10 passed / 0 failed / 0 skipped**。
- 前端：`npm run build`（`vue-tsc && vite build`）通过，0 类型错误；构建产物含全部来源标注文本。
- 真实模型 HTTP：**0**（所有模型调用为 replay；离线 API 阻断出站）。

---

## 十、仍需真实模型验证的部分（待办 / 推断）

1. **真实模型抽取的 JD**：本轮 JD 解析走离线 replay，人工确认的要求与真实抽取的措辞可能不同；推荐只读 title/职责/要求文本，编辑 requirements 不一定改变推荐结果（现有算法的真实特性，已用输入指纹识别过期，而不是假装结果会变）。需要真实样本评估命中质量。
2. **真实用户的能力状态**：`CONFLICTED` / `RECHECK_DUE` 候选由 replay 评价产生；本轮**没有**真实用户历史，也没有真实模型生成的 `suggested_training`（报告里为 `null`）。因此"模型建议 + 规则候选同时出现时的取舍"仍缺真实样本。
3. **岗位维度**：能力记忆仍按 `(user, topic)` 聚合，无岗位维度；候选已明确标注跨岗位聚合与当前相关性，但不能据此声称"本岗位胜任度"。
4. **浏览器未跑真实模型**：本轮额度为 0，所有页面数据来自离线 replay；真实模型下的推荐理由、候选展示与用户理解成本未验证。
5. **HTTP 层跨账号断言**：pytest 内只测 service 层；HTTP 404 由浏览器第二账号验证，未在 pytest 内重复启动完整 ASGI 栈（Windows 事件循环限制，已说明）。

---

## 结论

- 私有 JD 的 Skill 推荐已能从真实页面直接使用：确认后展示方向、版本、JD 逐字证据、规则理由与不确定性，并可一键预选面试/训练；手动选择不被覆盖；跨账号 404。
- 训练候选基于正式 `CapabilityMemory` 状态与 `training.modes()`，带 `evaluation:*` 引用，排序可复核，点击只预选、不创建；创建仍走幂等 + 重新校验。
- 未改 Harness OFF 契约、旧报告、Memory 核心聚合或报告 Schema；**无需迁移**。
- 仍需真实模型/真实用户样本验证推荐命中质量与"模型建议 vs 规则候选"的实际取舍。
