# R130：面试官自主决策、三种题型混合与前端产品化

日期：2026-11-18 · 基线：本地 HEAD `2010d81`（R127–R129 的改动仍在工作区，本轮在其之上增量修改，未提交、未 Push）
模型 HTTP：**0**（本轮只做代码、脚本化对话与浏览器只读验证）

---

## 1. #630 的真实决策链

展示库 `interview_agent_showcase_r123_20261117` 只读核查结果：

| 项 | 值 |
| --- | --- |
| 会话 | #630，COMPLETED，`QUESTION_COUNT_REACHED` |
| 计划/实际 | 3 题 / 3 题（`introduction_count=0`，`technical_question_count=3`） |
| 冻结 Main Prompt | `interview_main 2.9.0`；Evaluation `2.3.4` |
| 冻结 Skill | `java_backend 1.0.0`，priority_topics 前三位恰好是 java / redis / kafka |
| 三题 | 562 `NEXT`(redis, fact) → 563 `FOLLOW_UP`(redis) → 564 `FOLLOW_UP`(redis) |
| 两次追问理由 | 都是 `MISSING_MECHANISM` + `ANSWER_TOO_GENERAL` |
| 三次评价 | 3/2/2、2/1/1、1/1/1（技术/覆盖/深度），逐级下降 |

重放当时的 `planning_payload`（不改数据，只重算）可见**换题一直是合法的**：

- 第 2 次决策（答完 562）：`choices = [java: NEXT, redis: FOLLOW_UP/REQUEST_EXAMPLE/DEEP_DIVE, kafka: NEXT]`
- 第 3 次决策（答完 563）：同上，redis 仍只有追问，java/kafka 仍有 NEXT

所以 **#630 不是"模型想换题被控制器拦住"**，而是三个因素叠加：

1. **追问判据按"这一次回答"计数，不按"这条线索"计数。**
   旧提示词写的是"相关但含糊的回答可以获得一次自然澄清"；模型每次都把"上一次追问后的回答"当成新的一次含糊回答，于是永远满足"可以再澄清一次"的条件。控制器只在 `follow_ups_used >= 2` 时才收回追问权限——而 3 题预算下，2 次追问正好把整场用完。
2. **Evaluation 的 `missing_points` 事实上成了待办清单。**
   两次追问的理由都是 `MISSING_MECHANISM`，题目措辞也是把缺失点直接翻成问句（"库存这个 key 具体怎么存"→"库存数量本身怎么存"）。提示词里"不要把 missing_points 变成下一句"只是软约束，没有可判定的信号。
3. **控制器没有任何"这条线索已经问不出新东西"的事实。**
   `adaptive` 里只有 `knowledge_state` 与 `choices`，没有"这条线索用了几次追问""上一次回答有没有带来新信息""还剩几个名额"。模型只能靠语感判断，而语感在短面试里倾向于继续挖。

另外确认了一条真实限制：**同一目标内部无法换角度**。`decision_space` 对当前目标只给 `FOLLOW_UP/REQUEST_EXAMPLE/DEEP_DIVE`，因此"Redis 秒杀项目 → Redis 基础（RDB/AOF）"这种最自然的项目转八股，在旧代码里必须换到另一个目标才合法。

## 2. P0：把"该不该继续挖"变成可判定的事实

### 2.1 `adaptive.thread`（新增，纯确定性，零模型调用）

`adaptive.py::thread_pacing()` 从**已持久化的题目 + 评价后的知识状态**推导：

```
root_question_id / follow_ups_used / follow_ups_allowed
same_topic_streak / remaining_slots
latest_answer_added_information   # 本次回答是否解决了旧缺口或产生了正向观察
resolved_gap_count
stalled            # 已用掉一次追问 且 本次回答没有带来新信息
prefer_switch      # 同一主题已连续 2 次 且 只剩 1 个名额
```

`stalled` 的判据刻意保守：必须是"已经澄清过一次"且"这次既没解决旧缺口、也没有任何 `PARTIALLY_DEMONSTRATED`/`DEMONSTRATED_IN_SESSION` 观察"。**回答有料就不会被判停滞**，深度追问不受影响；只有"问了一轮还是原地"才触发。

### 2.2 控制器收回追问权（不是靠提示词）

`decision_space()` 在 `stalled=true` 且**存在其他目标**时，把当前目标整条移出合法空间，模型只能换题；若其他目标都不可用（都已被证明或冷却），则保留追问，避免把模型逼进无解。

这条规则让 #630 的情形变成：答完 563 且无进展 → 不再提供 redis 追问，第三个名额用于换题。

### 2.3 同一目标可以换角度（解开真实限制）

当前目标新增 `NEXT` 选项，但**排除本题已用过的那个冻结来源**（`exclude_source_ref`）。于是"项目题 → 同主题八股/场景"合法，而"把同一道题换个说法再问"仍然不合法（重复措辞另有指纹校验）。

### 2.4 Prompt 3.0.0

`interview_main 3.0.0` = 冻结的 `2.9.0` 原文 + `QUESTION SHAPES` + `THREAD PACING` 两节。核心规则从"软提示"变成"对着 payload 里的字段做判断"：

