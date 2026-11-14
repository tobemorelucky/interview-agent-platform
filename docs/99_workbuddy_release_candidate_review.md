# 第二十轮：Release Candidate V1 质量验收

> 目的不是加功能，而是回答两个问题：**第十九轮的修复在真实模型下是否成立**，
> 以及**现在能不能冻结成可演示的候选版本**。
>
> 本轮真实模型：**6 次 HTTP / 13,177 token（结算值）/ 6 次全部 SETTLED**，
> 上限 6 次 / 60,000 token / 15 分钟。启动器实测模型时间约 **50 秒**（09:28:07–09:28:57）。
> 只跑简历抽取、简历画像选择与私有 JD 抽取，**没有**跑面试、Evaluation、Coach。
>
> **样本量声明**：2 份合成简历（A 复用第十八轮、E 为本轮新增）、1 份真实归档公开 JD（C）、
> 1 份合成对照 JD（D）。这只能支持**针对性结论**，不构成统计意义上的质量结论。
> 人工标注在调用前写定（`.runtime/r20/annotation_r20.json`），模型输出未参与标注。

---

## 1. 第十九轮真实模型复核结果（逐项）

| # | 复核项 | 结论 | 证据（本轮真实调用） |
|---|---|---|---|
| 1 | 全职经历进入 `work_experience` | **成立** | A：模型自己就把该条报为 `work_experience`（`reported_as=work_experience`），服务端 11 个锚点全在 `work`；`internships=[]` |
| 2 | 工作与实习同时存在时正确分开 | **成立** | E：`work_experience` 1 条（DEMO 云计算公司 2022.07-2025.06）、`internships` 1 条（DEMO 电商公司 2021.07-2021.09），互不串桶 |
| 3 | 模糊经历保持不确定 | **成立（样本有限）** | E 的「其他经历 / DEMO 开源社区 文档贡献者」被判 `UNRESOLVED`，原文逐字保留进 `unclassified_experience`，未强行归类 |
| 4 | 证据仍可逐字追溯 | **成立** | 33 条画像事实 **33/33** 的 statement 是其至少一条证据的逐字切片；雇佣类事实的证据小节分别为 `work` / `internship`，无交叉 |
| 5 | JD 强制/列明/优先区分正确 | **成立** | C：3 条 `LISTED_UNDER_REQUIREMENTS` + 1 条 `EXPLICIT_MANDATORY`；D：4 条 LISTED + 1 条 `EXPLICIT_PREFERRED`，与标注逐条一致 |
| 6 | 章节识别是否产生新误判 | **未发现** | 2 份 JD 均无地点/城市行进入要求；职责 3 条未混入资格；C 的「或…」可选分支未被升级为强制 |
| 7 | 标题与要求冲突时提示 AMBIGUOUS | **成立** | D（AI 标题 + Java 要求）：`AMBIGUOUS`、`auto_select=false`、`TITLE_CONFLICTS_WITH_REQUIREMENTS`，无系统首推 |
| 8 | 修复没有让原本正确的样本退化 | **未发现退化** | C 仍 `RECOMMENDED`（ai_agent_application，20 分，内容 12）；离线全量 930 passed / 0 failed |

**这一轮最重要的新事实**：第十九轮只能证明"服务端能纠正模型的桶"，本轮证明**模型侧也改对了**——
A 的响应里 `work_experience` 是模型自己给出的，不再是服务端搬过去的。这一点在 HTTP=0 的第十九轮是无法验证的。

**仍然没有回答的**：无小节简历、纯英文简历、超长 JD 的表现；也没跑 B（易误判样本）以确认没有把"原本正确"的案例改坏，
只有 C/D/A/E 四个样本与离线回归作为退化证据。

---

## 2. 简历与 JD 人工标注对照

### 2.1 简历（2 份，逐条）

