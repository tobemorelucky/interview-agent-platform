# R135：全系统最终审计、剩余修复与真实 API 验收

日期：2026-11-19　起点 HEAD：`dc64479`（R134）　本轮模型 HTTP：**27 / 50**　Token：**217,176 / 400,000**　DeepSeek Search/Fetch：**0**

## 结论：CONDITIONAL GO

具备可展示的完整主链路：注册/简历/JD 匹配、Skill 推荐、自适应面试、真实模型提问与评价、报告、成长记忆提案、面经采集审核与按需检索、三题型，均有真实模型或真实数据证据。**唯一阻断正式展示的前提**：生产部署需补 `confirm_unknown_retry` 前端确认（本轮已修复代码，未做真实故障注入验收），且需接受"检索面经尚未真正成为最终题源"这一已知限制。详见 §7 GO 判据。

本轮是交付验收，不是继续研发。所有结论标注为事实（有代码/DB/会话/测试证据）或推断，未通过人为破坏真实会话制造错误，未用 Mock 冒充真实调用，未改历史评价/审核/真实数据。

---

## 1. 第一阶段：零 HTTP 全系统预检

| 检查项 | 结果 | 证据 |
|---|---|---|
| Git HEAD/最近提交 | `dc64479` ← `d97a260` ← `6c32b23` ← `849f632`… | `git log --oneline -12` |
| R127–R134 代码完整 | 干净 worktree 从 HEAD 检出 1389 文件成功 | `git worktree add --detach /tmp/r135_clean HEAD` |
| 工作区 | 无已跟踪文件改动（预检时）；40 个未跟踪均为历轮 `data/` 产物 | `git status --short` |
| 服务状态 | PostgreSQL/Redis/MinIO/SearXNG 健康；API 8000、Web 5173 监听；worker pong | `status-dev.bat`、`netstat` |
| 实际连接库 | API 连 `interview_agent_showcase_r132_20261118`（启动日志 `DB=…r132`，`MODEL_LEDGER_DATABASE` 同步） | `showcase-target.ps1`；API 返回 50 个展示岗位 |
| status 显示 0025 的澄清 | `status-dev.bat` 读 `.env` 默认正式库，不切库；实际服务库是 0030 | 各库 `alembic_version`：正式库 0025、R132/R134/R135 均 0030 |
| R134 数据完整 | 面经 1（百度/APPROVED/0.72/job 31/reviewed_at 非空）、17 题、会话 641/642/643、报告 19 完整 | R134 库 SQL |
| 残留任务 | R134 会话 641/642 COMPLETED、643 IN_PROGRESS（定向复测用一轮即停）；0 RESERVED 账本；R134 会话 `unknown_model_calls` 全空 | R134 库 SQL |
| 迁移一致性 | R132/R134/R135 均 alembic 0030；正式库 0025 保持历史不动 | `alembic_version` |
| Skill manifest | 10 个 Skill 全部加载、0 quarantine；R132 更新的 ai_agent/frontend/java 为 1.0.1，其余 1.0.0 | `skill_registry.discover(refresh=True)` |
| 干净克隆测试 | 21 个面试/数据相关文件在全新克隆库 `interview_agent_wb_r135_clean_test_20261119` 上 **239 passed / 0 failed**（另有 14 核心文件基准 157 passed） | 见 §6 |
| 未清除历史数据 | 全程新建独立库，未删改任何业务/审核/评价记录 | 操作记录 |

关于电脑重启：`docker compose up -d postgres redis minio` 后 PostgreSQL 做了崩溃恢复（fsync 大量库文件，约 1 分钟 unhealthy 后 healthy）。服务通过后台保活任务重启（start-showcase.bat，指向 R132 库，不碰 R134/R135）。临时保活任务与正式启动脚本职责分离：保活只持有 shell 让子进程存活，库名由 `-Database` 参数显式授权，不靠环境变量猜测。

---

## 2. 第二阶段：全系统功能完成度矩阵

判定：**LIVE_PASS** 真实调用完成 · **REPLAY_PASS** 离线模拟通过 · **FALLBACK_PASS** 仅保底恢复 · **PARTIAL** 部分实现 · **NOT_VERIFIED** 未验证 · **FAILED** 真实失败 · **NOT_IMPLEMENTED** 未开发。

### A. 用户与基础数据

