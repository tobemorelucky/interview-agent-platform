# R122 — 三场 Live 展示版最终验收（Java 后端 + 跨场次训练）

> 证据等级：**事实**=命令/DB/账本/截图实测；**判定**=基于事实的质量结论；**建议**=后续判断。
> 本轮真实模型消耗：**B 场 8 HTTP / 51,081 token（charged 口径）+ C 场 7 HTTP / 41,175 token = 15 HTTP / 92,256 token**（分场结算）。
> 三场累计展示沙箱账本：23 HTTP / 139,936 token。SHOWCASE 沙箱外的一切未动。

---

## 0. 一句话结论（先读这个）

- **C 场（跨场次 WEAKNESS 训练）完全达标**：真实模型 7 HTTP / 41,175 token，adaptive + Evaluation 2.3.4，三题围绕 R121 Live 评价形成的训练目标，前后状态 PRACTICING→GAP_SUSPECTED 按规则记录。
- **B 场（Java 后端 Live）三题与全链路完成，但未满足"自适应 + Evaluation 2.3.4"前置**：我的浏览器脚本漏勾"自适应面试"，会话走了 Skill 引擎 1.1.0 prompt（session 15 `adaptive_policy=None`）。这是**执行配置错误**，不是产品缺陷；但按验收标准，B 场**不能记为完全通过**。剩余预算（5 HTTP / ~39K）不足以重跑，按"实质失败即停、不反复购买模型输出"未超预算重跑。
- **R121 A 场、C 场确认使用冻结的 2.3.4**（step_audit 实证），三场评价均能识别答非所问与技术错误，`covered_points` 诚实。**不能仅凭三场 COMPLETED 认定面试质量优秀**——详见 §4。

---

## 1. 阶段一：保护 R120/R121 成果（0 HTTP）

### 1.1 Git 与提交
本轮开始 HEAD `1d87a0b`，工作区有 R120/R121 遗留的未提交源码、测试、文档与产物。已分三类做**本地提交（未 push）**：

| 提交 | 内容 |
|---|---|
| `9c1e660` | 代码：D1 词表修复（`requirement_vocabulary.py`，保留"或"链）+ R121/R122 脚本 + `.gitignore` 排除 Chrome profile |
| `aa69f23` | 测试：`test_r121_d1_strength_fix.py`（71 passed） |
| `564acf0` | 文档与产物：`docs/120`、`docs/121`、项目记忆、审计 JSON、15 张 UI 截图 |

提交前已扫描：无 `sk-*` / `ghp_*` / Bearer 密钥，Chrome profile（85M）与第三方源 HTML 明确排除。R120/R121 的 Session/Evaluation/报告/截图原样保留。

### 1.2 环境恢复
开始时三个基础设施容器（postgres/redis/minio）处于 **Exited (255)** 状态，PostgreSQL 日志显示非正常关机触发 WAL 恢复。执行 `docker compose up -d postgres redis minio` 重启等待恢复完成后，三库状态与 R121 结束时**逐表一致**（崩溃恢复未损坏数据）：

| 库 | alembic | 关键计数 |
|---|---|---|
| `interview_agent`（正式） | 0025 | users 5 / resumes 38 / sessions 19 / evals 41 / reports 5 / **账本 0** |
| `interview_agent_wb_jd_r31_20261116`（JD 沙箱） | 0029 | 12 版本 / **账本 32 / 81,458 token** / 0 session |
| `interview_agent_showcase_r1_20261116`（展示沙箱） | 0029 | 9 用户→10 / sessions 13 / evals 29 / reports 9 / 账本 8 / 42,614 |

### 1.3 D1 复核（0 HTTP）
- `test_r121_d1_strength_fix.py` + `test_r35_requirement_vocabulary.py`：**71 passed**。
- 三条样本实测：83「漏洞挖掘，或安卓安全…优先」→ **PREFERRED**、84「CVE/Syzkaller…优先」→ **PREFERRED**、117「Verl…了解分布式…优先」→ **REQUIRED**（真 mixed，未误降级）。