| 标注 | 应然 | 实际 | 判定 |
|---|---|---|---|
| A：全职（某互联网公司 / Python 后端开发工程师） | `work_experience` ≥1，`internships` 0 | work 1 / intern 0 | 一致 |
| A：画像类别 | 工作事实不得标成实习 | v1 画像 `work_experience` 1 / `internship_experience` 0 | 一致 |
| A：画像 unknowns | 应提示"实习经历无证据" | `unknowns` 含「实习经历：当前简历材料中未提供相关证据」 | 一致 |
| E1：全职（DEMO 云计算公司） | `work_experience` | work 1，锚点 7 个全在 `work` | 一致 |
| E2：实习（DEMO 电商公司 2021.07-2021.09） | `internships` | intern 1，锚点在 `internship` | 一致 |
| E3：项目（DEMO 对账平台） | `projects` | 项目小节 | 一致 |
| E4：其他经历（DEMO 开源社区 文档贡献者） | 既非工作也非实习 | 抽取层 `unclassified_experience` 1 条；画像层落入 `projects`（仍非雇佣） | 一致（两处都未当作雇佣） |
| EN1/EN2：不得串桶 | — | 未发生 | 通过 |
| EN3：不得给 E4 编造雇佣类型 | — | 未编造 | 通过 |

画像事实共 **33 条**（A 版本 23 条 + E 版本 10 条按事实表计），逐字可追溯 **33/33**。

### 2.2 JD（2 份，逐条）

| 标注 | 应然 | 实际 | 判定 |
|---|---|---|---|
| C1 专业范围 | LISTED | `UNKNOWN / LISTED_UNDER_REQUIREMENTS` | 一致 |
| C2 大模型框架 | LISTED | 同上 | 一致 |
| C3 「或有大模型…」 | LISTED（可选分支） | 同上 | 一致 |
| C4 「至少一种编程语言」 | EXPLICIT_MANDATORY | `REQUIRED / EXPLICIT_MANDATORY` | 一致 |
| C 地点/城市列表 | 不是要求 | 未出现 | 通过 |
| D1–D4 | LISTED | 4 条均 `LISTED_UNDER_REQUIREMENTS` | 一致 |
| D5 「…者优先」 | EXPLICIT_PREFERRED | `PREFERRED / EXPLICIT_PREFERRED` | 一致 |
| 证据 | 每条都是逐字 span | 9/9 行 evidence 为原文子串 | 通过 |

**注意一个仍然存在的噪声**：C 上 `bank_it` 得到 3 分，命中的是通用词「编程」（capability_group），
不是它真正的能力。因为它内容分 3 < 门槛 6，所以 `eligible=false`、不会被选中——但"无关 Skill 拿到低分"这一点没有消除。

---

## 3. 前端体验结果（真实 Chrome 157 headless / CDP 9222）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 工作 / 实习分栏 | 通过 | 画像页「工作」= DEMO 云计算公司 2022.07-2025.06 后端开发工程师；「实习」= DEMO 电商公司 2021.07-2021.09 后端开发实习生；每条依据链接标注「工作经历」/「实习经历」 |
| 无法分类经历的可见性 | **本轮最小增补** | 简历报告页新增「待确认经历（1）」：DEMO 开源社区 · 文档贡献者 + 原文职责，并说明"不计入任何一类，也不会当作工作年限" |
| JD 强度标签 + 依据 | 通过 | 性质下拉下灰字：「列在任职要求里，原文没有写必须」/「原文写明必须或最低门槛（必须 / 至少 / 及以上）」 |
| 推荐理由与逐字证据分离 | 通过 | 「规则判定（算法按关键词加分，不是 JD 原文）：…」与「JD 原文逐字证据：…」分开列出 |
| 冲突时不自动选 | 通过 | D：`多个方向接近，请手动选择 · 岗位名称与岗位要求指向不同方向：不自动选择`，全页没有「系统首推」 |
| 分值口径 | 通过 | 「命中权重 20（要求与职责 12 + 岗位名称 8）」，并声明"不是概率、百分比或模型置信度" |
| 手动选择不被覆盖 | 通过 | 手动选 Java 后端开发后 3.5 秒仍为 Java 后端开发 |
| 旧 Session 冻结 | 通过 | C 的 v1 建会话 → 改一条要求并重新确认到 v2 → 例会话说仍是「私有 JD 第 1 版」 |