| 功能 | 判定 | 证据 |
|---|---|---|
| 注册登录/鉴权/用户隔离 | LIVE_PASS | API 登录返回 token，43 用户/20 简历；`user_id` 贯穿会话归属；干净库测试含隔离用例 |
| 简历上传/解析/画像 | LIVE_PASS（代码+数据+测试） | `ResumeListPage.vue:84-99` multipart 上传；20 条简历数据；画像 generation 链路见 governance |
| 简历事实/证据引用/修改 | LIVE_PASS | R135 会话 643 开场题引用 `fact:117`；`candidate_evidence` 投影；报告逐题证据 |
| 私人岗位/公共岗位管理 | LIVE_PASS | 50 公共岗位 + 私人 JD 页面 `JobTargetPage.vue:122-196`；R135 测试 `test_private_jd_skill_recommendation` 通过 |
| JD 解析与岗位匹配 | LIVE_PASS | 252 条 job_requirements；匹配链路有真实数据与测试；报告页保留 AI 排名 |
| 数据权限/异常/重复请求 | LIVE_PASS（测试） | 隔离闸 `db_targets`、幂等键、UNKNOWN 租约；干净库测试全通过 |

### B. 专业 Skill

| 功能 | 判定 | 证据 |
|---|---|---|
| 10 Skill 可用/冻结一致 | LIVE_PASS | `discover` 加载 10、0 quarantine；冻结 fingerprint 与 wire 一致（R131 已逐位校验） |
| 自动推荐准确 | LIVE_PASS | R132 修 ReAct 大小写碰撞后 50 岗位 32→35 auto；R135 干净库 `test_skill_recommendation*` 通过 |
| 岗位方向覆盖 | PARTIAL | 10 个方向覆盖主流，但长尾方向（如 Go/分布式）复用 Agent/Java，未单独建 Skill——R132 已论证重叠，不为数量新建 |
| Skill 真影响提问 | LIVE_PASS | R135 真实三方向开场题明显不同（见 §4 测试 3） |
| 错误加载/跨场次污染/题源错配 | LIVE_PASS（无污染） | R131 wire 审计每场只含本场 Skill；R135 三会话各自 Skill 正确 |
| manifest 完整 | LIVE_PASS | 10 个版本目录均含 manifest.json；干净克隆检出齐全 |

### C. 搜索与面经 Agent

| 功能 | 判定 | 证据 |
|---|---|---|
| DeepSeek Search/Fetch | LIVE_PASS（R132 历史） | R132 20/20 HTTP、Search 4/4、Fetch 3/4；本轮按约束 0 调用 |
| SearXNG 旧入口 | PARTIAL | 容器健康、代码保留，但 R132 后主链路走 DeepSeek 官方，SearXNG 不再产生真实验收数据 |
| WebCollectionService / PublicDataResearchAgent | LIVE_PASS | 复用现有链路，R132 采集百度面经入库 |
| Extraction/Routing/Reliability/Quality | LIVE_PASS | R135 真实抽取 2 次（见 §4 测试 6），28 题全部逐字命中原文 |
| 原文证据/来源快照 | LIVE_PASS | 快照 52/53 各题有 `evidence_ids`→span；`source_url` 为 nowcoder 原文 |
| 公司/岗位关联 | LIVE_PASS | experience 1 精确关联 company 6/job 31；17 题 job_id=31 |
| 管理员审核与发布 | LIVE_PASS | experience 1 `review_status=APPROVED`、`reviewed_at` 非空、AuditLog 记录（R132） |
| 面经入库/去重/召回 | LIVE_PASS（受限） | 精确作用域冻结 ≤5 经验/20 题；工具召回返回 experience 1 + 3 题；去重靠 fingerprint |
| 面经题真正用于最终提问 | FAILED（本轮） | 两次真实自主调用工具、数据正确，但最终题源均为 Skill，`used_tool_result=false`（见 §3 P0-1） |

### D. 模拟面试