- `stalled=true` → 不许把同一细节换第三种问法；
- `stalled=false` **不等于**必须追问：答得充分可以收线换题，答得深可以继续挖；
- `prefer_switch=true` → 少名额时优先换目标；
- 不把 `missing_points` 当清单，不对"我不知道"重复追问。

`context_projection` 白名单加入 `3.0.0`，`2.9.0/2.8.0/2.7.0/2.6.0/2.5.0/2.3.3` 全部保留；已冻结会话的 Prompt 文本一字未改，Evaluation 仍是 `2.3.4`。

## 3. P1：三种题型混合，不新增 Agent

题型不是新的 Agent，也不是新的枚举，而是**题源能承载的形状**。`decision_candidates()` 现在为每个候选发布 `question_shapes`（只对 `NEXT` 有意义）：

| 冻结题源 | 可用形状 |
| --- | --- |
| 候选人项目事实（`projects/work/internship`） | `PROJECT`、`SCENARIO` |
| 候选人技能事实（`skills`） | `FUNDAMENTALS` |
| 岗位要求（`JOB_REQUIREMENT`） | `FUNDAMENTALS`、`SCENARIO` |
| 公共题（`PUBLIC_QUESTION`） | `FUNDAMENTALS` |
| 通用 Skill 主题（`DYNAMIC_GENERATED`） | `FUNDAMENTALS`、`SCENARIO` |

约束全部保留：

- 不规定比例、不要求三种都出现；一场短面试可以只用一种；
- 项目线程可以自然演化为场景；
- 八股/场景必须有合法题源：要求、Skill 主题或已冻结的公共题；
- 目标主题、来源绑定、`validate_decision`、`focus_gap_ids`、证据引用一条没放松；
- 风格只影响追问力度与措辞，不影响题型与评分。

**为什么不会重新触发 `ADAPTIVE_TOPIC_MISMATCH`**：校验只在"题目没有点名目标主题"时才要求"题源冻结文本本身含该主题 + 题目不涉及其他技术主题"。场景题通常点名目标（"Redis 主从切换…MySQL 兜底"），点名即通过；要求驱动的八股即使不重复"Redis"，也由冻结要求文本兜底。真正跑偏（通用 Skill 题源却问 Kafka）仍然拒绝——见测试。

## 4. P2：前端产品化（只改文案与层级，不重做页面）

### 4.1 改了什么

| 位置 | 之前 | 之后 |
| --- | --- | --- |
| 首页标题 | 面向秋招的多 Agent 求职与模拟面试系统 | 准备你的下一场技术面试 |
| 首页 | 6 个 Agent 名称标签、"本地开发说明"（JWT/Live Search） | 移除；只留"简历 → 面试 → 复盘"路径 |
| 首页指标 | 待处理长期记忆建议 | 待你确认的记忆建议 |
| 报告 · 练习推荐 | 当前可训练候选（规则生成 · 读取时计算 · 0 次模型调用）／按当前正式能力状态与 training.modes() 重新计算… | 可以接着练什么／根据你最新的面试表现推荐练习方向；建议会随后续面试表现更新 |
| 报告 · 练习条目 | 规则优先级 24、正式依据、能力修订、scope_note、computed_at、modes_source | 只留方向名 + 类型 + 一句理由；其余进"高级详情" |
| 报告 · 建议措辞 | 来源：模型在生成本报告时给出…不是已排好的训练，也不是此刻重新计算的可执行状态… | 这是生成报告时给出的建议，不会自动排成训练。 |
| 报告 · 技术详情 | 报告 ID / 版本 / 提示版本 | "高级详情"，措辞改为第 N 次生成 |
| 训练中心 | 已注册 Skill、按现有准入规则、训练候选读取失败 | 方向、直接说明、练习方向读取失败 |
| 方向推荐 | ranking_points / content_points / title_points、推荐输入指纹 | 匹配依据（按关键词匹配，不等于岗位原文）；权重与标识进"高级详情" |
| 面试页 | Skill 版本 / 策略版本、题目技术详情、自适应考察范围（本场固定） | 本场已固定说明、高级详情、本场考察范围 |
| 成长记录 | 本人确认的 长期成长记忆、稳定能力认证 | 本人确认的长期记忆、能力认证 |
| 全站副标题 | 面向秋招的多 Agent 求职与模拟面试系统 | 简历 → 模拟面试 → 复盘成长 |

后端用户可见字符串同步改写（`training_candidates.SEMANTICS`、状态理由、`skill_recommendation.RANKING_SEMANTICS` / `TARGET_SKILL_DISCLAIMER` / `confidence_note`），因为这些字符串会直接渲染在页面上。

### 4.2 保留了什么

- 评分依据、五维分数、逐题复盘、参考答案；
- 记忆的"本人确认"与"系统推断"分栏、来源场次、批准/修正/撤销；
- 隐私与风险说明（岗位描述未经企业核实、推荐不代表能力、记忆需本人确认）；
- 错误提示与"安全重试"；
- 审计与可追溯性：所有技术细节仍在"高级详情"折叠区，默认不展示。