截图：`r20_01_profile_employment`、`r20_02_jd_basis`、`r20_03_jd_ambiguous`、`r20_04_session_v1`、
`r20_05_reconfirmed`、`r20_06_old_session`、`r20_07_unclassified`。

**关于「待确认经历」的判断依据**：第十九轮把无法判定的经历放进 `unclassified_experience`，
但该字段没有出现在任何页面（画像页只展示事实分类，报告页只展示摘要/亮点/风险）。本轮真实样本 E 正好产生 1 条，
确认用户无法核对这份履历，因此按授权做了最小增补：**只读展示，不改画像数据结构、不改类别语义**。

---

## 4. 真实 HTTP / Token / Capture / Replay

| 项目 | 数值 |
|---|---|
| 真实 HTTP | **6**（API 2 + Worker 4） |
| 闸门估算 token | 52,212 / 上限 60,000 |
| 结算 token（`model_admissions.actual_tokens` 合计） | **13,177**（预占 74,425） |
| 延迟 | 2,824 / 2,326 / 11,203 / 3,449 / 4,110 / 1,999 ms |
| 准入状态 | 6 行全部 `SETTLED`，无 UNKNOWN、无 repair、无配额错误 |
| Capture | `.runtime/r20/capture/{api,worker}` 6 个 JSON：route、model、prompt sha256、response_text、usage、latency；**无 Key、无 Authorization、无完整 prompt、无简历原文** |
| Replay | 本轮 6 条 capture 可直接被 `CaptureReplay` 复用；第十八轮 capture 继续可用 |

**两道闸门是分开的**：API 进程 2 次/20k，Worker 进程 4 次/40k，合计正好等于本轮 6 次/60k 上限。
启动器新增的 `--live-max-*` 只能收紧不能放宽（超过 `live_model_gate.LIMITS` 直接退出），并在启动时打印 `LIVE_LIMITS`。

**一次构造性失败（已修）**：第一次上传简历返回 500 `NoSuchBucket`——本轮把 bucket 换成了新的按轮命名，
而 `MinioObjectStorageProvider.upload()` 从不创建 bucket（`ensure_bucket()` 只有知识库导入在调用）。
这是一个**新环境首次上传必失败**的真实缺陷，已修（`2332e06`）并补两条测试。

---

## 5. 产品功能成熟度矩阵

### A. 有真实 Live 证据

| 能力 | Live 证据 | 规模 |
|---|---|---|
| 简历文本抽取 → 结构化 | 本轮 A/E + 第十八轮 A/B | 4 份简历 / 4 次调用 |
| 画像事实选择与技能状态 | 本轮 A/E（prompt 1.0.5）+ 第十八轮 | 4 份画像 |
| 私有 JD 抽取（含强度/章节） | 本轮 C/D + 第十八轮 C/D | 4 份 JD / 4 次调用（另 2 份公开 JD 走同一路由） |
| 面试 Main / Evaluation 主链路 | docs/83（18 题真实长场）、docs/85 | 1 场 18 答 + 1 次 Coach |
| Coach 报告（Harness OFF） | docs/85、docs/93/94 | 4 次真实调用 |
| 预算终止与准入结算 | docs/82/83 + 本轮 6 行 SETTLED | 多轮 |

### B. 只有 Mock/Replay + 浏览器证据

| 能力 | 离线证据 | 说明 |
|---|---|---|
| 私有 JD Skill 推荐 | 规则确定性（0 模型调用）+ 本轮真实 JD 输入 + 17 条单测 | 设计上不需要模型 |
| 私有 JD 训练的创建/幂等/RECHECK | 10 条幂等用例 + RECHECK 用例 | 无模型 |
| 能力记忆（`capability_service`） | 单测 + 长场数据回放 | 无模型 |
| 简历上传→MinIO→Celery→画像 E2E | 第十七/十九轮 pytest E2E + 浏览器 | 模型为 Replay |
| JD 编辑/版本冻结/跨账号隔离 | 3 条编辑证据 + 404 用例 + 本轮浏览器 | — |
| 前端入口竞态 | `interviewEntry` 7 条 node:test | 纯函数 |