| 功能 | 判定 | 证据 |
|---|---|---|
| Main Agent 提问 | LIVE_PASS | R135 6 场会话、20+ 道真实模型生成题 |
| Director 自适应决策 | LIVE_PASS | `TARGET_SOURCE_PAIR_V1` 合法候选；R135 full 4 轮含一次合法换题 |
| 主动工具调用 | LIVE_PASS | R135 3 次真实工具调用（search×2、read_active_gaps×1） |
| 项目/基础/场景题 | LIVE_PASS（代码+测试） | `question_shapes` 由规则发布；干净库 `test_r130_interviewer_judgement` 通过；未强求每场三型全出现 |
| 回答相关性/澄清 | LIVE_PASS | `answer_relevance.py`；前端 confirm_off_topic 真接通 |
| 追问/换题/结束 | LIVE_PASS | R135 full 会话 QUESTION_COUNT_REACHED 正常结束；R135 p02 模型自主 FOLLOW_UP |
| 技术评分 | LIVE_PASS | R135 每题一次评分、4 评对应 4 答；五维完整 |
| 错误恢复/幂等 | FALLBACK_PASS + LIVE_PASS | R135 full 1 次 ADAPTIVE_TOPIC_MISMATCH 由保底恢复；0 重复评分；重复请求幂等测试通过 |
| Token/调用预算 | LIVE_PASS | 硬账本贯穿；本轮 27/50、217k/400k，无自动重试 |

### E. 报告与长期记忆

| 功能 | 判定 | 证据 |
|---|---|---|
| 完整面试报告 | LIVE_PASS | R135 full 报告 20 COMPLETED，producer SUMMARY_COACH_AGENT |
| 五维评分/逐题证据 | LIVE_PASS | 报告页 `InterviewReportPage.vue:251,282,358-368`；`dimension_averages` + covered/missing points |
| Coach 复盘 | LIVE_PASS | R135 full 报告含 coach analysis；Coach Harness 默认 OFF（R94 已证工具调用价值低） |
| 成长记忆提案 | LIVE_PASS | R135 full 3 条 PENDING（PREPARATION_FOCUS），未自动批准 |
| 用户审核/确认/撤销 | PARTIAL | 提案批准/驳回/修正真接通；能力推断可撤销；**已批准提案无"撤销/反悔"入口**（只能删整条记忆） |
| 记忆影响下一场 | REPLAY_PASS | 19 场快照含 memory_guided_plan；真实模型是否因记忆改变提问未做对照实验——不声称因果 |
| 不把推断当用户事实 | LIVE_PASS | 记忆层显式 `semantics=USER_CONFIRMED / INFERRED`；提案需用户确认；测试断言未确认不进 VERIFIED |

### F. 前端

| 功能 | 判定 | 证据 |
|---|---|---|
| 自适应/Skill 选择/训练 | LIVE_PASS 接通 | `InterviewChatPage.vue:1494,1445-1458`；`TrainingPage.vue:96-107` |
| opening 三方式 | LIVE_PASS 接通 | `interview.ts:82-85`；UI `:1179,399,414-425` |
| 面经管理/审核（管理员） | LIVE_PASS 接通 | `AdminExperiencesPage.vue`、`AdminExperienceReviewPage.vue`，含来源/题源/证据 |
| 报告/记忆提案入口 | LIVE_PASS 接通 | `InterviewReportPage.vue:326-355` |
| 私人岗位/简历上传 | LIVE_PASS 接通 | `JobTargetPage.vue`、`ResumeListPage.vue` |
| confirm_off_topic | LIVE_PASS 接通 | `InterviewChatPage.vue:1124-1131,460-470` |
| **main_harness 开关** | NOT_IMPLEMENTED（前端） | 后端完整（`schemas.py:29` + `main_harness.py`），前端 0 匹配、不传参。本轮所有 Harness 均由 API 参数开启。属验收/调试能力，普通用户无需开关——有意为之，不作为阻断 |
| **confirm_unknown_retry** | 已修复，REPLAY_PASS | 本轮修复前端完整链路（详见 §3），tsc/build 通过；未做真实故障注入 |
| 浏览器交互验收 | NOT_VERIFIED | agent-browser 未安装；已验证页面可访问、Vite 提供新代码、tsc/build 通过 |

---

## 3. 第三阶段：P0 根因与修复

### P0-1：面经检索结果未用于提问

**追踪链（全部有证据）**：