### 1.4 模型路由 guard（0 HTTP）
`modelscope` provider + `deepseek-ai/DeepSeek-V4.1-Flash-1017` + governed=True + 无 fallback + Main/Coach Harness OFF + Evaluation 2.3.4 冻结。三场开始前均通过。

---

## 2. 阶段二：B 场 Java 后端 Live —— 三题完成，但配置未达标

### 2.1 前置（0 HTTP，全部通过）
- **JD**：JD5 `沐瞳科技 · 后端开发工程师（AI向/数据平台）`，PUBLISHED，10 条 requirement 各有 claim+evidence（快照 `ec6f96246d81…`，870 字），无 D1 行。
- **Skill**：`recommend_jobs([5])` → `java_backend`（1.0.0，15 分领先，`auto_select=true`，无歧义），与岗位方向一致；**未发现 `NO_APPLICABLE_SKILL` 被强改**。
- **合成材料**：用户 B `showcase-r2-live-B-synthetic@example.com`（id=10）+ Java 后端合成简历（Spring Boot/Redis/Kafka/MySQL/缓存一致性/消息可靠性，明确标注 SYNTHETIC，不冒充真人）。画像由真实模型抽取（1 HTTP / 2,069 token，账本 8→9）。

### 2.2 真实浏览器操作（9 张截图，`data/r72_r122_b_live/`）
FastAPI（**真实治理路由**，未 patch outbound）+ Vite + 无头 Chrome CDP，走真实 Service/Controller，非脚本插表。59 条 API 请求 **58×200 + 1×201，0 个 4xx/5xx**（`data/r70_r122_live_ui/api_requests.jsonl`）。操作链：登录 → Dashboard → 公共职业页（JD5 详情+来源）→ 简历/画像页 → 选 JD5（自动推荐 `java_backend`）→ 创建会话 → 三题作答 → 报告页生成 Coach。

三题冻结回答（脚本内固定，不作为 prompt 参数；模型只看到考生输入）：①完整技术回答 ②明显不完整（"细节别人处理，我只负责调用"）③**含技术错误**（"SETNX 就能保证分布式事务强一致，Redis 单线程即可，无需两阶段提交"）。

### 2.3 结果与判定
session 15：**COMPLETED / 3 Evaluation / Coach 生成**（`SUMMARY_COACH_AGENT` 1.0.3）。三题整体均分 1.6（题目回答质量差，符合冻结回答设计）。

**未达标项（事实，必须记录）**：
- session 15 `context_json.adaptive_policy = None`，`workflow_mode=m4` 但**非自适应**；step_audit 显示 `interview_main 1.1.0` / `answer_evaluation 1.1.0`。
- 原因：我的浏览器脚本 `r72` 漏勾前端"自适应面试"复选框（`r65` 有 `cdp.check('自适应', True)`，`r72` 遗漏）。**这是我的执行配置错误**，不是产品缺陷。
- 后果：B 场**未使用 Evaluation 2.3.4**，不满足用户明确的"自适应开启 + Evaluation 2.3.4 原样冻结"前置。
- 未重跑的理由：B 场已用 8 HTTP（画像1+浏览器7）/ 51,081 token；13/90K 上限仅余 5 HTTP / ~39K，不足以完成一场自适应 3 题（需 ~7 HTTP）。遵循"实质失败后停止、不反复购买模型输出、分场结算"，**不超预算重跑**。

**判定**：B 场可作为"Java 后端真实模型 3 题 Live + 全链路"证据；**不可作为"自适应 + 2.3.4 Java Live"验收通过**。补救需用户单独授权新一轮 13/90K（见 §7）。

---

## 3. 阶段三：C 场跨场次 WEAKNESS 训练 Live —— 完全达标