### C. 功能存在但验证不充分

1. **无小节 / 弱结构简历**：本轮没有样本，只有离线用例。
2. **纯英文简历与中英混排**：别名支持但无真实样本。
3. **超长 JD**：RULE 截断 `requirements[:30]`、职责 `[:20]`，未验证长 JD 丢关键要求。
4. **Job Matching**（`/match`）：确定性证据矩阵，浏览器有展示，无真实用户材料对照。
5. **公开岗位 Research Agent**：第十八轮验证过抽取（2 次调用），但"抓取→研究→发布"整链没有端到端 Live。
6. **管理端经验审核**：有页面与接口，未见真实运营数据。
7. **Celery 重投递恢复**：`acks_late=True`，简历任务有 `task_id` 陈旧性检查，但 worker 崩溃后的实际重投未演练。

### D. 正式开放真实用户前必须处理

见 §8。

---

## 6. 已通过 Live 的关键链路（可以演示的部分）

1. 注册 → 上传简历 → Celery 解析 → 结构化报告 → 画像版本（`producer=RESUME_PROFILE_AGENT`、`prompt_version=1.0.5`、真实 DeepSeek）。
2. 粘贴私有 JD → 模型解析 → 服务端按章节定强度 → 确认 → 确定性推荐 → 进入面试（技能预选）。
3. 面试主链路的真实长场（docs/83：18 题、18 条正式评价、预算合法终止）与 Coach 报告（docs/85/93）。
4. 版本冻结：改 JD 生成 v2 后，旧会话仍绑定 v1。
5. 预算与准入：每次真实调用都有 `model_admissions` 记录，闸门拒绝时 `request_sent=False`。

---

## 7. 尚未完成真实验证的能力

| 能力 | 缺什么 |
|---|---|
| 简历 B（易误判样本） | 本轮预算用尽未跑；"没有改坏原有正确案例"只有离线证据 |
| 无小节/英文简历 | 无样本，无真实输出 |
| 面试长场在最新代码上的复跑 | 最新长场是 2026-11-11（docs/83），此后有上下文/预算/画像改动 |
| Coach 在新画像上的表现 | coach 最后一次 Live 在 docs/93，画像 prompt 已升到 1.0.5 |
| 私有 JD 训练的完整 UI 闭环 | 第十七/十九轮为离线 + 浏览器，无 Live 模型参与 |
| 管理端与研究 Agent 的端到端 | 无运营数据 |

---

## 8. 正式发布阻断项（D）

| # | 风险 | 现状 | 严重度 |
|---|---|---|---|
| D1 | **数据库版本落差**：生产库 `0025`，代码 head `0027`（`0026_capability_memory`、`0027_user_job_targets`） | 未对生产库执行迁移；新库已按 0027 验证 | 阻断"直接部署到现有生产库"；对新装环境不阻断 |
| D2 | **用户数据删除不可行**：简历被画像/证据引用时 `409 RESUME_REFERENCED_BY_PROFILE`；JD 被会话引用时 `409 JOB_TARGET_IN_USE`；两者都没有软删除/匿名化路径 | 设计上"保历史"，但用户无法删除自己的私人材料 | 阻断"正式开放真实用户"（个人信息删除诉求） |
| D3 | **JD 抽取用量记在 `legacy_llm`**：`governed_call` 在无 `model_scope` 时用兜底路由名，JD 抽取没有设置 scope | 预算正确（按用户计），但**无法按功能归因花费** | 高：审计/成本核算需要 |
| D4 | **Celery 重投递语义未验证**：`acks_late=True` 且无 `autoretry`，worker 崩溃后任务可能重跑一次模型调用；简历任务有陈旧性检查，但重跑会重复计费 | 未演练 | 中高 |
| D5 | **UNKNOWN 模型请求**：答题路径有 `unknown_model_calls` 围栏；`ModelAdmission` 无 `business_key`，其他路径（简历/JD）没有等价围栏 | 未验证 | 中 |
| D6 | **未推送备份**：全部代码只在本地（见 §9） | 远端落后 96 个提交 | 高（机器损坏即丢失） |
| D7 | **前端未做生产化**：`vite build` 通过，但没有部署编排、没有 HTTPS/域名、错误上报 | 演示用 dev server | 中（演示可接受） |