1. 模型为何调用工具：R134/R135 harness.5 提示明确告知本场有可查面经，且题干不在输入里；R135 两次真实测试模型均自主调用 `search_reviewed_interview_experiences`（topics=["Agent","多 Agent 架构"]）。
2. 工具返回完整数据：`OK / REVIEWED_EXPERIENCES`，含 experience:1、round=一面、reliability=0.72、reviewed_at、source_url=nowcoder、3 道题的 ref/text/dimension，`trust=UNTRUSTED_DATA`。
3. 返回内容进入第二次输入：R135 wire 证据显示 `tool_feedback` 出现在第二次 MainHarnessAction 输入。
4. 模型能否看到 Question/Experience ID、内容、来源：能看到 ref（public_question:2/3/4）、题干、来源；**但此前看不到 candidate_id**。
5. Director 是否允许合法题源：**允许**。R135 零 HTTP 重建 session 643 候选集，`public_question:2/3/4` 均为合法 candidate_id 且带 NEXT 策略（18 候选中 7 个 public_question）。
6. 模型选通用 Skill 的真实原因：工具给的是 `source.ref`，模型要提交的是 `candidate_id`（哈希串），二者缺显式映射；模型退回唯一明显可选的 Skill 候选。这是**投影缺陷**，不是模型偏见。
7. `used_tool_result` 计算准确：R134/R135 审计正确标记 `false`（最终 source_ref=skill:*，不在工具返回 refs 中），无"实际参考但漏记"。
8. 无伪造来源引用。

**修复**：工具返回题目时附带该 ref 在本场对应的合法 `candidate_id`（仅当候选存在且含 NEXT 策略，绝不凭空生成），并在 harness.5 提示中告知模型可据此选择。新增 2 项零 HTTP 测试（含"无合法候选不伪造 id"、"非 NEXT 不映射"），`2 passed`。

**真实验收结果**：R135 p01/p02 两次调用，工具均返回了带 candidate_id 的真实面经题；但模型最终仍选 Skill 源，`used_tool_result=false`。**判定 FAILED（未达成"检索结果用于提问"）**。按用户要求，不强迫、不伪造、不反复重试碰运气。这说明"给出 candidate_id"是必要条件但非充分条件——真实模型仍偏好 Skill。剩余建议见 §7。

### P0-2：知识缺口读取与追问

**根因**：R134 中模型 3 次选 FOLLOW_UP 但填 `focus_gap_ids=[]`，而 gap ID 只能通过 `read_active_knowledge_gaps` 获取——模型未读工具就追问，被严格校验拒绝后走保底。

**修复**：harness.5 提示强化因果——"若 active_gaps.count>0 且选 FOLLOW_UP，必须先调 read_active_knowledge_gaps 取真实 gap ID 填入 focus_gap_ids；空 focus 会被拒；不想针对缺口就选 NEXT"。严格校验与保底一行未改。

**真实验收结果**：
- R135 p01 第二轮：模型**自主调用 `read_active_knowledge_gaps`**（R134 为 0 次），拿到 4 个真实 gap ID，填 `focus_gap_ids=[gap_0af0…]` 并提交 FOLLOW_UP。这是 R134 没有的**真实合法自主追问路径**。
- 但该次同时把 gap ID 误塞进 `supporting_refs`，触发 `UNKNOWN_DIRECTOR_REFERENCE`（gap ID 不是合法 director ref）。已补提示明确"gap ID 只属 focus_gap_ids，不属 supporting_refs"。
- R135 p02 重跑：模型改选 NEXT（合法通过），无非法 focus。

**判定**：工具自主调用与真实 gap ID 获取 **LIVE_PASS**；完整"读缺口→合法 FOLLOW_UP 提交成功"链 **PARTIAL**（路径真实发生一次，但被 supporting_refs 误用阻断；提示已修，未在预算内再次取得全绿样本）。保底仍安全，无过度触发。

### P0-3：重启与任务恢复

零 HTTP 验证（R135 库）：6 场会话重启后全部可读；**0 重复评分**（每 answer 一条 evaluation）；**0 RESERVED 账本**；**0 RUNNING 残留**；报告 COMPLETED、3 条 PENDING 提案完整；R134 会话 `unknown_model_calls` 全空。判定 **LIVE_PASS**（会话读取/幂等/事务/锁）。会话 644 停在 IN_PROGRESS 是 p01 真实失败记录，未改写。

### P1：真实面经抽取

复用 R132 快照 52/53，用生产 `interview_extract` 1.3.0 prompt + `FragmentInterviewExtraction` schema 真实模型抽取（2 HTTP）：