### 3.1 前置（0 HTTP）
用户 A（id=9，`showcase-r1-live-synthetic-*@example.com`）是 R121 首场 Live 专用新用户：仅 session 14（Live，3 eval），能力 `agent=PRACTICING` rev1，观察全部引用 evaluation 27/28/29（session 14）。**未混入任何 Replay 观察**。

合法候选唯一：`ai_agent_application:agent`（Agent 工具与状态管理），PRACTICING → `available_modes=[FULL, WEAKNESS]`，rev1，未撤销，observation_refs=evaluation:27/28/29。无 CONFLICTED/RECHECK_DUE → **只能 WEAKNESS，不能 RECHECK**，未伪造资格。

### 3.2 执行（真实模型，7 HTTP / 41,175 token）
- 首题由 `r74` 创建 session 16 并生成（1 HTTP / 4,203）；因脚本读错字段名中断，**未重复 start**。
- `r75` 从 session 16 续跑三题 + Coach + feedback（6 HTTP / 36,972）。冻结三题回答在脚本内固定（Agent 工具校验/状态管理/事件溯源）。
- 全程 adaptive + **Evaluation 2.3.4**（step_audit 实证 main 2.3.3 / eval 2.3.4），session 16 `adaptive_policy` 存在。

### 3.3 十项验收点逐条（全部达标）
1. ✅ 训练候选：`ai_agent_application:agent` PRACTICING，依据 evaluation 27/28/29。
2. ✅ `memory_guided_plan` 精确引用 evaluation:27/28/29（冻结快照实测）。
3. ✅ 冻结快照含 `training.mode=WEAKNESS`、`selected_knowledge_id=ai_agent_application:agent`。
4. ✅ Main 实际问题围绕该目标：三题均属"Agent 工具调用前后判断 / 幂等键 / 状态恢复"。
5. ✅ `adaptive_decision.strategy/target_knowledge_id`：q35 NEXT + 目标；q36/q37 FOLLOW_UP + 目标，全部 `RELATED_TARGET_SELECTED` + `ACCEPTED`（feedback.memory_usage 实证）。
6. ✅ 利用上次缺口追问：q36/q37 针对 q35 未说清的"幂等键怎么生成 / 崩溃后重放"深挖，**非机械重复原题**。
7. ✅ 3 条真实 Evaluation 落库（evaluations 33/34/35）。
8. ✅ Coach 正常生成（report 11，`SUMMARY_COACH_AGENT`）。
9. ✅ 训练反馈：`before_state=PRACTICING`→`after_state=GAP_SUSPECTED`（rev1→2）、`effective_evaluations=3`、`independent_evaluations=1`、`result=OBSERVED`、next_action=安排独立复核。
10. ✅ 按实际规则记录为 GAP_SUSPECTED，**未强求升级 VERIFIED**。

**边界如实说明**：Memory 被读取只证明数据链通；题目/策略符合目标才证明训练机制本场生效（本场满足）；**一次成功不构成统计或因果意义的"训练提升"证明**。

---

## 4. 阶段四：三场综合质量审查

| 维度 | A / R121 Agent FULL (session 14) | B / Java（非自适应, 15） | C / WEAKNESS (session 16) |
|---|---|---|---|
| 真实 HTTP / token(charged) | 8 / 42,614 | 8 / 51,081 | 7 / 41,175 |
| 自适应 / prompt | ✅ adaptive / 2.3.3+2.3.4 | ❌ 非自适应 / **1.1.0** | ✅ adaptive / 2.3.3+2.3.4 |
| 会话状态 | COMPLETED | COMPLETED | COMPLETED |
| Evaluation 落库 | 3 | 3 | 3 |
| 技术错误识别 | 见 §4.2 | ✅ Q34 SETNX 强一致错误被判 correct=1，factual_issues 明确指出 | ✅ Q37 "重放可回滚外部扣款"误导被指出 |
| `covered_points` 诚实 | 未逐条复核（R121 已记 6/6 quote 假阴性更正） | ✅ 不完整/错误回答 covered_points=0 | ✅ 答非所问的 Q36/Q37 covered 从 4 降到 2/1 |
| Coach | ✅ 引用持久评价 | ✅ 统计来自 3 评价，overall 1.6 | ✅ 引用本场 3 评价 |
| Memory | agent=PRACTICING | （用户 B 能力未单独核验，非验收点） | PRACTICING→GAP_SUSPECTED |
| 前端操作 | R121 UI 11/11 | ✅ 9 张截图 / 59 API 全 2xx | 后端 Service 驱动（脚本，非 UI） |
| 失败/修复次数 | 1 次脚本字段假阴性（0 HTTP 更正） | 1 次启动器语法修复（0 HTTP）+ 1 次自适应漏勾（未补救） | 1 次字段名中断（0 HTTP 续跑，未重复出题） |