---

## 9. 本地与远端 Git 状态、备份风险

- 本地 HEAD：`3e19529` + 本轮 3 个代码提交（见 §10），分支 `master`，工作区仅 `.workbuddy/` 未跟踪。
- 远端基线：`origin/master = 411c345`（"feat: checkpoint interview training and ModelScope support"），
  本地领先 **99 个提交**（本轮开始时 93；本轮新增 6 次提交：3 个代码 + docs/99 + `.codebuddy` 记忆 + 本处计数修正）。
- 本轮检查远端时 GitHub 不可达：`schannel: server closed abruptly (missing close_notify)`，
  **未能确认远端当前实际 head**，只能读到上次 fetch 时的 `411c345`；`git ls-remote` 同样失败。
- 未执行 push / merge / rebase / reset / clean；未修改历史。
- 敏感信息只读扫描（检查时 `origin/master..HEAD` 的 169 个改动文件、全部新增行）：
  - 没有 `sk-`、`Bearer <token>`、`AKIA`、`-----BEGIN`、`api_key=` 命中；
  - 没有非 `example.com` 邮箱、没有手机号；
  - `.gitignore` 覆盖 `.env`、`.runtime/`、`dist/`、`.venv/`、`node_modules/`，仓库内只有 `.env.example`；
  - `.runtime/**`（含本轮 capture 与样本）**不在**版本库中。
- 备份建议：先在一台可信机器或私有远端推送一次（或至少 `git bundle` 归档），再谈冻结演示版。

---

## 10. 最终建议

**结论：可以冻结为"演示版"，不建议直接小范围真实用户试用。**

依据：

1. 三项修复在真实模型下**都成立**，而且第一项（工作/实习）是本轮**模型侧**首次被证实改对，不再是服务端兜底。
2. 演示链路（简历→画像、JD→推荐→面试、冻结版本、预算审计）在本轮都有真实证据，可以讲清楚"哪里是模型、哪里是规则、哪里是服务端裁决"。
3. 但 D2（用户数据无法删除）与 D1（生产库 0025→0027 落差）属于**对外承诺层面**的风险，不是演示层面；
   真实用户一旦上传简历就无法撤回，这个问题在试用阶段会直接变成信任问题。
4. D3/D4/D5 是审计与恢复的完整性缺口：演示能跑，出事说不清。

**进入小范围试用前的最小清单（建议按序）**：
1. 推送/备份代码（D6），并确认远端可用。
2. 给简历与私有 JD 提供"删除/匿名化"路径（D2）——哪怕只是"删除画像与证据后允许删除简历"。
3. 让 JD 抽取写入真实 route（D3），否则账单无法按功能归因。
4. 演练一次 worker 崩溃后的重投（D4），记录是否会重复计费。
5. 在现有生产库上演练 0025→0027（D1），并准备回滚。

**本轮代码改动（3 个提交）**：
- `090ea14` 闸门可按轮收紧（6/60k/15min 才可能被真正执行）
- `ee08ec0` 简历报告页新增「待确认经历」只读展示
- `2332e06` 首次上传自动创建 bucket（本轮真实 500 的修复）

**测试**：业务全量 **930 passed / 0 failed / 1 skipped**；迁移专项 **10 passed**；前端单测 7 passed；
`vue-tsc && vite build` 通过。跳过的 1 条仍是缺本地 `realworld_eval/v1` benchmark bundle，与本轮无关。

**真实模型 HTTP**：6（上限 6）。未跑面试/Evaluation/Coach。

**是否可以开始下一轮功能开发**：不建议。下一步应由产品判断"演示版冻结"还是"按上述清单补 5 项"，不宜继续加功能。