| 快照 | 正文 | 片段分类 | 真实模型出题 | 逐字命中原文 | rejected |
|---|---|---|---|---|---|
| 52 携程 | 1846 字 | 20 QUESTION / 19 CONTEXT | 20 | 20/20 | 0 |
| 53 快手 | 7261 字 | 8 QUESTION / 2 CONTEXT / 206 ANSWER | 8 | 8/8 | 0 |

CONTEXT/ANSWER 未冒充 QUESTION（53 有 206 个 ANSWER 仅出 8 题）；编号标题真实问题保留；付费预览未推断隐藏内容；失败可定位 fragment。**LIVE_PASS**。

---

## 4. 第四阶段：真实 API 全流程验收

| 测试 | 场景 | HTTP/Token | 结果 |
|---|---|---|---|
| 1 有面经岗位 | session 644/645，百度 job 31，harness on，2 题 | 4+5 / 72k | 工具自主调用 2 次、数据返回正确；最终题源 Skill；一次合法 gap 追问路径（被 supporting_ref 误用阻断）→ FAILED（题源）/ LIVE_PASS（调用） |
| 2 无面经岗位 | session 648，job 34，rag_retrieval | 3 / 32k | 无工具调用，正常出题完成，未编造公司历史题 → LIVE_PASS |
| 3 多 Skill | session 646/647/648，Java/Agent/RAG | 7 / 46k | 三方向开场题明显不同且紧扣各自专业 → LIVE_PASS |
| 4 多轮面试 | session 649，job 31，4 题 + 报告 | 9 / 83k | 4 题 4 评、1 次可恢复换题、COMPLETED、报告 20、3 条 PENDING 提案 → LIVE_PASS |
| 5 失败恢复 | 故障由真实流程产生（UNKNOWN_DIRECTOR_REFERENCE、ADAPTIVE_TOPIC_MISMATCH）+ 零 HTTP DB 校验 | 0 额外 | 保底恢复、0 重复评分、0 未结算账本、0 RUNNING 残留 → LIVE_PASS（幂等）/ FALLBACK_PASS（恢复） |
| 6 抽取流程 | 快照 52/53 真实模型抽取 | 2 / 16k | 28 题逐字命中、分类正确、审核候选保存待人工 → LIVE_PASS |

---

## 5. 实际调用与成本

| 指标 | 数值 |
|---|---|
| 模型 HTTP | **27**（Main 14、Evaluation 10、Coach 1、Extract 2） |
| Token | **217,176**（输入与输出合计，来自 step.token_usage_json；未逐条拆分输入/输出，不编造） |
| Search / Fetch（DeepSeek Web） | 0 |
| 工具调用 | 3（search_reviewed_interview_experiences ×2 均 OK；read_active_knowledge_gaps ×1 OK） |
| 查询成功且用于提问 | 0（used_tool_result 全部 false） |
| 查询成功但未用于提问 | 2（面经检索） |
| 确定性保底 | ≥2（full 的 ADAPTIVE_TOPIC_MISMATCH、p01 的 gap focus 拒绝） |
| 抽取成功/失败 | 成功 2 快照（20+8 题）/ 失败 0 |
| 失败/修复调用 | 未对不确定请求自动重试；1 次路由配置错误（未发 HTTP）、1 次 run 残留已标 FAILED |
| 费用 | **无法核实**。未查提供商账单、无可靠单价，不以估算冒充实测；Token/HTTP 为实测 |

预算先后经三个阶段脚本串行执行，每阶段以启动时的 max step id 为基线，最终用 `created_at >= 2026-11-19` 统一交叉校验，两者一致（27/217,176）。

---

## 6. 工程交付