### 4.1 质量缺陷清单（严重程度）
- **中（阻断 B 场验收）**：B 场未开自适应 → 非 2.3.4。执行配置错误，非产品缺陷。复现：session 15 `adaptive_policy=None` + step_audit 1.1.0。**阻碍用户把 B 场当作 2.3.4 验收依据**；不阻碍功能演示（Java 面试全流程可看）。
- **低（测试材料设计，非系统缺陷）**：B 场 Q33/Q34、C 场 Q36/Q37 的冻结回答与追问话题不完全对应（答非所问），导致分数偏低。这**反而证明评价诚实**（covered_points 不虚增、能识别答非所问）。复现：`data/r73_r122_audit/session_15.json`、session_16.json。
- **低（历史遗留，已记录）**：R121 首场的 quote 审计脚本曾以 question_id 映射产生 6/6 假阴性，已用 answer_id 零 HTTP 更正；本轮 B/C 的 `quote_all_ok=false` 同理——Evaluation 2.3.4 的 artifact 是**叙述式评价**，引用内嵌在 `evaluation` 文本中，不是独立 quote 字段，探针按独立 quote 字段统计必然为 0。**不能据此认定引用造假**；§4.2 已用文本内引述抽核对。

### 4.2 引用真实性抽核（叙述式评价）
- B Q32：评价内"先更新数据库再删缓存"逐字来自该回答；`factual_or_reasoning_issues` 指出"两种一致性策略混用"属实。
- B Q34：评价逐条驳斥 SETNX 强一致说法（"SETNX 只提供互斥，不保证跨 Redis/MySQL 事务原子性"，"Redis 单线程只保证单条命令/脚本"），并指出未意识到锁超时/误删/续期——**高质量评价，非机械打分**。
- C Q37：评价"重放重建状态"给 covered，但指出"未说明外部扣款副作用不能重放回滚"——**细微技术边界被正确识别**。

### 4.3 不能认定"质量优秀"的理由
三场均 COMPLETED，但：B 场 prompt 不达标；三场回答多为刻意设计的低质/错误回答（用于检验评价），平均分 1.6–2.x；C 场只跑一次无统计意义。**结论：链路与评价机制通过验收，面试质量本身不构成优秀证据。**

---

## 5. 阶段五交付与资产

### 5.1 Git
提交 `9c1e660`（代码）、`aa69f23`（测试）、`564acf0`（文档/产物），**未 push**。本报告与 R122 新脚本/数据在报告完成后一并本地提交。

### 5.2 真实 JD 正式发布数量
JD 沙箱：12 版本 = **9 PUBLISHED + 3 WAITING_REVIEW**；展示沙箱同步 12 版本（9 PUBLISHED）。距 45–50 份正式 JD 缺口 **36–41 份**。52 份语料仅 3 家（蔚来/沐瞳/麒麟）有可用官方 DOM，企业多样性已到语料上限。