### 4.3 截图

`data/r130_ui/`：

- `11_dashboard_before.png` / `10_dashboard_after.png`：首页标题与开发说明
- `22_report_training_before.png` / `21_report_training_after.png`：报告页练习推荐区块
- `20_report_after.png`、`30_training_after.png`、`40_memory_after.png`、`50_interview_after.png`：其余页面现状

> 说明：两张 before 图是把对应 Vue 文件临时还原成 R129 文案后**在同一账号、同一会话数据下重拍**的，拍完立即按哈希校验恢复原文件；不是历史截图。首页 before 图中 Agent 标签已随组件一起移除，故未出现在画面里。

## 5. 验证

### 5.1 自动化（0 模型 HTTP）

```
122 passed  （11 个相关测试文件，新库 interview_agent_v21_r129_20261118）
```

新增 `apps/api/tests/test_r130_interviewer_judgement.py`（6 项）：

1. Prompt 3.0.0 含 `QUESTION SHAPES` / `THREAD PACING`，且 2.9.0 文本未被改写、Evaluation 仍 2.3.4；
2. **#630 反向用例**：项目题 → 一次澄清 → 无新信息 ⇒ `thread.stalled=true`、redis 整条从合法空间消失、第三题落到 kafka 场景题；主/评调用各 3/2 次，没有为"停滞"补一次调用；
3. 答得有料时 `stalled=false`，追问仍然合法（深挖不被误伤）；
4. `question_shapes` 按冻结题源下发（项目事实带 PROJECT+SCENARIO，技能事实只带 FUNDAMENTALS，要求/通用主题带 FUNDAMENTALS+SCENARIO）；
5. 项目题演化成场景题、要求驱动的八股（不重复"Redis"）都能过门禁；通用 Skill 题源跑偏到 Kafka 仍报 `ADAPTIVE_TOPIC_MISMATCH`；
6. 项目线程可以用**同一目标的另一个冻结来源**转成八股（NEXT + `requirement:` 来源），而不是被迫换主题。

同时更新了三处旧断言，因为它们描述的是 R130 明确要改的旧行为：

- `test_r125_conversation.py`：原来断言"同一线索问两次不知道后还给第二次追问"，现在断言"一次澄清后换题"；
- `test_v211_director_contract.py`：`NEXT` 原来必须换目标，现在允许"同目标 + 不同来源"，但仍不允许复用同一 target+source；
- `test_training_candidates.py`：`semantics` 不再含"读取这一刻"这类内部表述。

### 5.2 浏览器（只读）

- 首页：`多 Agent:false / 本地开发说明:false`；
- 报告页：`training.modes():false / 规则投影:false / 规则优先级:false / 准入规则:false / 提示版本:false`；
- 面试页：`评分显示` 双模式仍在（模拟：结束后看 / 练习：提交后看），进行中的评分折叠未被破坏；
- 三个页面在 1440px 下无横向溢出、无错位。

### 5.3 账本

`experience_agent_runs` 在 06:40 UTC（本轮开始）之后新增 **0** 条；#630 的 8 次调用与报告 2 次调用都是 14:25 前后你自己那场测试产生的。

## 6. 修改文件

后端（`apps/api/src/interview_api/modules/interview/`）：
`adaptive.py`、`adaptive_prompts.py`、`context_projection.py`、`controller.py`、`skill_recommendation.py`、`training_candidates.py`

前端（`apps/web/src/`）：
`pages/InterviewReportPage.vue`、`pages/InterviewChatPage.vue`、`pages/DashboardPage.vue`、`pages/JobTargetPage.vue`、`pages/JobMatchingPage.vue`、`pages/TrainingPage.vue`、`pages/MemoryPage.vue`、`layouts/PublicLayout.vue`

测试（`apps/api/tests/`）：
新增 `test_r130_interviewer_judgement.py`；更新 `test_r125_conversation.py`、`test_v211_director_contract.py`、`test_director_reference_repair.py`、`test_r127_adaptive_recovery.py`、`test_r129_relevance_and_dialogue.py`、`test_training_candidates.py`

未改：Evaluation 2.3.4、Skill 注册表与数量、岗位库、正式库、#630 任何数据、已冻结会话的 Prompt 文本。未提交、未 Push。

## 7. 需要你真人验收

1. **短面试（3 题）**：故意在第一题答得含糊、追问后再给一次模糊回答，看第三题是否换到别的方向（而不是第三次问同一个 key）。
2. **有料的回答**：在某一题讲得足够细，确认面试官仍会继续深挖，而不是机械换题。
3. **项目转八股**：观察是否出现"项目聊完→同主题基础题/场景题"，且题目仍然对得上目标。
4. **题型自然度**：场景题是否像真实工程追问，而不是把 `missing_points` 翻译成问句。
5. **前端**：首页、报告、训练、成长记录是否还有你觉得"像后台"的措辞；面试页双模式是否符合预期。

真实对话质量只能由你这一场模型测试判断；本轮所有对话验证都是脚本化契约测试，不代表模型真实表达。