| 项 | 结果 |
|---|---|
| Git HEAD/历史 | R134 `dc64479`；本轮修复提交见文末，未 Push |
| 干净克隆可运行 | `git worktree` 检出 1389 文件；`interview_agent_wb_r135_clean_test_20261119` 全新克隆库跑核心测试 157 passed/0 failed |
| 广覆盖测试 | 21 个面试/数据相关文件在干净克隆源码 + 全新库上 **239 passed / 0 failed**（3m10s）；零模型 HTTP |
| 过夹具修复 | `test_private_jd_skill_recommendation.py` 硬编码 `1.0.0`，R132 已将 ai_agent 升 1.0.1 → 改为读已注册最新版本（不削弱校验）；4 个 ERROR 系 basetemp 相对路径在 worktree 下父目录不存在，改绝对路径后消失，均非生产 bug |
| 数据库迁移一致 | 各验收库 0030，正式库 0025 不动 |
| Skill manifest | 10 个齐全，fresh clone 不丢 |
| Prompt 版本 | harness.1–5、Eval 2.3.4 未改；改 V5 正文用冻结 system+fingerprint 校验，旧会话不受影响（已验证 session 643 投影正常） |
| 配置/启动文档 | start-showcase.bat / status / stop 脚本齐备，`-Database` 显式授权 |
| API Key/隐私 | 未打印任何密钥；precheck 仅输出 `key_set=True`；无真实简历/个人隐私写入报告 |
| 无效入口 | 修 `confirm_unknown_retry`；`main_harness` 前端仍无开关（验收能力，非阻断） |

---

## 7. 剩余任务清单与 GO 判据

### P0（影响主链路，需处理）
1. **面经检索结果未成为题源**：工具已返回 candidate_id，真实模型两次仍选 Skill。用户影响：审核面经未能真正用于提问，检索能力"半接通"。
   - 根因：模型偏好 + UNTRUSTED_DATA 低信任权重；非系统阻断。已修投影（candidate_id），未解决偏好。
   - 是否验收：否（FAILED）。
   - 仍需：在不强迫调用的前提下，提高检索题源的相对吸引力（例如把面经题作为带证据的候选项直接列入 candidates 并标注 reported+reviewed，而非仅在工具反馈里）。**注意**：这会改动候选项呈现，须重跑严格校验与保底测试，不能只为 used_tool_result=true 伪造来源。
   - 对稳定功能影响：中等（改候选集会触及 Director）。
2. **confirm_unknown_retry 前端**：本轮已修代码（tsc/build 通过、Vite 已加载），但未做真实故障注入，标 REPLAY_PASS。上线前需在预发用一次真实 UNKNOWN 验证确认框与幂等重试。

### P1（体验/完整性）
3. 已批准记忆提案无"撤销/反悔"入口——只能删整条记忆。影响小，非主链路。
4. 普通用户看不到面经题源明细（`answer_source`/`source_anchor`）——仅管理员审核页可见。属信息透明增强。
5. SearXNG 旧入口不再产生真实数据——保留代码，不影响主链路。

### P2（可选）
6. 浏览器交互自动化验收（agent-browser 未安装）——本轮只做到页面可访问 + 构建/类型检查通过。
7. 记忆影响下一场的因果对照实验——当前只有 REPLAY_PASS。

### GO / CONDITIONAL GO / NO-GO 判据

- **GO**：具备可展示完整主链路，不要求所有扩展达生产级。
- **NO-GO**：主链路断点、数据损坏、无法端到端。
- 本轮主链路（简历→JD→Skill→面试→评价→报告→记忆提案）全部 LIVE_PASS，无数据损坏，**不是 NO-GO**。
- 但 P0-1（面经题源）与 P0-2 完整追问链仍未达成，且 confirm_unknown_retry 未做真实验收——因此判定 **CONDITIONAL GO**。

**转为 GO 的最小剩余任务**：
1. 在预发真实验证一次 confirm_unknown_retry 确认框 + 幂等重试（不消耗本轮预算）。
2. 明确对外口径：面经检索为"可查、可展示证据、辅助参考"，**不宣称"自动采用面经题提问"**；若产品要求自动采用，再按 P0-1 建议改候选项呈现并完整回归。

完成这 2 项即可作为正式可展示的 Agent 项目。

---

## 附：证据文件

- `data/r135_audit/r135_p01.json`、`r135_p02.json`、`r135_skills.json`、`r135_full.json`、`r135_extraction.json`、`r135_*.log`
- 验收库 `interview_agent_showcase_r135_20261119`；干净测试库 `interview_agent_wb_r135_clean_test_20261119`
- 干净 worktree `/tmp/r135_clean`（HEAD dc64479）
- 本轮修复：`main_harness.py`（候选绑定 + gap/ref 提示）、`adaptive_prompts.py`（harness.5 提示）、`test_r135_review_candidate_binding.py`、`interview.ts` + `InterviewChatPage.vue`（confirm_unknown_retry）、`scripts/r135_live_accept.py`、`scripts/r135_extract_live.py`