### 5.3 账本（分场结算）
| 场 | 行 | HTTP | token(charged) | 路由 |
|---|---|---|---|---|
| R121 A | 1–8 | 8 | 42,614 | profile×1 + main×3 + eval×3 + coach×1 |
| B | 9–16 | 8 | 51,081 | profile×1 + main×3 + eval×3 + coach×1 |
| C | 17–23 | 7 | 41,175 | main×3 + eval×3 + coach×1 |
| **累计** | 23 | **23** | **139,936** | 全部 SETTLED，单一模型，无 Repair/切 Provider/UNKNOWN |

### 5.4 资产边界（全部未越界）
正式库 0025 / 账本 0；JD 沙箱 0029 / 账本 32 / 81,458（未写）；历史 Live 封存库未读未写；MinIO 生产桶 `interview-agent` 48 对象未变；Evaluation 2.3.4、Coach Harness、L3/L4、0030 迁移均未触碰；未清库/删除/归档/push/reset/clean。

### 5.5 截图
B 场 9 张：`data/r72_r122_b_live/b01_login.png` … `b07_report_coach.png`（3 张答题）。R121 UI 15 张见 `data/r64_showcase_ui/`。前端 API 请求日志 `data/r70_r122_live_ui/api_requests.jsonl`（59 条全 2xx）。

---

## 6. 距完整用户流程还差什么（不缩成 JD 导入工程）

已跑通的真实闭环：**真实(合成标注)简历 → 真实画像 → 真实已发布 JD → 自适应面试 → 每题 Evaluation → Coach → Memory → 跨场次训练**（A 场 + C 场合格，B 场链路完整但 prompt 不达标）。

缺口（按优先级）：
1. **B 场 2.3.4 补验**（需新授权 13/90K）：勾自适应重跑一遍 Java 后端 3 题。
2. **真实简历 0 份**：至今所有"简历"均为合成标注材料；用户亲自上传真实（脱敏）简历仍未完成——这是最终交付的最后一道用户验收。
3. **面经 59 题未入业务表**：仅有出处的改写题，未走 Research→Evidence→审核→发布。
4. **45–50 份正式 JD**：现 9 份；需先采集新企业官方页（破 3 家上限），再按每批 6 份 / 12 HTTP / ~30K 推进。
5. **长场 20 轮 Live** 仍未通过（历史记录，本轮未触碰）。

---

## 7. 下一阶段最小执行计划（供用户裁决，本轮不执行）

- **7.1 B 场补验**：单独授权 13 HTTP / 90K，复用用户 B 画像，勾选自适应重跑 Java 3 题（约 7 HTTP / 42K），核对 step_audit = 2.3.4。
- **7.2 第三场（用户亲测）准备**：展示沙箱已就绪（9 PUBLISHED、api 200）；需用户本人上传脱敏真实简历，前端走一次"选岗→面试→报告"，本轮所有后台服务（API 8000 / Vite 5173 / CDP 9222）已停，按启动脚本重启即可。
- **7.3 剩余 36–41 份 JD**：先补采新企业官方 DOM（当前仅 3 家可用），分 6–7 批，每批 12 HTTP / 约 30K，逐份严格审核后才发布；不为凑数放宽 Evidence 或接受改写。
- **7.4 面经入库**：先定"有出处改写题"的 Claim 标注规范，再走 Research 链路，独立预算。

---

## 附：关键文件索引
- 报告：`docs/120`（SHOWCASE-R1）、`docs/121`（UI+首场 Live）、本文件。
- 脚本：`r70`（真实 Live runtime）、`r71`（B 准备）、`r72`（B 浏览器 Live）、`r73`（Live 审计）、`r74/r75`（C 场创建/续跑）。
- 数据：`data/r72_r122_b_live/`（B 截图+报告）、`data/r74_r122_c_live/`（C 报告）、`data/r73_r122_audit/`（逐题评价审计）、`data/r70_r122_live_ui/api_requests.jsonl`（前端 API 日志）。
- 证据等级：本文件全部数字来自本轮只读 DB/账本/文件命令；§4 质量判定为基于 artifact 文本的**判定**，非模型自评。
